"""M3U parser: turns playlist text into Channel objects."""
from __future__ import annotations

import re

from models import Channel, detect_resolution

_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')

# EXTVLCOPT option name -> HTTP header name
_HEADER_MAP = {
    "http-referrer": "Referer",
    "http-user-agent": "User-Agent",
    "http-origin": "Origin",
}


def _split_extinf(line: str) -> tuple[str, str]:
    """Split an #EXTINF line into (attribute part, display title)."""
    body = line[len("#EXTINF:"):]
    in_quote = False
    for i, ch in enumerate(body):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            return body[:i], body[i + 1:].strip()
    return body, ""


def parse_header(text: str) -> dict:
    """Return the attributes on the #EXTM3U header line."""
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith("#EXTM3U"):
            return dict(_ATTR_RE.findall(s))
    return {}


def parse(text: str, source: str = "", priority: int = 100) -> list[Channel]:
    """Parse M3U text into a list of Channel objects."""
    channels: list[Channel] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        s = lines[i].strip()
        if not s.startswith("#EXTINF:"):
            i += 1
            continue

        attr_part, name = _split_extinf(s)
        attrs = dict(_ATTR_RE.findall(attr_part))
        group = attrs.get("group-title", "")
        headers: dict = {}

        # Scan forward for EXTVLCOPT / EXTGRP directives and the stream URL.
        j = i + 1
        url = ""
        while j < n:
            t = lines[j].strip()
            if not t:
                j += 1
                continue
            if t.startswith("#EXTVLCOPT:"):
                opt = t[len("#EXTVLCOPT:"):]
                if "=" in opt:
                    k, v = opt.split("=", 1)
                    hk = _HEADER_MAP.get(k.strip().lower())
                    if hk:
                        headers[hk] = v.strip()
                j += 1
                continue
            if t.startswith("#EXTGRP:"):
                group = t[len("#EXTGRP:"):].strip() or group
                j += 1
                continue
            if t.startswith("#"):
                j += 1
                continue
            url = t
            break

        if url:
            channels.append(
                Channel(
                    name=name,
                    url=url,
                    tvg_id=attrs.get("tvg-id", ""),
                    tvg_name=attrs.get("tvg-name", ""),
                    tvg_logo=attrs.get("tvg-logo", ""),
                    group=group,
                    headers=headers,
                    attrs={
                        k: v
                        for k, v in attrs.items()
                        if k not in {"tvg-id", "tvg-name", "tvg-logo", "group-title"}
                    },
                    source=source,
                    priority=priority,
                    resolution=detect_resolution(name, attrs.get("tvg-name", "")),
                )
            )
        i = j + 1

    return channels
