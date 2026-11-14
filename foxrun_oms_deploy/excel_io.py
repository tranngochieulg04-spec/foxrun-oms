"""FOXRUN OMS Excel Import & Export Unified Engine.
Ensures 100% styled .xlsx exports and resilient parsing of standard, Thai J&T, and multi-sheet workbooks.
"""
import io
import re
from decimal import Decimal, InvalidOperation
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Common font & style definitions
HEADER_FILL = PatternFill(start_color='001529', end_color='001529', fill_type='solid')
HEADER_FONT = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
DATA_FONT = Font(name='Segoe UI', size=10)
BORDER_SIDE = Side(style='thin', color='D9D9D9')
THIN_BORDER = Border(left=BORDER_SIDE, right=BORDER_SIDE, top=BORDER_SIDE, bottom=BORDER_SIDE)

THAI_STATUS_MAP = {
    'ส่งสำเร็จ': 'DELIVERED',
    'เซ็นรับแล้ว': 'DELIVERED',
    'นำจ่ายสำเร็จ': 'DELIVERED',
    'DELIVERED': 'DELIVERED',
    'SUCCESS': 'DELIVERED',
    'SUCCESSFUL': 'DELIVERED',
    'COMPLETED': 'DELIVERED',
    'POD': 'DELIVERED',
    'GIAO THÀNH CÔNG': 'DELIVERED',
    'ĐÃ GIAO': 'DELIVERED',
    'HOÀN THÀNH': 'DELIVERED',
    'กำลังจัดส่ง': 'IN_TRANSIT',
    'รับพัสดุแล้ว': 'IN_TRANSIT',
    'พัสดุกำลังจัดส่ง': 'IN_TRANSIT',
    'IN TRANSIT': 'IN_TRANSIT',
    'IN_TRANSIT': 'IN_TRANSIT',
    'SHIPPED': 'IN_TRANSIT',
    'ON THE WAY': 'IN_TRANSIT',
    'ĐANG VẬN CHUYỂN': 'IN_TRANSIT',
    'LẤY HÀNG THÀNH CÔNG': 'IN_TRANSIT',
    'กำลังนำจ่าย': 'OUT_FOR_DELIVERY',
    'OUT FOR DELIVERY': 'OUT_FOR_DELIVERY',
    'OUT_FOR_DELIVERY': 'OUT_FOR_DELIVERY',
    'ĐANG GIAO': 'OUT_FOR_DELIVERY',
    'ส่งไม่สำเร็จ': 'DELIVERY_FAILED',
    'DELIVERY FAILED': 'DELIVERY_FAILED',
    'DELIVERY_FAILED': 'DELIVERY_FAILED',
    'FAILED': 'DELIVERY_FAILED',
    'GIAO THẤT BẠI': 'DELIVERY_FAILED',
    'พัสดุตีกลับ': 'RETURNING',
    'ตีกลับ': 'RETURNING',
    'RETURNING': 'RETURNING',
    'RTO': 'RETURNING',
    'ĐANG HOÀN': 'RETURNING',
    'CHUYỂN HOÀN': 'RETURNING',
    'ตีกลับสำเร็จ': 'RETURNED',
    'RETURNED': 'RETURNED',
    'RTO DELIVERED': 'RETURNED',
    'ĐÃ HOÀN': 'RETURNED',
    'พัสดุมีปัญหา': 'EXCEPTION',
    'EXCEPTION': 'EXCEPTION',
    'DAMAGED': 'EXCEPTION',
    'LOST': 'EXCEPTION',
    'BẤT THƯỜNG': 'EXCEPTION',
    'ยกเลิก': 'CANCELLED',
    'CANCELLED': 'CANCELLED',
    'ĐÃ HỦY': 'CANCELLED'
}

KNOWN_CUSTOMER_PREFIXES = ['HC', 'FG', 'DG', 'AH', 'AR', 'AD', 'HL', 'FR']


def build_styled_workbook(title: str, headers: list[str], rows: list[list], text_columns: set[int] = None, number_columns: set[int] = None, currency_columns: set[int] = None) -> io.BytesIO:
    """Builds a beautiful Excel workbook with auto-fit, header fill, borders, and enforced @ text format."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]
    
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = THIN_BORDER
        
    text_cols = text_columns or set()
    num_cols = number_columns or set()
    curr_cols = currency_columns or set()
    
    for row_idx, r in enumerate(rows, 2):
        ws.append(r)
        for col_idx, cell in enumerate(ws[row_idx]):
            cell.font = DATA_FONT
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical='center')
            
            val = cell.value
            if col_idx in text_cols:
                cell.number_format = '@'
                cell.value = str(val) if val is not None else ''
            elif col_idx in curr_cols and val not in (None, ''):
                try:
                    cell.value = float(val)
                    cell.number_format = '#,##0.00' if '.' in str(val) else '#,##0'
                except (ValueError, TypeError):
                    cell.number_format = '@'
            elif col_idx in num_cols and val not in (None, ''):
                try:
                    cell.value = int(val)
                    cell.number_format = '#,##0'
                except (ValueError, TypeError):
                    cell.number_format = '@'
                    
    # Auto-fit column widths
    for col in ws.columns:
        max_len = 0
        for cell in col:
            v_str = str(cell.value or '')
            if len(v_str) > max_len:
                max_len = len(v_str)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 14), 45)
        
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def normalize_thai_phone(phone_val) -> str:
    if phone_val is None:
        return ''
    raw = str(phone_val).strip()
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 9 and digits[0] in ('6', '8', '9'):
        return '0' + digits
    return digits or raw


def normalize_thai_cod(cod_val) -> str:
    if cod_val is None:
        return '0'
    s = str(cod_val).strip().replace(' ', '')
    if not s:
        return '0'
    # Check 2.690.00 or 1.500.00
    if re.fullmatch(r'\d{1,3}\.\d{3}\.00', s) or re.fullmatch(r'\d{1,3}\.\d{3},00', s):
        s = s[:-3].replace('.', '').replace(',', '')
    # Check 1.500 or 2.690 (Thai thousand separator with period)
    elif re.fullmatch(r'\d{1,3}\.\d{3}', s):
        s = s.replace('.', '')
    elif ',' in s and '.' not in s:
        s = s.replace(',', '')
    try:
        num = Decimal(s)
        return str(int(num)) if num == int(num) else str(num)
    except Exception:
        return s or '0'


def parse_thai_note_skus(note_str: str, default_sku: str = '') -> list[dict]:
    if not note_str or not str(note_str).strip():
        return [{'sku': default_sku or 'ITEM', 'quantity': 1}]
    
    s = str(note_str).strip()
    # Remove order code prefix if present, e.g. "HC-2411-001: SERUM x 2"
    s = re.sub(r'^(?:[A-Za-z]{2,5}[-_]?[0-9A-Za-z\-]+)\s*[:\-]\s*', '', s).strip()
    
    items = []
    parts = re.split(r'[,;\n\+]|\s+/\s+', s)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.search(r'([A-Za-z0-9_\-\.]+)\s*(?:x|\*|\:|\-)?\s*\(?(\d+)\)?$', p)
        if m:
            sku = m.group(1).strip()
            qty = int(m.group(2))
            if sku and qty > 0:
                items.append({'sku': sku, 'quantity': qty})
        elif len(p) >= 2:
            items.append({'sku': p, 'quantity': 1})
            
    if not items:
        items = [{'sku': default_sku or 'ITEM', 'quantity': 1}]
    return items


def is_thai_jt_header(header_keys: list[str]) -> bool:
    thai_markers = ['เลขพัสดุ', 'ชื่อผู้รับ', 'เบอร์โทรผู้รับ', 'ที่อยู่', 'ตำบล', 'อำเภอ', 'จังหวัด', 'รหัสไปรษณีย์', 'ชื่อสินค้า', 'หมายเหตุ']
    match_count = sum(1 for m in thai_markers if any(m in k for k in header_keys))
    return match_count >= 3


def parse_orders_excel_workbook(content_bytes: bytes, filename: str = '') -> list[dict]:
    """Parses Excel or CSV orders into standard dictionary rows."""
    if filename.lower().endswith('.csv'):
        text = content_bytes.decode('utf-8-sig')
        import csv
        reader = list(csv.DictReader(io.StringIO(text)))
        return reader
        
    wb = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)
    sheet_names = wb.sheetnames
    
    # 1. Check for multi-sheet workbook (foxrun_all_in_one_master.xlsx)
    target_sheet = wb.active
    for sname in ['Orders_Import', 'Orders', 'Đơn hàng', 'OrdersImport', 'Sheet1']:
        if sname in sheet_names:
            target_sheet = wb[sname]
            break
            
    rows = list(target_sheet.iter_rows(values_only=True))
    
    # Check for Tracking_Update sheet in multi-sheet workbook
    tracking_updates = {}
    for sname in ['Tracking_Update', 'Tracking', 'Vận đơn']:
        if sname in sheet_names:
            tr_sheet = wb[sname]
            tr_rows = list(tr_sheet.iter_rows(values_only=True))
            if tr_rows:
                tr_headers = [str(h or '').strip().lower() for h in tr_rows[0]]
                t_col, s_col, n_col = -1, -1, -1
                for idx, h in enumerate(tr_headers):
                    if h in ['tracking', 'mã vận đơn', 'ma van don', 'tracking_number', 'เลขพัสดุ']: t_col = idx
                    elif h in ['status', 'trạng thái', 'trang thai', 'สถานะ']: s_col = idx
                    elif h in ['note', 'ghi chú', 'ghi chu', 'หมายเหตุ']: n_col = idx
                if t_col == -1: t_col = 0
                if s_col == -1 and len(tr_headers) > 1: s_col = 1
                if n_col == -1 and len(tr_headers) > 2: n_col = 2
                for tr_r in tr_rows[1:]:
                    if not any(c is not None for c in tr_r): continue
                    t_val = str(tr_r[t_col] if t_col < len(tr_r) and tr_r[t_col] is not None else '').strip()
                    s_val = str(tr_r[s_col] if s_col != -1 and s_col < len(tr_r) and tr_r[s_col] is not None else '').strip()
                    n_val = str(tr_r[n_col] if n_col != -1 and n_col < len(tr_r) and tr_r[n_col] is not None else '').strip()
                    norm_s = THAI_STATUS_MAP.get(s_val, THAI_STATUS_MAP.get(s_val.upper(), s_val))
                    if t_val:
                        tracking_updates[t_val] = {'status': norm_s, 'note': n_val}
            break

    wb.close()
    
    if not rows:
        return []
        
    headers = [str(h or '').strip() for h in rows[0]]
    raw_dicts = [dict(zip(headers, r)) for r in rows[1:] if any(c is not None for c in r)]
    
    # Check if this is a Thai J&T export file
    if is_thai_jt_header(headers):
        result_rows = []
        for line_no, r in enumerate(raw_dicts, 2):
            tracking = str(r.get('เลขพัสดุ') or '').strip()
            recipient = str(r.get('ชื่อผู้รับ') or '').strip()
            phone = normalize_thai_phone(r.get('เบอร์โทรผู้รับ'))
            
            addr1 = str(r.get('ที่อยู่') or '').strip()
            tambon = str(r.get('ตำบล') or '').strip()
            amphoe = str(r.get('อำเภอ') or '').strip()
            province = str(r.get('จังหวัด') or '').strip()
            postcode = str(r.get('รหัสไปรษณีย์') or '').strip()
            prod_name = str(r.get('ชื่อสินค้า') or '').strip()
            cod = normalize_thai_cod(r.get('COD'))
            raw_status = str(r.get('Status') or '').strip()
            status = THAI_STATUS_MAP.get(raw_status, THAI_STATUS_MAP.get(raw_status.upper(), ''))
            note = str(r.get('หมายเหตุ') or '').strip()
            
            # Code and Customer identification
            code = ''
            customer = ''
            m_code = re.search(r'\b(HC|FG|DG|AH|AR|AD|[A-Z]{2,4})[-_]?([0-9A-Za-z\-]+)\b', note)
            if m_code:
                customer = m_code.group(1).upper()
                code = m_code.group(0).upper()
            else:
                m_track = re.search(r'^(HC|FG|DG|AH|AR|AD|[A-Z]{2,4})[-_]?([0-9A-Za-z\-]+)$', tracking)
                if m_track:
                    customer = m_track.group(1).upper()
                    code = tracking
                else:
                    code = tracking or f'TH-ORD-{line_no}'
                    customer = 'HC'
                    
            parts = [addr1]
            if tambon:
                parts.append(tambon if tambon.startswith(('ต.', 'ตำบล')) else f'ต.{tambon}')
            if amphoe:
                parts.append(amphoe if amphoe.startswith(('อ.', 'อำเภอ')) else f'อ.{amphoe}')
            if province:
                parts.append(province if province.startswith(('จ.', 'จังหวัด')) else f'จ.{province}')
            if postcode:
                parts.append(postcode)
            full_address = ' '.join(p for p in parts if p).strip()
            
            skus = parse_thai_note_skus(note, default_sku=prod_name or 'ITEM')
            
            for idx, item in enumerate(skus):
                result_rows.append({
                    'code': code,
                    'customer': customer,
                    'tracking': tracking,
                    'cod': cod if idx == 0 else '0',
                    'currency': 'THB',
                    'country': 'TH',
                    'sku': item['sku'],
                    'quantity': item['quantity'],
                    'recipient': recipient,
                    'phone': phone,
                    'original_address': full_address,
                    'full_address': full_address,
                    'province': province,
                    'district': amphoe,
                    'postcode': postcode,
                    'status': status,
                    'note': note
                })
        return result_rows
        
    if tracking_updates:
        for r in raw_dicts:
            t = str(r.get('tracking') or r.get('mã vận đơn') or r.get('เลขพัสดุ') or '').strip()
            if t and t in tracking_updates:
                if not r.get('status') or r.get('status') == 'DRAFT':
                    r['status'] = tracking_updates[t]['status']
                if not r.get('note'):
                    r['note'] = tracking_updates[t]['note']
    return raw_dicts


def export_stock_xlsx(stocks: list[dict]) -> io.BytesIO:
    """Exports stock records as styled Excel workbook."""
    headers = ['Khách hàng', 'SKU sản phẩm', 'Tồn vật lý', 'Đã giữ', 'Khả dụng']
    rows = []
    for s in stocks:
        rows.append([
            str(s.get('customer') or ''),
            str(s.get('sku') or ''),
            int(s.get('on_hand') or 0),
            int(s.get('reserved') or 0),
            int(s.get('available') or 0)
        ])
    return build_styled_workbook('Tồn kho FOXRUN', headers, rows, text_columns={1, 2}, number_columns={3, 4, 5})


def export_customers_xlsx(customers: list[dict]) -> io.BytesIO:
    """Exports customer records as styled Excel workbook."""
    headers = ['ID', 'Tên khách hàng', 'Mã hiển thị', 'Mã chính', 'Từ khóa SKU', 'Phí giao cố định', 'Phí hoàn cố định', 'Phí COD (%)', 'Số đơn']
    rows = []
    for c in customers:
        rows.append([
            int(c.get('id') or 0),
            str(c.get('name') or ''),
            str(c.get('code') or ''),
            str(c.get('primary_code') or ''),
            str(c.get('sku_keywords') or ''),
            float(c.get('fixed_delivery_fee') or 0),
            float(c.get('fixed_return_fee') or 0),
            float(c.get('cod_fee_percent') or 0),
            int(c.get('order_count') or 0)
        ])
    return build_styled_workbook('Khách hàng FOXRUN', headers, rows, text_columns={3, 4, 5}, number_columns={1, 9}, currency_columns={6, 7, 8})


def export_tracking_xlsx(records: list[dict]) -> io.BytesIO:
    """Exports tracking reports as styled Excel workbook."""
    headers = ['Mã đơn', 'Mã vận đơn', 'Khách hàng', 'Trạng thái vận chuyển', 'Trạng thái xử lý', 'Người nhận', 'Số điện thoại', 'Tỉnh / Thành phố', 'Ghi chú vận chuyển', 'Thời gian cập nhật']
    rows = []
    for r in records:
        rows.append([
            str(r.get('code') or ''),
            str(r.get('tracking') or ''),
            str(r.get('customer') or ''),
            str(r.get('transport') or r.get('status') or ''),
            str(r.get('stage') or ''),
            str(r.get('recipient') or ''),
            str(r.get('phone') or ''),
            str(r.get('province') or ''),
            str(r.get('note') or ''),
            str(r.get('updated_at') or '')
        ])
    return build_styled_workbook('Báo cáo Tracking', headers, rows, text_columns={1, 2, 3, 7})



def parse_tracking_excel_workbook(content_bytes: bytes, filename: str = '') -> list[dict]:
    """Parses Excel or CSV tracking file into standard tracking dictionary."""
    if filename.lower().endswith('.csv'):
        import csv
        text = content_bytes.decode('utf-8-sig')
        return list(csv.DictReader(io.StringIO(text)))
        
    wb = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)
    target_sheet = wb.active
    for sname in ['Tracking_Update', 'Tracking', 'Vận đơn', 'Sheet1']:
        if sname in wb.sheetnames:
            target_sheet = wb[sname]
            break
            
    rows = list(target_sheet.iter_rows(values_only=True))
    wb.close()
    
    if not rows:
        return []
        
    headers = [str(h or '').strip().lower() for h in rows[0]]
    
    # Identify column indexes
    track_col = -1
    status_col = -1
    note_col = -1
    
    for idx, h in enumerate(headers):
        if h in ['tracking', 'mã vận đơn', 'ma van don', 'tracking_number', 'เลขพัสดุ', 'หมายเลขพัสดุ', 'waybill', 'awb', 'code']:
            if track_col == -1: track_col = idx
        elif h in ['status', 'trạng thái', 'trang thai', 'สถานะ', 'สถานะพัสดุ', 'tracking_status']:
            if status_col == -1: status_col = idx
        elif h in ['note', 'ghi chú', 'ghi chu', 'หมายเหตุ', 'reason', 'lý do']:
            if note_col == -1: note_col = idx
            
    if track_col == -1: track_col = 0
    if status_col == -1 and len(headers) > 1: status_col = 1
    if note_col == -1 and len(headers) > 2: note_col = 2
    
    out = []
    for r in rows[1:]:
        if not any(c is not None for c in r):
            continue
        tracking_val = str(r[track_col] if track_col < len(r) and r[track_col] is not None else '').strip()
        raw_status = str(r[status_col] if status_col != -1 and status_col < len(r) and r[status_col] is not None else '').strip()
        note_val = str(r[note_col] if note_col != -1 and note_col < len(r) and r[note_col] is not None else '').strip()
        
        norm_status = THAI_STATUS_MAP.get(raw_status, THAI_STATUS_MAP.get(raw_status.upper(), raw_status))
        
        out.append({
            'tracking': tracking_val,
            'status': norm_status,
            'note': note_val
        })
        
    return out
