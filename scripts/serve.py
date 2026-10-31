"""Start the loopback service and open its bundled interface."""
from pathlib import Path
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main():
    import uvicorn
    port = int(os.environ.get("VEIL_PORT", "8010"))
    if not 1024 <= port <= 65535:
        raise SystemExit("VEIL_PORT must be between 1024 and 65535.")
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(f"Port {port} is in use. Close the other Veil window, or run VEIL_PORT={port+1} ./start.sh")
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Your private workspace: {url}\n  Keep this window open. Press Control+C to stop and delete temporary working copies.\n", flush=True)
    if "--no-browser" not in sys.argv:
        def open_when_ready():
            for _ in range(60):
                try:
                    with urllib.request.urlopen(f"{url}/api/health", timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except OSError:
                    time.sleep(0.25)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port,
                access_log=False, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
