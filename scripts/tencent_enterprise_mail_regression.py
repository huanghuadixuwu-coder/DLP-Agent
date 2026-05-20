from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.mail_dlp_full_regression import main


if __name__ == "__main__":
    raise SystemExit(main())
