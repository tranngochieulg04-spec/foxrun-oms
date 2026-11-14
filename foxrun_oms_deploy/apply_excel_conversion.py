# -*- coding: utf-8 -*-
import sys

# 1. Update workflow.py
with open('workflow.py', 'r', encoding='utf-8') as f:
    wf = f.read()

target_wf = """        return StreamingResponse(iter(['\\ufeff'+buff.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=\"foxrun_orders.csv\"'})"""

replace_wf = """        return StreamingResponse(iter(['\\ufeff'+buff.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=\"foxrun_orders.csv\"'})

    @app.get('/api/wf/sample/orders.xlsx')
    def sample_orders_xlsx():
        from fastapi.responses import FileResponse
        path = os.path.join(os.path.dirname(__file__), 'foxrun_order_sample.xlsx')
        if not os.path.exists(path):
            raise HTTPException(404, 'File mẫu không tìm thấy.')
        return FileResponse(path, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename='foxrun_order_sample.xlsx')

    @app.get('/api/wf/sample/tracking.xlsx')
    def sample_tracking_xlsx():
        from fastapi.responses import FileResponse
        path = os.path.join(os.path.dirname(__file__), 'foxrun_tracking_sample.xlsx')
        if not os.path.exists(path):
            raise HTTPException(404, 'File mẫu không tìm thấy.')
        return FileResponse(path, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename='foxrun_tracking_sample.xlsx')

    @app.get('/api/wf/export-xlsx')
    @app.get('/api/wf/orders/export-xlsx')
    def export_xlsx(db=Depends(get_db),user=Depends(current_user)):
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Đơn hàng FOXRUN"

        fields=['code','customer','tracking','cod','currency','stage','transport','finance','completed_at','returned_at','recipient','phone','full_address','province','district','postcode','country','sku','quantity']
        headers_vn=['Mã đơn','Khách hàng','Mã vận đơn','Tiền COD','Tiền tệ','Trạng thái xử lý','Trạng thái giao','Đối soát','Ngày giao','Ngày hoàn','Người nhận','Điện thoại','Địa chỉ chi tiết','Tỉnh / Bang','Quận / Huyện','Mã bưu cục (Postcode)','Thị trường','SKU sản phẩm','Số lượng']

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
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        for o in db.scalars(select(Order).order_by(Order.id)).all():
            if management.is_deleted(db, o.id): continue
            row=serialize(db,o,db.get(Flow,o.id))
            for item in row['items'] or [{'sku':'','quantity':''}]:
                out={k:row.get(k,'') for k in fields};out.update(item)
                row_vals=[]
                for k in fields:
                    v=out.get(k,'')
                    if k in {'cod','quantity'} and v not in ('', None):
                        try:
                            v = float(v) if k == 'cod' else int(v)
                        except (ValueError, TypeError):
                            pass
                    elif isinstance(v,str) and v.startswith(('=','+','-','@','\\t','\\r')):
                        v="'"+v
                    row_vals.append(v)
                ws.append(row_vals)

        for row in ws.iter_rows(min_row=2, min_col=1, max_col=len(fields)):
            for cell in row:
                cell.font = data_font
                cell.border = thin_border
                cell.alignment = Alignment(vertical='center')

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 40)

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': 'attachment; filename=\"foxrun_orders.xlsx\"'}
        )"""

assert target_wf in wf, "target_wf not found in workflow.py"
wf = wf.replace(target_wf, replace_wf, 1)

with open('workflow.py', 'w', encoding='utf-8') as f:
    f.write(wf)
print('Updated workflow.py')

# 2. Update management.py
with open('management.py', 'r', encoding='utf-8') as f:
    mg = f.read()

target_mg = """        return StreamingResponse(iter(['\\ufeff'+buffer.getvalue()]), media_type='text/csv; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename=\"foxrun_period_'+str(pid)+'.csv\"'})"""

replace_mg = """        return StreamingResponse(iter(['\\ufeff'+buffer.getvalue()]), media_type='text/csv; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename=\"foxrun_period_'+str(pid)+'.csv\"'})

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
            headers={'Content-Disposition': f'attachment; filename=\"foxrun_period_{pid}.xlsx\"'}
        )"""

assert target_mg in mg, "target_mg not found in management.py"
mg = mg.replace(target_mg, replace_mg, 1)

with open('management.py', 'w', encoding='utf-8') as f:
    f.write(mg)
print('Updated management.py')

# 3. Update workflow.html
with open('workflow.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Replace Export CSV button with Excel + CSV
target_export_html = """extraButtons+`<a href="/api/wf/export"><button>Xuất CSV</button></a><button id="refresh">↻ Làm mới</button>${newButton()}`"""
replace_export_html = """extraButtons+`<a href="/api/wf/export-xlsx"><button class="primary small" style="background:#107c41;color:#fff;border-color:#107c41;font-weight:600">📊 Xuất Excel (.xlsx)</button></a><a href="/api/wf/export"><button class="small">Xuất CSV</button></a><button id="refresh">↻ Làm mới</button>${newButton()}`"""

assert target_export_html in html, "target_export_html not found in workflow.html"
html = html.replace(target_export_html, replace_export_html, 1)

# Replace sample download buttons in view === 'imports'
target_sample_orders = """<button class="small" id="sampleOrders">Tải file mẫu đơn</button>"""
replace_sample_orders = """<a href="/api/wf/sample/orders.xlsx" download="foxrun_order_sample.xlsx"><button class="small" type="button" style="background:#f6ffed;color:#107c41;border-color:#b7eb8f;font-weight:600">📥 Tải file mẫu đơn (Excel .xlsx)</button></a>"""

assert target_sample_orders in html, "target_sample_orders not found"
html = html.replace(target_sample_orders, replace_sample_orders, 1)

target_sample_tracking = """<button class="small" id="sampleTracking">Tải file mẫu tracking</button>"""
replace_sample_tracking = """<a href="/api/wf/sample/tracking.xlsx" download="foxrun_tracking_sample.xlsx"><button class="small" type="button" style="background:#f6ffed;color:#107c41;border-color:#b7eb8f;font-weight:600">📥 Tải file mẫu tracking (Excel .xlsx)</button></a>"""

assert target_sample_tracking in html, "target_sample_tracking not found"
html = html.replace(target_sample_tracking, replace_sample_tracking, 1)

# Update file inputs accept
html = html.replace('accept=".csv,.xlsx"', 'accept=".xlsx,.xls,.csv"')

with open('workflow.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Updated workflow.html')

# 4. Update management_ui.js for Period Export
with open('management_ui.js', 'r', encoding='utf-8') as f:
    mui = f.read()

target_period_export = """periodTotals(period.totals) + '<div class="actions pad"><a href="/api/wf/periods/' + id + '/export"><button>Xuất bảng kê CSV</button></a>' +"""
replace_period_export = """periodTotals(period.totals) + '<div class="actions pad"><a href="/api/wf/periods/' + id + '/export-xlsx"><button class="primary small" style="background:#107c41;color:#fff;border-color:#107c41;font-weight:600">📊 Xuất Excel (.xlsx)</button></a><a href="/api/wf/periods/' + id + '/export"><button class="small">Xuất CSV</button></a>' +"""

assert target_period_export in mui, "target_period_export not found in management_ui.js"
mui = mui.replace(target_period_export, replace_period_export, 1)

with open('management_ui.js', 'w', encoding='utf-8') as f:
    f.write(mui)
print('Updated management_ui.js')
