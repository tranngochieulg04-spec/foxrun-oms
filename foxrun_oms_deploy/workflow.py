"""FOXRUN operational workflow. Additive tables preserve the Phase 2 database."""
import csv
import io
import json
import secrets
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
import sqlite3

from fastapi import Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, Integer, String, Text, Numeric, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, select, func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


def install(app, core):
    # Receive the running module explicitly (also works with uvicorn / TestClient).
    globals().update({n:getattr(core,n) for n in ['Base','User','Customer','Alias','Order','Item','Audit','TrackingEvent','engine','SessionLocal','get_db','current_user','audit','read_rows','password_hash']})
    Base=core.Base

    class Flow(Base):
        __tablename__='workflow_orders'
        order_id=Column(Integer,ForeignKey('orders.id'),primary_key=True)
        stage=Column(String(30),default='REVIEW',nullable=False)
        version=Column(Integer,default=1,nullable=False)
        recipient=Column(String(200),default='')
        phone=Column(String(80),default='')
        country=Column(String(2),default='MY')
        original_address=Column(Text,default='')
        full_address=Column(Text,default='')
        province=Column(String(150),default='')
        district=Column(String(150),default='')
        postcode=Column(String(20),default='')
        carrier=Column(String(100),default='')
        note=Column(Text,default='')
        assigned=Column(String(100),default='')
        issue=Column(Text,default='')
        return_received=Column(Integer,default=0,nullable=False)
        updated_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
        carrier_fee=Column(Numeric(18,2),default=0,nullable=False)
        service_fee=Column(Numeric(18,2),default=0,nullable=False)
        carrier_received=Column(Numeric(18,2),default=0,nullable=False)
        seller_paid=Column(Numeric(18,2),default=0,nullable=False)
        reconciled=Column(Integer,default=0,nullable=False)
        settlement_reference=Column(String(200),default='')

    class Stock(Base):
        __tablename__='workflow_stock'
        id=Column(Integer,primary_key=True)
        customer_id=Column(Integer,ForeignKey('customers.id'),nullable=False)
        sku=Column(String(120),nullable=False)
        on_hand=Column(Integer,default=0,nullable=False)
        reserved=Column(Integer,default=0,nullable=False)
        __table_args__=(UniqueConstraint('customer_id','sku'),CheckConstraint('on_hand >= 0 AND reserved >= 0 AND on_hand >= reserved',name='stock_nonnegative'),)

    class TrackingClaim(Base):
        __tablename__='workflow_tracking_claims'
        tracking=Column(String(200),primary_key=True)
        order_id=Column(Integer,ForeignKey('orders.id'),unique=True,nullable=False)

    class Reservation(Base):
        __tablename__='workflow_reservations'
        id=Column(Integer,primary_key=True)
        order_id=Column(Integer,ForeignKey('orders.id'),nullable=False)
        stock_id=Column(Integer,ForeignKey('workflow_stock.id'),nullable=False)
        quantity=Column(Integer,nullable=False)
        state=Column(String(20),default='RESERVED',nullable=False)
        __table_args__=(UniqueConstraint('order_id','stock_id'),)

    class Ledger(Base):
        __tablename__='workflow_stock_ledger'
        id=Column(Integer,primary_key=True)
        stock_id=Column(Integer,ForeignKey('workflow_stock.id'),nullable=False)
        order_id=Column(Integer,ForeignKey('orders.id'))
        quantity=Column(Integer,nullable=False)
        reason=Column(Text,nullable=False)
        actor=Column(String(100),nullable=False)
        created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

    class Preview(Base):
        __tablename__='workflow_previews'
        id=Column(String(64),primary_key=True)
        user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
        kind=Column(String(30),nullable=False)
        payload=Column(Text,nullable=False)
        created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
        confirmed=Column(Integer,default=0,nullable=False)
        result=Column(Text)

    Base.metadata.create_all(engine)
    globals().update(Flow=Flow,Stock=Stock,Reservation=Reservation,Ledger=Ledger,Preview=Preview,TrackingClaim=TrackingClaim)

    @app.middleware('http')
    async def protect(request:Request,call_next):
        from fastapi.responses import JSONResponse
        if request.method in {'POST','PUT','PATCH','DELETE'} and request.url.path.startswith('/api/'):
            origin=request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'Yêu cầu khác nguồn bị từ chối.'},status_code=403)
            if request.headers.get('x-foxrun-request')!='1':
                return JSONResponse({'detail':'Thiếu mã yêu cầu. Tải lại giao diện FOXRUN.'},status_code=403)
            # Supersede old write APIs so workflow / stock rules cannot be bypassed.
            path=request.url.path
            allowed=(path.startswith('/api/wf/') or path.startswith('/api/v1/auth/') or path.startswith('/api/v1/customers') or path.startswith('/api/v1/customer-aliases') or path=='/api/v1/search/bulk')
            if not allowed:
                return JSONResponse({'detail':'Dùng màn hình workflow mới để giữ nhất quán dữ liệu.'},status_code=410)
        try:
            response=await call_next(request)
        except Exception as exc:
            response=JSONResponse({'detail': f'Lỗi hệ thống: {str(exc)}' if str(exc) else 'Đã xảy ra lỗi máy chủ nội bộ.'}, status_code=500)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(IntegrityError)
    async def integrity_error(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Dữ liệu bị trùng hoặc vừa được thay đổi. Tải lại và thử lại.'},status_code=409)

    @app.exception_handler(sqlite3.Error)
    async def sqlite_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Lỗi cơ sở dữ liệu. Vui lòng thử lại sau.'},status_code=500)

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Lỗi truy vấn cơ sở dữ liệu. Vui lòng thử lại.'},status_code=500)

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': f'Lỗi hệ thống: {str(exc)}' if str(exc) else 'Đã xảy ra lỗi máy chủ nội bộ.'}, status_code=500)

    @app.get('/api/wf/meta')
    def meta(user=Depends(current_user)):
        return {'stages':STAGES,'tracking':TRACKING,'roles':ROLES,'version':'3.3','capabilities':{'pancake_live':False,'ai_live':bool(__import__('os').environ.get('GEMINI_API_KEY') and __import__('os').environ.get('GEMINI_MODEL')),'multi_shipment':False}}

    @app.get('/api/wf/orders')
    def list_orders(q:str='',view:str='all',customer_id:int=0,db=Depends(get_db),user=Depends(current_user)):
        stmt=select(Order).order_by(Order.id.desc())
        if customer_id:stmt=stmt.where(Order.customer_id==customer_id)
        objects=db.scalars(stmt).all()
        terms=[x.strip().lower() for x in q.replace(';','\n').replace(',','\n').splitlines() if x.strip()]
        result=[]
        for o in objects:
            if management.is_deleted(db, o.id):continue
            f=db.get(Flow,o.id)
            row=serialize(db,o,f)
            if terms and not any(match_term(t,row) for t in terms):continue
            if view!='all' and not in_view(row,view):continue
            result.append(row)
        return result

    @app.get('/api/wf/orders/{oid}')
    def detail(oid:int,db=Depends(get_db),user=Depends(current_user)):
        o=find_order(db,oid)
        row=serialize(db,o,db.get(Flow,oid))
        row['history']=[{'at':a.created_at,'actor':a.actor,'action':a.action,'detail':json.loads(a.detail)} for a in db.scalars(select(Audit).where(Audit.detail.like('%"order_id": '+str(oid)+'%')).order_by(Audit.id.desc())).all() if json.loads(a.detail).get('order_id')==oid]
        row['tracking_history']=[{'at':e.created_at,'status':e.new_status,'note':e.note,'source':e.source_type} for e in db.scalars(select(TrackingEvent).where(TrackingEvent.order_id==oid).order_by(TrackingEvent.id.desc())).all()]
        return row

    @app.post('/api/wf/orders')
    def create(data:OrderInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','operator')
        o,f=make_order(db,data.model_dump(),user)
        if o.tracking:
            claim_tracking(o, o.tracking, db)
        db.commit()
        return serialize(db,o,f)

    @app.patch('/api/wf/orders/{oid}')
    def edit(oid:int,data:EditInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','operator')
        o,f=lock_order(db,oid,data.version)
        if f.stage not in {'REVIEW','ERROR','WAIT_STOCK'}:raise HTTPException(409,'Chỉ sửa nội dung trước khi giữ tồn. Hủy và tạo đơn mới nếu cần thay đổi sau đó.')
        before=serialize(db,o,f)
        for k in ADDRESS_FIELDS:
            if k!='original_address':setattr(f,k,getattr(data,k).strip())
        f.assigned=data.assigned.strip();f.note=data.note.strip();complete_address(f)
        o.cod=data.cod;o.currency=data.currency
        o.items=[Item(sku=i.sku,quantity=i.quantity) for i in data.items]
        f.stage='REVIEW';f.issue=''
        touch(f);audit(db,user,'order.edit',{'order_id':oid,'version':f.version,'before':{k:before[k] for k in ADDRESS_FIELDS+['cod','currency','items','note','assigned']},'after':data.model_dump(mode='json')})
        db.commit();return serialize(db,o,f)

    def claim_tracking(order, tracking, db):
        if db.scalar(select(Order.id).where(Order.tracking == tracking, Order.id != order.id, ~Order.id.in_(select(management.DeletedOrder.order_id))).limit(1)):
            raise HTTPException(409, 'Mã vận đơn đã gắn với đơn khác.')
        claim = db.scalar(select(TrackingClaim).where(TrackingClaim.order_id == order.id))
        if claim and claim.tracking == tracking:
            return
        if claim:
            db.delete(claim)
            db.flush()
        db.add(TrackingClaim(tracking=tracking, order_id=order.id))
        db.flush()

    @app.post('/api/wf/orders/{oid}/action')
    def action(oid:int,data:ActionInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin',*(['operator'] if data.action in {'approve','cancel'} else ['warehouse']))
        o,f=lock_order(db,oid,data.version)
        act=data.action
        if act=='approve':
            if f.stage not in {'REVIEW','ERROR','WAIT_STOCK'}:raise HTTPException(409,'Đơn không ở bước chờ duyệt.')
            errs=validate_order(o,f)
            if errs:f.stage='ERROR';f.issue='; '.join(errs)
            else:
                stock_rows=[];missing=[]
                for sku,qty in sorted(quantities(o).items()):
                    s=db.scalar(select(Stock).where(Stock.customer_id==o.customer_id,Stock.sku==sku).with_for_update())
                    if not s or s.on_hand-s.reserved<qty:missing.append(sku)
                    else:stock_rows.append((s,qty))
                if missing:f.stage='WAIT_STOCK';f.issue='Thiếu tồn: '+', '.join(missing)
                else:
                    for s,qty in stock_rows:
                        # Conditional writes also protect SQLite's single-writer path.
                        r=db.execute(update(Stock).where(Stock.id==s.id,Stock.on_hand-Stock.reserved>=qty).values(reserved=Stock.reserved+qty).execution_options(synchronize_session=False))
                        if r.rowcount!=1:raise HTTPException(409,'Tồn vừa thay đổi. Duyệt lại đơn.')
                        db.add(Reservation(order_id=oid,stock_id=s.id,quantity=qty))
                    f.stage='PICKING';f.issue=''
        elif act=='picked':
            expect(f,'PICKING');f.stage='PACKING'
        elif act=='packed':
            expect(f,'PACKING');f.stage='READY'
        elif act=='ship':
            expect(f,'READY')
            tracking=data.tracking.strip();carrier=data.carrier.strip()
            if not tracking or not carrier:raise HTTPException(422,'Cần mã vận đơn và hãng vận chuyển.')
            claim_tracking(o, tracking, db)
            for r in db.scalars(select(Reservation).where(Reservation.order_id==oid,Reservation.state=='RESERVED').order_by(Reservation.stock_id)).all():
                result=db.execute(update(Stock).where(Stock.id==r.stock_id,Stock.on_hand>=r.quantity,Stock.reserved>=r.quantity).values(on_hand=Stock.on_hand-r.quantity,reserved=Stock.reserved-r.quantity).execution_options(synchronize_session=False))
                if result.rowcount!=1:raise HTTPException(409,'Tồn không khớp, cần kiểm tra trước khi bàn giao.')
                r.state='SHIPPED';db.add(Ledger(stock_id=r.stock_id,order_id=oid,quantity=-r.quantity,reason='Bàn giao vận chuyển',actor=user.username))
            o.tracking=tracking;f.carrier=carrier;f.stage='SHIPPED'
            record_tracking(db,o,user,'IN_TRANSIT',data.note or 'Bàn giao vận chuyển','WAREHOUSE')
        elif act=='cancel':
            if f.stage in {'SHIPPED','CANCELLED'}:raise HTTPException(409,'Không thể hủy đơn đã bàn giao hoặc đã hủy.')
            for r in db.scalars(select(Reservation).where(Reservation.order_id==oid,Reservation.state=='RESERVED').order_by(Reservation.stock_id)).all():
                db.execute(update(Stock).where(Stock.id==r.stock_id).values(reserved=Stock.reserved-r.quantity).execution_options(synchronize_session=False));r.state='RELEASED'
            f.stage='CANCELLED';f.issue=data.note
        else:raise HTTPException(422,'Thao tác không hợp lệ.')
        touch(f);audit(db,user,'workflow.'+act,{'order_id':oid,'stage':f.stage,'note':data.note,'version':f.version})
        db.commit();return serialize(db,o,f)

    @app.post('/api/wf/orders/{oid}/tracking')
    def tracking(oid:int,data:TrackingInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','operator','warehouse')
        o,f=lock_order(db,oid,data.version)
        set_tracking(db,o,f,user,data.status,data.note)
        db.commit();return serialize(db,o,f)

    @app.post('/api/wf/orders/{oid}/return')
    def receive_return(oid:int,data:ReturnInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','warehouse')
        o,f=lock_order(db,oid,data.version)
        if f.stage!='SHIPPED' or o.status!='RETURNED':raise HTTPException(409,'Chỉ nhận hoàn khi vận chuyển đã báo hoàn về.')
        if f.return_received:raise HTTPException(409,'Đã kiểm nhận hoàn. Không nhập tồn lần hai.')
        supplied={}
        for line in data.lines:
            if line.sku in supplied:raise HTTPException(422,'SKU bị lặp trong phiếu hoàn.')
            supplied[line.sku]=line
        if set(supplied)!=set(quantities(o)):raise HTTPException(422,'Cần kiểm đủ từng SKU trong đơn.')
        for sku,qty in sorted(quantities(o).items()):
            line=supplied[sku]
            if line.good+line.damaged+line.missing!=qty:raise HTTPException(422,'Tổng hàng tốt, hỏng và thiếu phải bằng số lượng xuất của '+sku)
            if line.good:
                s=db.scalar(select(Stock).where(Stock.customer_id==o.customer_id,Stock.sku==sku).with_for_update())
                if not s:s=Stock(customer_id=o.customer_id,sku=sku,on_hand=0,reserved=0);db.add(s);db.flush()
                db.execute(update(Stock).where(Stock.id==s.id).values(on_hand=Stock.on_hand+line.good).execution_options(synchronize_session=False))
                db.add(Ledger(stock_id=s.id,order_id=oid,quantity=line.good,reason='Nhận hàng hoàn đạt kiểm tra',actor=user.username))
        f.return_received=1;touch(f)
        audit(db,user,'return.receive',{'order_id':oid,'lines':[x.model_dump() for x in data.lines],'note':data.note})
        db.commit();return serialize(db,o,f)

    @app.post('/api/wf/orders/{oid}/finance')
    def finance(oid:int,data:FinanceInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','finance')
        o,f=lock_order(db,oid,data.version)
        management.check_finance_open(db, oid)
        management.expenses.check_manual_fees(db, o, f, data)
        if o.status not in {'DELIVERED','RETURNED'}:raise HTTPException(409,'Cần kết quả giao thành công hoặc hoàn hàng để đối soát.')
        if o.status=='RETURNED' and not f.return_received:raise HTTPException(409,'Kho cần kiểm nhận hàng hoàn trước khi chốt đối soát.')
        if not data.reference.strip():raise HTTPException(422,'Cần tham chiếu bảng kê / chứng từ tiền.')
        cod=Decimal(o.cod) if o.status=='DELIVERED' else Decimal('0')
        expected=cod-data.carrier_fee
        due=expected-data.service_fee
        if data.confirm and expected!=data.carrier_received:raise HTTPException(422,'Tiền hãng chuyển chưa khớp COD trừ phí vận chuyển.')
        if data.seller_paid and not data.confirm:raise HTTPException(422,'Cần đối soát khớp trước khi ghi tiền đã thanh toán seller.')
        if data.seller_paid and ((due>=0 and not Decimal('0')<=data.seller_paid<=due) or (due<0 and not due<=data.seller_paid<=Decimal('0'))):raise HTTPException(422,'Số tiền thanh toán vượt khoản phải trả / thu seller.')
        before={k:str(getattr(f,k)) for k in ['carrier_fee','service_fee','carrier_received','seller_paid']}
        for k in before:setattr(f,k,getattr(data,k))
        f.reconciled=int(data.confirm);f.settlement_reference=data.reference.strip();touch(f)
        audit(db,user,'finance.save',{'order_id':oid,'before':before,'after':data.model_dump(mode='json')})
        db.commit();return serialize(db,o,f)

    @app.get('/api/wf/stock')
    def stock(db=Depends(get_db),user=Depends(current_user)):
        result=[]
        for s in db.scalars(select(Stock).order_by(Stock.customer_id,Stock.sku)).all():
            c=db.get(Customer,s.customer_id)
            result.append({'id':s.id,'customer_id':s.customer_id,'customer':c.name,'sku':s.sku,'on_hand':s.on_hand,'reserved':s.reserved,'available':s.on_hand-s.reserved})
        return result

    @app.post('/api/wf/stock')
    def adjust_stock(data:StockInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','warehouse')
        if not db.get(Customer,data.customer_id):raise HTTPException(404,'Không tìm thấy khách.')
        s=db.scalar(select(Stock).where(Stock.customer_id==data.customer_id,Stock.sku==data.sku).with_for_update())
        if not s:s=Stock(customer_id=data.customer_id,sku=data.sku,on_hand=0,reserved=0);db.add(s);db.flush()
        r=db.execute(update(Stock).where(Stock.id==s.id,Stock.on_hand+data.quantity>=Stock.reserved).values(on_hand=Stock.on_hand+data.quantity).execution_options(synchronize_session=False))
        if r.rowcount!=1:raise HTTPException(409,'Điều chỉnh làm tồn âm hoặc thấp hơn số đã giữ.')
        db.add(Ledger(stock_id=s.id,quantity=data.quantity,reason=data.reason,actor=user.username))
        audit(db,user,'stock.adjust',data.model_dump());db.commit()
        return {'ok':True}

    @app.get('/api/wf/stock/history')
    def stock_history(db=Depends(get_db),user=Depends(current_user)):
        return [{'id':x.id,'voided':bool(db.get(management.VoidedReceipt,x.id)),'at':x.created_at,'customer':db.get(Customer,db.get(Stock,x.stock_id).customer_id).name,'sku':db.get(Stock,x.stock_id).sku,'quantity':x.quantity,'order_id':x.order_id,'reason':x.reason,'actor':x.actor} for x in db.scalars(select(Ledger).order_by(Ledger.id.desc()).limit(500)).all()]

    @app.get('/api/wf/audit')
    def audits(db=Depends(get_db),user=Depends(current_user)):
        return [{'at':x.created_at,'actor':x.actor,'action':x.action,'detail':json.loads(x.detail)} for x in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(500)).all()]

    @app.patch('/api/wf/customers/{cid}')
    def edit_customer(cid:int,data:CustomerEdit,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','operator')
        c=db.get(Customer,cid)
        if not c:raise HTTPException(404,'Không tìm thấy khách.')
        c.name=data.name.strip();audit(db,user,'customer.edit',{'customer_id':cid,'name':c.name});db.commit();return {'ok':True}

    @app.get('/api/wf/users')
    def users(db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin')
        return [{'id':u.id,'username':u.username,'role':u.role} for u in db.scalars(select(User)).all()]

    @app.post('/api/wf/users')
    def add_user(data:UserInput,db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin')
        if db.scalar(select(User).where(User.username==data.username)):raise HTTPException(409,'Tài khoản đã tồn tại.')
        u=User(username=data.username,password_hash=password_hash.hash(data.password),role=data.role)
        db.add(u);audit(db,user,'user.create',{'username':data.username,'role':data.role});db.commit();return {'ok':True}

    @app.post('/api/wf/password')
    def password(data:PasswordInput,db=Depends(get_db),user=Depends(current_user)):
        if not password_hash.verify(data.old,user.password_hash):raise HTTPException(422,'Mật khẩu hiện tại không đúng.')
        user.password_hash=password_hash.hash(data.new);audit(db,user,'user.password',{'username':user.username});db.commit();return {'ok':True}

    @app.post('/api/wf/import/preview')
    async def preview_import(source:str='excel',cod_format:str='plain',file:UploadFile=File(...),db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','operator')
        if source not in {'excel','pancake'}:raise HTTPException(422,'Nguồn không hợp lệ.')
        try:raw=read_rows(file.filename or '',await file.read(10*1024*1024+1))
        except HTTPException:raise
        except Exception:raise HTTPException(422,'Không đọc được file. Dùng CSV UTF-8 hoặc XLSX hợp lệ.')
        if cod_format not in {'plain','vn'}:raise HTTPException(422,'Định dạng COD không hợp lệ.')
        rows=prepare_import(db,raw,source,cod_format)
        return save_preview(db,user,'orders',rows,file.filename or '')

    @app.post('/api/wf/tracking/preview')
    async def preview_tracking(file:UploadFile=File(...),db=Depends(get_db),user=Depends(current_user)):
        require(user,'admin','operator','warehouse')
        try:raw=read_rows(file.filename or '',await file.read(10*1024*1024+1))
        except HTTPException:raise
        except Exception:raise HTTPException(422,'Không đọc được file tracking. Dùng CSV UTF-8 hoặc XLSX hợp lệ.')
        rows=[];seen=set()
        for i,r in enumerate(raw,2):
            code=str(r.get('tracking') or '').strip();status=TRACK_ALIASES.get(str(r.get('status') or r.get('trạng thái') or '').strip().upper(),'')
            found=db.scalars(select(Order).where(Order.tracking==code)).all() if code else []
            error=''
            if not code or not status:error='Thiếu vận đơn hoặc trạng thái không hợp lệ'
            elif code in seen:error='Vận đơn lặp trong file'
            elif len(found)!=1:error='Không tìm thấy hoặc vận đơn bị trùng'
            if not error:
                o=found[0];f=db.get(Flow,o.id)
                if effective_stage(o,f)!='SHIPPED':error='Đơn chưa bàn giao'
                elif not tracking_allowed(o.status,status):error='Chuyển trạng thái không hợp lệ'
                rows.append({'line':i,'tracking':code,'status':status,'order_id':o.id,'version':f.version if f else 0,'previous':o.status,'error':error,'note':str(r.get('note') or '')[:2000]})
            else:rows.append({'line':i,'tracking':code,'status':status,'error':error})
            seen.add(code)
        return save_preview(db,user,'tracking',rows)

    @app.post('/api/wf/import/confirm')
    def confirm_preview(data:ConfirmInput,db=Depends(get_db),user=Depends(current_user)):
        p=db.scalar(select(Preview).where(Preview.id==data.preview_id).with_for_update())
        if not p or p.user_id!=user.id:raise HTTPException(404,'Không tìm thấy bản xem trước.')
        if p.confirmed:return json.loads(p.result)
        created=p.created_at.replace(tzinfo=timezone.utc) if p.created_at.tzinfo is None else p.created_at
        if datetime.now(timezone.utc)-created>timedelta(minutes=20):raise HTTPException(409,'Bản xem trước hết hạn. Vui lòng nhập lại file.')
        require(user,'admin',*(['operator'] if p.kind=='orders' else ['operator','warehouse']))
        rows=json.loads(p.payload)
        if p.kind not in {'orders','tracking'}:raise HTTPException(422,'Dùng xác nhận địa chỉ riêng.')
        if not rows or any(r.get('error') for r in rows):raise HTTPException(422,'Sửa các lỗi cấu trúc và xem trước lại trước khi nhập.')
        result={'created':0,'skipped':0,'updated':0}
        # Claim preview before mutating data (works with SQLite and PostgreSQL).
        r=db.execute(update(Preview).where(Preview.id==p.id,Preview.confirmed==0).values(confirmed=1).execution_options(synchronize_session=False))
        if r.rowcount!=1:raise HTTPException(409,'Lần nhập đang được xử lý ở cửa sổ khác.')
        if p.kind=='orders':
            for row in rows:
                o=db.scalar(select(Order).where(Order.customer_id==row['data']['customer_id'],Order.source==row['data']['source'],Order.code==row['data']['code']))
                if o:
                    if management.is_deleted(db, o.id):
                        o, flow = lock_order(db, o.id, db.get(Flow, o.id).version, include_deleted=True)
                        values = OrderInput(**row['data']).model_dump()
                        management.ensure_customer_active(db, o.customer_id)
                        o.items = [Item(sku=sku, quantity=qty) for sku,qty in aggregate_items(values['items']).items()]
                        o.cod = values['cod']
                        o.currency = values['currency']
                        o.tracking = values.get('tracking', '').strip()
                        for key in ADDRESS_FIELDS:
                            setattr(flow, key, values[key])
                        flow.assigned = values['assigned']
                        flow.note = values['note']
                        complete_address(flow)
                        errors = validate_order(o, flow)
                        flow.stage = 'ERROR' if errors else 'REVIEW'
                        flow.issue = '; '.join(errors)
                        db.delete(db.get(management.DeletedOrder, o.id))
                        if o.tracking:
                            claim_tracking(o, o.tracking, db)
                        audit(db,user,'import.order',{'order_id':o.id,'preview_id':p.id,'source_lines':row['lines'],'restored':True})
                        result['created']+=1
                        continue
                    result['skipped']+=1
                    continue
                created_order,_=make_order(db,OrderInput(**row['data']).model_dump(),user)
                if created_order.tracking:
                    claim_tracking(created_order, created_order.tracking, db)
                audit(db,user,'import.order',{'order_id':created_order.id,'preview_id':p.id,'source_lines':row['lines']})
                result['created']+=1
        else:
            for row in rows:
                o,f=lock_order(db,row['order_id'],row['version'])
                if o.status!=row['previous']:raise HTTPException(409,'Tracking vừa thay đổi. Xem trước lại file.')
                if o.status==row['status']:result['skipped']+=1;continue
                set_tracking(db,o,f,user,row['status'],row['note'],'FILE');result['updated']+=1
        p.result=json.dumps(result);audit(db,user,'import.confirm',{'kind':p.kind,'preview_id':p.id,**result});db.commit();return result

    @app.get('/api/wf/export')
    def export(db=Depends(get_db),user=Depends(current_user)):
        buff=io.StringIO();fields=['code','customer','tracking','cod','currency','stage','transport','finance','completed_at','returned_at','recipient','phone','full_address','province','district','postcode','country','sku','quantity']
        writer=csv.DictWriter(buff,fieldnames=fields);writer.writeheader()
        for o in db.scalars(select(Order).order_by(Order.id)).all():
            if management.is_deleted(db, o.id):continue
            row=serialize(db,o,db.get(Flow,o.id))
            for item in row['items'] or [{'sku':'','quantity':''}]:
                out={k:row.get(k,'') for k in fields};out.update(item)
                # Prevent spreadsheet formula execution in exported customer-supplied text.
                writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for k,v in out.items()})
        return StreamingResponse(iter(['\ufeff'+buff.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="foxrun_orders.csv"'})

    # Put role validation on the inherited customer endpoints as well.
    @app.middleware('http')
    async def customer_role_guard(request:Request,call_next):
        path=request.url.path
        if request.method in {'POST','PATCH','DELETE'} and (path.startswith('/api/v1/customers') or path.startswith('/api/v1/customer-aliases')):
            from fastapi.responses import JSONResponse
            with SessionLocal() as db:
                try:
                    u=current_user(request,db);require(u,'admin','operator')
                except HTTPException as e:return JSONResponse({'detail':e.detail},status_code=e.status_code)
        return await call_next(request)

    # Replace the existing homepage route without changing old read APIs.
    for route in list(app.router.routes):
        if getattr(route,'path',None)=='/':app.router.routes.remove(route)
    @app.get('/',response_class=HTMLResponse)
    def homepage():return Path(__file__).with_name('workflow.html').read_text(encoding='utf-8')

    @app.get('/management-ui.js')
    def management_javascript():
        from fastapi.responses import Response
        return Response(Path(__file__).with_name('management_ui.js').read_text(encoding='utf-8'),
                        media_type='text/javascript')

    global management
    import management
    import sys
    management.install(app, sys.modules[__name__], action, claim_tracking)



STAGES={'REVIEW':'Chờ duyệt','ERROR':'Đơn lỗi','WAIT_STOCK':'Chờ hàng','PICKING':'Chờ lấy hàng','PACKING':'Chờ đóng gói','READY':'Chờ bàn giao','SHIPPED':'Đã bàn giao','CANCELLED':'Đã hủy'}
TRACKING={'NOT_SHIPPED':'Chưa gửi','IN_TRANSIT':'Đang vận chuyển','OUT_FOR_DELIVERY':'Đang giao','DELIVERY_FAILED':'Giao thất bại','DELIVERED':'Đã giao','RETURNING':'Đang hoàn','RETURNED':'Đã hoàn','EXCEPTION':'Bất thường','UNKNOWN':'Chưa rõ'}
ROLES={'admin':'Quản trị','operator':'Đơn hàng','warehouse':'Kho','finance':'Đối soát','viewer':'Chỉ xem'}
ADDRESS_FIELDS=['recipient','phone','country','original_address','full_address','province','district','postcode']
TRANSITIONS={'NOT_SHIPPED':{'IN_TRANSIT'},'IN_TRANSIT':set(TRACKING)-{'NOT_SHIPPED','UNKNOWN'},'OUT_FOR_DELIVERY':{'OUT_FOR_DELIVERY','IN_TRANSIT','DELIVERED','DELIVERY_FAILED','RETURNING','EXCEPTION'},'DELIVERY_FAILED':{'DELIVERY_FAILED','IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERED','RETURNING','EXCEPTION'},'EXCEPTION':set(TRACKING)-{'NOT_SHIPPED','UNKNOWN'},'UNKNOWN':set(TRACKING)-{'NOT_SHIPPED'},'DELIVERED':{'DELIVERED','RETURNING'},'RETURNING':{'RETURNING','RETURNED','EXCEPTION'},'RETURNED':{'RETURNED'}}
TRACK_ALIASES={**{k:k for k in TRACKING},**{v.upper():k for k,v in TRACKING.items()},'IN TRANSIT':'IN_TRANSIT','OUT FOR DELIVERY':'OUT_FOR_DELIVERY','DELIVERY FAILED':'DELIVERY_FAILED','GIAO THÀNH CÔNG':'DELIVERED','HOAN THANH':'DELIVERED','HOAN HANG':'RETURNED'}

class LineInput(BaseModel):
    sku:str=Field(min_length=1,max_length=120)
    quantity:int=Field(ge=1,le=1000000)
    @field_validator('sku')
    @classmethod
    def valid_sku(cls,v):
        if not v.strip():raise ValueError('SKU trống')
        return v.strip()

class AddressInput(BaseModel):
    recipient:str=Field(default='',max_length=200)
    phone:str=Field(default='',max_length=80)
    country:str='MY'
    original_address:str=Field(default='',max_length=4000)
    full_address:str=Field(default='',max_length=4000)
    province:str=Field(default='',max_length=150)
    district:str=Field(default='',max_length=150)
    postcode:str=Field(default='',max_length=20)
    assigned:str=Field(default='',max_length=100)
    note:str=Field(default='',max_length=2000)
    cod:Decimal=Field(default=Decimal('0'),ge=0,max_digits=18,decimal_places=2)
    currency:str='MYR'
    items:list[LineInput]=Field(min_length=1,max_length=200)
    @field_validator('currency')
    @classmethod
    def valid_currency(cls,v):
        v=v.strip().upper()
        if v not in {'MYR','THB','PHP','IDR','USD','VND'}:raise ValueError('Tiền tệ không hỗ trợ')
        return v
    @field_validator('country')
    @classmethod
    def valid_country(cls,v):
        v=v.strip().upper()
        if v not in {'MY','TH','PH','ID','US','VN'}:raise ValueError('Quốc gia không hỗ trợ')
        return v

class OrderInput(AddressInput):
    customer_id:int
    code:str=Field(min_length=1,max_length=200)
    source:str='manual'
    tracking: str = Field(default='', max_length=200)
    @field_validator('code','source')
    @classmethod
    def not_empty(cls,v):
        if not v.strip():raise ValueError('Không được để trống')
        return v.strip()

class EditInput(AddressInput):
    version:int=Field(ge=0)

class ActionInput(BaseModel):
    version:int=Field(ge=0)
    action:str
    tracking:str=Field(default='',max_length=200)
    carrier:str=Field(default='',max_length=100)
    note:str=Field(default='',max_length=2000)

class TrackingInput(BaseModel):
    version:int=Field(ge=0)
    status:str
    note:str=Field(default='',max_length=2000)

class ReturnLine(BaseModel):
    sku:str
    good:int=Field(ge=0)
    damaged:int=Field(ge=0)
    missing:int=Field(ge=0)
class ReturnInput(BaseModel):
    version:int=Field(ge=0)
    lines:list[ReturnLine]=Field(min_length=1,max_length=200)
    note:str=Field(default='',max_length=2000)
class FinanceInput(BaseModel):
    version:int=Field(ge=0)
    carrier_fee:Decimal=Field(ge=0,max_digits=18,decimal_places=2)
    service_fee:Decimal=Field(ge=0,max_digits=18,decimal_places=2)
    carrier_received:Decimal=Field(max_digits=18,decimal_places=2)
    seller_paid:Decimal=Field(default=0,max_digits=18,decimal_places=2)
    reference:str=Field(min_length=1,max_length=200)
    confirm:bool=False
class StockInput(BaseModel):
    customer_id:int
    sku:str=Field(min_length=1,max_length=120)
    quantity:int=Field(ge=-1000000,le=1000000)
    reason:str=Field(min_length=3,max_length=500)
    @field_validator('sku','reason')
    @classmethod
    def trimmed(cls,v):
        if not v.strip():raise ValueError('Không để trống')
        return v.strip()
class CustomerEdit(BaseModel):
    name:str=Field(min_length=1,max_length=200)
    @field_validator('name')
    @classmethod
    def trim(cls,v):
        if not v.strip():raise ValueError('Không để trống tên')
        return v.strip()
class UserInput(BaseModel):
    username:str=Field(pattern=r'^[a-zA-Z0-9_.-]{3,60}$')
    password:str=Field(min_length=12,max_length=200)
    role:str
    @field_validator('role')
    @classmethod
    def role_known(cls,v):
        if v not in ROLES:raise ValueError('Vai trò không hợp lệ')
        return v
class PasswordInput(BaseModel):
    old:str
    new:str=Field(min_length=12,max_length=200)
class ConfirmInput(BaseModel):
    preview_id:str


def require(user,*roles):
    if user.role not in roles:raise HTTPException(403,'Tài khoản không có quyền thực hiện thao tác này.')
def now():return datetime.now(timezone.utc)
def touch(f):f.updated_at=now()
def find_order(db,oid):
    o=db.get(Order,oid)
    if not o or management.is_deleted(db, oid):raise HTTPException(404,'Không tìm thấy đơn.')
    return o
def effective_stage(o,f):return f.stage if f else ('SHIPPED' if o.status in set(TRACKING)-{'NOT_SHIPPED','UNKNOWN'} else 'REVIEW')
def lock_order(db,oid,version,include_deleted=False):
    o=db.scalar(select(Order).where(Order.id==oid).with_for_update())
    if not o or (not include_deleted and management.is_deleted(db,oid)):raise HTTPException(404,'Không tìm thấy đơn.')
    f=db.get(Flow,oid)
    if not f:
        if version!=0:raise HTTPException(409,'Dữ liệu vừa thay đổi. Tải lại đơn.')
        f=Flow(order_id=oid,stage=effective_stage(o,None),version=0);db.add(f);db.flush()
    if f.version!=version:raise HTTPException(409,'Có người vừa sửa đơn này. Tải lại trước khi thao tác.')
    result=db.execute(update(Flow).where(Flow.order_id==oid,Flow.version==version).values(version=version+1).execution_options(synchronize_session=False))
    if result.rowcount!=1:raise HTTPException(409,'Có người vừa sửa đơn này. Tải lại trước khi thao tác.')
    db.refresh(f)
    return o,f
def expect(f,stage):
    if f.stage!=stage:raise HTTPException(409,'Đơn chưa ở bước '+STAGES[stage])
def quantities(o):
    result=defaultdict(int)
    for i in o.items:result[i.sku]+=i.quantity
    return dict(result)
def complete_address(f):
    address=(f.full_address or f.original_address or '').strip()
    for part in [f.district,f.province,f.postcode]:
        if part and part.strip().casefold() not in address.casefold():address+=(', ' if address else '')+part.strip()
    f.full_address=address

def validate_order(o,f):
    errors=[]
    for key,label in [('recipient','người nhận'),('phone','số điện thoại'),('full_address','địa chỉ chi tiết'),('province','tỉnh/bang'),('district','huyện/thành phố')]:
        if not (getattr(f,key) or '').strip():errors.append('Thiếu '+label)
    if f.country!='VN' and not (f.postcode or '').strip():errors.append('Thiếu postcode')
    if f.country in {'MY','TH','ID','VN'} and f.postcode and (len(f.postcode)!=5 or not f.postcode.isdigit()):errors.append('Postcode phải có 5 chữ số')
    if f.country=='US' and not __import__('re').fullmatch(r'\d{5}(-\d{4})?',f.postcode or ''):errors.append('ZIP Mỹ cần dạng 12345 hoặc 12345-6789')
    if f.country=='PH' and (len(f.postcode or '')!=4 or not f.postcode.isdigit()):errors.append('Postcode Philippines phải có 4 chữ số')
    if not o.items or any(not i.sku.strip() or i.quantity<1 for i in o.items):errors.append('Thiếu SKU / số lượng hợp lệ')
    if Decimal(o.cod)<0:errors.append('COD không được âm')
    return errors

def make_order(db,data,user):
    management.ensure_customer_active(db, data['customer_id'])
    if not db.get(Customer,data['customer_id']):raise HTTPException(422,'Khách hàng chưa tồn tại.')
    if db.scalar(select(Order).where(Order.customer_id==data['customer_id'],Order.source==data['source'],Order.code==data['code'])):raise HTTPException(409,'Mã đơn đã tồn tại trong nguồn và khách này.')
    o=Order(customer_id=data['customer_id'],code=data['code'],source=data['source'],cod=data['cod'],currency=data['currency'],tracking=data.get('tracking', '').strip(),status='NOT_SHIPPED',items=[Item(sku=sku,quantity=qty) for sku,qty in aggregate_items(data['items']).items()])
    db.add(o);db.flush()
    f=Flow(order_id=o.id,stage='REVIEW',**{k:data[k] for k in ADDRESS_FIELDS},assigned=data['assigned'],note=data['note'])
    if not f.original_address:f.original_address=f.full_address
    complete_address(f)
    db.add(f);db.flush()
    errs=validate_order(o,f)
    if errs:f.stage='ERROR';f.issue='; '.join(errs)
    audit(db,user,'order.create',{'order_id':o.id,'code':o.code,'source':o.source})
    return o,f

def aggregate_items(items):
    result=defaultdict(int)
    for i in items:
        if hasattr(i,'model_dump'):i=i.model_dump()
        result[i['sku']]+=i['quantity']
    return dict(result)

def record_tracking(db,o,user,status,note,source='MANUAL'):
    previous=o.status;o.status=status
    db.add(TrackingEvent(order_id=o.id,tracking_number=o.tracking or '',previous_status=previous,new_status=status,note=note,updated_by=user.id,source_type=source))
def tracking_allowed(old,new):return new in TRANSITIONS.get(old,{'IN_TRANSIT'})
def set_tracking(db,o,f,user,status,note,source='MANUAL'):
    if f.stage!='SHIPPED':raise HTTPException(409,'Đơn chưa được bàn giao vận chuyển.')
    if status not in TRACKING or not tracking_allowed(o.status,status):raise HTTPException(409,'Chuyển trạng thái không hợp lệ. Đơn đã hoàn không thể đổi sang đã giao.')
    if f.reconciled and status!=o.status:raise HTTPException(409,'Đơn đã đối soát. Cần xử lý điều chỉnh tài chính trước khi thay đổi kết quả giao.')
    if o.status!=status:
        record_tracking(db,o,user,status,note,source);touch(f)
        audit(db,user,'tracking.update',{'order_id':o.id,'status':status,'note':note})


def serialize(db,o,f):
    stage=effective_stage(o,f)
    vals={k:(getattr(f,k) or '') if f else '' for k in ADDRESS_FIELDS+['assigned','note','carrier','issue','settlement_reference']}
    vals['country']=vals['country'] or 'MY'
    status=o.status if o.status in TRACKING else 'NOT_SHIPPED'
    last=db.scalar(select(TrackingEvent).where(TrackingEvent.order_id==o.id).order_by(TrackingEvent.created_at.desc(),TrackingEvent.id.desc()).limit(1))
    at=last.created_at if last else (f.updated_at if f else o.created_at)
    if at and at.tzinfo is None:at=at.replace(tzinfo=timezone.utc)
    stale=status in {'IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERY_FAILED','EXCEPTION'} and bool(at) and now()-at>timedelta(days=5)
    fees={k:Decimal(getattr(f,k) or 0) if f else Decimal('0') for k in ['carrier_fee','service_fee','carrier_received','seller_paid']}
    collect=Decimal(o.cod) if status=='DELIVERED' else Decimal('0')
    expected=collect-fees['carrier_fee'];due=expected-fees['service_fee']
    finance='PENDING'
    if f and f.reconciled:finance='SETTLED' if fees['seller_paid']==due else 'RECONCILED'
    elif f and f.settlement_reference:finance='MISMATCH' if expected!=fees['carrier_received'] else 'REVIEW'
    return {**management.order_extras(db,o),'id':o.id,'code':o.code,'source':o.source,'customer_id':o.customer_id,'customer':o.customer.name,'tracking':o.tracking or '', 'cod':str(o.cod),'currency':o.currency,'stage':stage,'transport':status,'version':f.version if f else 0,'created_at':o.created_at,'updated_at':f.updated_at if f else o.created_at,'stale':stale,'tracking_at':at,'return_received':bool(f.return_received) if f else False,'items':[{'sku':sku,'quantity':qty} for sku,qty in quantities(o).items()],'finance':finance,**vals,**{k:str(v) for k,v in fees.items()},'carrier_expected':str(expected),'carrier_difference':str(fees['carrier_received']-expected),'seller_due':str(due),'seller_balance':str(due-fees['seller_paid'])}

def match_term(term,row):
    if term.startswith('#'):return row['code'].lower().split('-')[-1]==term[1:] or row['tracking'].lower().split('-')[-1]==term[1:]
    return any(term in str(row[k]).lower() for k in ['code','tracking','customer','recipient','phone'])
def in_view(r,view):
    return {'review':r['stage']=='REVIEW','errors':r['stage']=='ERROR','warehouse':r['stage'] in {'PICKING','PACKING','READY','WAIT_STOCK'},'shipping':r['stage']=='SHIPPED' and r['transport'] not in {'DELIVERED','RETURNED'},'exceptions':r['stale'] or r['transport'] in {'DELIVERY_FAILED','EXCEPTION'},'returns':r['transport'] in {'RETURNING','RETURNED'},'finance':r['transport'] in {'DELIVERED','RETURNED'} and r['finance']!='SETTLED'}.get(view,False)

def save_preview(db,user,kind,rows,filename=''):
    p=Preview(id=secrets.token_hex(24),user_id=user.id,kind=kind,payload=json.dumps(rows,ensure_ascii=False,default=str))
    db.add(p)
    db.flush()
    if kind == 'orders':
        db.add(management.ImportInfo(preview_id=p.id,filename=filename[:255]))
    db.commit()
    return {'preview_id':p.id,'kind':kind,'total':len(rows),'errors':sum(bool(r.get('error')) for r in rows),'rows':rows}

def prepare_import(db,raw,source,cod_format='plain'):
    mappings={'người nhận':'recipient','tên người nhận':'recipient','sđt':'phone','số điện thoại':'phone','địa chỉ':'original_address','address':'original_address','địa chỉ chi tiết':'full_address','tỉnh/bang':'province','huyện/thành phố':'district','quốc gia':'country','postcode':'postcode','mã đơn hàng':'code','mã khách':'customer','mã sản phẩm':'sku','tiền thu hộ':'cod'}
    grouped={};results=[]
    for line,row in enumerate(raw,2):
        r={mappings.get(k,k):str(v).strip() if v is not None else '' for k,v in row.items()}
        code=r.get('code','');customer_code=r.get('customer','').upper()
        c=db.scalar(select(Customer).where(Customer.code==customer_code))
        if not c:
            a=db.scalar(select(Alias).where(Alias.alias==customer_code));c=a.customer if a else None
        try:
            if not code:raise ValueError('Thiếu mã đơn')
            if not c:raise ValueError('Chưa có khách / bí danh: '+customer_code)
            management.ensure_customer_active(db, c.id)
            # Explicit decimal currency input. Do not guess thousands separators across currencies.
            amount=r.get('cod') or '0'
            raw_cod=next((v for k,v in row.items() if mappings.get(k,k)=='cod'),None)
            if cod_format=='vn' and isinstance(raw_cod,str):amount=amount.replace('.','').replace(',','.')
            if ',' in amount:raise ValueError('COD cần dạng số không phân nhóm, ví dụ 1470 hoặc 150.50')
            data={'code':code,'customer_id':c.id,'source':source,'cod':amount,'currency':r.get('currency') or 'MYR','items':[{'sku':r.get('sku',''),'quantity':r.get('quantity') or 1}],**{k:r.get(k,'') for k in ADDRESS_FIELDS},'assigned':r.get('assigned',''),'note':r.get('note','')}
            data['country']=r.get('country') or 'MY'
            data['full_address']=data['full_address'] or data['original_address']
            data['tracking'] = r.get('tracking', '')
            valid=OrderInput(**data).model_dump(mode='json')
            key=(c.id,code)
            if key in grouped:
                previous=grouped[key]
                if Decimal(previous['data']['cod'])!=Decimal(valid['cod']) or any(previous['data'][k]!=valid[k] for k in ['currency',*ADDRESS_FIELDS]):raise ValueError('Thông tin cấp đơn không khớp giữa các dòng SKU')
                previous_tracking = previous['data']['tracking']
                current_tracking = valid['tracking']
                if previous_tracking and current_tracking and previous_tracking != current_tracking:
                    raise ValueError('Mã vận đơn không khớp giữa các dòng SKU của cùng đơn.')
                previous['data']['tracking'] = previous_tracking or current_tracking
                previous['data']['items'].extend(valid['items']);previous['lines'].append(line)
            else:
                entry={'line':line,'lines':[line],'code':code,'customer':c.name,'data':valid,'error':'','exists':bool(db.scalar(select(Order).where(Order.customer_id==c.id,Order.source==source,Order.code==code,~Order.id.in_(select(management.DeletedOrder.order_id)))))}
                grouped[key]=entry;results.append(entry)
        except Exception as e:
            results.append({'line':line,'code':code,'customer':customer_code,'error':str(e)[:600]})
    claimed_in_file = set()
    for entry in grouped.values():
        tracking = entry['data']['tracking']
        if not tracking or entry['exists']:
            continue
        if tracking in claimed_in_file or db.scalar(select(Order.id).where(Order.tracking == tracking, ~Order.id.in_(select(management.DeletedOrder.order_id))).limit(1)):
            entry['error'] = 'Mã vận đơn đã gắn với đơn khác.'
        claimed_in_file.add(tracking)
    return results
