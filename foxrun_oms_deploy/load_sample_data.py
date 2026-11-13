"""Automated sample data loader for FOXRUN OMS.

Seeds customer 'HL' if absent, initializes stock for sample SKUs,
and ingests sample files (foxrun_order_sample.xlsx, sample_orders.csv,
and sample_tracking.csv) via the standard ingestion engine.
Ensures multi-line order aggregation for FR-MY-000125 without errors or duplicate lines.
"""
import os
import sys
from decimal import Decimal
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Setup default environment if running standalone
settings_file = ROOT / 'data' / 'local_settings.json'
if settings_file.exists():
    try:
        import json
        config = json.loads(settings_file.read_text(encoding='utf-8'))
        if config.get('password'):
            os.environ.setdefault('ADMIN_PASSWORD', config['password'])
        if config.get('username'):
            os.environ.setdefault('ADMIN_USERNAME', config['username'])
        if config.get('secret'):
            os.environ.setdefault('SESSION_SECRET', config['secret'])
    except Exception:
        pass

os.environ.setdefault('ADMIN_PASSWORD', 'TestAdmin123456')
os.environ.setdefault('ADMIN_USERNAME', 'admin')
os.environ.setdefault('SESSION_SECRET', 'sample-loader-secret-key-32chars')
os.environ.setdefault('COOKIE_SECURE', 'false')

import main
import workflow as w


def load_sample_data(base_dir=None, verbose=True):
    """Seed customer HL and ingest sample files via standard ingestion engine."""
    if base_dir is None:
        base_dir = ROOT
    base_dir = Path(base_dir)

    def log(msg):
        if verbose:
            print(f"[load_sample_data] {msg}")

    # 1. Ensure admin user exists
    with main.SessionLocal() as db:
        admin = db.scalar(select(main.User).where(main.User.username == 'admin'))
        if not admin:
            admin_pwd = os.environ.get('ADMIN_PASSWORD', 'TestAdmin123456')
            admin = main.User(username='admin', password_hash=main.password_hash.hash(admin_pwd), role='admin')
            db.add(admin)
            db.commit()
            db.refresh(admin)
            log("Created admin user.")
        admin_id = admin.id

        # 2. Seed customer 'HL' if absent
        hl = db.scalar(select(main.Customer).where(main.Customer.code == 'HL'))
        if not hl:
            hl = main.Customer(name='Huy Long', code='HL')
            db.add(hl)
            db.commit()
            db.refresh(hl)
            log(f"Seeded customer 'HL' (id={hl.id}, name='{hl.name}').")
        else:
            log(f"Customer 'HL' already present (id={hl.id}, name='{hl.name}').")

        # 3. Ensure stock for SKUs in sample data
        for sku in ['MASK', 'SERUM']:
            s = db.scalar(select(w.Stock).where(w.Stock.customer_id == hl.id, w.Stock.sku == sku))
            if not s:
                db.add(w.Stock(customer_id=hl.id, sku=sku, on_hand=100, reserved=0))
                db.commit()
                log(f"Initialized stock for SKU '{sku}' (100 units).")

    # 4. Authenticate client
    client = TestClient(main.app)
    token = main.sign(admin_id, int(__import__('time').time()) + 8 * 3600)
    client.cookies.set('foxrun_session', token)
    H = {'X-Foxrun-Request': '1'}

    results = {}

    # 5. Load foxrun_order_sample.xlsx via standard ingestion engine
    xlsx_file = base_dir / 'foxrun_order_sample.xlsx'
    if xlsx_file.exists():
        with open(xlsx_file, 'rb') as f:
            content = f.read()
        r_prev = client.post(
            '/api/wf/import/preview',
            headers=H,
            files={'file': ('foxrun_order_sample.xlsx', content, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
        )
        if r_prev.status_code != 200:
            raise RuntimeError(f"Preview failed for foxrun_order_sample.xlsx: {r_prev.status_code} {r_prev.text}")
        prev_data = r_prev.json()
        r_conf = client.post(
            '/api/wf/import/confirm',
            headers=H,
            json={'preview_id': prev_data['preview_id']}
        )
        if r_conf.status_code != 200:
            raise RuntimeError(f"Confirm failed for foxrun_order_sample.xlsx: {r_conf.status_code} {r_conf.text}")
        results['foxrun_order_sample.xlsx'] = r_conf.json()
        log(f"foxrun_order_sample.xlsx imported: {r_conf.json()}")

    # 6. Load sample_orders.csv via standard ingestion engine
    csv_file = base_dir / 'sample_orders.csv'
    if csv_file.exists():
        with open(csv_file, 'rb') as f:
            content = f.read()
        r_prev = client.post(
            '/api/wf/import/preview',
            headers=H,
            files={'file': ('sample_orders.csv', content, 'text/csv')}
        )
        if r_prev.status_code != 200:
            raise RuntimeError(f"Preview failed for sample_orders.csv: {r_prev.status_code} {r_prev.text}")
        prev_data = r_prev.json()
        r_conf = client.post(
            '/api/wf/import/confirm',
            headers=H,
            json={'preview_id': prev_data['preview_id']}
        )
        if r_conf.status_code != 200:
            raise RuntimeError(f"Confirm failed for sample_orders.csv: {r_conf.status_code} {r_conf.text}")
        results['sample_orders.csv'] = r_conf.json()
        log(f"sample_orders.csv imported: {r_conf.json()}")

    # 7. Verify multi-line order FR-MY-000125 aggregation
    with main.SessionLocal() as db:
        orders = db.scalars(select(main.Order).where(main.Order.code == 'FR-MY-000125')).all()
        if len(orders) != 1:
            raise AssertionError(f"Expected exactly 1 order for FR-MY-000125, found {len(orders)}")
        order = orders[0]
        items = {item.sku: item.quantity for item in order.items}
        if items != {'MASK': 2, 'SERUM': 1}:
            raise AssertionError(f"Expected items {{'MASK': 2, 'SERUM': 1}}, got {items}")
        if Decimal(str(order.cod)) != Decimal('150'):
            raise AssertionError(f"Expected COD 150, got {order.cod}")
        log(f"Verified multi-line aggregation for FR-MY-000125: items={items}, COD={order.cod} {order.currency}")

    # 8. Load sample_tracking.csv via standard ingestion engine
    tracking_file = base_dir / 'sample_tracking.csv'
    if tracking_file.exists():
        # Advance order FR-MY-000125 through workflow stages to SHIPPED so tracking preview can match
        orders_resp = client.get('/api/wf/orders?q=FR-MY-000125', headers=H)
        if orders_resp.status_code == 200 and orders_resp.json():
            ord_info = orders_resp.json()[0]
            stage = ord_info['stage']
            ver = ord_info['version']
            oid = ord_info['id']

            if stage in {'REVIEW', 'WAIT_STOCK'}:
                r_app = client.post(f'/api/wf/orders/{oid}/action', headers=H, json={'version': ver, 'action': 'approve'})
                if r_app.status_code == 200:
                    ver = r_app.json()['version']
                    stage = r_app.json()['stage']

            if stage == 'PICKING':
                r_pick = client.post(f'/api/wf/orders/{oid}/action', headers=H, json={'version': ver, 'action': 'picked'})
                if r_pick.status_code == 200:
                    ver = r_pick.json()['version']
                    stage = r_pick.json()['stage']

            if stage == 'PACKING':
                r_pack = client.post(f'/api/wf/orders/{oid}/action', headers=H, json={'version': ver, 'action': 'packed'})
                if r_pack.status_code == 200:
                    ver = r_pack.json()['version']
                    stage = r_pack.json()['stage']

            if stage == 'READY':
                r_ship = client.post(f'/api/wf/orders/{oid}/action', headers=H, json={
                    'version': ver,
                    'action': 'ship',
                    'tracking': 'TEST-MY-000125',
                    'carrier': 'J&T'
                })
                if r_ship.status_code == 200:
                    log(f"Advanced order {oid} to SHIPPED with tracking TEST-MY-000125.")

        with open(tracking_file, 'rb') as f:
            content = f.read()
        r_prev = client.post(
            '/api/wf/tracking/preview',
            headers=H,
            files={'file': ('sample_tracking.csv', content, 'text/csv')}
        )
        if r_prev.status_code != 200:
            raise RuntimeError(f"Preview failed for sample_tracking.csv: {r_prev.status_code} {r_prev.text}")
        prev_data = r_prev.json()
        r_conf = client.post(
            '/api/wf/import/confirm',
            headers=H,
            json={'preview_id': prev_data['preview_id']}
        )
        if r_conf.status_code != 200:
            raise RuntimeError(f"Confirm failed for sample_tracking.csv: {r_conf.status_code} {r_conf.text}")
        results['sample_tracking.csv'] = r_conf.json()
        log(f"sample_tracking.csv imported: {r_conf.json()}")

        # Verify tracking status updated to DELIVERED
        with main.SessionLocal() as db:
            order = db.scalar(select(main.Order).where(main.Order.code == 'FR-MY-000125'))
            if order.status != 'DELIVERED':
                raise AssertionError(f"Expected status DELIVERED for FR-MY-000125, got {order.status}")
            log(f"Verified tracking status for FR-MY-000125: {order.status}")

    log("Sample data loading and multi-line verification completed successfully.")
    return results


if __name__ == '__main__':
    load_sample_data()
