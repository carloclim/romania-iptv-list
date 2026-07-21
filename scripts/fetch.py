"""Async download of remote M3U sources."""
from __future__ import annotations

import asyncio

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


async def _fetch_one(client: httpx.AsyncClient, src: dict) -> tuple[str, str | None]:
    sid = src.get("id", "?")
    url = src.get("url", "")
    if not url:
        print(f"[fetch] {sid}: no url configured, skipping")
        return sid, None
    try:
        r = await client.get(url, timeout=30, follow_redirects=True)
        if r.status_code == 200 and r.text.strip():
            print(f"[fetch] {sid}: OK ({len(r.text)} bytes)")
            return sid, r.text
        print(f"[fetch] {sid}: HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001 - tolerant by design
        print(f"[fetch] {sid}: {exc}")
    return sid, None


async def fetch_all(sources: list[dict]) -> dict[str, str]:
    """Download every enabled source. Returns {source_id: text}."""
    enabled = [s for s in sources if s.get("enabled")]
    out: dict[str, str] = {}
    if not enabled:
        return out
    async with httpx.AsyncClient(headers={"User-Agent": DEFAULT_UA}) as client:
        results = await asyncio.gather(*[_fetch_one(client, s) for s in enabled])
    for sid, text in results:
        if text:
            out[sid] = text
    return out
