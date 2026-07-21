"""Fetch and apply official channel logos from Digi's public grid endpoint."""
from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

import httpx

from models import Channel

_QUALITY_RE = re.compile(r"\b(?:hd|sd|4k|1080p|720p|576p|480p|360p|1080i|576i|540p)\b", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", value)
    value = _QUALITY_RE.sub(" ", value)
    return _NON_ALNUM.sub("", value)


class _DigiLogoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None
        self._div_depth = 0

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        if tag == "div":
            classes = (attrs.get("class") or "").split()
            if self._row is None and "table-row" in classes and attrs.get("data-channel-name"):
                self._row = {
                    "name": (attrs.get("data-channel-name") or "").strip(),
                    "category": (attrs.get("data-category") or "").strip(),
                    "logo": "",
                }
                self._div_depth = 1
            elif self._row is not None:
                self._div_depth += 1
        elif tag == "img" and self._row is not None and not self._row["logo"]:
            self._row["logo"] = (attrs.get("src") or "").strip()

    def handle_startendtag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs_list)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._row is not None:
            self._div_depth -= 1
            if self._div_depth <= 0:
                if self._row["name"] and self._row["logo"]:
                    self.rows.append(self._row)
                self._row = None
                self._div_depth = 0


def fetch_digi_logos(url: str, timeout: int = 20) -> dict[str, str]:
    """Return normalized channel name -> official Digi logo URL."""
    if not url:
        return {}
    try:
        with httpx.Client(headers={"User-Agent": "romania-iptv-list/1.0"}) as client:
            response = client.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[logos] Digi request failed: {exc}")
        return {}

    parser = _DigiLogoParser()
    parser.feed(response.text)
    logos: dict[str, str] = {}
    for row in parser.rows:
        key = _key(row["name"])
        if not key:
            continue
        # Prefer the HD record when both SD and HD rows use the same logo key.
        if key not in logos or "hd" in row["name"].lower():
            logos[key] = row["logo"]
    print(f"[logos] Digi: parsed {len(logos)} official logos")
    return logos


def apply_digi_logos(channels: list[Channel], logos: dict[str, str]) -> int:
    """Fill empty channel logos from Digi's normalized logo map."""
    applied = 0
    for channel in channels:
        if channel.tvg_logo:
            continue
        candidates = [channel.name, channel.tvg_name, channel.tvg_id]
        logo = next((logos.get(_key(value), "") for value in candidates if _key(value)), "")
        if logo:
            channel.tvg_logo = logo
            applied += 1
    return applied
