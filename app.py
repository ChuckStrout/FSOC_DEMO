from __future__ import annotations

import subprocess
import sys
from pathlib import Path


if __name__ == "__main__":
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(Path(__file__).with_name("streamlit_app.py"))],
        check=False,
    )
