"""Streamlit entry point for the reconciliation demonstration."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from reconciliation_agent.ui import main  # noqa: E402

if __name__ == "__main__":
    main()
