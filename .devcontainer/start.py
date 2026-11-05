"""Start a persistent preview when the Codespace starts; no packages required."""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def ready(port):
    try:
        # Loopback health checks must not use an inherited HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/api/catalog', timeout=1) as response:
            data = json.load(response)
            return 'departments' in data and 'indicators_by_department' in data
    except (OSError, ValueError, urllib.error.URLError):
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if ready(args.port):
        print(f'성과관리 프로그램 실행 중: 포트 {args.port}')
        return
    data_dir = ROOT / 'data'
    data_dir.mkdir(exist_ok=True)
    log_path = data_dir / 'codespaces.log'
    with log_path.open('ab') as log:
        process = subprocess.Popen(
            [sys.executable, '-u', str(ROOT / 'app.py'), '--host', '0.0.0.0', '--port', str(args.port)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(40):
        if process.poll() is not None:
            raise SystemExit(f'시작 실패. 로그 확인: {log_path}')
        if ready(args.port):
            (data_dir / 'codespaces.pid').write_text(str(process.pid))
            print(f'성과관리 프로그램 준비 완료. PORTS에서 {args.port} 포트의 브라우저 열기를 선택하세요.')
            return
        time.sleep(0.25)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    raise SystemExit(f'시작 시간 초과. 로그 확인: {log_path}')

if __name__ == '__main__':
    main()
