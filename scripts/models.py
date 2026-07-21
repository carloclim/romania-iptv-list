"""Data model and normalization helpers for channels."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_QUALITY_RE = re.compile(r"(\d{3,4})[pi]\b")
_NONALNUM = re.compile(r"[^a-z0-9]+")
_ID_SUFFIX = re.compile(r"@\w+$")


def canonical_id(tvg_id: str) -> str:
    """Normalize a tvg-id for cross-source matching (e.g. 'PROTV.ro@SD' -> 'protv.ro')."""
    if not tvg_id:
        return ""
    val = tvg_id.strip().lower()
    val = _ID_SUFFIX.sub("", val)
    return val


def canonical_name(name: str) -> str:
    """Normalize a display name for matching when no tvg-id is present."""
    if not name:
        return ""
    val = name.lower()
    val = re.sub(r"\(.*?\)", "", val)  # drop "(1080p)"
    val = re.sub(r"\[.*?\]", "", val)  # drop "[Not 24/7]"
    return _NONALNUM.sub("", val)


def detect_resolution(*texts: str) -> int:
    """Best vertical resolution found in any of the given strings, else 0."""
    best = 0
    for t in texts:
        if not t:
            continue
        for m in _QUALITY_RE.finditer(t):
            best = max(best, int(m.group(1)))
    return best


@dataclass
class Channel:
    name: str
    url: str
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    group: str = ""
    headers: dict = field(default_factory=dict)
    attrs: dict = field(default_factory=dict)
    source: str = ""
    priority: int = 100
    status: str = "unknown"  # working | dead | geo | timeout | unknown
    resolution: int = 0

    @property
    def key(self) -> str:
        return canonical_id(self.tvg_id) or canonical_name(self.name)
