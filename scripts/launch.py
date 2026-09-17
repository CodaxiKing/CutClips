"""Start both supported repository layouts without shadowing stdlib select."""
import select  # Load the native module before adding the project directory.
import os
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
if not (root / "clipforge").is_dir():
    sys.path.insert(0, str(root.parent))
os.environ.setdefault("CLIPFORGE_STORAGE", str(root / "storage"))

if sys.argv[1] == "api":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=int(sys.argv[2]), access_log=False)
else:
    runpy.run_module("api.worker", run_name="__main__")
