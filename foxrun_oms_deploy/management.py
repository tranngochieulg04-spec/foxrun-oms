"""Bulk operations, recoverable deletion and reconciliation periods."""
import csv
import io
import json
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, select, update
from sqlalchemy.exc import IntegrityError

import expenses

LOCAL_TIME = timezone(timedelta(hours=7))


class OrderSelection(BaseModel):
    id: int
    version: int = Field(ge=0)


class BulkInput(BaseModel):
    orders: list[OrderSelection] = Field(min_length=1, max_length=500)
    action: str
    status: str = ''
    note: str = Field(default='', max_length=2000)
    carrier: str = Field(default='', max_length=100)
    assigned: str = Field(default='', max_length=100)


class PeriodInput(BaseModel):
    name: str = Field(default='', max_length=200)
    date_from: date
    date_to: date
    customer_id: int | None = None
    date_basis: str = 'completion'


class PeriodBulkCreateInput(BaseModel):
    date_from: date
    date_to: date
    date_basis: str = 'completion'


class PeriodAction(BaseModel):
    action: str
    version: int


def install(app, workflow, action_handler, claim_handler):
    global w, DeletedOrder, DeletedCustomer, DeletedImport, VoidedReceipt, Period, PeriodLine, ImportInfo
    w = workflow

    class DeletedOrder(w.Base):
        __tablename__ = 'workflow_deleted_orders'
        order_id = Column(Integer, ForeignKey('orders.id'), primary_key=True)
        deleted_at = Column(DateTime(timezone=True), nullable=False, default=w.now)
        actor = Column(String(100), nullable=False)

    class DeletedCustomer(w.Base):
        __tablename__ = 'workflow_deleted_customers'
        customer_id = Column(Integer, ForeignKey('customers.id'), primary_key=True)
        deleted_at = Column(DateTime(timezone=True), nullable=False, default=w.now)
        actor = Column(String(100), nullable=False)

    class DeletedImport(w.Base):
        __tablename__ = 'workflow_deleted_imports'
        preview_id = Column(String(64), ForeignKey('workflow_previews.id'), primary_key=True)
        deleted_at = Column(DateTime(timezone=True), nullable=False, default=w.now)

    class ImportInfo(w.Base):
        __tablename__ = 'workflow_import_info'
        preview_id = Column(String(64), ForeignKey('workflow_previews.id'), primary_key=True)
        filename = Column(String(255), nullable=False)

    class VoidedReceipt(w.Base):
        __tablename__ = 'workflow_voided_receipts'
        ledger_id = Column(Integer, ForeignKey('workflow_stock_ledger.id'), primary_key=True)
        reversed_by = Column(String(100), nullable=False)
        reversed_at = Column(DateTime(timezone=True), default=w.now, nullable=False)

    class Period(w.Base):
        __tablename__ = 'workflow_reconciliation_periods'
        id = Column(Integer, primary_key=True)
        name = Column(String(200), nullable=False)
        date_from = Column(String(10), nullable=False)
        date_to = Column(String(10), nullable=False)
        date_basis = Column(String(20), nullable=False)
        customer_id = Column(Integer, ForeignKey('customers.id'))
        state = Column(String(20), default='DRAFT', nullable=False)
        version = Column(Integer, default=1, nullable=False)
        created_at = Column(DateTime(timezone=True), default=w.now, nullable=False)
        actor = Column(String(100), nullable=False)

    class PeriodLine(w.Base):
        __tablename__ = 'workflow_reconciliation_period_lines'
        order_id = Column(Integer, ForeignKey('orders.id'), primary_key=True)
        period_id = Column(Integer, ForeignKey('workflow_reconciliation_periods.id'), nullable=False)
        snapshot = Column(Text)

    w.Base.metadata.create_all(w.engine)
    import sys
    expenses.install(app, workflow, sys.modules[__name__])

    @app.post('/api/wf/bulk')
    def bulk(data: BulkInput, db=Depends(w.get_db), user=Depends(w.current_user)):
        if data.action not in {'approve', 'picked', 'packed', 'ship', 'cancel', 'tracking', 'delete', 'assign', 'handover'}:
            raise HTTPException(422, 'Thao tác hàng loạt không hợp lệ.')
        if len({row.id for row in data.orders}) != len(data.orders):
            raise HTTPException(422, 'Danh sách đơn bị lặp.')
        results = []
        for selected in data.orders:
            try:
                if data.action == 'delete':
                    w.require(user, 'admin', 'operator')
                    order, flow = w.lock_order(db, selected.id, selected.version)
                    remove_order(db, order, flow, user)
                    db.commit()
                    result = {'id': selected.id, 'ok': True, 'stage': 'DELETED'}
                elif data.action == 'handover':
                    w.require(user, 'admin', 'operator', 'warehouse')
                    order, flow = w.lock_order(db, selected.id, selected.version)
                    flow.stage = 'SHIPPED'
                    if data.carrier:
                        flow.carrier = data.carrier
                    w.touch(flow)
                    w.audit(db, user, 'order.handover', {'order_id': selected.id})
                    db.commit()
                    result = {'id': selected.id, 'ok': True, 'stage': flow.stage}
                elif data.action == 'tracking':
                    w.require(user, 'admin', 'operator', 'warehouse')
                    order, flow = w.lock_order(db, selected.id, selected.version)
                    w.set_tracking(db, order, flow, user, data.status, data.note)
                    db.commit()
                    result = {'id': selected.id, 'ok': True, 'stage': flow.stage}
                elif data.action == 'assign':
                    w.require(user, 'admin', 'operator')
                    order, flow = w.lock_order(db, selected.id, selected.version)
                    assignee = data.assigned.strip() if data.assigned.strip() else data.note.strip()
                    flow.assigned = assignee
                    w.touch(flow)
                    w.audit(db, user, 'order.assign', {'order_id': selected.id, 'assigned': assignee})
                    db.commit()
                    result = {'id': selected.id, 'ok': True, 'stage': flow.stage, 'assigned': flow.assigned}
                else:
                    order = w.find_order(db, selected.id)
                    payload = w.ActionInput(
                        version=selected.version, action=data.action,
                        tracking=order.tracking or '', carrier=data.carrier, note=data.note
                    )
                    row = action_handler(selected.id, payload, db, user)
                    result = {'id': selected.id, 'ok': True, 'stage': row['stage'], 'issue': row['issue']}
                results.append(result)
            except (HTTPException, IntegrityError) as error:
                db.rollback()
                detail = error.detail if isinstance(error, HTTPException) else 'Dữ liệu bị trùng hoặc vừa thay đổi.'
                results.append({'id': selected.id, 'ok': False, 'error': str(detail)})
        return {'results': results, 'succeeded': sum(row['ok'] for row in results), 'total': len(results)}

    @app.get('/api/wf/trash')
    def trash(db=Depends(w.get_db), user=Depends(w.current_user)):
        return {
            'orders': [
                {**w.serialize(db, db.get(w.Order, row.order_id), db.get(w.Flow, row.order_id)),
                 'deleted_at': row.deleted_at, 'deleted_by': row.actor}
                for row in db.scalars(select(DeletedOrder).order_by(DeletedOrder.deleted_at.desc())).all()
            ],
            'customers': [
                {'id': row.customer_id, 'name': db.get(w.Customer, row.customer_id).name,
                 'code': db.get(w.Customer, row.customer_id).code, 'deleted_at': row.deleted_at}
                for row in db.scalars(select(DeletedCustomer)).all()
            ]
        }

    @app.post('/api/wf/orders/{oid}/restore')
    def restore_order(oid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'operator')
        deleted = db.get(DeletedOrder, oid)
        if not deleted:
            raise HTTPException(404, 'Đơn chưa nằm trong thùng rác.')
        order = db.get(w.Order, oid)
        ensure_customer_active(db, order.customer_id)
        flow = db.get(w.Flow, oid)
        order, flow = w.lock_order(db, oid, flow.version if flow else 0, include_deleted=True)
        if order.tracking:
            claim_handler(order, order.tracking, db)
        flow.stage = 'REVIEW'
        flow.issue = ''
        db.delete(deleted)
        w.audit(db, user, 'order.restore', {'order_id': oid})
        db.commit()
        return {'ok': True}

    @app.delete('/api/wf/customers/{cid}')
    def delete_customer(cid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'operator')
        customer = db.scalar(select(w.Customer).where(w.Customer.id == cid).with_for_update())
        if not customer:
            raise HTTPException(404, 'Không tìm thấy khách hàng.')
        if not db.get(DeletedCustomer, cid):
            db.add(DeletedCustomer(customer_id=cid, actor=user.username))
            w.audit(db, user, 'customer.delete', {'customer_id': cid, 'name': customer.name})
            db.commit()
        return {'ok': True}

    @app.post('/api/wf/customers/{cid}/restore')
    def restore_customer(cid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'operator')
        deleted = db.get(DeletedCustomer, cid)
        if not deleted:
            raise HTTPException(404, 'Khách chưa nằm trong thùng rác.')
        db.delete(deleted)
        w.audit(db, user, 'customer.restore', {'customer_id': cid})
        db.commit()
        return {'ok': True}

    @app.get('/api/wf/imports')
    def imports(db=Depends(w.get_db), user=Depends(w.current_user)):
        result = []
        for preview in db.scalars(select(w.Preview).where(w.Preview.kind == 'orders', w.Preview.confirmed == 1).order_by(w.Preview.created_at.desc())).all():
            ids = import_order_ids(db, preview.id)
            result.append({
                'id': preview.id, 'filename': (db.get(ImportInfo, preview.id).filename if db.get(ImportInfo, preview.id) else 'Phiếu nhập đơn'), 'at': preview.created_at, 'actor': db.get(w.User, preview.user_id).username,
                'total': len(ids), 'order_ids': ids, 'deleted': bool(db.get(DeletedImport, preview.id))
            })
        return result

    @app.delete('/api/wf/imports/{preview_id}')
    def delete_import(preview_id: str, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'operator')
        preview = db.scalar(select(w.Preview).where(w.Preview.id == preview_id).with_for_update())
        if not preview or preview.kind != 'orders' or not preview.confirmed:
            raise HTTPException(404, 'Không tìm thấy phiếu nhập đơn.')
        if db.get(DeletedImport, preview_id):
            return {'ok': True, 'deleted': 0}
        count = 0
        for oid in sorted(import_order_ids(db, preview_id)):
            if db.get(DeletedOrder, oid):
                continue
            order = db.get(w.Order, oid)
            flow = db.get(w.Flow, oid)
            order, flow = w.lock_order(db, oid, flow.version if flow else 0)
            remove_order(db, order, flow, user)
            count += 1
        db.add(DeletedImport(preview_id=preview_id))
        w.audit(db, user, 'import.delete', {'preview_id': preview_id, 'deleted': count})
        db.commit()
        return {'ok': True, 'deleted': count}

    @app.delete('/api/wf/stock/receipts/{lid}')
    def void_receipt(lid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'warehouse')
        ledger = db.scalar(select(w.Ledger).where(w.Ledger.id == lid).with_for_update())
        if not ledger or ledger.order_id is not None or ledger.quantity <= 0:
            raise HTTPException(409, 'Chỉ xóa phiếu nhập tồn độc lập có số lượng dương.')
        if db.get(VoidedReceipt, lid):
            raise HTTPException(409, 'Phiếu nhập đã được xóa trước đó.')
        db.add(VoidedReceipt(ledger_id=lid, reversed_by=user.username))
        db.flush()
        result = db.execute(update(w.Stock).where(
            w.Stock.id == ledger.stock_id, w.Stock.on_hand - ledger.quantity >= w.Stock.reserved
        ).values(on_hand=w.Stock.on_hand-ledger.quantity).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, 'Hàng của phiếu đã được xuất hoặc đang giữ cho đơn. Cần xử lý các đơn liên quan trước.')
        db.add(w.Ledger(stock_id=ledger.stock_id, quantity=-ledger.quantity,
                        reason='Xóa phiếu nhập kho #' + str(lid), actor=user.username))
        w.audit(db, user, 'stock.receipt.delete', {'receipt_id': lid, 'quantity': ledger.quantity})
        db.commit()
        return {'ok': True}

    @app.get('/api/wf/period-candidates')
    def candidates(date_from: date, date_to: date, customer_id: int | None = None,
                   date_basis: str = 'completion', db=Depends(w.get_db), user=Depends(w.current_user)):
        return period_candidates(db, date_from, date_to, customer_id, date_basis)

    @app.get('/api/wf/periods')
    def periods(db=Depends(w.get_db), user=Depends(w.current_user)):
        return [period_result(db, period) for period in db.scalars(select(Period).order_by(Period.id.desc())).all()]

    @app.post('/api/wf/periods')
    def create_period(data: PeriodInput, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        period_name = data.name.strip() if data.name else ''
        if not period_name:
            if data.customer_id:
                cust = db.get(w.Customer, data.customer_id)
                code_str = (getattr(cust, 'primary_code', None) or cust.code) if cust else str(data.customer_id)
                period_name = f"Đối soát {code_str} ({data.date_from.strftime('%d/%m')} - {data.date_to.strftime('%d/%m/%Y')})"
            else:
                period_name = f"Đối soát ({data.date_from.strftime('%d/%m')} - {data.date_to.strftime('%d/%m/%Y')})"
        rows = period_candidates(db, data.date_from, data.date_to, data.customer_id, data.date_basis)
        available = [row for row in rows if not row['period_id']]
        if not available:
            raise HTTPException(422, 'Khoảng ngày này không có đơn đủ điều kiện chưa thuộc đợt khác.')
        period = Period(name=period_name, date_from=data.date_from.isoformat(),
                        date_to=data.date_to.isoformat(), date_basis=data.date_basis,
                        customer_id=data.customer_id, actor=user.username)
        db.add(period)
        db.flush()
        for row in available:
            db.add(PeriodLine(order_id=row['id'], period_id=period.id))
        w.audit(db, user, 'period.create', {'period_id': period.id, 'order_ids': [row['id'] for row in available]})
        db.commit()
        return period_result(db, period)

    @app.get('/api/wf/periods/preview')
    def preview_period(customer_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                       date_basis: str = 'completion', db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        if not date_from or not date_to:
            raise HTTPException(422, 'Vui lòng chọn khoảng ngày.')
        if date_from > date_to:
            raise HTTPException(422, 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
        candidates = period_candidates(db, date_from, date_to, customer_id, date_basis)
        available = [row for row in candidates if not row.get('period_id')]
        del_cnt = sum(1 for r in available if r.get('transport') == 'DELIVERED')
        ret_cnt = sum(1 for r in available if r.get('transport') == 'RETURNED')
        total_cod = sum(Decimal(r.get('cod') or 0) for r in available if r.get('transport') == 'DELIVERED')
        total_fixed_fee = sum(Decimal(r.get('fixed_delivery_fee') or 0) if r.get('transport') == 'DELIVERED' else Decimal(r.get('fixed_return_fee') or 0) for r in available)
        total_special_fee = sum(Decimal(r.get('special_fee') or 0) for r in available)
        total_cod_fee = sum(Decimal(r.get('cod_fee') or 0) for r in available if r.get('transport') == 'DELIVERED')
        total_fee = sum(Decimal(r.get('total_fee') or 0) for r in available)
        net_seller = sum(Decimal(r.get('net_seller') or 0) for r in available)
        suggested_name = ''
        if customer_id:
            c = db.get(w.Customer, customer_id)
            if c:
                code_str = getattr(c, 'primary_code', None) or c.code
                suggested_name = f"Đối soát {code_str} ({date_from.strftime('%d/%m')} - {date_to.strftime('%d/%m/%Y')})"
        return {
            'candidates': available,
            'total_orders': len(available),
            'delivered_count': del_cnt,
            'returned_count': ret_cnt,
            'total_cod': str(total_cod),
            'total_fixed_fee': str(total_fixed_fee),
            'total_special_fee': str(total_special_fee),
            'total_cod_fee': str(total_cod_fee),
            'total_fee': str(total_fee),
            'net_seller': str(net_seller),
            'suggested_name': suggested_name,
            'orders': available
        }

    @app.get('/api/wf/periods/{pid}')
    def period_detail(pid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        period = db.get(Period, pid)
        if not period:
            raise HTTPException(404, 'Không tìm thấy đợt đối soát.')
        return period_result(db, period, include_rows=True)

    @app.post('/api/wf/periods/bulk-create')
    def bulk_create_periods(data: PeriodBulkCreateInput, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        if data.date_from > data.date_to:
            raise HTTPException(422, 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
        candidates = period_candidates(db, data.date_from, data.date_to, None, data.date_basis)
        available = [row for row in candidates if not row.get('period_id')]
        by_customer = {}
        for row in available:
            cid = row['customer_id']
            by_customer.setdefault(cid, []).append(row)
        created_periods = []
        for cid, rows in by_customer.items():
            if not rows:
                continue
            c = db.get(w.Customer, cid)
            if not c:
                continue
            code_str = getattr(c, 'primary_code', None) or c.code
            period_name = f"Đối soát {code_str} ({data.date_from.strftime('%d/%m')} - {data.date_to.strftime('%d/%m/%Y')})"
            period = Period(
                name=period_name,
                date_from=data.date_from.isoformat(),
                date_to=data.date_to.isoformat(),
                date_basis=data.date_basis,
                customer_id=cid,
                state='DRAFT',
                version=1,
                actor=user.username
            )
            db.add(period)
            db.flush()
            for row in rows:
                db.add(PeriodLine(order_id=row['id'], period_id=period.id, snapshot=json.dumps(row, ensure_ascii=False)))
            created_periods.append({'id': period.id, 'name': period.name, 'customer_id': cid, 'orders_count': len(rows)})
        w.audit(db, user, 'periods.bulk_create', {'count': len(created_periods)})
        db.commit()
        return {'ok': True, 'count': len(created_periods), 'periods': created_periods}

    @app.post('/api/wf/periods/{pid}')
    def change_period(pid: int, data: PeriodAction, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        period = db.scalar(select(Period).where(Period.id == pid).with_for_update())
        if not period:
            raise HTTPException(404, 'Không tìm thấy đợt đối soát.')
        result = db.execute(update(Period).where(Period.id == pid, Period.version == data.version)
                            .values(version=Period.version+1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, 'Đợt đối soát vừa thay đổi. Tải lại trước khi thao tác.')
        db.refresh(period)
        lines = db.scalars(select(PeriodLine).where(PeriodLine.period_id == pid).order_by(PeriodLine.order_id)).all()
        if data.action == 'close':
            if period.state != 'DRAFT':
                raise HTTPException(409, 'Đợt đã chốt.')
            for line in lines:
                order = db.scalar(select(w.Order).where(w.Order.id == line.order_id).with_for_update())
                flow = db.scalar(select(w.Flow).where(w.Flow.order_id == order.id).with_for_update())
                row = w.serialize(db, order, flow)
                if row['finance'] not in {'RECONCILED', 'SETTLED'} or row['transport'] not in {'DELIVERED', 'RETURNED'}:
                    raise HTTPException(409, 'Đơn ' + row['code'] + ' chưa đối soát khớp.')
                line.snapshot = json.dumps(row, default=str, ensure_ascii=False)
            expenses.check_sheet_current(db, period)
            period.state = 'CLOSED'
        elif data.action == 'reopen':
            period.state = 'DRAFT'
            for line in lines:
                line.snapshot = None
        elif data.action == 'delete':
            if period.state != 'DRAFT':
                raise HTTPException(409, 'Mở lại đợt đã chốt trước khi xóa.')
            for line in lines:
                db.delete(line)
            expenses.remove_sheet(db, pid)
            db.flush()
            db.delete(period)
        else:
            raise HTTPException(422, 'Thao tác đợt đối soát không hợp lệ.')
        w.audit(db, user, 'period.'+data.action, {'period_id': pid})
        db.commit()
        return {'ok': True}

    @app.get('/api/wf/periods/{pid}/export')
    def export_period(pid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        period = db.get(Period, pid)
        if not period:
            raise HTTPException(404, 'Không tìm thấy đợt đối soát.')
        result = period_result(db, period, include_rows=True)
        columns = ['code', 'customer', 'tracking', 'completed_at', 'returned_at', 'currency', 'cod',
                   'carrier_fee', 'service_fee', 'carrier_received', 'seller_paid', 'seller_balance']
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for row in result['orders']:
            values = {key: row.get(key, '') for key in columns}
            writer.writerow({key: "'" + str(value) if str(value).startswith(('=', '+', '-', '@')) else value
                             for key, value in values.items()})
        return StreamingResponse(iter(['\ufeff'+buffer.getvalue()]), media_type='text/csv; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename="foxrun_period_'+str(pid)+'.csv"'})

    @app.get('/api/wf/periods/{pid}/export-xlsx')
    def export_period_xlsx(pid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        period = db.get(Period, pid)
        if not period:
            raise HTTPException(404, 'Không tìm thấy đợt đối soát.')
        result = period_result(db, period, include_rows=True)
        columns = ['code', 'customer', 'tracking', 'completed_at', 'returned_at', 'currency', 'cod',
                   'carrier_fee', 'service_fee', 'carrier_received', 'seller_paid', 'seller_balance']
        headers_vn = ['Mã đơn', 'Khách hàng', 'Mã vận đơn', 'Ngày giao', 'Ngày hoàn', 'Tiền tệ', 'Tiền COD',
                      'Phí vận chuyển', 'Phí dịch vụ FOXRUN', 'Hãng thực chuyển', 'Đã trả Seller', 'Số dư Seller']

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Đối soát #{pid}"
        ws.append(headers_vn)

        header_fill = PatternFill(start_color='001529', end_color='001529', fill_type='solid')
        header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
        data_font = Font(name='Segoe UI', size=10)
        thin_border = Border(
            left=Side(style='thin', color='D9D9D9'),
            right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'),
            bottom=Side(style='thin', color='D9D9D9')
        )

        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')

        for row in result['orders']:
            row_vals = []
            for k in columns:
                v = row.get(k, '')
                if k in {'cod', 'carrier_fee', 'service_fee', 'carrier_received', 'seller_paid', 'seller_balance'} and v not in ('', None):
                    try:
                        v = float(v)
                    except (ValueError, TypeError):
                        pass
                elif isinstance(v, str) and v.startswith(('=', '+', '-', '@')):
                    v = "'" + v
                row_vals.append(v)
            ws.append(row_vals)

        for row in ws.iter_rows(min_row=2, min_col=1, max_col=len(columns)):
            for cell in row:
                cell.font = data_font
                cell.border = thin_border
                cell.alignment = Alignment(vertical='center')

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 35)

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="foxrun_period_{pid}.xlsx"'}
        )


def ensure_customer_active(db, customer_id):
    if db.get(DeletedCustomer, customer_id):
        raise HTTPException(409, 'Khách đã xóa. Khôi phục khách trong thùng rác trước khi tạo đơn.')


def is_deleted(db, order_id):
    return bool(db.get(DeletedOrder, order_id))


def check_finance_open(db, order_id):
    line = db.get(PeriodLine, order_id)
    if line:
        period = db.get(Period, line.period_id)
        if period.state == 'CLOSED':
            raise HTTPException(409, 'Đơn thuộc đợt đối soát đã chốt. Mở lại đợt trước khi sửa tiền.')


def remove_order(db, order, flow, user):
    if db.get(DeletedOrder, order.id):
        raise HTTPException(409, 'Đơn đã nằm trong thùng rác.')
    if flow.stage == 'SHIPPED':
        raise HTTPException(409, 'Đơn ' + order.code + ' đã xuất kho. Cần xử lý giao/hoàn và đối soát trước khi xóa phiếu nhập.')
    if db.get(PeriodLine, order.id):
        raise HTTPException(409, 'Đơn đang thuộc một đợt đối soát.')
    reservations = db.scalars(select(w.Reservation).where(
        w.Reservation.order_id == order.id, w.Reservation.state == 'RESERVED'
    ).order_by(w.Reservation.stock_id)).all()
    for reserved in reservations:
        db.execute(update(w.Stock).where(w.Stock.id == reserved.stock_id)
                   .values(reserved=w.Stock.reserved-reserved.quantity).execution_options(synchronize_session=False))
        db.delete(reserved)
    # Released rows can be recreated when the order is restored and approved again.
    for reserved in db.scalars(select(w.Reservation).where(w.Reservation.order_id == order.id)).all():
        db.delete(reserved)
    claim = db.scalar(select(w.TrackingClaim).where(w.TrackingClaim.order_id == order.id))
    if claim:
        db.delete(claim)
    flow.stage = 'CANCELLED'
    w.touch(flow)
    db.add(DeletedOrder(order_id=order.id, actor=user.username))
    w.audit(db, user, 'order.delete', {'order_id': order.id, 'code': order.code})


def import_order_ids(db, preview_id):
    ids = []
    audits = db.scalars(select(w.Audit).where(w.Audit.action == 'import.order')).all()
    for entry in audits:
        detail = json.loads(entry.detail)
        if detail.get('preview_id') == preview_id:
            ids.append(detail['order_id'])
    return list(dict.fromkeys(ids))


def order_extras(db, order):
    events = db.scalars(select(w.TrackingEvent).where(
        w.TrackingEvent.order_id == order.id,
        w.TrackingEvent.new_status.in_(['DELIVERED', 'RETURNED'])
    ).order_by(w.TrackingEvent.created_at, w.TrackingEvent.id)).all()
    delivered = next((event.created_at for event in events if event.new_status == 'DELIVERED'), None)
    if not delivered and getattr(order, 'completed_at', None):
        delivered = order.completed_at
    returned = next((event.created_at for event in events if event.new_status == 'RETURNED'), None)
    line = db.get(PeriodLine, order.id)
    return {'completed_at': delivered, 'returned_at': returned, 'period_id': line.period_id if line else None}


def period_candidates(db, start, end, customer_id, basis):
    if start > end:
        raise HTTPException(422, 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
    if basis not in {'completion', 'created'}:
        raise HTTPException(422, 'Chọn ngày hoàn thành hoặc ngày lên đơn.')
    first = datetime.combine(start, time.min, tzinfo=LOCAL_TIME)
    last = datetime.combine(end+timedelta(days=1), time.min, tzinfo=LOCAL_TIME)
    statement = select(w.Order).where(w.Order.status.in_(['DELIVERED', 'RETURNED'])).order_by(w.Order.id)
    if customer_id:
        statement = statement.where(w.Order.customer_id == customer_id)
    result = []
    for order in db.scalars(statement).all():
        if is_deleted(db, order.id):
            continue
        row = w.serialize(db, order, db.get(w.Flow, order.id))
        when = order.created_at if basis == 'created' else (
            row['returned_at'] if row['transport'] == 'RETURNED' else (row['completed_at'] or getattr(order, 'completed_at', None) or order.created_at)
        )
        if when is None:
            when = order.created_at
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if first <= when < last:
            result.append(row)
    return result


def period_result(db, period, include_rows=False):
    rows = []
    for line in db.scalars(select(PeriodLine).where(PeriodLine.period_id == period.id).order_by(PeriodLine.order_id)).all():
        if period.state == 'CLOSED' and line.snapshot:
            rows.append(json.loads(line.snapshot))
        else:
            order = db.get(w.Order, line.order_id)
            rows.append(w.serialize(db, order, db.get(w.Flow, order.id)))
    totals = {}
    keys = ('carrier_fee', 'service_fee', 'carrier_received', 'seller_paid', 'seller_balance')
    for row in rows:
        group = totals.setdefault(row['currency'], {key: Decimal('0') for key in ('cod', *keys)})
        group['cod'] += Decimal(row['cod']) if row['transport'] == 'DELIVERED' else Decimal('0')
        for key in keys:
            group[key] += Decimal(row[key])
    result = {
        'id': period.id, 'name': period.name, 'date_from': period.date_from, 'date_to': period.date_to,
        'date_basis': period.date_basis, 'customer_id': period.customer_id, 'state': period.state,
        'version': period.version, 'total': len(rows), 'created_at': period.created_at,
        'totals': {currency: {key: str(value) for key, value in group.items()} for currency, group in totals.items()}
    }
    if include_rows:
        result['orders'] = rows
    return result
