"""Explicit one-time model setup; this script never receives document content."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.model_manager import manager

print("Installing the checksum-verified Stanford clinical model (~440 MB)…", flush=True)
result = manager.install()
if result["state"] != "ready":
    raise SystemExit(result["error"])
print(f"Ready. Inference will run locally on {result['device']}.")
