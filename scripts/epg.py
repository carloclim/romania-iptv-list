"""EPG (XMLTV) helpers."""
from __future__ import annotations


def build_x_tvg_url(epg_urls: list[str]) -> str:
    """Join configured EPG guide URLs into a comma-separated x-tvg-url value.

    Most IPTV players (incl. those accepting an M3U URL) support a comma-separated
    list of XMLTV guide URLs in the #EXTM3U header.
    """
    return ",".join(u.strip() for u in epg_urls if u and u.strip())
