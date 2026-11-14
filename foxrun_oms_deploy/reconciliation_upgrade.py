"""Reconciliation Upgrade: Admin Fee Configs, Flexible Order Fee Adjustments with Mandatory Notes & Highlights, and Accounting Live-Formula Excel Toolkit."""
import io
import re
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Text, select, update, or_, text
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import main
import workflow as w
import management
import sheet_formulas

Base = main.Base
engine = main.engine
SessionLocal = main.SessionLocal

# --- DATABASE MODELS ---

class AdminFeeConfig(Base):
    __tablename__ = 'workflow_admin_fee_configs'
    id = Column(Integer, primary_key=True)
    fee_type = Column(String(20), default='FIXED', nullable=False)  # 'FIXED' or 'FORMULA'
    name = Column(String(150), nullable=False)
    target_type = Column(String(30), default='PER_ORDER', nullable=False)  # 'PER_ORDER', 'PER_CUSTOMER', 'PER_PERIOD'
    fee_category = Column(String(30), default='service_fee', nullable=False)  # 'service_fee' or 'carrier_fee'
    amount = Column(Numeric(18, 2), default=0, nullable=False)
    formula = Column(String(255), default='', nullable=False)  # e.g. 'COD * 0.015', 'QTY * 2'
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)  # None = toàn hệ thống
    customer_code = Column(String(50), default='', nullable=False)  # 'HC', 'FG', 'DG', 'AH' or ''
    status = Column(String(20), default='ALL', nullable=False)  # 'ALL', 'DELIVERED', 'RETURNED'
    is_active = Column(Integer, default=1, nullable=False)  # 1 = active, 0 = disabled
    note = Column(Text, default='', nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class OrderFeeAdjustment(Base):
    __tablename__ = 'workflow_order_fee_adjustments'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False)
    carrier_fee_before = Column(Numeric(18, 2), default=0, nullable=False)
    carrier_fee_after = Column(Numeric(18, 2), default=0, nullable=False)
    service_fee_before = Column(Numeric(18, 2), default=0, nullable=False)
    service_fee_after = Column(Numeric(18, 2), default=0, nullable=False)
    special_fee_before = Column(Numeric(18, 2), default=0, nullable=False)
    special_fee_after = Column(Numeric(18, 2), default=0, nullable=False)
    reason = Column(Text, nullable=False)  # BẮT BUỘC
    actor = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# --- PYDANTIC SCHEMAS ---

class AdminFeeConfigInput(BaseModel):
    fee_type: Literal['FIXED', 'FORMULA'] = 'FIXED'
    name: str = Field(min_length=1, max_length=150)
    target_type: Literal['PER_ORDER', 'PER_CUSTOMER', 'PER_PERIOD'] = 'PER_ORDER'
    fee_category: Literal['service_fee', 'carrier_fee'] = 'service_fee'
    amount: Decimal = Field(default=Decimal(0), ge=0)
    formula: str = Field(default='', max_length=255)
    customer_id: Optional[int] = None
    customer_code: Optional[str] = ''
    status: Literal['ALL', 'DELIVERED', 'RETURNED'] = 'ALL'
    is_active: int = 1
    note: str = Field(default='', max_length=1000)

    @field_validator('name', 'formula', 'customer_code', 'note', mode='before')
    @classmethod
    def clean_str(cls, v):
        return v.strip() if isinstance(v, str) else ('' if v is None else str(v))


class OrderFeeAdjustInput(BaseModel):
    version: int = Field(ge=0)
    carrier_fee: Optional[Decimal] = None
    service_fee: Optional[Decimal] = None
    special_fee: Optional[Decimal] = None
    reason: str = Field(min_length=1, max_length=1000)  # BẮT BUỘC

    @field_validator('reason', mode='before')
    @classmethod
    def check_reason(cls, v):
        val = str(v).strip() if v is not None else ''
        if not val:
            raise ValueError('Bắt buộc phải nhập Lý do điều chỉnh chi phí.')
        return val


# --- FORMULA EVALUATION HELPER ---

def evaluate_fee_formula(formula_str: str, variables: dict) -> Decimal:
    """
    Evaluates mathematical expressions (+, -, *, /, %) with variables:
    COD, QTY, SHIPPING_FEE, SERVICE_FEE.
    """
    norm = formula_str.strip()
    # Normalize variable names to case-insensitive
    sub_map = {
        r'\bCOD\b': 'cod',
        r'\bQTY\b': 'so_luong',
        r'\bQUANTITY\b': 'so_luong',
        r'\bSO_LUONG\b': 'so_luong',
        r'\bSHIPPING_FEE\b': 'phi_hang',
        r'\bCARRIER_FEE\b': 'phi_hang',
        r'\bPHI_HANG\b': 'phi_hang',
        r'\bSERVICE_FEE\b': 'phi_dv',
        r'\bPHI_DV\b': 'phi_dv',
        r'\bFIXED_FEE\b': 'phi_co_dinh',
    }
    for pat, rep in sub_map.items():
        norm = re.sub(pat, rep, norm, flags=re.IGNORECASE)
    
    # Use sheet_formulas
    f = sheet_formulas.Formula(norm)
    vars_eval = {
        'cod': Decimal(variables.get('cod') or 0),
        'cod_thu': Decimal(variables.get('cod_thu') or 0),
        'so_luong': Decimal(variables.get('so_luong') or variables.get('qty') or 1),
        'so_sku': Decimal(variables.get('so_sku') or 1),
        'don': Decimal(1),
        'phi_hang': Decimal(variables.get('carrier_fee') or 0),
        'phi_dv': Decimal(variables.get('service_fee') or 0),
        'phi_co_dinh': Decimal(variables.get('fixed_fee') or 0),
        'khach': variables.get('customer_code', ''),
        'tien_te': variables.get('currency', 'THB'),
        'trang_thai': variables.get('status', 'DELIVERED'),
        'true': True, 'false': False
    }
    return f.amount(vars_eval)


# --- EXCEL WORKBOOK GENERATOR (3 SHEETS LIVE FORMULAS) ---

def build_reconciliation_live_workbook(orders_data: list, configs_data: list, period_name: str = "Tất cả") -> io.BytesIO:
    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "1_Tong_Hop_Doi_Soat"
    ws_details = wb.create_sheet(title="2_Chi_Tiet_Don_Hang")
    ws_configs = wb.create_sheet(title="3_Cau_Hinh_Chi_Phi_Admin")

    # Styling constants
    header_navy = PatternFill('solid', fgColor='001529')
    header_blue = PatternFill('solid', fgColor='1890FF')
    header_green = PatternFill('solid', fgColor='107C41')
    yellow_highlight = PatternFill('solid', fgColor='FEF9C3')
    gray_zebra = PatternFill('solid', fgColor='F9FAFB')
    
    white_bold = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    text_font = Font(name="Segoe UI", size=9)
    bold_font = Font(name="Segoe UI", size=9, bold=True)
    title_font = Font(name="Segoe UI", size=14, bold=True, color="001529")
    kpi_val_font = Font(name="Segoe UI", size=13, bold=True, color="096DD9")
    
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    # ----------------------------------------------------
    # SHEET 2: 2_Chi_Tiet_Don_Hang
    # ----------------------------------------------------
    detail_headers = [
        "Mã đơn", "Khách hàng", "Mã vận đơn", "Tiền COD", "Tiền tệ",
        "Phí vận chuyển", "Phí FOXRUN", "Phụ phí vùng", "Hãng cần chuyển (Formula)",
        "Phải trả Seller (Formula)", "Trạng thái giao", "Đã chỉnh sửa phí?",
        "Lý do điều chỉnh (Note)", "Người sửa", "Thời gian sửa"
    ]
    ws_details.append(detail_headers)
    for col_idx, cell in enumerate(ws_details[1], start=1):
        cell.fill = header_navy
        cell.font = white_bold
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    num_rows = len(orders_data)
    for idx, row in enumerate(orders_data, start=2):
        is_adjusted = bool(row.get('fee_adjusted'))
        cod_val = float(Decimal(str(row.get('cod') or 0)))
        carrier_fee_val = float(Decimal(str(row.get('carrier_fee') or 0)))
        service_fee_val = float(Decimal(str(row.get('service_fee') or 0)))
        spec_fee_val = float(Decimal(str(row.get('special_fee') or 0)))
        
        # Live Excel formulas referencing other columns in the same row:
        # Col D: COD, Col F: Phí vận chuyển, Col G: Phí FOXRUN
        # Col I (Hãng cần chuyển) = IF(K2="DELIVERED", D2 - F2, -F2)
        # Col J (Phải trả Seller) = I2 - G2 - H2
        formula_carrier_received = f"=IF(K{idx}=\"DELIVERED\", D{idx}-F{idx}, -F{idx})"
        formula_seller_due = f"=I{idx}-G{idx}-H{idx}"

        adjust_txt = "⚠️ ĐÃ SỬA TAY" if is_adjusted else "Gốc"
        reason_txt = str(row.get('fee_adjust_reason') or '')
        actor_txt = str(row.get('fee_adjusted_by') or '')
        time_txt = str(row.get('fee_adjusted_at') or '')

        ws_details.append([
            str(row.get('code') or ''),
            str(row.get('customer') or ''),
            str(row.get('tracking') or ''),
            cod_val,
            str(row.get('currency') or 'THB'),
            carrier_fee_val,
            service_fee_val,
            spec_fee_val,
            formula_carrier_received,
            formula_seller_due,
            str(row.get('transport') or ''),
            adjust_txt,
            reason_txt,
            actor_txt,
            time_txt
        ])

        curr_row = ws_details[idx]
        fill_to_apply = yellow_highlight if is_adjusted else (gray_zebra if idx % 2 == 0 else None)
        for c_idx, cell in enumerate(curr_row, start=1):
            cell.font = bold_font if is_adjusted else text_font
            cell.border = thin_border
            if fill_to_apply:
                cell.fill = fill_to_apply
            
            # Format numbers
            if c_idx in (4, 6, 7, 8):
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif c_idx in (9, 10):
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if is_adjusted:
                    cell.font = bold_font
            elif c_idx in (1, 3):
                cell.number_format = '@'
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif c_idx in (5, 11, 12):
                cell.alignment = Alignment(horizontal="center", vertical="center")

    # ----------------------------------------------------
    # SHEET 1: 1_Tong_Hop_Doi_Soat
    # ----------------------------------------------------
    ws_summary.column_dimensions['A'].width = 6
    ws_summary.column_dimensions['B'].width = 30
    ws_summary.column_dimensions['C'].width = 24
    ws_summary.column_dimensions['D'].width = 40

    ws_summary['B2'] = "BÁO CÁO TỔNG HỢP ĐỐI SOÁT & CHI PHÍ FOXRUN OMS"
    ws_summary['B2'].font = title_font
    ws_summary['B3'] = f"Kỳ đối soát: {period_name} · Xuất ngày: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws_summary['B3'].font = text_font

    summary_cards = [
        ("Tổng số đơn đối soát", f"=COUNTA('2_Chi_Tiet_Don_Hang'!A2:A{max(2, num_rows + 1)})", "#,##0 đơn", "Tổng số lượng đơn trong đợt"),
        ("Tổng tiền COD thu hộ", f"=SUM('2_Chi_Tiet_Don_Hang'!D2:D{max(2, num_rows + 1)})", "#,##0.00", "Tổng tiền COD khách hàng đã thanh toán"),
        ("Tổng cước vận chuyển hãng", f"=SUM('2_Chi_Tiet_Don_Hang'!F2:F{max(2, num_rows + 1)})", "#,##0.00", "Tổng chi phí vận chuyển phải trả hãng"),
        ("Tổng phí dịch vụ FOXRUN", f"=SUM('2_Chi_Tiet_Don_Hang'!G2:G{max(2, num_rows + 1)})", "#,##0.00", "Tổng phí xử lý đơn & dịch vụ OMS"),
        ("Tổng phụ phí đặc biệt", f"=SUM('2_Chi_Tiet_Don_Hang'!H2:H{max(2, num_rows + 1)})", "#,##0.00", "Phụ phí vùng sâu / vùng xa / cồng kềnh"),
        ("Hãng vận chuyển cần chuyển", f"=SUM('2_Chi_Tiet_Don_Hang'!I2:I{max(2, num_rows + 1)})", "#,##0.00", "Live Formula: =SUM(Sheet2!I)"),
        ("Phải thanh toán Seller ròng", f"=SUM('2_Chi_Tiet_Don_Hang'!J2:J{max(2, num_rows + 1)})", "#,##0.00", "Live Formula: =SUM(Sheet2!J)"),
        ("Số đơn điều chỉnh thủ công", f"=COUNTIF('2_Chi_Tiet_Don_Hang'!L2:L{max(2, num_rows + 1)}, \"*ĐÃ SỬA*\")", "#,##0 đơn", "Các đơn được Admin sửa chi phí có Note & Tô vàng"),
    ]

    ws_summary['B5'] = "Chỉ số tài chính tổng hợp"
    ws_summary['C5'] = "Giá trị (Live Formula)"
    ws_summary['D5'] = "Ghi chú giải trình"
    for col_letter in ['B', 'C', 'D']:
        c = ws_summary[f"{col_letter}5"]
        c.fill = header_navy
        c.font = white_bold
        c.alignment = Alignment(horizontal="center", vertical="center")

    for i, (label, form, num_fmt, note_txt) in enumerate(summary_cards, start=6):
        c_lbl = ws_summary[f"B{i}"]
        c_val = ws_summary[f"C{i}"]
        c_not = ws_summary[f"D{i}"]

        c_lbl.value = label
        c_lbl.font = bold_font
        c_lbl.border = thin_border

        c_val.value = form
        c_val.font = kpi_val_font if i in (11, 12) else bold_font
        c_val.number_format = num_fmt
        c_val.border = thin_border
        c_val.alignment = Alignment(horizontal="right", vertical="center")
        if i in (11, 12):
            c_val.fill = PatternFill(start_color="E6F7FF", end_color="E6F7FF", fill_type="solid")
        elif i == 13:
            c_val.fill = yellow_highlight

        c_not.value = note_txt
        c_not.font = text_font
        c_not.border = thin_border

    # ----------------------------------------------------
    # SHEET 3: 3_Cau_Hinh_Chi_Phi_Admin
    # ----------------------------------------------------
    config_headers = [
        "Mã ID", "Loại phí", "Tên khoản phí", "Nhóm phí", "Áp dụng",
        "Số tiền cố định", "Công thức toán học", "Mã khách áp dụng",
        "Trạng thái đơn", "Bật/Tắt", "Ghi chú chi phí"
    ]
    ws_configs.append(config_headers)
    for cell in ws_configs[1]:
        cell.fill = header_green
        cell.font = white_bold
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for idx, cfg in enumerate(configs_data, start=2):
        ws_configs.append([
            cfg.get('id', idx - 1),
            "Cố định" if cfg.get('fee_type') == 'FIXED' else "Biến đổi (Công thức)",
            str(cfg.get('name') or ''),
            "Phí dịch vụ" if cfg.get('fee_category') == 'service_fee' else "Cước vận chuyển",
            str(cfg.get('target_type') or 'PER_ORDER'),
            float(Decimal(str(cfg.get('amount') or 0))),
            str(cfg.get('formula') or ''),
            str(cfg.get('customer_code') or 'Tất cả'),
            str(cfg.get('status') or 'ALL'),
            "Đang bật" if cfg.get('is_active', 1) else "Đã tắt",
            str(cfg.get('note') or '')
        ])
        for c_idx, cell in enumerate(ws_configs[idx], start=1):
            cell.font = text_font
            cell.border = thin_border
            if c_idx == 6:
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif c_idx == 7:
                cell.number_format = '@'

    # Auto-fit columns for all sheets
    for ws in [ws_details, ws_configs]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if val_str.startswith('='):
                    val_str = "123,456.78"
                max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 48)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# --- INSTALL FUNCTION TO FASTAPI APP ---

def install(app: FastAPI):
    # Ensure database tables exist
    Base.metadata.create_all(engine)
    
    # Safe column migrations for Flow table
    flow_migrations = [
        ('fee_adjusted', 'INTEGER DEFAULT 0'),
        ('fee_adjust_reason', "TEXT DEFAULT ''"),
        ('fee_adjusted_by', "VARCHAR(100) DEFAULT ''"),
        ('fee_adjusted_at', 'TIMESTAMP WITH TIME ZONE')
    ]
    for col, col_def in flow_migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE workflow_orders ADD COLUMN {col} {col_def}"))
                conn.commit()
        except Exception:
            pass

    # Safe table creation for AdminFeeConfig & OrderFeeAdjustment
    try:
        AdminFeeConfig.__table__.create(engine, checkfirst=True)
        OrderFeeAdjustment.__table__.create(engine, checkfirst=True)
    except Exception:
        pass

    # Seed default Admin fee configurations if empty
    with SessionLocal() as db:
        if db.scalar(select(AdminFeeConfig).limit(1)) is None:
            defaults = [
                AdminFeeConfig(fee_type='FIXED', name='Phí xử lý đơn cố định (Pick & Pack)', target_type='PER_ORDER', fee_category='service_fee', amount=Decimal('3.00'), note='Phí đóng gói và xử lý cố định cho mỗi đơn xuất'),
                AdminFeeConfig(fee_type='FORMULA', name='Phí bảo hiểm & thu hộ COD (1.5%)', target_type='PER_ORDER', fee_category='service_fee', formula='COD * 0.015', note='Tính 1.5% trên tổng giá trị thu hộ COD'),
                AdminFeeConfig(fee_type='FORMULA', name='Phí xử lý theo số lượng sản phẩm (QTY * 2)', target_type='PER_ORDER', fee_category='service_fee', formula='QTY * 2', note='Mỗi sản phẩm trong đơn tính 2 THB phí phụ kiện'),
                AdminFeeConfig(fee_type='FORMULA', name='Thuế VAT cước vận chuyển (7%)', target_type='PER_ORDER', fee_category='carrier_fee', formula='SHIPPING_FEE * 0.07', note='7% VAT trên cước vận chuyển hãng'),
            ]
            for d in defaults:
                db.add(d)
            db.commit()

    # ----------------------------------------------------
    # 1. ADMIN FEE CONFIGS API
    # ----------------------------------------------------
    @app.get('/api/wf/admin/fee-configs')
    def get_fee_configs(customer_id: Optional[int] = None, db=Depends(w.get_db), user=Depends(w.current_user)):
        # Auto-seed defaults if empty
        from sqlalchemy import func
        if db.scalar(select(func.count(AdminFeeConfig.id))) == 0:
            defaults = [
                AdminFeeConfig(fee_type='FIXED', name='Phí xử lý đơn cố định (Pick & Pack)', target_type='PER_ORDER', fee_category='service_fee', amount=Decimal('3.00'), note='Phí đóng gói và xử lý cố định cho mỗi đơn xuất'),
                AdminFeeConfig(fee_type='FORMULA', name='Phí bảo hiểm & thu hộ COD (1.5%)', target_type='PER_ORDER', fee_category='service_fee', formula='COD * 0.015', note='Tính 1.5% trên tổng giá trị thu hộ COD'),
                AdminFeeConfig(fee_type='FORMULA', name='Phí xử lý theo số lượng sản phẩm (QTY * 2)', target_type='PER_ORDER', fee_category='service_fee', formula='QTY * 2', note='Mỗi sản phẩm trong đơn tính 2 THB phí phụ kiện'),
                AdminFeeConfig(fee_type='FORMULA', name='Thuế VAT cước vận chuyển (7%)', target_type='PER_ORDER', fee_category='carrier_fee', formula='SHIPPING_FEE * 0.07', note='7% VAT trên cước vận chuyển hãng'),
            ]
            for d in defaults:
                db.add(d)
            db.commit()

        q = select(AdminFeeConfig)
        if customer_id is not None:
            q = q.where(or_(AdminFeeConfig.customer_id == customer_id, AdminFeeConfig.customer_id.is_(None)))
        items = db.scalars(q.order_by(AdminFeeConfig.id)).all()
        return [
            {
                'id': item.id,
                'fee_type': item.fee_type,
                'name': item.name,
                'target_type': item.target_type,
                'fee_category': item.fee_category,
                'amount': str(item.amount),
                'formula': item.formula,
                'customer_id': item.customer_id,
                'customer_code': item.customer_code,
                'status': item.status,
                'is_active': item.is_active,
                'note': item.note,
                'created_at': item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ]

    @app.post('/api/wf/admin/fee-configs')
    def create_fee_config(data: AdminFeeConfigInput, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin')
        if data.fee_type == 'FORMULA' and not data.formula.strip():
            raise HTTPException(422, 'Chi phí theo công thức bắt buộc phải nhập biểu thức toán học.')
        
        # Test compile formula if provided
        if data.fee_type == 'FORMULA':
            try:
                evaluate_fee_formula(data.formula, {'cod': 100, 'qty': 2, 'carrier_fee': 30, 'service_fee': 10})
            except Exception as e:
                raise HTTPException(422, f'Công thức toán học không hợp lệ: {str(e)}')

        c_code = data.customer_code or ''
        if data.customer_id and not c_code:
            cust = db.get(w.Customer, data.customer_id)
            if cust:
                c_code = cust.code

        item = AdminFeeConfig(
            fee_type=data.fee_type,
            name=data.name,
            target_type=data.target_type,
            fee_category=data.fee_category,
            amount=data.amount,
            formula=data.formula,
            customer_id=data.customer_id,
            customer_code=c_code,
            status=data.status,
            is_active=data.is_active,
            note=data.note
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        w.audit(db, user, 'admin.fee_config.create', {'id': item.id, 'name': item.name})
        return {'ok': True, 'id': item.id}

    @app.put('/api/wf/admin/fee-configs/{cfg_id}')
    def update_fee_config(cfg_id: int, data: AdminFeeConfigInput, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin')
        item = db.get(AdminFeeConfig, cfg_id)
        if not item:
            raise HTTPException(404, 'Không tìm thấy cấu hình chi phí.')

        if data.fee_type == 'FORMULA' and not data.formula.strip():
            raise HTTPException(422, 'Chi phí theo công thức bắt buộc phải nhập biểu thức toán học.')

        if data.fee_type == 'FORMULA':
            try:
                evaluate_fee_formula(data.formula, {'cod': 100, 'qty': 2, 'carrier_fee': 30, 'service_fee': 10})
            except Exception as e:
                raise HTTPException(422, f'Công thức toán học không hợp lệ: {str(e)}')

        item.fee_type = data.fee_type
        item.name = data.name
        item.target_type = data.target_type
        item.fee_category = data.fee_category
        item.amount = data.amount
        item.formula = data.formula
        item.customer_id = data.customer_id
        if data.customer_code:
            item.customer_code = data.customer_code
        item.status = data.status
        item.is_active = data.is_active
        item.note = data.note
        item.updated_at = datetime.now(timezone.utc)
        
        db.commit()
        w.audit(db, user, 'admin.fee_config.update', {'id': item.id, 'name': item.name})
        return {'ok': True}

    @app.delete('/api/wf/admin/fee-configs/{cfg_id}')
    def delete_fee_config(cfg_id: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin')
        item = db.get(AdminFeeConfig, cfg_id)
        if not item:
            raise HTTPException(404, 'Không tìm thấy cấu hình chi phí.')
        db.delete(item)
        db.commit()
        w.audit(db, user, 'admin.fee_config.delete', {'id': cfg_id, 'name': item.name})
        return {'ok': True}

    @app.patch('/api/wf/admin/fee-configs/{cfg_id}/toggle')
    def toggle_fee_config(cfg_id: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin')
        item = db.get(AdminFeeConfig, cfg_id)
        if not item:
            raise HTTPException(404, 'Không tìm thấy cấu hình chi phí.')
        item.is_active = 0 if item.is_active == 1 else 1
        item.updated_at = datetime.now(timezone.utc)
        db.commit()
        w.audit(db, user, 'admin.fee_config.toggle', {'id': cfg_id, 'is_active': item.is_active})
        return {'ok': True, 'is_active': item.is_active}

    # ----------------------------------------------------
    # 2. SỬA CHI PHÍ LINH HOẠT TỪNG ĐƠN (BẮT BUỘC NOTE LÝ DO)
    # ----------------------------------------------------
    @app.post('/api/wf/orders/{oid}/adjust-fees')
    def adjust_order_fees(oid: int, data: OrderFeeAdjustInput, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        reason_clean = data.reason.strip()
        if not reason_clean:
            raise HTTPException(422, 'Bắt buộc phải nhập Lý do điều chỉnh chi phí.')

        order, flow = w.lock_order(db, oid, data.version)
        c_before = Decimal(str(flow.carrier_fee or 0))
        s_before = Decimal(str(flow.service_fee or 0))
        sp_before = Decimal(str(order.special_fee or 0))

        c_after = data.carrier_fee if data.carrier_fee is not None else c_before
        s_after = data.service_fee if data.service_fee is not None else s_before
        sp_after = data.special_fee if data.special_fee is not None else sp_before

        # Update Flow & Order
        flow.carrier_fee = c_after
        flow.service_fee = s_after
        order.special_fee = sp_after
        
        # Mark visual flag and note for light yellow highlight
        now_dt = datetime.now(timezone.utc)
        flow.fee_adjusted = 1
        flow.fee_adjust_reason = reason_clean
        flow.fee_adjusted_by = user.username
        flow.fee_adjusted_at = now_dt
        w.touch(flow)

        # Record in history table
        adj = OrderFeeAdjustment(
            order_id=oid,
            carrier_fee_before=c_before,
            carrier_fee_after=c_after,
            service_fee_before=s_before,
            service_fee_after=s_after,
            special_fee_before=sp_before,
            special_fee_after=sp_after,
            reason=reason_clean,
            actor=user.username,
            created_at=now_dt
        )
        db.add(adj)

        w.audit(db, user, 'order.fee_adjust', {
            'order_id': oid,
            'code': order.code,
            'before': {'carrier_fee': str(c_before), 'service_fee': str(s_before), 'special_fee': str(sp_before)},
            'after': {'carrier_fee': str(c_after), 'service_fee': str(s_after), 'special_fee': str(sp_after)},
            'reason': reason_clean
        })
        db.commit()
        return w.serialize(db, order, flow)

    @app.get('/api/wf/orders/{oid}/fee-adjustments')
    def get_order_fee_adjustments(oid: int, db=Depends(w.get_db), user=Depends(w.current_user)):
        rows = db.scalars(select(OrderFeeAdjustment).where(OrderFeeAdjustment.order_id == oid).order_by(OrderFeeAdjustment.id.desc())).all()
        return [
            {
                'id': r.id,
                'order_id': r.order_id,
                'carrier_fee_before': str(r.carrier_fee_before),
                'carrier_fee_after': str(r.carrier_fee_after),
                'service_fee_before': str(r.service_fee_before),
                'service_fee_after': str(r.service_fee_after),
                'special_fee_before': str(r.special_fee_before),
                'special_fee_after': str(r.special_fee_after),
                'reason': r.reason,
                'actor': r.actor,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    # ----------------------------------------------------
    # 3. EXCEL TOOLKIT (.XLSX) WITH LIVE FORMULAS & HIGHLIGHTS
    # ----------------------------------------------------
    @app.get('/api/wf/reconcile/export-xlsx')
    def export_reconciliation_xlsx(db=Depends(w.get_db), user=Depends(w.current_user)):
        from sqlalchemy import func
        if db.scalar(select(func.count(AdminFeeConfig.id))) == 0:
            defaults = [
                AdminFeeConfig(fee_type='FIXED', name='Phí xử lý đơn cố định (Pick & Pack)', target_type='PER_ORDER', fee_category='service_fee', amount=Decimal('3.00'), note='Phí đóng gói và xử lý cố định cho mỗi đơn xuất'),
                AdminFeeConfig(fee_type='FORMULA', name='Phí bảo hiểm & thu hộ COD (1.5%)', target_type='PER_ORDER', fee_category='service_fee', formula='COD * 0.015', note='Tính 1.5% trên tổng giá trị thu hộ COD'),
                AdminFeeConfig(fee_type='FORMULA', name='Phí xử lý theo số lượng sản phẩm (QTY * 2)', target_type='PER_ORDER', fee_category='service_fee', formula='QTY * 2', note='Mỗi sản phẩm trong đơn tính 2 THB phí phụ kiện'),
                AdminFeeConfig(fee_type='FORMULA', name='Thuế VAT cước vận chuyển (7%)', target_type='PER_ORDER', fee_category='carrier_fee', formula='SHIPPING_FEE * 0.07', note='7% VAT trên cước vận chuyển hãng'),
            ]
            for d in defaults:
                db.add(d)
            db.commit()

        orders_raw = []
        for o in db.scalars(select(w.Order).order_by(w.Order.id.desc())).all():
            if management.is_deleted(db, o.id):
                continue
            f = db.get(w.Flow, o.id)
            if o.status in {'DELIVERED', 'RETURNED', 'RETURNED_COMPLETED'} or (f and f.stage == 'SHIPPED'):
                data = w.serialize(db, o, f)
                orders_raw.append(data)

        cfgs = db.scalars(select(AdminFeeConfig).order_by(AdminFeeConfig.id)).all()
        cfgs_data = [
            {
                'id': c.id, 'fee_type': c.fee_type, 'name': c.name, 'target_type': c.target_type,
                'fee_category': c.fee_category, 'amount': c.amount, 'formula': c.formula,
                'customer_code': c.customer_code, 'status': c.status, 'is_active': c.is_active, 'note': c.note
            }
            for c in cfgs
        ]

        buf = build_reconciliation_live_workbook(orders_raw, cfgs_data, "Tổng hợp toàn hệ thống")
        filename = f"foxrun_doi_soat_live_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return StreamingResponse(
            buf,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )

    # ----------------------------------------------------
    # 4. NHẬP FILE ĐỐI SOÁT TỪ HÃNG VẬN CHUYỂN (.XLSX)
    # ----------------------------------------------------
    @app.post('/api/wf/reconcile/upload-carrier-excel')
    async def upload_carrier_excel(file: UploadFile = File(...), apply: bool = False, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'finance')
        content = await file.read()
        filename = (file.filename or '').lower()

        if not filename.endswith(('.xlsx', '.xlsm')):
            raise HTTPException(422, 'Vui lòng tải lên file Excel (.xlsx hoặc .xlsm).')

        try:
            wb = load_workbook(filename=io.BytesIO(content), data_only=True)
            ws = wb.active
        except Exception as e:
            raise HTTPException(422, f'Không thể mở file Excel: {str(e)}')

        iter_rows = ws.iter_rows(values_only=True)
        header = next(iter_rows, None)
        if not header:
            raise HTTPException(422, 'File Excel trống.')

        headers = [str(h).strip().lower() if h is not None else '' for h in header]

        # Alias mappings
        col_map = {}
        for idx, h in enumerate(headers):
            if any(k in h for k in ['mã vận đơn', 'tracking', 'waybill', 'เลขพัสดุ', 'vận đơn']):
                col_map['tracking'] = idx
            elif any(k in h for k in ['tiền thu hộ', 'cod', 'cod đã thu', 'tiền cod', 'cod_received', 'ยอดเงิน']):
                col_map['cod'] = idx
            elif any(k in h for k in ['cước', 'phí vận chuyển', 'shipping_fee', 'carrier_fee', 'phí ship', 'ค่าส่ง']):
                col_map['carrier_fee'] = idx
            elif any(k in h for k in ['trạng thái', 'status', 'tình trạng', 'สถานะ']):
                col_map['status'] = idx

        if 'tracking' not in col_map:
            raise HTTPException(422, 'Không tìm thấy cột Mã vận đơn trong file. Vui lòng kiểm tra tiêu đề cột.')

        matched = []
        mismatched = []
        not_found = []

        for line_num, r in enumerate(iter_rows, start=2):
            if not r or not any(v is not None and str(v).strip() for v in r):
                continue
            
            raw_tracking = str(r[col_map['tracking']] or '').strip() if col_map.get('tracking') is not None and col_map['tracking'] < len(r) else ''
            if not raw_tracking:
                continue

            order = db.scalar(select(w.Order).where(w.Order.tracking == raw_tracking, ~w.Order.id.in_(select(management.DeletedOrder.order_id))))
            if not order:
                not_found.append({
                    'line': line_num,
                    'tracking': raw_tracking,
                    'note': 'Mã vận đơn không có trên hệ thống'
                })
                continue

            flow = db.get(w.Flow, order.id)
            sys_cod = Decimal(str(order.cod or 0))
            sys_carrier_fee = Decimal(str(flow.carrier_fee or 0)) if flow else Decimal(0)

            # Read carrier COD and Fee
            carrier_cod_raw = r[col_map['cod']] if col_map.get('cod') is not None and col_map['cod'] < len(r) else None
            carrier_fee_raw = r[col_map['carrier_fee']] if col_map.get('carrier_fee') is not None and col_map['carrier_fee'] < len(r) else None
            carrier_status_raw = str(r[col_map['status']] or '').strip() if col_map.get('status') is not None and col_map['status'] < len(r) else ''

            try:
                carrier_cod = Decimal(str(carrier_cod_raw).replace(',', '').strip()) if carrier_cod_raw is not None and str(carrier_cod_raw).strip() != '' else sys_cod
            except (InvalidOperation, ValueError):
                carrier_cod = sys_cod

            try:
                carrier_fee = Decimal(str(carrier_fee_raw).replace(',', '').strip()) if carrier_fee_raw is not None and str(carrier_fee_raw).strip() != '' else sys_carrier_fee
            except (InvalidOperation, ValueError):
                carrier_fee = sys_carrier_fee

            cod_diff = carrier_cod - sys_cod
            fee_diff = carrier_fee - sys_carrier_fee
            is_diff = (cod_diff != Decimal(0)) or (fee_diff != Decimal(0))

            info = {
                'line': line_num,
                'order_id': order.id,
                'code': order.code,
                'customer': order.customer.name,
                'tracking': raw_tracking,
                'sys_cod': str(sys_cod),
                'carrier_cod': str(carrier_cod),
                'cod_diff': str(cod_diff),
                'sys_carrier_fee': str(sys_carrier_fee),
                'carrier_fee': str(carrier_fee),
                'fee_diff': str(fee_diff),
                'carrier_status': carrier_status_raw,
                'is_diff': is_diff
            }

            if is_diff:
                mismatched.append(info)
            else:
                matched.append(info)

            # If apply flag is true, update flow amounts
            if apply and flow:
                flow.carrier_fee = carrier_fee
                flow.carrier_received = carrier_cod - carrier_fee
                flow.settlement_reference = f"Excel Hãng {file.filename or ''}"
                flow.reconciled = 0 if is_diff else 1
                w.touch(flow)

        if apply:
            w.audit(db, user, 'reconcile.apply_carrier_excel', {
                'matched': len(matched),
                'mismatched': len(mismatched),
                'not_found': len(not_found),
                'filename': file.filename
            })
            db.commit()

        return {
            'success': True,
            'filename': file.filename,
            'total_rows': len(matched) + len(mismatched) + len(not_found),
            'matched_count': len(matched),
            'mismatched_count': len(mismatched),
            'not_found_count': len(not_found),
            'matched': matched[:100],
            'mismatched': mismatched,
            'not_found': not_found[:100],
            'applied': apply
        }
