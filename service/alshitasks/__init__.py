"""Alshival.Ai per-user service."""

# Private, packaged logging codecs; no runtime downloads or global pip installs.
import sys
from pathlib import Path
_vendor = Path(__file__).resolve().parents[1] / "_vendor"
_versioned = _vendor / f"{sys.version_info.major}.{sys.version_info.minor}"
if _versioned.is_dir():
    _vendor = _versioned
if _vendor.is_dir():
    sys.path.insert(0, str(_vendor))
