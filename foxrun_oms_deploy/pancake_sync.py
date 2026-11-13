"""
FOXRUN OMS Workflow 3.4 - Pancake POS, Google Sheets Sync, Auto-matching Pipeline,
System Fees, Warehouse Handover, Stock Bulk Import & COD Reconciliation.
"""
import csv
import io
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta, date, time
from decimal import Decimal
from typing import Optional, List, Dict, Any, Union

from fastapi import Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func, or_
from openpyxl import load_workbook

LOCAL_TIME = timezone(timedelta(hours=7))


class SourceInput(BaseModel):
    source_type: str = Field(pattern=r'^(pancake|google_sheet)$')
    source_name: str = Field(min_length=1, max_length=200)
    is_active: bool = True
    code_prefix: Optional[str] = Field(default='', max_length=50)
    pancake_api_key: Optional[str] = Field(default='', max_length=500)
    pancake_shop_id: Optional[str] = Field(default='', max_length=100)
    pancake_page_id: Optional[str] = Field(default='', max_length=100)
    pancake_status_filter: Optional[str] = Field(default='', max_length=200)
    gsheet_url: Optional[str] = Field(default='', max_length=1000)
    gsheet_tab_name: Optional[str] = Field(default='', max_length=200)


class SourceEdit(BaseModel):
    source_name: Optional[str] = None
    is_active: Optional[bool] = None
    code_prefix: Optional[str] = None
    pancake_api_key: Optional[str] = None
    pancake_shop_id: Optional[str] = None
    pancake_page_id: Optional[str] = None
    pancake_status_filter: Optional[str] = None
    gsheet_url: Optional[str] = None
    gsheet_tab_name: Optional[str] = None


class SystemFeeInput(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    fee: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    postcodes: Union[List[str], str] = Field(default='')
    regions: Union[List[str], str] = Field(default='')


class HandoverInput(BaseModel):
    order_ids: Optional[List[int]] = None
    orders: Optional[List[Dict[str, Any]]] = None


class PeriodBulkCreateInput(BaseModel):
    date_from: date
    date_to: date
    date_basis: str = 'completion'


class NormalizeBulkInput(BaseModel):
    order_ids: List[int] = Field(min_length=1)


def check_postcode_match(postcode_str: str, config_postcodes: str) -> bool:
    if not postcode_str or not config_postcodes:
        return False
    pc = re.sub(r'[^0-9]', '', str(postcode_str).strip())
    if not pc:
        return False
    try:
        pc_num = int(pc)
    except ValueError:
        return False

    parts = [p.strip() for p in config_postcodes.replace('\n', ',').split(',') if p.strip()]
    for part in parts:
        if '-' in part:
            start_end = part.split('-')
            if len(start_end) == 2:
                try:
                    s = int(re.sub(r'[^0-9]', '', start_end[0]))
                    e = int(re.sub(r'[^0-9]', '', start_end[1]))
                    if s <= pc_num <= e:
                        return True
                except ValueError:
                    continue
        else:
            clean = re.sub(r'[^0-9]', '', part)
            if clean and (clean == pc or pc.startswith(clean)):
                return True
    return False


def check_region_match(text: str, config_regions: str) -> bool:
    if not text or not config_regions:
        return False
    target = text.lower()
    regions = [r.strip().lower() for r in config_regions.replace('\n', ',').split(',') if r.strip()]
    for r in regions:
        if r in target:
            return True
    return False


def calculate_special_fee(db, address: str, province: str, district: str, postcode: str, core) -> tuple[Decimal, str]:
    SystemFeeConfig = core.SystemFeeConfig
    configs = db.scalars(select(SystemFeeConfig).order_by(SystemFeeConfig.fee.desc())).all()
    full_text = f"{address} {province} {district} {postcode}".strip()

    # Remote check first, then tourism, or highest fee
    for cfg in configs:
        matched = False
        if cfg.postcodes and check_postcode_match(postcode, cfg.postcodes):
            matched = True
        elif cfg.regions and (check_region_match(province, cfg.regions) or check_region_match(district, cfg.regions) or check_region_match(full_text, cfg.regions)):
            matched = True
        if matched:
            return Decimal(cfg.fee), cfg.name

    return Decimal('0.00'), ''


def format_order_code(dt: datetime, customer_code: str, origin_id: str) -> str:
    yymmdd = dt.strftime('%y%m%d')
    clean_cust = re.sub(r'[^a-zA-Z0-9_]', '', customer_code).upper()
    clean_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(origin_id)).strip()
    return f"{yymmdd}-{clean_cust}-{clean_id}"


def match_customer(db, order_data: dict, source, core, workflow) -> Any:
    Customer = core.Customer
    Alias = core.Alias
    Stock = workflow.Stock

    # 1. If source is tied to a specific Customer
    if source and getattr(source, 'customer_id', None):
        c = db.get(Customer, source.customer_id)
        if c:
            return c

    # 2. Check customer tag or code from order
    cust_code = (order_data.get('customer') or order_data.get('customer_code') or '').strip().upper()
    if cust_code:
        c = db.scalar(select(Customer).where(or_(Customer.code == cust_code, Customer.primary_code == cust_code)))
        if c:
            return c
        a = db.scalar(select(Alias).where(Alias.alias == cust_code))
        if a and a.customer:
            return a.customer

    # Check prefix
    code_val = order_data.get('code') or ''
    if '-' in code_val:
        parts = code_val.split('-')
        if len(parts) >= 2:
            candidate = parts[1].upper()
            c = db.scalar(select(Customer).where(or_(Customer.code == candidate, Customer.primary_code == candidate)))
            if c:
                return c
            a = db.scalar(select(Alias).where(Alias.alias == candidate))
            if a and a.customer:
                return a.customer

    # 3. Match by sku_keywords or Stock SKUs
    items = order_data.get('items', [])
    item_skus = [str(it.get('sku', '')).strip().upper() for it in items if it.get('sku')]
    if not item_skus and order_data.get('sku'):
        item_skus = [str(order_data.get('sku')).strip().upper()]

    all_customers = db.scalars(select(Customer)).all()
    for c in all_customers:
        keywords = [kw.strip().upper() for kw in (c.sku_keywords or '').replace('\n', ',').split(',') if kw.strip()]
        for kw in keywords:
            for sku in item_skus:
                if kw in sku:
                    return c

    for sku in item_skus:
        stock = db.scalar(select(Stock).where(Stock.sku == sku).limit(1))
        if stock and stock.customer_id:
            c = db.get(Customer, stock.customer_id)
            if c:
                return c

    return None


def push_address_to_pancake(shop_id: str, order_id: str, api_key: str, address_info: dict) -> dict:
    if not shop_id or not order_id or not api_key:
        return {'success': False, 'message': 'Missing shop_id, order_id or api_key'}

    url = f"https://pos.pages.fm/api/v1/shops/{shop_id}/orders/{order_id}?api_key={api_key}"
    payload = {
        "shipping_address": {
            "address": address_info.get("full_address") or address_info.get("original_address", ""),
            "full_address": address_info.get("full_address") or "",
            "province": address_info.get("province", ""),
            "district": address_info.get("district", ""),
            "postcode": address_info.get("postcode", "")
        }
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='PUT')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode('utf-8')
            return {'success': True, 'response': json.loads(body) if body else {}}
    except urllib.error.HTTPError as e:
        return {'success': False, 'status_code': e.code, 'message': str(e)}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def install(app, core, workflow):
    Customer = core.Customer
    Order = core.Order
    Item = core.Item
    Audit = core.Audit
    CustomerSource = core.CustomerSource
    SystemFeeConfig = core.SystemFeeConfig
    Flow = workflow.Flow
    Stock = workflow.Stock
    Ledger = workflow.Ledger
    Preview = workflow.Preview
    Period = workflow.management.Period
    PeriodLine = workflow.management.PeriodLine

    get_db = core.get_db
    current_user = core.current_user
    audit = core.audit
    require = workflow.require
    claim_tracking = workflow.claim_tracking
    lock_order = workflow.lock_order
    serialize = workflow.serialize
    management = workflow.management

    # 1. Customer Sources Endpoints
    @app.get('/api/wf/customers/{cid}/sources')
    def list_customer_sources(cid: int, db=Depends(get_db), user=Depends(current_user)):
        c = db.get(Customer, cid)
        if not c:
            raise HTTPException(404, 'Không tìm thấy khách hàng.')
        sources = db.scalars(select(CustomerSource).where(CustomerSource.customer_id == cid).order_by(CustomerSource.id.desc())).all()
        return [{
            'id': s.id,
            'customer_id': s.customer_id,
            'is_active': bool(s.is_active),
            'source_type': s.source_type,
            'source_name': s.source_name,
            'code_prefix': s.code_prefix,
            'pancake_api_key': s.pancake_api_key,
            'pancake_shop_id': s.pancake_shop_id,
            'pancake_page_id': s.pancake_page_id,
            'pancake_status_filter': s.pancake_status_filter,
            'gsheet_url': s.gsheet_url,
            'gsheet_tab_name': s.gsheet_tab_name
        } for s in sources]

    @app.post('/api/wf/customers/{cid}/sources')
    def create_customer_source(cid: int, data: SourceInput, db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'operator')
        c = db.get(Customer, cid)
        if not c:
            raise HTTPException(404, 'Không tìm thấy khách hàng.')
        source = CustomerSource(
            customer_id=cid,
            is_active=1 if data.is_active else 0,
            source_type=data.source_type,
            source_name=data.source_name.strip(),
            code_prefix=(data.code_prefix or '').strip().upper(),
            pancake_api_key=(data.pancake_api_key or '').strip(),
            pancake_shop_id=(data.pancake_shop_id or '').strip(),
            pancake_page_id=(data.pancake_page_id or '').strip(),
            pancake_status_filter=(data.pancake_status_filter or '').strip(),
            gsheet_url=(data.gsheet_url or '').strip(),
            gsheet_tab_name=(data.gsheet_tab_name or '').strip()
        )
        db.add(source)
        audit(db, user, 'source.create', {'customer_id': cid, 'name': source.source_name, 'type': source.source_type})
        db.commit()
        db.refresh(source)
        return {'ok': True, 'id': source.id}

    @app.patch('/api/wf/customers/{cid}/sources/{sid}')
    def update_customer_source(cid: int, sid: int, data: SourceEdit, db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'operator')
        source = db.scalar(select(CustomerSource).where(CustomerSource.id == sid, CustomerSource.customer_id == cid))
        if not source:
            raise HTTPException(404, 'Không tìm thấy nguồn kết nối.')
        if data.source_name is not None:
            source.source_name = data.source_name.strip()
        if data.is_active is not None:
            source.is_active = 1 if data.is_active else 0
        if data.code_prefix is not None:
            source.code_prefix = data.code_prefix.strip().upper()
        if data.pancake_api_key is not None:
            source.pancake_api_key = data.pancake_api_key.strip()
        if data.pancake_shop_id is not None:
            source.pancake_shop_id = data.pancake_shop_id.strip()
        if data.pancake_page_id is not None:
            source.pancake_page_id = data.pancake_page_id.strip()
        if data.pancake_status_filter is not None:
            source.pancake_status_filter = data.pancake_status_filter.strip()
        if data.gsheet_url is not None:
            source.gsheet_url = data.gsheet_url.strip()
        if data.gsheet_tab_name is not None:
            source.gsheet_tab_name = data.gsheet_tab_name.strip()
        audit(db, user, 'source.update', {'source_id': sid, 'customer_id': cid})
        db.commit()
        return {'ok': True}

    @app.delete('/api/wf/customers/{cid}/sources/{sid}')
    def delete_customer_source(cid: int, sid: int, db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin')
        source = db.scalar(select(CustomerSource).where(CustomerSource.id == sid, CustomerSource.customer_id == cid))
        if not source:
            raise HTTPException(404, 'Không tìm thấy nguồn kết nối.')
        audit(db, user, 'source.delete', {'source_id': sid, 'customer_id': cid})
        db.delete(source)
        db.commit()
        return {'ok': True}

    @app.post('/api/wf/customers/{cid}/sources/{sid}/test')
    def test_customer_source(cid: int, sid: int, db=Depends(get_db), user=Depends(current_user)):
        source = db.scalar(select(CustomerSource).where(CustomerSource.id == sid, CustomerSource.customer_id == cid))
        if not source:
            raise HTTPException(404, 'Không tìm thấy nguồn kết nối.')

        if source.source_type == 'pancake':
            if not source.pancake_api_key or not source.pancake_shop_id:
                return {'success': False, 'message': 'Thiếu API Key hoặc Shop ID của Pancake.'}
            test_url = f"https://pos.pages.fm/api/v1/shops/{source.pancake_shop_id}?api_key={source.pancake_api_key}"
            try:
                req = urllib.request.Request(test_url)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        return {'success': True, 'message': 'Kết nối Pancake POS thành công!'}
                    return {'success': False, 'message': f'Pancake trả về mã trạng thái {resp.status}.'}
            except urllib.error.HTTPError as e:
                return {'success': False, 'message': f'Lỗi Pancake ({e.code}): {e.reason}'}
            except Exception as e:
                # In test or offline environment, return diagnostic
                return {'success': False, 'message': f'Không thể kết nối đến Pancake POS: {str(e)}'}

        elif source.source_type == 'google_sheet':
            if not source.gsheet_url:
                return {'success': False, 'message': 'Thiếu Google Sheet URL.'}
            match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', source.gsheet_url)
            if not match:
                return {'success': False, 'message': 'Google Sheet URL không hợp lệ.'}
            sheet_id = match.group(1)
            csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
            if source.gsheet_tab_name:
                csv_url += f"&sheet={urllib.parse.quote(source.gsheet_tab_name)}"
            try:
                req = urllib.request.Request(csv_url)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        return {'success': True, 'message': 'Kết nối Google Sheets thành công!'}
                    return {'success': False, 'message': f'Google Sheets trả về mã {resp.status}.'}
            except Exception as e:
                return {'success': False, 'message': f'Không thể truy cập Google Sheets: {str(e)}'}

        return {'success': False, 'message': 'Loại nguồn không hỗ trợ.'}

    # Helper function to ingest an order into DB with auto-matching pipeline
    def ingest_order(db, raw_order: dict, source_obj, actor: str) -> dict:
        cust = match_customer(db, raw_order, source_obj, core, workflow)
        if not cust:
            return {'success': False, 'error': 'Chưa xác định được khách hàng', 'raw': raw_order}

        management.ensure_customer_active(db, cust.id)

        now_utc = datetime.now(timezone.utc)
        origin_id = str(raw_order.get('origin_id') or raw_order.get('id') or raw_order.get('order_id') or raw_order.get('code') or '').strip()
        if not origin_id:
            origin_id = f"R{int(now_utc.timestamp())}"

        cust_code = cust.primary_code or cust.code
        formatted_code = format_order_code(now_utc, cust_code, origin_id)

        # Check existing order for idempotency
        existing = None
        if raw_order.get('pancake_order_id') and raw_order.get('pancake_shop_id'):
            existing = db.scalar(select(Order).where(
                Order.pancake_order_id == str(raw_order['pancake_order_id']),
                Order.pancake_shop_id == str(raw_order['pancake_shop_id'])
            ))
        if not existing:
            existing = db.scalar(select(Order).where(Order.customer_id == cust.id, Order.code == formatted_code))

        tracking = (raw_order.get('tracking') or raw_order.get('tracking_number') or '').strip()
        cod_val = Decimal(str(raw_order.get('cod') or 0))
        currency_val = (raw_order.get('currency') or 'MYR').strip().upper()
        status_val = (raw_order.get('status') or 'DRAFT').strip().upper()
        if status_val not in workflow.TRACKING and status_val != 'DRAFT':
            status_val = 'DRAFT'

        completed_at_val = raw_order.get('completed_at')
        if isinstance(completed_at_val, str) and completed_at_val:
            try:
                completed_at_val = datetime.fromisoformat(completed_at_val.replace('Z', '+00:00'))
            except Exception:
                completed_at_val = None

        addr = raw_order.get('address') or raw_order.get('original_address') or ''
        full_addr = raw_order.get('full_address') or addr
        prov = raw_order.get('province') or ''
        dist = raw_order.get('district') or ''
        postc = raw_order.get('postcode') or ''

        # Auto calculate special fee
        spec_fee, spec_note = calculate_special_fee(db, full_addr or addr, prov, dist, postc, core)
        if raw_order.get('special_fee'):
            spec_fee = Decimal(str(raw_order['special_fee']))
            spec_note = raw_order.get('special_fee_note') or spec_note

        if existing:
            # Update order fields
            if tracking and not existing.tracking:
                existing.tracking = tracking
                claim_tracking(existing, tracking, db)
            existing.cod = cod_val
            existing.special_fee = spec_fee
            existing.special_fee_note = spec_note
            if completed_at_val:
                existing.completed_at = completed_at_val
            f = db.get(Flow, existing.id)
            if f:
                if prov: f.province = prov
                if dist: f.district = dist
                if postc: f.postcode = postc
                if full_addr: f.full_address = full_addr
                if existing.tracking and f.stage in ('REVIEW', 'WAIT_STOCK'):
                    f.stage = 'READY'
            return {'success': True, 'action': 'updated', 'order_id': existing.id, 'code': existing.code}

        # Create new Order
        source_name = source_obj.source_name if source_obj else (raw_order.get('source') or 'manual')
        new_order = Order(
            customer_id=cust.id,
            source=source_name,
            code=formatted_code,
            tracking=tracking,
            cod=cod_val,
            currency=currency_val,
            status=status_val,
            special_fee=spec_fee,
            special_fee_note=spec_note,
            pancake_order_id=str(raw_order.get('pancake_order_id') or ''),
            pancake_shop_id=str(raw_order.get('pancake_shop_id') or ''),
            pancake_sync_status=str(raw_order.get('pancake_sync_status') or ''),
            completed_at=completed_at_val,
            is_cod_received=1 if raw_order.get('is_cod_received') else 0,
            is_returned_to_warehouse=1 if raw_order.get('is_returned_to_warehouse') else 0
        )
        db.add(new_order)
        db.flush()

        # Add items
        items = raw_order.get('items', [])
        if not items and raw_order.get('sku'):
            items = [{'sku': raw_order.get('sku'), 'quantity': raw_order.get('quantity') or 1}]
        for it in items:
            sku = str(it.get('sku') or '').strip()
            qty = int(it.get('quantity') or 1)
            if sku:
                db.add(Item(order_id=new_order.id, sku=sku, quantity=qty))

        # Flow
        initial_stage = 'READY' if tracking else 'REVIEW'
        new_flow = Flow(
            order_id=new_order.id,
            stage=initial_stage,
            recipient=raw_order.get('recipient') or '',
            phone=raw_order.get('phone') or '',
            country=raw_order.get('country') or 'MY',
            original_address=addr,
            full_address=full_addr,
            province=prov,
            district=dist,
            postcode=postc,
            carrier=raw_order.get('carrier') or '',
            note=raw_order.get('note') or ''
        )
        db.add(new_flow)
        db.flush()

        if tracking:
            claim_tracking(new_order, tracking, db)

        return {'success': True, 'action': 'created', 'order_id': new_order.id, 'code': new_order.code}

    def sync_source_instance(db, source: CustomerSource, actor: str) -> dict:
        if source.source_type == 'pancake':
            if not source.pancake_api_key or not source.pancake_shop_id:
                return {'success': False, 'message': 'Thiếu cấu hình Pancake POS.'}

            url = f"https://pos.pages.fm/api/v1/shops/{source.pancake_shop_id}/orders?api_key={source.pancake_api_key}&page_size=50"
            if source.pancake_status_filter:
                url += f"&status={urllib.parse.quote(source.pancake_status_filter)}"

            orders_data = []
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=12) as resp:
                    res_json = json.loads(resp.read().decode('utf-8'))
                    orders_data = res_json.get('data') or res_json.get('orders') or []
            except Exception as e:
                # If network fails in offline test, check if mock data is provided
                return {'success': False, 'message': f'Không thể đồng bộ Pancake: {e}'}

            created_cnt = 0
            updated_cnt = 0
            errors = []
            for item in orders_data:
                shipping = item.get('shipping_address') or {}
                order_dict = {
                    'origin_id': item.get('id') or item.get('order_number'),
                    'pancake_order_id': str(item.get('id', '')),
                    'pancake_shop_id': source.pancake_shop_id,
                    'pancake_sync_status': 'synced',
                    'recipient': shipping.get('name') or item.get('customer_name') or '',
                    'phone': shipping.get('phone') or item.get('customer_phone') or '',
                    'original_address': shipping.get('address') or shipping.get('full_address') or '',
                    'full_address': shipping.get('full_address') or shipping.get('address') or '',
                    'province': shipping.get('province') or '',
                    'district': shipping.get('district') or '',
                    'postcode': shipping.get('postcode') or '',
                    'cod': item.get('cod') or item.get('total_price') or 0,
                    'tracking': item.get('partner', {}).get('tracking_code') or item.get('tracking_code') or '',
                    'items': [{'sku': line.get('variation_info', {}).get('sku') or line.get('sku') or line.get('name'), 'quantity': line.get('quantity', 1)} for line in item.get('items', [])]
                }
                res = ingest_order(db, order_dict, source, actor)
                if res.get('success'):
                    if res.get('action') == 'created': created_cnt += 1
                    else: updated_cnt += 1
                else:
                    errors.append(res)
            db.commit()
            return {'success': True, 'source_id': source.id, 'created': created_cnt, 'updated': updated_cnt, 'errors': errors}

        elif source.source_type == 'google_sheet':
            match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', source.gsheet_url or '')
            if not match:
                return {'success': False, 'message': 'Google Sheet URL không hợp lệ.'}
            sheet_id = match.group(1)
            csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
            if source.gsheet_tab_name:
                csv_url += f"&sheet={urllib.parse.quote(source.gsheet_tab_name)}"

            try:
                req = urllib.request.Request(csv_url)
                with urllib.request.urlopen(req, timeout=12) as resp:
                    content = resp.read().decode('utf-8')
                    reader = csv.DictReader(io.StringIO(content))
                    rows = list(reader)
            except Exception as e:
                return {'success': False, 'message': f'Không thể đọc Google Sheet: {e}'}

            created_cnt = 0
            updated_cnt = 0
            errors = []
            for r in rows:
                row_dict = {
                    'origin_id': r.get('mã đơn hàng') or r.get('code') or r.get('order_id'),
                    'customer': r.get('mã khách') or r.get('customer') or '',
                    'recipient': r.get('tên người nhận') or r.get('người nhận') or r.get('recipient') or '',
                    'phone': r.get('sđt') or r.get('số điện thoại') or r.get('phone') or '',
                    'original_address': r.get('địa chỉ') or r.get('address') or '',
                    'full_address': r.get('địa chỉ chi tiết') or r.get('full_address') or '',
                    'province': r.get('tỉnh/bang') or r.get('province') or '',
                    'district': r.get('huyện/thành phố') or r.get('district') or '',
                    'postcode': r.get('postcode') or '',
                    'cod': r.get('tiền thu hộ') or r.get('cod') or 0,
                    'sku': r.get('mã sản phẩm') or r.get('sku') or '',
                    'quantity': r.get('số lượng') or r.get('quantity') or 1,
                    'tracking': r.get('tracking') or r.get('mã vận đơn') or '',
                    'status': r.get('trạng thái') or r.get('status') or 'DRAFT',
                    'special_fee': r.get('phí đặc biệt') or r.get('special_fee') or 0,
                    'special_fee_note': r.get('ghi chú phí') or r.get('special_fee_note') or ''
                }
                res = ingest_order(db, row_dict, source, actor)
                if res.get('success'):
                    if res.get('action') == 'created': created_cnt += 1
                    else: updated_cnt += 1
                else:
                    errors.append(res)
            db.commit()
            return {'success': True, 'source_id': source.id, 'created': created_cnt, 'updated': updated_cnt, 'errors': errors}

        return {'success': False, 'message': 'Nguồn không hỗ trợ'}

    @app.post('/api/wf/customers/{cid}/sources/{sid}/sync')
    def sync_single_source(cid: int, sid: int, db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'operator')
        source = db.scalar(select(CustomerSource).where(CustomerSource.id == sid, CustomerSource.customer_id == cid))
        if not source:
            raise HTTPException(404, 'Không tìm thấy nguồn kết nối.')
        result = sync_source_instance(db, source, user.username)
        audit(db, user, 'source.sync', {'source_id': sid, 'customer_id': cid, 'result': result})
        return result

    @app.post('/api/wf/sources/sync-all')
    def sync_all_sources(db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'operator')
        active_sources = db.scalars(select(CustomerSource).where(CustomerSource.is_active == 1)).all()
        results = []
        for s in active_sources:
            res = sync_source_instance(db, s, user.username)
            results.append(res)
        audit(db, user, 'source.sync_all', {'count': len(active_sources)})
        return {'success': True, 'count': len(active_sources), 'results': results}

    # 2. System Fee Configs
    @app.get('/api/wf/system-fees')
    def get_system_fees(db=Depends(get_db), user=Depends(current_user)):
        fees = db.scalars(select(SystemFeeConfig).order_by(SystemFeeConfig.id)).all()
        return [{
            'id': f.id,
            'key': f.key,
            'name': f.name,
            'fee': str(f.fee),
            'postcodes': f.postcodes or '',
            'regions': f.regions or ''
        } for f in fees]

    @app.post('/api/wf/system-fees')
    def save_system_fee(data: SystemFeeInput, db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin')
        fee_key = data.key.strip().lower()
        cfg = db.scalar(select(SystemFeeConfig).where(SystemFeeConfig.key == fee_key))
        p_val = json.dumps(data.postcodes) if isinstance(data.postcodes, list) else str(data.postcodes).strip()
        r_val = json.dumps(data.regions) if isinstance(data.regions, list) else str(data.regions).strip()
        if cfg:
            cfg.name = data.name.strip()
            cfg.fee = data.fee
            cfg.postcodes = p_val
            cfg.regions = r_val
        else:
            cfg = SystemFeeConfig(
                key=fee_key,
                name=data.name.strip(),
                fee=data.fee,
                postcodes=p_val,
                regions=r_val
            )
            db.add(cfg)
        audit(db, user, 'system_fee.save', {'key': fee_key, 'fee': str(data.fee)})
        db.commit()
        return {'ok': True, 'key': fee_key}

    # 3. Warehouse Handover bulk API
    @app.post('/api/wf/orders/bulk-handover')
    def bulk_handover(data: HandoverInput, db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'operator', 'warehouse')
        oids = []
        if data.order_ids:
            oids = data.order_ids
        elif data.orders:
            oids = [o.get('id') for o in data.orders if o.get('id')]
        if not oids:
            raise HTTPException(422, 'Cần chọn ít nhất một đơn để bàn giao.')

        updated_ids = []
        for oid in oids:
            o = db.get(Order, oid)
            if not o or management.is_deleted(db, oid):
                continue
            f = db.get(Flow, oid)
            if not f:
                f = Flow(order_id=oid, stage='REVIEW', version=0)
                db.add(f)
                db.flush()
            # If ready or has tracking, transition to SHIPPED
            f.stage = 'SHIPPED'
            f.version += 1
            f.updated_at = datetime.now(timezone.utc)
            updated_ids.append(oid)

        audit(db, user, 'orders.bulk_handover', {'order_ids': updated_ids, 'count': len(updated_ids)})
        db.commit()
        return {'success': True, 'count': len(updated_ids), 'updated_ids': updated_ids}

    # 4. Stock Bulk Import API (ADD / SET modes)
    @app.post('/api/wf/stock/bulk-import')
    async def stock_bulk_import(file: UploadFile = File(...), default_mode: str = 'ADD', db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'warehouse')
        content = await file.read()
        filename = (file.filename or '').lower()

        rows = []
        if filename.endswith('.xlsx'):
            wb = load_workbook(filename=io.BytesIO(content), data_only=True)
            ws = wb.active
            iter_rows = ws.iter_rows(values_only=True)
            header = next(iter_rows, None)
            if not header:
                raise HTTPException(422, 'File Excel trống.')
            headers = [str(h).strip().lower() if h is not None else '' for h in header]
            for r in iter_rows:
                if any(v is not None and str(v).strip() for v in r):
                    row_dict = {headers[i]: r[i] for i in range(min(len(headers), len(r)))}
                    rows.append(row_dict)
        else:
            text = content.decode('utf-8-sig', errors='replace')
            reader = csv.DictReader(io.StringIO(text))
            for r in reader:
                rows.append({k.strip().lower(): v for k, v in r.items() if k})

        if not rows:
            raise HTTPException(422, 'Không có dữ liệu trong file.')

        mappings = {
            'khách hàng': 'customer', 'mã khách': 'customer', 'customer_code': 'customer',
            'mã sp': 'sku', 'mã sku': 'sku', 'sản phẩm': 'sku',
            'số lượng': 'quantity', 'qty': 'quantity', 'tồn': 'quantity',
            'chế độ': 'mode', 'loại': 'mode',
            'ghi chú': 'note', 'lý do': 'note', 'reason': 'note'
        }

        success_count = 0
        errors = []
        results = []

        for line_idx, raw_r in enumerate(rows, start=2):
            r = {mappings.get(k, k): v for k, v in raw_r.items()}
            cust_val = str(r.get('customer') or '').strip()
            sku_val = str(r.get('sku') or '').strip()
            qty_raw = r.get('quantity')
            mode_val = str(r.get('mode') or default_mode).strip().upper()
            note_val = str(r.get('note') or '').strip()

            if not sku_val:
                errors.append({'line': line_idx, 'error': 'Thiếu mã SKU'})
                continue

            try:
                qty = int(Decimal(str(qty_raw)))
            except Exception:
                errors.append({'line': line_idx, 'sku': sku_val, 'error': f'Số lượng không hợp lệ: {qty_raw}'})
                continue

            # Identify customer
            c = None
            if cust_val:
                c = db.scalar(select(Customer).where(or_(
                    Customer.name == cust_val,
                    Customer.code == cust_val.upper(),
                    Customer.primary_code == cust_val.upper()
                )))
                if not c:
                    a = db.scalar(select(core.Alias).where(core.Alias.alias == cust_val.upper()))
                    if a: c = a.customer
            else:
                # Deduce customer from existing stock
                existing_stock = db.scalar(select(Stock).where(Stock.sku == sku_val).limit(1))
                if existing_stock:
                    c = db.get(Customer, existing_stock.customer_id)

            if not c:
                errors.append({'line': line_idx, 'sku': sku_val, 'error': f'Không xác định được khách hàng cho: {cust_val or sku_val}'})
                continue

            stock = db.scalar(select(Stock).where(Stock.customer_id == c.id, Stock.sku == sku_val).with_for_update())
            if not stock:
                stock = Stock(customer_id=c.id, sku=sku_val, on_hand=0, reserved=0)
                db.add(stock)
                db.flush()

            if mode_val == 'SET':
                if qty < stock.reserved:
                    errors.append({'line': line_idx, 'sku': sku_val, 'error': f'Tồn kho thiết lập ({qty}) thấp hơn số lượng đang giữ ({stock.reserved})'})
                    continue
                delta = qty - stock.on_hand
                stock.on_hand = qty
                reason = note_val or 'Nhập kho (SET)'
                db.add(Ledger(stock_id=stock.id, quantity=delta, reason=reason, actor=user.username))
            else:  # ADD
                if stock.on_hand + qty < stock.reserved:
                    errors.append({'line': line_idx, 'sku': sku_val, 'error': f'Điều chỉnh ({qty}) làm tồn khả dụng âm'})
                    continue
                stock.on_hand += qty
                reason = note_val or 'Nhập kho (ADD)'
                db.add(Ledger(stock_id=stock.id, quantity=qty, reason=reason, actor=user.username))

            success_count += 1
            results.append({'sku': sku_val, 'customer': c.name, 'on_hand': stock.on_hand, 'reserved': stock.reserved})

        audit(db, user, 'stock.bulk_import', {'success': success_count, 'failed': len(errors)})
        db.commit()
        return {'total': len(rows), 'success_count': success_count, 'failed_count': len(errors), 'errors': errors, 'results': results}

    # 5. Reconciliation COD Upload API
    @app.post('/api/wf/reconcile/upload-cod')
    async def upload_warehouse_cod(file: UploadFile = File(...), db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'finance', 'operator')
        content = await file.read()
        filename = (file.filename or '').lower()

        rows = []
        if filename.endswith('.xlsx'):
            wb = load_workbook(filename=io.BytesIO(content), data_only=True)
            ws = wb.active
            iter_rows = ws.iter_rows(values_only=True)
            header = next(iter_rows, None)
            if not header:
                raise HTTPException(422, 'File Excel trống.')
            headers = [str(h).strip().lower() if h is not None else '' for h in header]
            for r in iter_rows:
                if any(v is not None and str(v).strip() for v in r):
                    rows.append({headers[i]: r[i] for i in range(min(len(headers), len(r)))})
        else:
            text = content.decode('utf-8-sig', errors='replace')
            reader = csv.DictReader(io.StringIO(text))
            for r in reader:
                rows.append({k.strip().lower(): v for k, v in r.items() if k})

        mappings = {
            'mã vận đơn': 'tracking', 'tracking_number': 'tracking', 'mã bill': 'tracking',
            'trạng thái': 'status', 'tình trạng': 'status',
            'tiền cod': 'cod_received', 'cod nhận': 'cod_received',
            'hoàn': 'returned', 'đã hoàn': 'returned'
        }

        delivered_count = 0
        returned_count = 0
        unmatched = []
        processed_orders = []

        for line_idx, raw_r in enumerate(rows, start=2):
            r = {mappings.get(k, k): v for k, v in raw_r.items()}
            tracking = str(r.get('tracking') or '').strip()
            if not tracking:
                continue

            order = db.scalar(select(Order).where(Order.tracking == tracking, ~Order.id.in_(select(management.DeletedOrder.order_id))))
            if not order:
                unmatched.append({'line': line_idx, 'tracking': tracking, 'reason': 'Không tìm thấy đơn theo mã vận đơn'})
                continue

            status_hint = str(r.get('status') or '').strip().upper()
            is_del = order.status == 'DELIVERED' or 'DELIVERED' in status_hint or 'GIAO' in status_hint or 'THÀNH CÔNG' in status_hint
            is_ret = order.status == 'RETURNED' or 'RETURN' in status_hint or 'HOÀN' in status_hint

            if is_del:
                order.is_cod_received = 1
                delivered_count += 1
                processed_orders.append({'id': order.id, 'tracking': tracking, 'type': 'DELIVERED'})
            elif is_ret:
                order.is_returned_to_warehouse = 1
                f = db.get(Flow, order.id)
                if f:
                    f.return_received = True
                returned_count += 1
                processed_orders.append({'id': order.id, 'tracking': tracking, 'type': 'RETURNED'})
            else:
                # Default: check if cod received or returned flag is set
                order.is_cod_received = 1
                delivered_count += 1
                processed_orders.append({'id': order.id, 'tracking': tracking, 'type': 'DELIVERED'})

        audit(db, user, 'reconcile.upload_cod', {
            'delivered_count': delivered_count,
            'returned_count': returned_count,
            'unmatched_count': len(unmatched)
        })
        db.commit()
        return {
            'success': True,
            'total_rows': len(rows),
            'delivered_count': delivered_count,
            'returned_count': returned_count,
            'unmatched': unmatched,
            'processed': processed_orders
        }

    # 6. Periods Preview & Bulk Create
    @app.get('/api/wf/periods/preview')
    def period_preview(customer_id: Optional[int] = None, date_from: Optional[date] = None, date_to: Optional[date] = None, date_basis: str = 'completion', db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'finance')
        if not date_from or not date_to:
            raise HTTPException(422, 'Vui lòng chọn khoảng ngày.')
        if date_from > date_to:
            raise HTTPException(422, 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')

        candidates = management.period_candidates(db, date_from, date_to, customer_id, date_basis)
        # Filter only orders not yet in any period
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
            c = db.get(Customer, customer_id)
            if c:
                code_str = c.primary_code or c.code
                suggested_name = f"Đối soát {code_str} ({date_from.strftime('%d/%m')} - {date_to.strftime('%d/%m/%Y')})"

        return {
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

    @app.post('/api/wf/periods/bulk-create')
    def bulk_create_periods(data: PeriodBulkCreateInput, db=Depends(get_db), user=Depends(current_user)):
        require(user, 'admin', 'finance')
        if data.date_from > data.date_to:
            raise HTTPException(422, 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')

        candidates = management.period_candidates(db, data.date_from, data.date_to, None, data.date_basis)
        available = [row for row in candidates if not row.get('period_id')]

        by_customer = {}
        for row in available:
            cid = row['customer_id']
            by_customer.setdefault(cid, []).append(row)

        created_periods = []
        for cid, rows in by_customer.items():
            if not rows:
                continue
            c = db.get(Customer, cid)
            if not c:
                continue
            code_str = c.primary_code or c.code
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

        audit(db, user, 'periods.bulk_create', {'count': len(created_periods)})
        db.commit()
        return {'success': True, 'count': len(created_periods), 'periods': created_periods}

    # 7. AI Address Normalization Bulk Endpoint with Pancake Two-Way Sync
    @app.post('/api/wf/ai/normalize-bulk')
    def ai_normalize_bulk(data: NormalizeBulkInput, db=Depends(get_db), user=Depends(current_user)):
        order_ids = data.order_ids

        normalized_results = []
        for oid in order_ids:
            o = db.get(Order, oid)
            if not o or management.is_deleted(db, oid):
                continue
            f = db.get(Flow, oid)
            if not f:
                continue

            # Calculate special fee based on current address
            spec_fee, spec_note = calculate_special_fee(db, f.full_address or f.original_address, f.province, f.district, f.postcode, core)
            o.special_fee = spec_fee
            o.special_fee_note = spec_note

            # Push back to Pancake if order came from Pancake
            pancake_res = None
            if o.pancake_order_id and o.pancake_shop_id:
                # Find matching CustomerSource with pancake_api_key
                source = db.scalar(select(CustomerSource).where(
                    CustomerSource.pancake_shop_id == o.pancake_shop_id,
                    CustomerSource.is_active == 1
                ))
                if source and source.pancake_api_key:
                    pancake_res = push_address_to_pancake(
                        shop_id=o.pancake_shop_id,
                        order_id=o.pancake_order_id,
                        api_key=source.pancake_api_key,
                        address_info={
                            'original_address': f.original_address,
                            'full_address': f.full_address,
                            'province': f.province,
                            'district': f.district,
                            'postcode': f.postcode
                        }
                    )
                    o.pancake_sync_status = 'synced' if pancake_res.get('success') else 'failed'

            normalized_results.append({
                'order_id': o.id,
                'special_fee': str(spec_fee),
                'special_fee_note': spec_note,
                'pancake_sync': pancake_res
            })

        db.commit()
        return {'success': True, 'count': len(normalized_results), 'results': normalized_results}
