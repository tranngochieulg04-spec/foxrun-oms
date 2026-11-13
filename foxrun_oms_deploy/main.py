import os, io, re, csv, json, hmac, hashlib, secrets, time, uuid, sqlite3
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, String, Integer, Numeric, ForeignKey, DateTime, Text, UniqueConstraint, select, func, or_, event, Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from pwdlib import PasswordHash
from openpyxl import load_workbook

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./foxrun_dev.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

if DATABASE_URL.startswith('sqlite'):
    engine = create_engine(
        DATABASE_URL,
        connect_args={'check_same_thread': False, 'timeout': 5},
        pool_pre_ping=True
    )
    @event.listens_for(Engine, 'connect')
    def set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA busy_timeout=5000;')
            cursor.execute('PRAGMA foreign_keys=ON;')
            cursor.close()
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=300
    )

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
password_hash = PasswordHash.recommended()
SECRET = os.environ.get('SESSION_SECRET','dev-only-change-me')
COOKIE_SECURE = os.environ.get('COOKIE_SECURE','false').lower() == 'true'
app = FastAPI(title='FOXRUN OMS Workflow', version='3.3.0')

class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True)
    username=Column(String(100),unique=True,nullable=False)
    password_hash=Column(Text,nullable=False)
    role=Column(String(20),default='admin')

class Customer(Base):
    __tablename__='customers'
    id=Column(Integer,primary_key=True)
    name=Column(String(200),nullable=False)
    code=Column(String(80),unique=True,nullable=False)
    primary_code=Column(String(80),nullable=True)
    sku_keywords=Column(Text,default='')
    fixed_delivery_fee=Column(Numeric(18,2),default=0)
    fixed_return_fee=Column(Numeric(18,2),default=0)
    cod_fee_percent=Column(Numeric(18,4),default=0)
    aliases=relationship('Alias',back_populates='customer',cascade='all,delete-orphan')
    sources=relationship('CustomerSource',back_populates='customer',cascade='all,delete-orphan')

class CustomerSource(Base):
    __tablename__='customer_sources'
    id=Column(Integer,primary_key=True)
    customer_id=Column(Integer,ForeignKey('customers.id'),nullable=False)
    is_active=Column(Integer,default=1,nullable=False)
    source_type=Column(String(50),nullable=False) # 'pancake' | 'google_sheet'
    source_name=Column(String(200),default='')
    code_prefix=Column(String(50),default='')
    pancake_api_key=Column(Text,default='')
    pancake_shop_id=Column(String(100),default='')
    pancake_page_id=Column(String(100),default='')
    pancake_status_filter=Column(String(200),default='')
    gsheet_url=Column(Text,default='')
    gsheet_tab_name=Column(String(200),default='')
    customer=relationship('Customer',back_populates='sources')

class SystemFeeConfig(Base):
    __tablename__='system_fee_configs'
    id=Column(Integer,primary_key=True)
    key=Column(String(50),unique=True,nullable=False)
    name=Column(String(100),nullable=False)
    fee=Column(Numeric(18,2),default=0,nullable=False)
    postcodes=Column(Text,default='')
    regions=Column(Text,default='')

class Alias(Base):
    __tablename__='customer_aliases'
    id=Column(Integer,primary_key=True)
    customer_id=Column(Integer,ForeignKey('customers.id'),nullable=False)
    alias=Column(String(80),unique=True,nullable=False)
    customer=relationship('Customer',back_populates='aliases')

class Order(Base):
    __tablename__='orders'
    id=Column(Integer,primary_key=True)
    customer_id=Column(Integer,ForeignKey('customers.id'),nullable=False)
    source=Column(String(80),nullable=False,default='manual')
    code=Column(String(200),nullable=False)
    tracking=Column(String(200),default='')
    cod=Column(Numeric(18,2),default=0)
    currency=Column(String(3),default='MYR')
    status=Column(String(40),default='DRAFT')
    special_fee=Column(Numeric(18,2),default=0)
    special_fee_note=Column(Text,default='')
    pancake_order_id=Column(String(100),default='')
    pancake_shop_id=Column(String(100),default='')
    pancake_sync_status=Column(String(50),default='')
    completed_at=Column(DateTime(timezone=True),nullable=True)
    is_cod_received=Column(Integer,default=0)
    is_returned_to_warehouse=Column(Integer,default=0)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    customer=relationship('Customer')
    items=relationship('Item',cascade='all,delete-orphan')
    __table_args__=(UniqueConstraint('customer_id','source','code',name='uq_order_source'),)

class Item(Base):
    __tablename__='order_items'
    id=Column(Integer,primary_key=True)
    order_id=Column(Integer,ForeignKey('orders.id'),nullable=False)
    sku=Column(String(120),default='')
    quantity=Column(Integer,default=1)

class ImportBatch(Base):
    __tablename__='import_batches'
    id=Column(Integer,primary_key=True)
    filename=Column(String(255))
    rows=Column(Integer,default=0)
    created=Column(Integer,default=0)
    updated=Column(Integer,default=0)
    errors=Column(Text,default='[]')
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer,primary_key=True)
    actor=Column(String(100))
    action=Column(String(100))
    detail=Column(Text)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class TrackingPreview(Base):
    __tablename__='tracking_manual_previews'
    id=Column(String(36),primary_key=True)
    created_by=Column(Integer,ForeignKey('users.id'),nullable=False)
    payload=Column(Text,nullable=False)
    results=Column(Text,nullable=False)
    expires_at=Column(DateTime(timezone=True),nullable=False)
    confirmed_at=Column(DateTime(timezone=True))

class TrackingEvent(Base):
    __tablename__='tracking_events'
    id=Column(Integer,primary_key=True)
    order_id=Column(Integer,ForeignKey('orders.id'),nullable=False)
    tracking_number=Column(String(200),nullable=False)
    previous_status=Column(String(40))
    new_status=Column(String(40),nullable=False)
    source_type=Column(String(30),nullable=False,default='MANUAL')
    note=Column(Text,default='')
    updated_by=Column(Integer,ForeignKey('users.id'),nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

class TrackingConfirmation(Base):
    __tablename__='tracking_confirmations'
    idempotency_key=Column(String(100),primary_key=True)
    preview_id=Column(String(36),nullable=False)
    created_by=Column(Integer,ForeignKey('users.id'),nullable=False)
    result=Column(Text,nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

from sqlalchemy import inspect as _sa_inspect, text as _sa_text

def run_db_migrations(target_engine):
    try:
        inspector = _sa_inspect(target_engine)
        existing_tables = inspector.get_table_names()
        with target_engine.begin() as conn:
            if 'customers' in existing_tables:
                columns = {col['name'] for col in inspector.get_columns('customers')}
                if 'primary_code' not in columns:
                    conn.execute(_sa_text("ALTER TABLE customers ADD COLUMN primary_code VARCHAR(80)"))
                if 'sku_keywords' not in columns:
                    conn.execute(_sa_text("ALTER TABLE customers ADD COLUMN sku_keywords TEXT DEFAULT ''"))
                if 'fixed_delivery_fee' not in columns:
                    conn.execute(_sa_text("ALTER TABLE customers ADD COLUMN fixed_delivery_fee NUMERIC(18,2) DEFAULT 0"))
                if 'fixed_return_fee' not in columns:
                    conn.execute(_sa_text("ALTER TABLE customers ADD COLUMN fixed_return_fee NUMERIC(18,2) DEFAULT 0"))
                if 'cod_fee_percent' not in columns:
                    conn.execute(_sa_text("ALTER TABLE customers ADD COLUMN cod_fee_percent NUMERIC(18,4) DEFAULT 0"))
                    
            if 'orders' in existing_tables:
                columns = {col['name'] for col in inspector.get_columns('orders')}
                if 'special_fee' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN special_fee NUMERIC(18,2) DEFAULT 0"))
                if 'special_fee_note' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN special_fee_note TEXT DEFAULT ''"))
                if 'pancake_order_id' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN pancake_order_id VARCHAR(100) DEFAULT ''"))
                if 'pancake_shop_id' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN pancake_shop_id VARCHAR(100) DEFAULT ''"))
                if 'pancake_sync_status' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN pancake_sync_status VARCHAR(50) DEFAULT ''"))
                if 'completed_at' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN completed_at TIMESTAMP WITH TIME ZONE"))
                if 'is_cod_received' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN is_cod_received INTEGER DEFAULT 0"))
                if 'is_returned_to_warehouse' not in columns:
                    conn.execute(_sa_text("ALTER TABLE orders ADD COLUMN is_returned_to_warehouse INTEGER DEFAULT 0"))
    except Exception as e:
        pass

Base.metadata.create_all(engine)  # MVP bootstrap; replace with Alembic before production migrations
run_db_migrations(engine)

with SessionLocal() as db:
    if not db.scalar(select(User).limit(1)):
        username=os.environ.get('ADMIN_USERNAME','admin')
        password=os.environ.get('ADMIN_PASSWORD')
        if not password or len(password)<12:
            password='Admin@Foxrun12345'
        db.add(User(username=username,password_hash=password_hash.hash(password),role='admin'))
        db.commit()
    # Seed default system fee configs
    if not db.scalar(select(SystemFeeConfig).where(SystemFeeConfig.key == 'tourism')):
        db.add(SystemFeeConfig(
            key='tourism',
            name='Phí du lịch / Đảo',
            fee=Decimal('30.00'),
            postcodes='07000-07999, 87000-87999, 26800-26899, 32300-32399',
            regions='Langkawi, Tioman, Labuan, Pangkor'
        ))
    if not db.scalar(select(SystemFeeConfig).where(SystemFeeConfig.key == 'remote')):
        db.add(SystemFeeConfig(
            key='remote',
            name='Phí vùng xa',
            fee=Decimal('50.00'),
            postcodes='88000-99999',
            regions='Sabah, Sarawak'
        ))
    db.commit()

def get_db():
    with SessionLocal() as db: yield db

def sign(user_id:int, expiry:int):
    payload=f'{user_id}.{expiry}'
    signature=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
    return f'{payload}.{signature}'

def current_user(request:Request,db:Session=Depends(get_db)):
    token=request.cookies.get('foxrun_session','')
    try:
        uid, expiry, signature=token.split('.')
        msg=f'{uid}.{expiry}'
        expected=hmac.new(SECRET.encode(),msg.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected) or int(expiry)<time.time(): raise ValueError()
        user=db.get(User,int(uid))
        if not user: raise ValueError()
        return user
    except (ValueError,AttributeError): raise HTTPException(401,'Please log in')

def audit(db,user,action,detail):
    db.add(Audit(actor=user.username,action=action,detail=json.dumps(detail,ensure_ascii=False)))

class Login(BaseModel):
    username:str
    password:str

@app.post('/api/v1/auth/login')
def login(data:Login,db:Session=Depends(get_db)):
    user=db.scalar(select(User).where(User.username==data.username))
    if not user or not password_hash.verify(data.password,user.password_hash): raise HTTPException(401,'Invalid credentials')
    resp=JSONResponse({'username':user.username,'role':user.role})
    resp.set_cookie('foxrun_session',sign(user.id,int(time.time())+8*3600),httponly=True,secure=COOKIE_SECURE,samesite='lax',max_age=8*3600)
    return resp

@app.post('/api/v1/auth/logout')
def logout():
    resp=JSONResponse({'ok':True})
    resp.delete_cookie('foxrun_session')
    return resp

@app.get('/api/v1/auth/me')
def me(user:User=Depends(current_user)): return {'username':user.username,'role':user.role}

class CustomerIn(BaseModel):
    name:str=Field(min_length=1,max_length=200)
    code:str=Field(min_length=1,max_length=80)
    primary_code:Optional[str]=None
    sku_keywords:Optional[str]=''
    fixed_delivery_fee:Optional[Decimal]=Decimal('0')
    fixed_return_fee:Optional[Decimal]=Decimal('0')
    cod_fee_percent:Optional[Decimal]=Decimal('0')

@app.get('/api/v1/customers')
def customers(db:Session=Depends(get_db),user:User=Depends(current_user)):
    return [{
        'id':c.id,
        'name':c.name,
        'code':c.code,
        'primary_code':c.primary_code or c.code,
        'sku_keywords':c.sku_keywords or '',
        'fixed_delivery_fee':str(c.fixed_delivery_fee or 0),
        'fixed_return_fee':str(c.fixed_return_fee or 0),
        'cod_fee_percent':str(c.cod_fee_percent or 0),
        'aliases':[a.alias for a in c.aliases]
    } for c in db.scalars(select(Customer).order_by(Customer.id.desc())).all() if not db.get(_workflow.management.DeletedCustomer,c.id)]

@app.post('/api/v1/customers')
def create_customer(data:CustomerIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    code=data.code.strip().upper()
    if not code or not data.name.strip():raise HTTPException(422,'Tên khách và mã khách không được để trống.')
    if db.scalar(select(Customer).where(Customer.code==code)) or db.scalar(select(Alias).where(Alias.alias==code)):
        raise HTTPException(409,'Customer code already exists')
    c=Customer(
        name=data.name.strip(),
        code=code,
        primary_code=(data.primary_code or code).strip().upper(),
        sku_keywords=(data.sku_keywords or '').strip(),
        fixed_delivery_fee=data.fixed_delivery_fee or Decimal('0'),
        fixed_return_fee=data.fixed_return_fee or Decimal('0'),
        cod_fee_percent=data.cod_fee_percent or Decimal('0')
    )
    db.add(c); audit(db,user,'customer.create',{'code':code}); db.commit();db.refresh(c)
    return {
        'id':c.id,
        'name':c.name,
        'code':c.code,
        'primary_code':c.primary_code,
        'sku_keywords':c.sku_keywords,
        'fixed_delivery_fee':str(c.fixed_delivery_fee or 0),
        'fixed_return_fee':str(c.fixed_return_fee or 0),
        'cod_fee_percent':str(c.cod_fee_percent or 0)
    }

class AliasIn(BaseModel):
    alias:str=Field(min_length=1,max_length=80)

@app.post('/api/v1/customers/{customer_id}/aliases')
def add_alias(customer_id:int,data:AliasIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not db.get(Customer,customer_id): raise HTTPException(404,'Customer not found')
    value=data.alias.strip().upper()
    if not value:raise HTTPException(422,'Bí danh không được để trống.')
    if db.scalar(select(Alias).where(Alias.alias==value)) or db.scalar(select(Customer).where(Customer.code==value)):
        raise HTTPException(409,'Alias already used')
    a=Alias(customer_id=customer_id,alias=value)
    db.add(a);audit(db,user,'alias.create',{'customer_id':customer_id,'alias':value});db.commit()
    return {'id':a.id,'alias':value}

@app.delete('/api/v1/customer-aliases/{alias_id}')
def remove_alias(alias_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    a=db.get(Alias,alias_id)
    if not a: raise HTTPException(404,'Alias not found')
    audit(db,user,'alias.delete',{'alias':a.alias});db.delete(a);db.commit()
    return {'ok':True}

class ItemIn(BaseModel):
    sku:str=''
    quantity:int=Field(default=1,ge=1)
class OrderIn(BaseModel):
    customer_id:int
    code:str=Field(min_length=1)
    source:str='manual'
    tracking:str=''
    cod:Decimal=Decimal('0')
    currency:str='MYR'
    items:list[ItemIn]=Field(default_factory=list)

@app.post('/api/v1/orders')
def create_order(data:OrderIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not db.get(Customer,data.customer_id):raise HTTPException(404,'Customer not found')
    if db.scalar(select(Order).where(Order.customer_id==data.customer_id,Order.source==data.source,Order.code==data.code)):
        raise HTTPException(409,'Order already exists')
    o=Order(customer_id=data.customer_id,code=data.code,source=data.source,tracking=data.tracking,cod=data.cod,currency=data.currency,items=[Item(sku=i.sku,quantity=i.quantity) for i in data.items])
    db.add(o);audit(db,user,'order.create',{'code':data.code});db.commit();db.refresh(o)
    return {'id':o.id,'code':o.code}

@app.get('/api/v1/orders')
def orders(q:str='',limit:int=100,db:Session=Depends(get_db),user:User=Depends(current_user)):
    stmt=select(Order).join(Customer)
    if q.strip():
        term=f'%{q.strip()}%'
        stmt=stmt.where(or_(Order.code.ilike(term),Order.tracking.ilike(term),Customer.name.ilike(term)))
    result=db.scalars(stmt.order_by(Order.id.desc()).limit(min(max(limit,1),500))).all()
    return [{'id':o.id,'code':o.code,'customer':o.customer.name,'tracking':o.tracking,'cod':str(o.cod),'currency':o.currency,'status':o.status,'items':[{'sku':i.sku,'quantity':i.quantity} for i in o.items]} for o in result]

class BulkSearch(BaseModel):
    queries:list[str]=Field(min_length=1,max_length=500)
@app.post('/api/v1/search/bulk')
def bulk_search(data:BulkSearch,db:Session=Depends(get_db),user:User=Depends(current_user)):
    terms=[x.strip().lower() for x in data.queries if x.strip()]
    if not terms:return []
    conditions=[]
    for term in terms:
        like=f'%{term}%'
        conditions.extend([Order.code.ilike(like),Order.tracking.ilike(like)])
    result=db.scalars(select(Order).where(or_(*conditions)).order_by(Order.id.desc()).limit(500)).all()
    return [{'id':o.id,'code':o.code,'tracking':o.tracking,'customer':o.customer.name,'status':o.status} for o in result]

FIELDS=['code','customer','tracking','cod','currency','sku','quantity']
ALIASES={'order_id':'code','order_code':'code','mã đơn':'code','ma don':'code','khách hàng':'customer','khach hang':'customer','customer_code':'customer','mã vận đơn':'tracking','ma van don':'tracking','tracking_number':'tracking','mã sku':'sku','số lượng':'quantity','so luong':'quantity'}
def read_rows(name: str,content:bytes):
    if len(content)>10*1024*1024:raise HTTPException(413,'File too large (10 MB max)')
    if name.lower().endswith('.xlsx'):
        wb=load_workbook(io.BytesIO(content),read_only=True,data_only=True)
        ws=wb.active
        iterator=ws.iter_rows(values_only=True)
        header=next(iterator,[])
        rows=[dict(zip([str(h or '').strip() for h in header],row)) for row in iterator]
        wb.close()
    elif name.lower().endswith('.csv'):
        text=content.decode('utf-8-sig')
        rows=list(csv.DictReader(io.StringIO(text)))
    else:raise HTTPException(400,'Only .csv or .xlsx supported')
    if len(rows)>10000:raise HTTPException(413,'Maximum 10,000 rows per import')
    normalized=[]
    for row in rows:
        normalized.append({ALIASES.get(str(k).strip().lower(),str(k).strip().lower()):v for k,v in row.items()})
    return normalized

def parse_cod(value):
    s=str(value or '0').strip().replace(' ','')
    if re.fullmatch(r'\d{1,3}(\.\d{3})+',s):s=s.replace('.','')
    try:return Decimal(s)
    except InvalidOperation:raise ValueError('Invalid COD')

def validate_rows(rows):
    errors=[]
    for i,r in enumerate(rows,2):
        if not str(r.get('code') or '').strip():errors.append({'row':i,'error':'Missing order code'})
        if not str(r.get('customer') or '').strip():errors.append({'row':i,'error':'Missing customer code'})
        try:
            parse_cod(r.get('cod'))
            if int(r.get('quantity') or 1)<1:raise ValueError()
        except (ValueError,TypeError):errors.append({'row':i,'error':'Invalid COD or quantity'})
    return errors

@app.post('/api/v1/imports/preview')
async def preview(file:UploadFile=File(...),user:User=Depends(current_user)):
    rows=read_rows(file.filename or '',await file.read())
    return {'total':len(rows),'sample':rows[:5],'errors':validate_rows(rows)[:100]}

@app.post('/api/v1/imports/commit')
async def commit(file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(current_user)):
    rows=read_rows(file.filename or '',await file.read())
    errors=validate_rows(rows)
    if errors:raise HTTPException(422,{'errors':errors[:100]})
    batch=ImportBatch(filename=file.filename,rows=len(rows));db.add(batch)
    try:
        for row in rows:
            customer_code=str(row['customer']).strip().upper()
            customer=db.scalar(select(Customer).where(Customer.code==customer_code))
            if not customer:
                alias=db.scalar(select(Alias).where(Alias.alias==customer_code))
                customer=alias.customer if alias else None
            if not customer:raise HTTPException(422,f'Unknown customer: {customer_code}. Create customer/alias first.')
            code=str(row['code']).strip()
            order=db.scalar(select(Order).where(Order.customer_id==customer.id,Order.source=='excel',Order.code==code))
            if order:
                batch.updated+=1
            else:
                order=Order(customer_id=customer.id,source='excel',code=code,tracking=str(row.get('tracking') or ''),cod=parse_cod(row.get('cod')),currency=str(row.get('currency') or 'MYR').upper())
                db.add(order);db.flush();batch.created+=1
            sku=str(row.get('sku') or '').strip()
            if sku:
                existing=db.scalar(select(Item).where(Item.order_id==order.id,Item.sku==sku))
                if not existing:db.add(Item(order_id=order.id,sku=sku,quantity=int(row.get('quantity') or 1)))
        audit(db,user,'import.commit',{'filename':file.filename,'rows':len(rows)});db.commit()
    except Exception:
        db.rollback();raise
    return {'batch_id':batch.id,'rows':batch.rows,'created':batch.created,'existing':batch.updated}

@app.api_route('/api/health', methods=['GET', 'HEAD'])
@app.api_route('/api/v1/health', methods=['GET', 'HEAD'])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(select(1))
        return {'status': 'ok', 'db': 'connected', 'ok': True}
    except Exception as e:
        raise HTTPException(503, f'Database check failed: {e}')

@app.api_route('/', methods=['GET', 'HEAD'], response_class=HTMLResponse)
def home():
    with open('index.html',encoding='utf-8') as f:return f.read()


# Manual tracking: preview never mutates orders; confirm revalidates in a transaction.
TRACKING_STATUSES={'NOT_SHIPPED','IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERY_FAILED','DELIVERED','RETURNING','RETURNED','EXCEPTION','UNKNOWN'}

class ManualTrackingPreviewIn(BaseModel):
    carrier:str=Field(default='other',max_length=60)
    tracking_numbers:list[str]=Field(min_length=1,max_length=500)
    status:str
    note:str=Field(default='',max_length=2000)

class ManualTrackingConfirmIn(BaseModel):
    preview_id:str
    selected_order_ids:list[int]=Field(min_length=1,max_length=500)
    idempotency_key:str=Field(min_length=8,max_length=100)

def tracking_preview_row(db,tracking):
    matches=db.scalars(select(Order).where(Order.tracking==tracking)).all()
    if not matches:return {'tracking_number':tracking,'eligible':False,'reason':'NOT_FOUND','matches':[]}
    if len(matches)>1:return {'tracking_number':tracking,'eligible':False,'reason':'AMBIGUOUS','matches':[{'order_id':o.id,'order_code':o.code} for o in matches]}
    o=matches[0]
    return {'tracking_number':tracking,'eligible':True,'reason':'OK','matches':[{'order_id':o.id,'order_code':o.code,'customer':o.customer.name,'current_status':o.status}]}

@app.post('/api/v1/tracking/manual/preview')
def manual_tracking_preview(data:ManualTrackingPreviewIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if data.status not in TRACKING_STATUSES:raise HTTPException(422,'Invalid tracking status')
    numbers=list(dict.fromkeys(x.strip() for x in data.tracking_numbers if x.strip()))
    if not numbers:raise HTTPException(422,'No valid tracking numbers')
    rows=[tracking_preview_row(db,x) for x in numbers]
    now=datetime.now(timezone.utc)
    preview=TrackingPreview(id=str(uuid.uuid4()),created_by=user.id,payload=data.model_dump_json(),results=json.dumps(rows,ensure_ascii=False),expires_at=now+timedelta(minutes=10))
    db.add(preview);db.commit()
    return {'preview_id':preview.id,'expires_at':preview.expires_at.isoformat(),'status':data.status,'rows':rows}

@app.post('/api/v1/tracking/manual/confirm')
def manual_tracking_confirm(data:ManualTrackingConfirmIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    existing=db.get(TrackingConfirmation,data.idempotency_key)
    if existing:
        if existing.created_by!=user.id or existing.preview_id!=data.preview_id:raise HTTPException(409,'Idempotency key already used')
        return json.loads(existing.result)
    preview=db.get(TrackingPreview,data.preview_id)
    if not preview or preview.created_by!=user.id:raise HTTPException(404,'Preview not found')
    expiry=preview.expires_at.replace(tzinfo=timezone.utc) if preview.expires_at.tzinfo is None else preview.expires_at
    if expiry<datetime.now(timezone.utc):raise HTTPException(409,'Preview expired; preview again')
    if preview.confirmed_at:raise HTTPException(409,'Preview already confirmed')
    payload=json.loads(preview.payload); rows=json.loads(preview.results)
    allowed={m['order_id']:(row['tracking_number'],m['current_status']) for row in rows if row['eligible'] for m in row['matches']}
    selected=list(dict.fromkeys(data.selected_order_ids))
    if any(i not in allowed for i in selected):raise HTTPException(422,'Selection contains an ineligible order')
    # Verify every selected order still matches the preview; all-or-nothing on conflicts.
    orders=[]
    for oid in selected:
        o=db.get(Order,oid)
        tracking,old_status=allowed[oid]
        if not o or o.tracking!=tracking or o.status!=old_status:
            raise HTTPException(409,f'Order {oid} changed; preview again')
        if db.scalar(select(func.count(Order.id)).where(Order.tracking==tracking))!=1:
            raise HTTPException(409,f'Tracking {tracking} is no longer unique')
        if o.status=='RETURNED' and payload['status']=='DELIVERED':
            raise HTTPException(409,f'Order {oid} has return/delivery conflict; review required')
        orders.append(o)
    updated=[];skipped=[]
    for o in orders:
        if o.status==payload['status']:
            skipped.append(o.id);continue
        previous=o.status
        o.status=payload['status']
        db.add(TrackingEvent(order_id=o.id,tracking_number=o.tracking,previous_status=previous,new_status=o.status,note=payload['note'],updated_by=user.id))
        updated.append(o.id)
    result={'updated_count':len(updated),'skipped_count':len(skipped),'updated_order_ids':updated,'skipped_order_ids':skipped}
    preview.confirmed_at=datetime.now(timezone.utc)
    db.add(TrackingConfirmation(idempotency_key=data.idempotency_key,preview_id=preview.id,created_by=user.id,result=json.dumps(result)))
    audit(db,user,'tracking.manual.confirm',{'preview_id':preview.id,'updated_order_ids':updated,'skipped_order_ids':skipped,'status':payload['status'],'carrier_label':payload['carrier']})
    db.commit()
    return result

@app.get('/api/v1/tracking/orders/{order_id}/history')
def tracking_history(order_id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not db.get(Order,order_id):raise HTTPException(404,'Order not found')
    events=db.scalars(select(TrackingEvent).where(TrackingEvent.order_id==order_id).order_by(TrackingEvent.id.desc())).all()
    return [{'id':e.id,'tracking_number':e.tracking_number,'previous_status':e.previous_status,'new_status':e.new_status,'source_type':e.source_type,'note':e.note,'updated_by':e.updated_by,'created_at':e.created_at.isoformat()} for e in events]

@app.get('/tracking/manual',response_class=HTMLResponse)
def tracking_manual_ui():
    with open('tracking_manual.html',encoding='utf-8') as f:return f.read()


# Phase 2: file-based tracking updates with independent per-row statuses.
TRACKING_FILE_HEADERS = {
    'tracking_number': 'tracking', 'tracking': 'tracking', 'mã vận đơn': 'tracking',
    'ma van don': 'tracking', 'waybill': 'tracking', 'awb': 'tracking',
    'status': 'status', 'trạng thái': 'status', 'trang thai': 'status',
    'carrier': 'carrier', 'hãng vận chuyển': 'carrier', 'hang van chuyen': 'carrier',
    'note': 'note', 'ghi chú': 'note', 'ghi chu': 'note'
}
STATUS_TRANSLATIONS = {
    'DELIVERED':'DELIVERED', 'GIAO THÀNH CÔNG':'DELIVERED', 'HOAN THANH':'DELIVERED',
    'IN TRANSIT':'IN_TRANSIT', 'IN_TRANSIT':'IN_TRANSIT', 'ĐANG VẬN CHUYỂN':'IN_TRANSIT',
    'OUT FOR DELIVERY':'OUT_FOR_DELIVERY', 'OUT_FOR_DELIVERY':'OUT_FOR_DELIVERY',
    'DELIVERY FAILED':'DELIVERY_FAILED', 'DELIVERY_FAILED':'DELIVERY_FAILED',
    'RETURNED':'RETURNED', 'HOAN HANG':'RETURNED', 'RETURNING':'RETURNING',
    'EXCEPTION':'EXCEPTION'
}

def parse_tracking_file(name, content):
    raw=read_rows(name,content)
    normalized=[]
    for line, original in enumerate(raw,2):
        r={TRACKING_FILE_HEADERS.get(str(k).strip().lower(),str(k).strip().lower()):v for k,v in original.items()}
        tracking=str(r.get('tracking') or '').strip()
        raw_status=str(r.get('status') or '').strip()
        status=STATUS_TRANSLATIONS.get(raw_status.upper())
        normalized.append({'line':line,'tracking_number':tracking,'raw_status':raw_status,
                           'status':status,'carrier':str(r.get('carrier') or 'other').strip(),
                           'note':str(r.get('note') or '').strip()[:2000]})
    return normalized

@app.post('/api/v1/tracking/file/preview')
async def tracking_file_preview(file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(current_user)):
    parsed=parse_tracking_file(file.filename or '',await file.read())
    if not parsed:raise HTTPException(422,'Empty tracking file')
    seen=set(); rows=[]
    for r in parsed:
        tracking=r['tracking_number']
        if not tracking or not r['status']:
            rows.append({**r,'eligible':False,'reason':'MISSING_TRACKING_OR_INVALID_STATUS','matches':[]});continue
        if tracking in seen:
            rows.append({**r,'eligible':False,'reason':'DUPLICATE_IN_FILE','matches':[]});continue
        seen.add(tracking)
        lookup=tracking_preview_row(db,tracking)
        reason=lookup['reason']; eligible=lookup['eligible']
        if eligible and lookup['matches'][0]['current_status']=='RETURNED' and r['status']=='DELIVERED':
            eligible=False;reason='RETURN_DELIVERY_CONFLICT'
        rows.append({**r,'eligible':eligible,'reason':reason,'matches':lookup['matches']})
    now=datetime.now(timezone.utc)
    preview=TrackingPreview(id=str(uuid.uuid4()),created_by=user.id,
        payload=json.dumps({'kind':'file','filename':file.filename},ensure_ascii=False),
        results=json.dumps(rows,ensure_ascii=False),expires_at=now+timedelta(minutes=10))
    db.add(preview);db.commit()
    return {'preview_id':preview.id,'expires_at':preview.expires_at.isoformat(),
            'total':len(rows),'eligible':sum(x['eligible'] for x in rows),'rows':rows}

class TrackingFileConfirmIn(BaseModel):
    preview_id:str
    selected_order_ids:list[int]=Field(min_length=1,max_length=10000)
    idempotency_key:str=Field(min_length=8,max_length=100)

@app.post('/api/v1/tracking/file/confirm')
def tracking_file_confirm(data:TrackingFileConfirmIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    existing=db.get(TrackingConfirmation,data.idempotency_key)
    if existing:
        if existing.created_by!=user.id or existing.preview_id!=data.preview_id:raise HTTPException(409,'Idempotency key reused')
        return json.loads(existing.result)
    preview=db.get(TrackingPreview,data.preview_id)
    if not preview or preview.created_by!=user.id:raise HTTPException(404,'Preview not found')
    payload=json.loads(preview.payload)
    if payload.get('kind')!='file':raise HTTPException(422,'Not a file preview')
    expiry=preview.expires_at.replace(tzinfo=timezone.utc) if preview.expires_at.tzinfo is None else preview.expires_at
    if expiry<datetime.now(timezone.utc) or preview.confirmed_at:raise HTTPException(409,'Preview expired or already confirmed')
    rows=json.loads(preview.results)
    allowed={m['order_id']:(r,m['current_status']) for r in rows if r['eligible'] for m in r['matches']}
    selected=list(dict.fromkeys(data.selected_order_ids))
    if any(oid not in allowed for oid in selected):raise HTTPException(422,'Ineligible selection')
    targets=[]
    for oid in selected:
        row,old=allowed[oid]
        o=db.get(Order,oid)
        if not o or o.tracking!=row['tracking_number'] or o.status!=old:
            raise HTTPException(409,f'Order {oid} changed; preview again')
        if db.scalar(select(func.count(Order.id)).where(Order.tracking==o.tracking))!=1:
            raise HTTPException(409,f'Tracking {o.tracking} is ambiguous')
        if o.status=='RETURNED' and row['status']=='DELIVERED':
            raise HTTPException(409,'Return/delivery conflict')
        targets.append((o,row))
    updated=[];skipped=[]
    for o,row in targets:
        if o.status==row['status']:
            skipped.append(o.id);continue
        previous=o.status;o.status=row['status']
        db.add(TrackingEvent(order_id=o.id,tracking_number=o.tracking,
            previous_status=previous,new_status=o.status,source_type='FILE',
            note=row['note'],updated_by=user.id))
        updated.append(o.id)
    result={'updated_count':len(updated),'skipped_count':len(skipped),
            'updated_order_ids':updated,'skipped_order_ids':skipped}
    preview.confirmed_at=datetime.now(timezone.utc)
    db.add(TrackingConfirmation(idempotency_key=data.idempotency_key,
        preview_id=preview.id,created_by=user.id,result=json.dumps(result)))
    audit(db,user,'tracking.file.confirm',{'filename':payload['filename'],'result':result})
    db.commit()
    return result

@app.get('/tracking/file',response_class=HTMLResponse)
def tracking_file_ui():
    with open('tracking_file.html',encoding='utf-8') as f:return f.read()

# Additive operational workflow, Phase 3.
import sys as _sys
import workflow as _workflow
_workflow.install(app, _sys.modules[__name__])
import address_ai as _address_ai
_address_ai.install(app, _workflow)
import pancake_sync as _pancake_sync
_pancake_sync.install(app, _sys.modules[__name__], _workflow)
