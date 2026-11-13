"""Single-computer launcher using SQLite. No Docker is required."""
import json
import os
import secrets
import sqlite3
import threading
import webbrowser
from pathlib import Path

root=Path(__file__).resolve().parent
os.chdir(root)
data=root/'data'
data.mkdir(exist_ok=True)
db_file=data/'foxrun.db'
with sqlite3.connect(str(db_file)) as _conn:
    _conn.execute('PRAGMA journal_mode=WAL;')
    _conn.execute('PRAGMA busy_timeout=5000;')
    _conn.execute('PRAGMA foreign_keys=ON;')
settings=data/'local_settings.json'
if not settings.exists():
    config={'username':'admin','password':secrets.token_hex(12),'secret':secrets.token_hex(32)}
    settings.write_text(json.dumps(config,indent=2),encoding='utf-8')
    (data/'FIRST_LOGIN.txt').write_text('FOXRUN OMS LOCAL\nURL: http://localhost:8000\nUsername: '+config['username']+'\nPassword: '+config['password']+'\nDoi mat khau trong Thiet lap sau khi dang nhap.\n',encoding='utf-8')
config=json.loads(settings.read_text(encoding='utf-8'))
os.environ.setdefault('DATABASE_URL','sqlite:///'+str(db_file))
os.environ.setdefault('ADMIN_USERNAME',config['username'])
os.environ.setdefault('ADMIN_PASSWORD',config['password'])
os.environ.setdefault('SESSION_SECRET',config['secret'])
os.environ.setdefault('COOKIE_SECURE','false')
# Optional local Gemini key/model may be set in the process environment or local settings.
for key in ['GEMINI_API_KEY','GEMINI_MODEL']:
    if config.get(key):os.environ.setdefault(key,config[key])
import socket
def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

lan_ip = get_lan_ip()
print('FOXRUN OMS LOCAL:          http://localhost:8000')
print(f'FOXRUN OMS MẠNG LAN/WI-FI: http://{lan_ip}:8000')
print('Tai khoan khoi tao: data/FIRST_LOGIN.txt. Du lieu: data/foxrun.db')
print('Ban SQLite mot may - ho tro moi nguoi trong mang LAN ket noi dung chung.')

def open_when_ready():
    import time
    import urllib.request
    for _ in range(45):
        try:
            with urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=1):pass
            webbrowser.open('http://localhost:8000');return
        except Exception:time.sleep(1)
if __name__=='__main__':
    import uvicorn
    threading.Thread(target=open_when_ready,daemon=True).start()
    host = os.environ.get('FOXRUN_HOST', '0.0.0.0')
    uvicorn.run('main:app',host=host,port=8000)
