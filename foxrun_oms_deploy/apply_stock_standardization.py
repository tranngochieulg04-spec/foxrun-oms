"""Standardize Stock Lifecycle in FOXRUN OMS according to order lifecycle."""
import re
from pathlib import Path

wf_path = Path("workflow.py")
content = wf_path.read_text(encoding="utf-8")

# 1. Add stock_restored column to Flow class if not present
if "stock_restored=Column" not in content and "stock_restored = Column" not in content:
    target = "return_received=Column(Integer,default=0,nullable=False)"
    replacement = "return_received=Column(Integer,default=0,nullable=False)\n        stock_restored=Column(Integer,default=0,nullable=False)"
    assert target in content, "return_received column not found in Flow definition"
    content = content.replace(target, replacement, 1)

# 2. Add safe ALTER TABLE migration right after Base.metadata.create_all(engine)
if "ALTER TABLE workflow_orders ADD COLUMN stock_restored" not in content:
    target = "Base.metadata.create_all(engine)"
    migration = """Base.metadata.create_all(engine)
    try:
        from sqlalchemy import text
        with engine.connect() as _migration_conn:
            _migration_conn.execute(text("ALTER TABLE workflow_orders ADD COLUMN stock_restored INTEGER DEFAULT 0"))
            _migration_conn.commit()
    except Exception:
        pass"""
    assert target in content, "Base.metadata.create_all(engine) not found"
    content = content.replace(target, migration, 1)

# 3. Add RETURNED_COMPLETED to TRACKING, TRANSITIONS, and TRACK_ALIASES
if "'RETURNED_COMPLETED'" not in content:
    # TRACKING
    target_tr = "'RETURNED':'Đã hoàn'"
    repl_tr = "'RETURNED':'Đã hoàn','RETURNED_COMPLETED':'Hoàn thành công'"
    content = content.replace(target_tr, repl_tr, 1)

    # TRANSITIONS
    target_trans = "'RETURNING':{'RETURNING','RETURNED','EXCEPTION'},'RETURNED':{'RETURNED'}}"
    repl_trans = "'RETURNING':{'RETURNING','RETURNED','RETURNED_COMPLETED','EXCEPTION'},'RETURNED':{'RETURNED','RETURNED_COMPLETED'},'RETURNED_COMPLETED':{'RETURNED_COMPLETED'}}"
    content = content.replace(target_trans, repl_trans, 1)

    # TRACK_ALIASES
    target_al = "'HOAN HANG':'RETURNED'}"
    repl_al = "'HOAN HANG':'RETURNED','HOÀN HÀNG':'RETURNED','RETURNED_COMPLETED':'RETURNED_COMPLETED','HOÀN THÀNH CÔNG':'RETURNED_COMPLETED','HOAN THANH CONG':'RETURNED_COMPLETED','HOÀN VỀ KHO':'RETURNED_COMPLETED','HOAN VE KHO':'RETURNED_COMPLETED'}"
    content = content.replace(target_al, repl_al, 1)

# 4. Add centralized stock functions: deduct_order_stock_for_shipping, release_order_stock_reservation, restore_order_stock_on_return
stock_functions = '''
def deduct_order_stock_for_shipping(db, order, flow, actor='system'):
    """
    3. Bàn giao / Xuất kho vận chuyển:
    Giảm Đã giữ (reserved) đi quantity và giảm Tồn vật lý (on_hand) đi quantity.
    Ghi sổ cái Ledger: 'Bàn giao vận chuyển - Mã đơn [code]'.
    """
    reservations = db.scalars(
        select(Reservation).where(Reservation.order_id == order.id, Reservation.state == 'RESERVED').order_by(Reservation.stock_id)
    ).all()
    if reservations:
        for r in reservations:
            result = db.execute(
                update(Stock)
                .where(Stock.id == r.stock_id, Stock.on_hand >= r.quantity, Stock.reserved >= r.quantity)
                .values(on_hand=Stock.on_hand - r.quantity, reserved=Stock.reserved - r.quantity)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                s = db.get(Stock, r.stock_id)
                if s:
                    s.on_hand = max(0, s.on_hand - r.quantity)
                    s.reserved = max(0, s.reserved - r.quantity)
            r.state = 'SHIPPED'
            db.add(Ledger(
                stock_id=r.stock_id,
                order_id=order.id,
                quantity=-r.quantity,
                reason=f'Bàn giao vận chuyển - Mã đơn {order.code}',
                actor=actor
            ))
    else:
        for sku, qty in sorted(quantities(order).items()):
            if qty <= 0:
                continue
            s = db.scalar(select(Stock).where(Stock.customer_id == order.customer_id, Stock.sku == sku).with_for_update())
            if not s:
                s = Stock(customer_id=order.customer_id, sku=sku, on_hand=0, reserved=0)
                db.add(s)
                db.flush()
            s.on_hand = max(0, s.on_hand - qty)
            db.add(Ledger(
                stock_id=s.id,
                order_id=order.id,
                quantity=-qty,
                reason=f'Bàn giao vận chuyển - Mã đơn {order.code}',
                actor=actor
            ))


def release_order_stock_reservation(db, order, flow, actor='system', note=''):
    """
    4. Hủy đơn trước khi bàn giao (đang ở trạng thái đã giữ tồn):
    Giảm Đã giữ (reserved) đi quantity để trả lại Khả dụng (on_hand giữ nguyên).
    """
    reservations = db.scalars(
        select(Reservation).where(Reservation.order_id == order.id, Reservation.state == 'RESERVED').order_by(Reservation.stock_id)
    ).all()
    for r in reservations:
        db.execute(
            update(Stock).where(Stock.id == r.stock_id)
            .values(reserved=Stock.reserved - r.quantity)
            .execution_options(synchronize_session=False)
        )
        r.state = 'RELEASED'


def restore_order_stock_on_return(db, order, flow, actor='system', reason=None):
    """
    5. Hoàn hàng thành công (khi đơn chuyển sang Hoàn thành công / RETURNED_COMPLETED):
    Tự động cộng lại quantity vào Tồn vật lý (giúp Khả dụng tăng lại tương ứng),
    ghi rõ bản ghi vào Lịch sử nhập / xuất với lý do 'Hoàn hàng thành công - Mã đơn [code]',
    và gắn cờ chống cộng trùng (stock_restored = 1) để dù cập nhật trạng thái nhiều lần cũng chỉ cộng lại tồn kho đúng 1 lần duy nhất.
    """
    if getattr(flow, 'stock_restored', 0) == 1:
        return False

    existing = db.scalar(
        select(Ledger.id).where(
            Ledger.order_id == order.id,
            Ledger.quantity > 0,
            or_(
                Ledger.reason.like('%Hoàn hàng%'),
                Ledger.reason.like('%hàng hoàn%'),
                Ledger.reason.like('%RETURNED%')
            )
        ).limit(1)
    )
    if existing:
        flow.stock_restored = 1
        flow.return_received = 1
        return False

    item_map = quantities(order)
    for sku, qty in sorted(item_map.items()):
        if qty <= 0:
            continue
        s = db.scalar(select(Stock).where(Stock.customer_id == order.customer_id, Stock.sku == sku).with_for_update())
        if not s:
            s = Stock(customer_id=order.customer_id, sku=sku, on_hand=0, reserved=0)
            db.add(s)
            db.flush()
        db.execute(
            update(Stock)
            .where(Stock.id == s.id)
            .values(on_hand=Stock.on_hand + qty)
            .execution_options(synchronize_session=False)
        )
        ledger_reason = reason or f'Hoàn hàng thành công - Mã đơn {order.code}'
        db.add(Ledger(
            stock_id=s.id,
            order_id=order.id,
            quantity=qty,
            reason=ledger_reason,
            actor=actor
        ))

    flow.stock_restored = 1
    flow.return_received = 1
    touch(flow)
    return True
'''

if "def deduct_order_stock_for_shipping" not in content:
    # Insert right before def set_tracking
    target_pos = "def set_tracking(db,o,f,user,status,note,source='MANUAL'):"
    assert target_pos in content, "def set_tracking not found"
    content = content.replace(target_pos, stock_functions + "\n" + target_pos, 1)

# 5. In set_tracking: Call restore_order_stock_on_return if status in {'RETURNED_COMPLETED'}
target_set_track = "if o.status!=status:\n        record_tracking(db,o,user,status,note,source);touch(f)\n        audit(db,user,'tracking.update',{'order_id':o.id,'status':status,'note':note})"
repl_set_track = """if o.status!=status:
        record_tracking(db,o,user,status,note,source);touch(f)
        audit(db,user,'tracking.update',{'order_id':o.id,'status':status,'note':note})
        if status in {'RETURNED_COMPLETED'}:
            act_name = user.username if hasattr(user, 'username') else str(user)
            restore_order_stock_on_return(db, o, f, actor=act_name)"""

if target_set_track in content and "restore_order_stock_on_return(db, o, f, actor=act_name)" not in content:
    content = content.replace(target_set_track, repl_set_track, 1)

# 6. In receive_return: ensure f.stock_restored = 1
target_ret = "f.return_received=1;touch(f)"
repl_ret = "f.return_received=1;f.stock_restored=1;touch(f)"
if target_ret in content:
    content = content.replace(target_ret, repl_ret, 1)

# 7. In action 'ship': use deduct_order_stock_for_shipping
old_ship_block = """            claim_tracking(o, tracking, db)
            for r in db.scalars(select(Reservation).where(Reservation.order_id==oid,Reservation.state=='RESERVED').order_by(Reservation.stock_id)).all():
                result=db.execute(update(Stock).where(Stock.id==r.stock_id,Stock.on_hand>=r.quantity,Stock.reserved>=r.quantity).values(on_hand=Stock.on_hand-r.quantity,reserved=Stock.reserved-r.quantity).execution_options(synchronize_session=False))
                if result.rowcount!=1:raise HTTPException(409,'Tồn không khớp, cần kiểm tra trước khi bàn giao.')
                r.state='SHIPPED';db.add(Ledger(stock_id=r.stock_id,order_id=oid,quantity=-r.quantity,reason='Bàn giao vận chuyển',actor=user.username))
            o.tracking=tracking;f.carrier=carrier;f.stage='SHIPPED'"""

new_ship_block = """            claim_tracking(o, tracking, db)
            deduct_order_stock_for_shipping(db, o, f, user.username)
            o.tracking=tracking;f.carrier=carrier;f.stage='SHIPPED'"""

if old_ship_block in content:
    content = content.replace(old_ship_block, new_ship_block, 1)

# 8. In action 'cancel': use release_order_stock_reservation
old_cancel_block = """            for r in db.scalars(select(Reservation).where(Reservation.order_id==oid,Reservation.state=='RESERVED').order_by(Reservation.stock_id)).all():
                db.execute(update(Stock).where(Stock.id==r.stock_id).values(reserved=Stock.reserved-r.quantity).execution_options(synchronize_session=False));r.state='RELEASED'
            f.stage='CANCELLED';f.issue=data.note"""

new_cancel_block = """            release_order_stock_reservation(db, o, f, user.username, data.note)
            f.stage='CANCELLED';f.issue=data.note"""

if old_cancel_block in content:
    content = content.replace(old_cancel_block, new_cancel_block, 1)

# 9. In serialize: support RETURNED_COMPLETED in fees and returns filter
target_ser_ret = "elif status == 'RETURNED':"
repl_ser_ret = "elif status in {'RETURNED', 'RETURNED_COMPLETED'}:"
if target_ser_ret in content:
    content = content.replace(target_ser_ret, repl_ser_ret, 1)

target_filt_ret = "'returns':r['transport'] in {'RETURNING','RETURNED'},"
repl_filt_ret = "'returns':r['transport'] in {'RETURNING','RETURNED','RETURNED_COMPLETED'},"
if target_filt_ret in content:
    content = content.replace(target_filt_ret, repl_filt_ret, 1)

target_filt_fin = "'finance':r['transport'] in {'DELIVERED','RETURNED'} and r['finance']!='SETTLED'"
repl_filt_fin = "'finance':r['transport'] in {'DELIVERED','RETURNED','RETURNED_COMPLETED'} and r['finance']!='SETTLED'"
if target_filt_fin in content:
    content = content.replace(target_filt_fin, repl_filt_fin, 1)

target_filt_ship = "'shipping':r['stage']=='SHIPPED' and r['transport'] not in {'DELIVERED','RETURNED'},"
repl_filt_ship = "'shipping':r['stage']=='SHIPPED' and r['transport'] not in {'DELIVERED','RETURNED','RETURNED_COMPLETED'},"
if target_filt_ship in content:
    content = content.replace(target_filt_ship, repl_filt_ship, 1)

wf_path.write_text(content, encoding="utf-8")
print("workflow.py updated successfully!")
