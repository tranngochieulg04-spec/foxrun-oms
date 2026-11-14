import subprocess
import sys
import time
import urllib.request
import sqlite3
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    python_exe = root / '.venv' / 'Scripts' / 'python.exe'
    if not python_exe.exists():
        python_exe = sys.executable

    print(f"Starting run_local.py with {python_exe}...")
    proc = subprocess.Popen(
        [str(python_exe), "run_local.py"],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    health_url = "http://127.0.0.1:8000/api/v1/health"
    root_url = "http://127.0.0.1:8000/"
    health_ok = False

    try:
        print("Waiting for health endpoint...")
        for i in range(30):
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                print("Server terminated prematurely!")
                print("STDOUT:", stdout)
                print("STDERR:", stderr)
                sys.exit(1)
            try:
                with urllib.request.urlopen(health_url, timeout=1) as resp:
                    if resp.status == 200:
                        body = resp.read().decode('utf-8')
                        print(f"Health endpoint OK: {resp.status} - {body}")
                        health_ok = True
                        break
            except Exception:
                time.sleep(1)

        if not health_ok:
            print("Failed to reach health endpoint within timeout!")
            sys.exit(1)

        # Check root URL
        with urllib.request.urlopen(root_url, timeout=2) as resp:
            print(f"Root endpoint OK: {resp.status}")
            assert resp.status == 200, f"Expected 200 at root, got {resp.status}"

        # Confirm data/foxrun.db journal mode is wal
        db_path = root / "data" / "foxrun.db"
        assert db_path.exists(), f"Database file not found: {db_path}"

        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0]
            cursor.execute("PRAGMA busy_timeout;")
            busy_timeout = cursor.fetchone()[0]
            cursor.close()

        print(f"SQLite PRAGMA journal_mode: {journal_mode}")
        print(f"SQLite PRAGMA busy_timeout: {busy_timeout}")
        assert journal_mode.lower() == "wal", f"Expected WAL mode, got {journal_mode}"

        print("All server and database verifications passed successfully!")

    finally:
        print("Terminating server process...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print("Server terminated cleanly.")

if __name__ == "__main__":
    main()
