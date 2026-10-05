import tempfile
import time
from pathlib import Path

HEARTBEAT = Path(tempfile.gettempdir()) / "cluecdc-worker-health"


if __name__ == "__main__":
    raise SystemExit(
        0 if HEARTBEAT.is_file() and time.time() - HEARTBEAT.stat().st_mtime < 30 else 1
    )
