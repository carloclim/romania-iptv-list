"""Fetch and apply official channel logos from Digi's public grid endpoint."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from models import Channel

_QUALITY_RE = re.compile(r"\b(?:hd|sd|4k|1080p|720p|576p|480p|360p|1080i|576i|540p)\b", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_IPTV_ORG_CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
_IPTV_ORG_CHANNELS_CSV_URL = "https://raw.githubusercontent.com/iptv-org/database/master/data/channels.csv"
_ANTENAPLAY_LIVE_URL = "https://antenaplay.ro/live"
_ALIAS_STOPWORDS = {"ro", "romania", "tv", "channel", "live"}
_WEAK_LOGO_HOSTS = ("imgur.com", "wikipedia.org", "wikimedia.org")



def _key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", value)
    value = _QUALITY_RE.sub(" ", value)
    return _NON_ALNUM.sub("", value)


def _key_aliases(value: str) -> list[str]:
    """Return normalized key aliases for fuzzy matching official logo maps."""
    raw = unicodedata.normalize("NFKD", value or "")
    raw = raw.encode("ascii", "ignore").decode("ascii").lower()
    raw = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", raw)
    raw = _QUALITY_RE.sub(" ", raw)

    aliases: list[str] = []

    def add(candidate: str) -> None:
        candidate = _NON_ALNUM.sub("", candidate or "")
        if candidate and candidate not in aliases:
            aliases.append(candidate)

    compact = _NON_ALNUM.sub("", raw)
    add(compact)

    for suffix in ("romania", "channel", "tv"):
        if compact.endswith(suffix) and len(compact) > len(suffix) + 2:
            add(compact[: -len(suffix)])

    # Many tvg-ids are country-suffixed (e.g. amcro, eurosportroro).
    trimmed = compact
    for _ in range(2):
        if trimmed.endswith("ro") and len(trimmed) > 4:
            trimmed = trimmed[:-2]
            add(trimmed)

    tokens = re.findall(r"[a-z0-9]+", raw)
    if tokens:
        add("".join(tokens))
        filtered = [t for t in tokens if t not in _ALIAS_STOPWORDS]
        if filtered:
            add("".join(filtered))
            no_single_digits = [t for t in filtered if not (len(t) == 1 and t.isdigit())]
            if no_single_digits:
                add("".join(no_single_digits))

    return aliases


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


class _ImageLogoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        attrs = dict(attrs_list)
        alt = (attrs.get("alt") or "").strip()
        src = (attrs.get("src") or "").strip()
        if alt and src:
            self.items.append((alt, src))


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


def fetch_iptv_org_logos(url: str = "", timeout: int = 20) -> dict[str, str]:
    """Return normalized channel id/name -> official logo URL from IPTV-org metadata."""
    source = url or _IPTV_ORG_CHANNELS_URL
    try:
        with httpx.Client(headers={"User-Agent": "romania-iptv-list/1.0"}) as client:
            response = client.get(source, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
            rows = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[logos] IPTV-org request failed: {exc}")
        return {}

    logos: dict[str, str] = {}
    if not isinstance(rows, list):
        return logos

    for row in rows:
        if not isinstance(row, dict):
            continue
        logo = (row.get("logo") or "").strip()
        if not logo:
            continue

        for candidate in (row.get("id") or "", row.get("name") or "", row.get("tvg_id") or ""):
            key = _key(candidate)
            if key and key not in logos:
                logos[key] = logo

    print(f"[logos] IPTV-org: parsed {len(logos)} official logos")
    return logos


def fetch_antenaplay_logos(url: str = "", timeout: int = 20) -> dict[str, str]:
    """Return normalized channel name -> official logo URL from AntenaPlay's live page."""
    source = url or _ANTENAPLAY_LIVE_URL
    try:
        with httpx.Client(headers={"User-Agent": "romania-iptv-list/1.0"}) as client:
            response = client.get(source, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[logos] AntenaPlay request failed: {exc}")
        return {}

    parser = _ImageLogoParser()
    parser.feed(response.text)

    logos: dict[str, str] = {}
    for alt, src in parser.items:
        # Keep channel-like assets only.
        if "assets.antenaplay.ro" not in src:
            continue
        key = _key(alt)
        if not key:
            continue
        logos.setdefault(key, src)

    print(f"[logos] AntenaPlay: parsed {len(logos)} official logos")
    return logos


def fetch_iptv_org_websites(url: str = "", timeout: int = 20) -> dict[str, str]:
    """Return normalized id/name -> official channel website URL from IPTV-org CSV."""
    source = url or _IPTV_ORG_CHANNELS_CSV_URL
    try:
        with httpx.Client(headers={"User-Agent": "romania-iptv-list/1.0"}) as client:
            response = client.get(source, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
            text = response.text
    except httpx.HTTPError as exc:
        print(f"[logos] IPTV-org websites request failed: {exc}")
        return {}

    websites: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        website = (row.get("website") or "").strip()
        if not website:
            continue
        for candidate in ((row.get("id") or "").strip(), (row.get("name") or "").strip()):
            key = _key(candidate)
            if key and key not in websites:
                websites[key] = website

    print(f"[logos] IPTV-org websites: parsed {len(websites)} channels")
    return websites


def apply_website_fallback_logos(channels: list[Channel], websites: dict[str, str]) -> int:
    """Fallback: for empty logos, use website favicon derived from matched channel website."""
    applied = 0
    for channel in channels:
        if channel.tvg_logo:
            continue

        website = ""
        for value in (channel.tvg_id, channel.name, channel.tvg_name):
            for alias in _key_aliases(value):
                website = websites.get(alias, "")
                if website:
                    break
            if website:
                break

        if website:
            parsed = urlparse(website)
            host = parsed.netloc or parsed.path
            host = host.strip("/")
            if host:
                channel.tvg_logo = f"https://{host}/favicon.ico"
                applied += 1
                continue
            channel.tvg_logo = website.rstrip("/") + "/favicon.ico"
            applied += 1
    return applied


def apply_digi_logos(channels: list[Channel], logos: dict[str, str]) -> int:
    """Fill or improve channel logos from normalized official logo maps."""
    applied = 0
    for channel in channels:
        current_logo = (channel.tvg_logo or "").lower()
        should_replace = (not channel.tvg_logo) or any(host in current_logo for host in _WEAK_LOGO_HOSTS)
        if not should_replace:
            continue
        logo = ""
        for value in (channel.name, channel.tvg_name, channel.tvg_id):
            for alias in _key_aliases(value):
                logo = logos.get(alias, "")
                if logo:
                    break
            if logo:
                break
        if logo:
            channel.tvg_logo = logo
            applied += 1
    return applied
