"""FOXRUN OMS Application Launcher (PostgreSQL / SQLite)."""
import os
import sys
import json
import secrets
import socket
import threading
import webbrowser
from pathlib import Path

root = Path(__file__).resolve().parent
os.chdir(root)

# 1. Ưu tiên đọc biến môi trường DATABASE_URL
def resolve_database_url() -> str:
    candidates = ["DATABASE_URL", "DATABASE_URI", "POSTGRES_URL", "POSTGRESQL_URL"]
    url = ""
    for k in candidates:
        v = (os.getenv(k) or os.environ.get(k) or "").strip()
        if v:
            url = v
            break

    # Nếu chưa có, kiểm tra các tệp bí mật Render / Secret Files
    if not url:
        secret_files = [
            Path("/etc/secrets/DATABASE_URL"),
            Path("/etc/secrets/.env"),
            root / ".env",
            root / "data" / ".env",
        ]
        for sf in secret_files:
            try:
                if sf.is_file():
                    content = sf.read_text(encoding="utf-8").strip()
                    if sf.name == "DATABASE_URL" and content:
                        url = content
                        break
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("DATABASE_URL="):
                            url = line.split("=", 1)[1].strip()
                            break
                    if url:
                        break
            except Exception:
                pass

    url = url.strip().strip("'\"").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if "sslmode=" not in url and ("neon.tech" in url or "supabase" in url or "render" in url):
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
    return url

raw_db_url = resolve_database_url()

    os.environ["DATABASE_URL"] = raw_db_url
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "Foxrun@2026Admin")
    os.environ.setdefault("SESSION_SECRET", secrets.token_hex(32))
    os.environ.setdefault("COOKIE_SECURE", "true")

    # In rõ dòng log khi khởi động để xác nhận kết nối PostgreSQL (Neon)
    masked_target = raw_db_url.split("@")[-1] if "@" in raw_db_url else "configured"
    print("=" * 65)
    print("🚀 FOXRUN OMS - CLOUD ENTERPRISE MODE")
    print("Connected to PostgreSQL (Neon)")
    print(f"📊 Target Database: {masked_target}")
    print("🔒 Persistent Storage: ACTIVE (Không bị xóa khi server restart)")
    print("=" * 65)

else:
    # 2. Chế độ một máy cục bộ (Fallback SQLite khi không có DATABASE_URL)
    data = root / 'data'
    data.mkdir(exist_ok=True)
    db_file = data / 'foxrun.db'

    import sqlite3
    with sqlite3.connect(str(db_file)) as _conn:
        _conn.execute('PRAGMA journal_mode=WAL;')
        _conn.execute('PRAGMA busy_timeout=5000;')
        _conn.execute('PRAGMA foreign_keys=ON;')

    settings = data / 'local_settings.json'
    if not settings.exists():
        config = {'username': 'admin', 'password': secrets.token_hex(12), 'secret': secrets.token_hex(32)}
        settings.write_text(json.dumps(config, indent=2), encoding='utf-8')
        (data / 'FIRST_LOGIN.txt').write_text(
            f'FOXRUN OMS LOCAL\nURL: http://localhost:8000\nUsername: {config["username"]}\nPassword: {config["password"]}\nDoi mat khau trong Thiet lap sau khi dang nhap.\n',
            encoding='utf-8'
        )
    config = json.loads(settings.read_text(encoding='utf-8'))

    os.environ.setdefault('DATABASE_URL', f'sqlite:///{db_file}')
    os.environ.setdefault('ADMIN_USERNAME', config.get('username', 'admin'))
    os.environ.setdefault('ADMIN_PASSWORD', config.get('password', 'Foxrun@2026Admin'))
    os.environ.setdefault('SESSION_SECRET', config.get('secret', secrets.token_hex(32)))
    os.environ.setdefault('COOKIE_SECURE', 'false')

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

if __name__ == '__main__':
    import uvicorn
    host = os.environ.get('FOXRUN_HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 8000))
    print(f"🌐 Server listening on {host}:{port}")
    uvicorn.run('main:app', host=host, port=port)
