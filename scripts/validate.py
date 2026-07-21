"""Async stream health-checking and status classification."""
from __future__ import annotations

import asyncio

import httpx

from models import Channel

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

_HLS_MARKERS = ("#EXTM3U", "#EXT-X", "#EXTINF")


def _classify_http(status_code: int) -> str:
    # 401/403/451 usually means geo-restriction or token/auth, not a dead link.
    if status_code in (401, 403, 451):
        return "geo"
    return "dead"


def _looks_like_hls(url: str, content_type: str) -> bool:
    path = url.lower().split("?", 1)[0]
    return path.endswith(".m3u8") or "mpegurl" in content_type.lower()


async def _check(client: httpx.AsyncClient, ch: Channel, timeout: int, sem: asyncio.Semaphore) -> Channel:
    headers = dict(ch.headers)
    headers.setdefault("User-Agent", DEFAULT_UA)
    async with sem:
        try:
            async with client.stream(
                "GET", ch.url, headers=headers, timeout=timeout, follow_redirects=True
            ) as r:
                if r.status_code != 200:
                    ch.status = _classify_http(r.status_code)
                    return ch
                content_type = r.headers.get("content-type", "")
                chunk = b""
                async for part in r.aiter_bytes():
                    chunk += part
                    if len(chunk) >= 4096:
                        break
                if _looks_like_hls(ch.url, content_type):
                    body = chunk.decode("utf-8", "ignore")
                    ch.status = "working" if any(m in body for m in _HLS_MARKERS) else "dead"
                else:
                    # Raw TS / progressive stream: any data with HTTP 200 is good enough.
                    ch.status = "working" if chunk else "dead"
        except httpx.TimeoutException:
            ch.status = "timeout"
        except Exception:  # noqa: BLE001 - any transport error => dead
            ch.status = "dead"
    return ch


async def validate_all(
    channels: list[Channel], timeout: int = 12, concurrency: int = 20
) -> list[Channel]:
    """Check every channel and set its .status in place."""
    if not channels:
        return channels
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(verify=True) as client:
        await asyncio.gather(*[_check(client, ch, timeout, sem) for ch in channels])
    return channels
