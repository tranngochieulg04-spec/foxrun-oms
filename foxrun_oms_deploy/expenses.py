"""Formula-based fees for reconciliation periods."""
import csv
import hashlib
import io
import json
from decimal import Decimal, DecimalException
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, ForeignKey, Integer, Text, select, update

from sheet_formulas import CENT, MAX_FEE, Formula

FIELDS = ('carrier_fee', 'service_fee')


class ExpenseRule(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    formula: str = Field(min_length=1, max_length=240)
    fee: Literal['carrier_fee', 'service_fee'] = 'service_fee'
    status: Literal['ALL', 'DELIVERED', 'RETURNED'] = 'ALL'
    currency: Literal['', 'MYR', 'THB', 'PHP', 'IDR', 'USD', 'VND'] = ''
    customer_id: int | None = Field(default=None, gt=0)

    @field_validator('name', 'formula', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class RateRow(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)

    @field_validator('key', mode='before')
    @classmethod
    def trim_key(cls, value):
        return value.strip() if isinstance(value, str) else value


class ExpenseInput(BaseModel):
    rates: list[RateRow] | None = Field(default=None, max_length=500)
    rules: list[ExpenseRule] = Field(max_length=50)
    version: int = Field(ge=1)


class ExpenseApply(ExpenseInput):
    preview_key: str = Field(min_length=64, max_length=64)


def compile_rules(rules):
    compiled = []
    names = set()
    for rule in rules:
        name = rule.name.casefold()
        if name in names:
            raise HTTPException(422, 'Tên khoản phí bị trùng: ' + rule.name)
        names.add(name)
        try:
            compiled.append((rule, Formula(rule.formula)))
        except (ValueError, SyntaxError, DecimalException) as error:
            detail = str(error) if isinstance(error, ValueError) else 'Cú pháp hoặc số không hợp lệ.'
            raise HTTPException(422, rule.name + ': ' + detail) from error
    return compiled


def matches(rule, order):
    return (
        (rule.status == 'ALL' or rule.status == order.status)
        and (not rule.currency or rule.currency == order.currency)
        and (rule.customer_id is None or rule.customer_id == order.customer_id)
    )


def row_amounts(compiled, order, quantities, lookup):
    cod = Decimal(order.cod or 0)
    variables = {'cod': cod, 'cod_thu': cod if order.status == 'DELIVERED' else Decimal(0),
                 'so_luong': Decimal(sum(quantities.values())), 'so_sku': Decimal(len(quantities)), 'don': Decimal(1),
                 'khach': order.customer.code, 'tien_te': order.currency, 'trang_thai': order.status,
                 'true': True, 'false': False, **lookup}
    items, amounts = {}, {}
    for index, (rule, formula) in enumerate(compiled):
        if not matches(rule, order):
            continue
        try:
            value = formula.amount(variables)
            items[str(index)] = str(value)
            amounts[rule.fee] = amounts.get(rule.fee, Decimal(0)) + value
            if amounts[rule.fee] > MAX_FEE:
                raise ValueError('Tổng phí vượt giới hạn tiền của hệ thống.')
        except DecimalException as error:
            raise ValueError(rule.name + ': Kiểm tra phép chia cho 0 và giới hạn số.') from error
        except ValueError as error:
            raise ValueError(rule.name + ': ' + str(error)) from error
    return items, amounts


def install(app, workflow, periods):
    global w, p, Sheet
    w, p = workflow, periods

    class Sheet(w.Base):
        __tablename__ = 'workflow_expense_sheets'
        period_id = Column(Integer, ForeignKey('workflow_reconciliation_periods.id'), primary_key=True)
        payload = Column(Text, nullable=False)

    w.Base.metadata.create_all(w.engine)

    @app.get('/api/wf/periods/{pid}/expenses')
    def get_expenses(pid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        period = get_period(db, pid)
        sheet = load_sheet(db, pid)
        return {'version': period.version, 'state': period.state, **sheet, 'totals': fee_totals(sheet['rows'])}

    @app.post('/api/wf/periods/{pid}/expenses/preview')
    def preview_expenses(pid: int, data: ExpenseInput, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        period = get_period(db, pid, data.version)
        return make_plan(db, period, data.rules, data.rates)

    @app.post('/api/wf/periods/{pid}/expenses/apply')
    def apply_expenses(pid: int, data: ExpenseApply, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        period = get_period(db, pid, data.version, lock=True)
        plan = make_plan(db, period, data.rules, data.rates)
        if plan['preview_key'] != data.preview_key:
            raise HTTPException(409, 'Dữ liệu đã thay đổi. Xem trước lại trước khi áp dụng.')
        if plan['errors']:
            raise HTTPException(422, 'Có đơn chưa tính được phí. Kiểm tra bảng xem trước.')
        changed = db.execute(update(p.Period).where(p.Period.id == pid, p.Period.version == data.version,
                                                    p.Period.state == 'DRAFT').values(version=p.Period.version + 1))
        if changed.rowcount != 1:
            raise HTTPException(409, 'Đợt vừa thay đổi. Mở lại đợt để tính phí.')
        saved = {'rules': plan['rules'], 'rates': plan['rates'], 'rows': []}
        changed_orders = 0
        for row in plan['rows']:
            if not row['managed'] and row['before'] == row['after']:
                continue
            order, flow = w.lock_order(db, row['id'], row['version'])
            if row['before'] != row['after']:
                for field, value in row['after'].items():
                    setattr(flow, field, Decimal(value))
                flow.reconciled = 0
                w.touch(flow)
                w.audit(db, user, 'expense.apply', {'order_id': order.id, 'period_id': pid,
                                                     'before': row['before'], 'after': row['after']})
                changed_orders += 1
            if row['managed']:
                saved['rows'].append({k: row[k] for k in ('id', 'code', 'customer', 'tracking', 'currency',
                                                         'items', 'managed', 'baseline', 'after', 'status', 'customer_code')})
        sheet = db.get(Sheet, pid)
        if not sheet:
            sheet = Sheet(period_id=pid)
            db.add(sheet)
        sheet.payload = json.dumps(saved, ensure_ascii=False)
        w.audit(db, user, 'period.expenses', {'period_id': pid, 'rules': saved['rules'], 'rates': saved['rates'], 'changed': changed_orders})
        db.commit()
        return {'changed': changed_orders, 'orders': len(saved['rows'])}

    @app.get('/api/wf/periods/{pid}/expenses/export-xlsx')
    def export_expenses_xlsx(pid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        get_period(db, pid)
        buffer = make_workbook(load_sheet(db, pid))
        return StreamingResponse(
            buffer, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="foxrun_expenses_{pid}.xlsx"'},
        )

    @app.get('/api/wf/periods/{pid}/expenses/export')
    def export_expenses(pid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        get_period(db, pid)
        sheet = load_sheet(db, pid)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['Mã đơn', 'Khách', 'Mã vận đơn', 'Tiền tệ',
                         *(csv_text(rule['name']) for rule in sheet['rules']), 'Phí hãng', 'Phí FOXRUN'])
        for row in sheet['rows']:
            writer.writerow([*(csv_text(row[k]) for k in ('code', 'customer', 'tracking', 'currency')),
                             *(row['items'].get(str(i), '') for i in range(len(sheet['rules']))),
                             *(row['after'][field] if field in row['managed'] else '' for field in FIELDS)])
        return StreamingResponse(iter(['\ufeff'+buffer.getvalue()]), media_type='text/csv; charset=utf-8',
                                 headers={'Content-Disposition': f'attachment; filename="foxrun_expenses_{pid}.csv"'})


def make_workbook(sheet):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    details = book.active
    details.title = 'Chi tiết phí'
    rules = sheet['rules']
    details.append(['Mã đơn', 'Khách', 'Mã vận đơn', 'Tiền tệ', 'Trạng thái',
                    *(rule['name'] for rule in rules), 'Phí hãng tự tính', 'Phí FOXRUN tự tính'])
    for row in sheet['rows']:
        details.append([
            row['code'], row['customer'], row['tracking'], row['currency'], row.get('status', ''),
            *(Decimal(row['items'][str(i)]) if str(i) in row['items'] else None for i in range(len(rules))),
            *(Decimal(row['after'][field]) if field in row['managed'] else None for field in FIELDS),
        ])
    summary = book.create_sheet('Tổng hợp')
    summary.append(['Tiền tệ', *(rule['name'] for rule in rules), 'Phí hãng tự tính', 'Phí FOXRUN tự tính'])
    for currency, totals in sorted(fee_totals(sheet['rows']).items()):
        amounts = [totals['items'].get(str(i)) for i in range(len(rules))]
        amounts += [totals['after'].get(field) for field in FIELDS]
        summary.append([currency, *(Decimal(amount) if amount is not None else None for amount in amounts)])
    formulas = book.create_sheet('Công thức OMS')
    formulas.append(['Khoản phí', 'Công thức', 'Nhóm phí', 'Trạng thái', 'Tiền tệ', 'ID khách'])
    for rule in rules:
        formulas.append([rule['name'], rule['formula'], rule['fee'], rule['status'], rule['currency'], rule.get('customer_id')])
    rates = book.create_sheet('Bảng tra phí')
    rates.append(['Mã tra', 'Mức phí'])
    for row in sheet['rates']:
        rates.append([row['key'], Decimal(row['amount'])])
    for tab in book:
        tab.freeze_panes = 'A2'
        tab.auto_filter.ref = tab.dimensions
        widths = {}
        for row in tab.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    # Preserve tracking zeros and export formulas as text for review.
                    cell.data_type = 's'
                    cell.number_format = '@'
                elif isinstance(cell.value, Decimal):
                    cell.number_format = '#,##0.00'
                widths[cell.column] = max(widths.get(cell.column, 10), len(str(cell.value or '')) + 2)
                if cell.row == 1:
                    cell.font = Font(color='FFFFFF', bold=True)
                    cell.fill = PatternFill('solid', fgColor='17324D')
        for column, width in widths.items():
            tab.column_dimensions[get_column_letter(column)].width = min(width, 54)
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return buffer


def csv_text(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def get_period(db, pid, version=None, lock=False):
    query = select(p.Period).where(p.Period.id == pid)
    period = db.scalar(query.with_for_update() if lock else query)
    if not period:
        raise HTTPException(404, 'Không tìm thấy đợt đối soát.')
    if version is not None:
        if period.state != 'DRAFT':
            raise HTTPException(409, 'Mở lại đợt đã chốt trước khi đổi bảng kê phí.')
        if period.version != version:
            raise HTTPException(409, 'Đợt vừa thay đổi. Mở lại đợt để tính phí.')
    return period


def load_sheet(db, pid):
    sheet = db.get(Sheet, pid)
    payload = json.loads(sheet.payload) if sheet else {'rules': [], 'rows': []}
    return {'rates': [], **payload}


def remove_sheet(db, pid):
    sheet = db.get(Sheet, pid)
    if sheet:
        db.delete(sheet)


def check_manual_fees(db, order, flow, data):
    line = db.get(p.PeriodLine, order.id)
    if not line:
        return
    saved = next((row for row in load_sheet(db, line.period_id)['rows'] if row['id'] == order.id), None)
    if saved and any(getattr(data, field) != Decimal(getattr(flow, field)) for field in saved['managed']):
        raise HTTPException(409, 'Phí này lấy từ bảng kê tự tính. Sửa công thức trong đợt đối soát để đổi phí.')


def check_sheet_current(db, period):
    sheet = load_sheet(db, period.id)
    if not sheet['rules']:
        return
    plan = make_plan(db, period, [ExpenseRule(**rule) for rule in sheet['rules']])
    before = {row['id']: (row['items'], row['managed']) for row in sheet['rows']}
    after = {row['id']: (row['items'], row['managed']) for row in plan['rows'] if row['managed']}
    if plan['errors'] or before != after or any(row['before'] != row['after'] for row in plan['rows']):
        raise HTTPException(409, 'Bảng kê phí đã thay đổi. Xem trước và áp dụng lại trước khi chốt đợt.')


def fee_totals(rows):
    totals = {}
    for row in rows:
        if row.get('error') or not row['managed']:
            continue
        group = totals.setdefault(row['currency'], {'items': {}, 'after': {}})
        for index, amount in row['items'].items():
            group['items'][index] = group['items'].get(index, Decimal(0)) + Decimal(amount)
        for field in row['managed']:
            group['after'][field] = group['after'].get(field, Decimal(0)) + Decimal(row['after'][field])
    return {currency: {group: {key: str(amount) for key, amount in values.items()}
                       for group, values in totals_by_currency.items()}
            for currency, totals_by_currency in totals.items()}


def make_plan(db, period, rules, rates=None):
    compiled = compile_rules(rules)
    previous_sheet = load_sheet(db, period.id)
    if rates is None:
        rates = [RateRow(**row) for row in previous_sheet['rates']]
    rate_keys = [row.key.casefold() for row in rates]
    if len(set(rate_keys)) != len(rate_keys):
        raise HTTPException(422, 'Mã tra phí bị trùng.')
    lookup = {'bang_gia': tuple((row.key, row.amount) for row in rates),
              'ma_tra': tuple(row.key for row in rates), 'phi_tra': tuple(row.amount for row in rates)}
    old_rows = {row['id']: row for row in previous_sheet['rows']}
    rows = []
    query = select(p.PeriodLine).where(p.PeriodLine.period_id == period.id).order_by(p.PeriodLine.order_id)
    for line in db.scalars(query):
        order = db.get(w.Order, line.order_id)
        flow = db.get(w.Flow, order.id)
        before = {field: str(Decimal(getattr(flow, field, 0) or 0).quantize(CENT)) for field in FIELDS}
        previous = old_rows.get(order.id, {})
        baseline = {field: previous.get('baseline', {}).get(field, before[field]) for field in FIELDS}
        row = {'id': order.id, 'code': order.code, 'customer': order.customer.name, 'tracking': order.tracking or '',
               'currency': order.currency, 'status': order.status, 'customer_code': order.customer.code, 'version': flow.version if flow else 0, 'before': before,
               'after': dict(before), 'items': {}, 'managed': [], 'baseline': {}, 'error': ''}
        try:
            items, amounts = row_amounts(compiled, order, w.quantities(order), lookup)
            row['items'] = items
            row['managed'] = sorted(amounts)
            row['baseline'] = {field: baseline[field] for field in amounts}
            for field in FIELDS:
                if field in amounts:
                    row['after'][field] = str(amounts[field].quantize(CENT))
                elif field in previous.get('managed', []):
                    row['after'][field] = baseline[field]
            if row['managed'] or before != row['after']:
                if order.status not in {'DELIVERED', 'RETURNED'}:
                    raise ValueError('Đơn chưa có kết quả giao hoặc hoàn.')
                if before != row['after'] and flow and flow.seller_paid:
                    raise ValueError('Đơn đã ghi nhận tiền thanh toán seller, không thể thay đổi phí.')
        except ValueError as error:
            row['error'] = str(error)
        rows.append(row)
    payload = {'rules': [rule.model_dump() for rule in rules], 'rates': [rate.model_dump(mode='json') for rate in rates], 'rows': rows,
               'errors': sum(bool(row['error']) for row in rows), 'totals': fee_totals(rows)}
    payload['preview_key'] = hashlib.sha256(json.dumps([period.id, period.version, payload], sort_keys=True).encode()).hexdigest()
    return payload
