"""Import shim for src-layout execution without editable install."""

from __future__ import annotations

import sys
from pathlib import Path
from pkgutil import extend_path

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if _SRC_DIR.exists():
    src_text = str(_SRC_DIR)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)

__path__ = extend_path(__path__, __name__)  # type: ignore[name-defined]
