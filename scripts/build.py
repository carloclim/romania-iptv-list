"""Build the merged Romania IPTV playlist.

Pipeline: load config -> parse curated -> fetch+parse remote sources ->
apply blocklist -> dedupe/rank -> apply overrides -> (optionally validate + auto-heal)
-> order by group -> write playlists.

Run:
    python scripts/build.py                # full build with validation + auto-heal
    python scripts/build.py --no-validate  # fast, dedupe only (writes dist/romania.m3u)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import fetch as fetcher
import merge as merger
import parse as m3u_parse
from digi_logos import apply_digi_logos, fetch_digi_logos
from epg import build_x_tvg_url
from models import Channel
from validate import validate_all

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
DATA_DIR = ROOT / "data"

_REV_HEADER = {"Referer": "http-referrer", "User-Agent": "http-user-agent", "Origin": "http-origin"}

# Category shown at the bottom for streams that did not respond during validation.
TEST_GROUP = "Posibil Geo-blocat (de testat)"


# --------------------------------------------------------------------------- IO
def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _load_blocklist(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _load_excludes(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {ln.strip().lower() for ln in lines if ln.strip() and not ln.strip().startswith("#")}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


# --------------------------------------------------------------------- assembly
def collect_channels(remotes: list[dict]) -> tuple[list[Channel], list[str]]:
    """Parse curated + all enabled remote sources into one channel list."""
    channels: list[Channel] = []
    used: list[str] = []

    curated_text = (SOURCES_DIR / "curated.m3u").read_text(encoding="utf-8")
    curated = m3u_parse.parse(curated_text, source="curated", priority=0)
    if curated:
        channels.extend(curated)
        used.append("curated")

    fetched = asyncio.run(fetcher.fetch_all(remotes))
    prio = {s["id"]: s.get("priority", 100) for s in remotes}
    for sid, text in fetched.items():
        parsed = m3u_parse.parse(text, source=sid, priority=prio.get(sid, 100))
        if parsed:
            channels.extend(parsed)
            used.append(sid)

    return channels, used


def apply_overrides(chosen: list[Channel], overrides: list[dict]) -> list[Channel]:
    by_key = {c.key: c for c in chosen}
    for ov in overrides:
        if not ov.get("enabled", True):
            continue
        key = (ov.get("key") or "").strip().lower()
        url = (ov.get("url") or "").strip()
        if not key or not url:
            continue
        headers = {k: v for k, v in (ov.get("headers") or {}).items() if v}
        if key in by_key:
            ch = by_key[key]
            ch.url = url
            if headers:
                ch.headers = headers
            if ov.get("name"):
                ch.name = ov["name"]
            if ov.get("tvg_logo"):
                ch.tvg_logo = ov["tvg_logo"]
            if ov.get("group"):
                ch.group = ov["group"]
        else:
            ch = Channel(
                name=ov.get("name", key),
                url=url,
                tvg_id=ov.get("tvg_id", ""),
                tvg_logo=ov.get("tvg_logo", ""),
                group=ov.get("group", ""),
                headers=headers,
            )
            chosen.append(ch)
            by_key[key] = ch
        ch.source = "override"
        ch.priority = -100
        ch.status = "unknown"
    return chosen


def order_channels(channels: list[Channel], group_order: list[str]) -> list[Channel]:
    idx = {g: i for i, g in enumerate(group_order)}
    return sorted(channels, key=lambda c: (idx.get(c.group, len(group_order)), c.group.lower()))


def categorize(
    channels: list[Channel], group_order: list[str], rules: dict, default: str
) -> list[Channel]:
    """Assign a real category to channels whose source group is not already a known one."""
    known = set(group_order)
    for ch in channels:
        if ch.group in known:
            continue
        haystack = f"{ch.name} {ch.tvg_id}".lower()
        assigned = default
        for cat, keywords in rules.items():
            if any(k in haystack for k in keywords):
                assigned = cat
                break
        ch.group = assigned
    return channels


# ------------------------------------------------------------------ validation
async def validate_and_heal(
    chosen: list[Channel], cand_map: dict[str, list[Channel]], timeout: int, concurrency: int
) -> list[Channel]:
    await validate_all(chosen, timeout, concurrency)

    by_key = {c.key: c for c in chosen}
    alternates: list[Channel] = []
    for key, ch in by_key.items():
        if ch.status == "working" or ch.source == "override":
            continue
        alternates.extend(a for a in cand_map.get(key, []) if a.url != ch.url)

    if alternates:
        await validate_all(alternates, timeout, concurrency)
        healed: dict[str, Channel] = {}
        for a in alternates:
            if a.status == "working" and a.key not in healed:
                healed[a.key] = a
        for key, alt in healed.items():
            current = by_key[key]
            if current.status != "working" and current.source != "override":
                alt.group = current.group or alt.group
                alt.tvg_logo = current.tvg_logo or alt.tvg_logo
                chosen[chosen.index(current)] = alt
                by_key[key] = alt

    return chosen


# --------------------------------------------------------------------- rendering
def format_channel(ch: Channel) -> str:
    tvg_name = ch.tvg_name or ch.name
    out = [
        f'#EXTINF:-1 tvg-id="{ch.tvg_id}" tvg-name="{tvg_name}" '
        f'tvg-logo="{ch.tvg_logo}" group-title="{ch.group}",{ch.name}'
    ]
    for header_name, opt in _REV_HEADER.items():
        val = ch.headers.get(header_name)
        if val:
            out.append(f"#EXTVLCOPT:{opt}={val}")
    out.append(ch.url)
    return "\n".join(out)


def build_header(config: dict) -> str:
    parts = ["#EXTM3U"]
    x = build_x_tvg_url(config.get("epg_urls", []))
    if x:
        parts.append(f'x-tvg-url="{x}"')
    extra = (config.get("header_extra") or "").strip()
    if extra:
        parts.append(extra)
    return " ".join(parts)


def render(channels: list[Channel], config: dict, sources_used: list[str]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        build_header(config),
        f"## {config.get('title', 'Lista IPTV Romania')}",
        f"## Generat automat: {ts}  |  Canale: {len(channels)}",
        f"## Surse: {', '.join(sources_used)}",
        "## Nu se stocheaza fisiere video; doar linkuri publice. Vezi README (nota legala).",
    ]
    _emit_groups(lines, channels)
    return "\n".join(lines).rstrip() + "\n"


def _emit_groups(lines: list[str], channels: list[Channel]) -> None:
    current_group = object()
    for ch in channels:
        if ch.group != current_group:
            current_group = ch.group
            lines += [
                "",
                "#############################################",
                f"## {ch.group or 'Altele'}",
                "#############################################",
            ]
        lines.append(format_channel(ch))
        lines.append("")


def render_main(
    working: list[Channel], testing: list[Channel], config: dict, sources_used: list[str]
) -> str:
    """Working channels grouped by category first, then a labeled 'needs testing' section."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        build_header(config),
        f"## {config.get('title', 'Lista IPTV Romania')}",
        f"## Generat automat: {ts}  |  Functionale: {len(working)}  |  De testat: {len(testing)}",
        f"## Surse: {', '.join(sources_used)}",
        "## Nu se stocheaza fisiere video; doar linkuri publice. Vezi README (nota legala).",
    ]
    _emit_groups(lines, working)
    if testing:
        lines += [
            "",
            "#############################################",
            "## ----- Posibil Geo-blocat / nu raspunde - necesita testare -----",
            "#############################################",
        ]
        _emit_groups(lines, testing)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Build merged Romania IPTV playlist")
    ap.add_argument("--no-validate", action="store_true", help="skip stream health-checks")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output directory")
    args = ap.parse_args()

    config = _load_json(DATA_DIR / "config.json", {})
    remotes = _load_json(SOURCES_DIR / "remotes.json", {}).get("sources", [])
    overrides = _load_json(DATA_DIR / "overrides.json", {}).get("overrides", [])
    blocklist = _load_blocklist(DATA_DIR / "blocklist.txt")
    excludes = _load_excludes(DATA_DIR / "excludes.txt")
    group_order = config.get("group_order", [])
    vcfg = config.get("validate", {})
    out_dir = Path(args.out)

    print("Collecting channels...")
    channels, sources_used = collect_channels(remotes)
    print(f"  parsed {len(channels)} entries from: {', '.join(sources_used)}")

    channels = merger.apply_blocklist(channels, blocklist)
    channels = [channel for channel in channels if channel.key not in excludes]
    chosen, cand_map = merger.dedupe(channels)
    chosen = apply_overrides(chosen, overrides)
    cat_cfg = _load_json(DATA_DIR / "categories.json", {})
    chosen = categorize(chosen, group_order, cat_cfg.get("rules", {}), cat_cfg.get("default", "Generale"))
    digi_logos = fetch_digi_logos(config.get("digi_logo_url", ""))
    applied_logos = apply_digi_logos(chosen, digi_logos)
    print(f"  applied {applied_logos} Digi logos")
    print(f"  {len(chosen)} unique channels after dedupe/overrides")

    if args.no_validate:
        ordered = order_channels(chosen, group_order)
        used = sorted(set(c.source for c in ordered if c.source))
        _write(out_dir / "romania.m3u", render(ordered, config, used))
        print(f"  wrote {out_dir / 'romania.m3u'} ({len(ordered)} channels)")
        print("Skipping validation (--no-validate).")
        return 0

    print("Validating streams (this can take a minute)...")
    asyncio.run(
        validate_and_heal(
            chosen,
            cand_map,
            timeout=vcfg.get("timeout", 12),
            concurrency=vcfg.get("concurrency", 20),
        )
    )

    counts: dict[str, int] = {}
    for c in chosen:
        counts[c.status] = counts.get(c.status, 0) + 1
    print("  status:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    # Per-channel status report (status, group, name, source, resolution, url).
    status_rows = sorted(
        ((c.status, c.group or "Altele", c.name, c.source, c.resolution, c.url) for c in chosen),
        key=lambda r: (r[0], r[1], r[2].lower()),
    )
    status_path = out_dir / "status.csv"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with status_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["status", "group", "name", "source", "resolution", "url"])
        writer.writerows(status_rows)
    print(f"  wrote {status_path}")

    # Working channels keep their real categories and lead the list. Everything else is
    # parked in a single "needs testing" category at the bottom (some may be geo/token/
    # intermittent rather than truly dead), so the app shows it as its own group last.
    working = order_channels([c for c in chosen if c.status == "working"], group_order)
    testing = sorted(
        [c for c in chosen if c.status != "working"], key=lambda c: c.name.lower()
    )
    for c in testing:
        c.group = TEST_GROUP

    used = sorted(set(c.source for c in chosen if c.source))
    main_text = render_main(working, testing, config, used)

    _write(out_dir / "romania.m3u", main_text)
    print(f"  wrote {out_dir / 'romania.m3u'} ({len(working)} working + {len(testing)} to test)")

    root_file = config.get("output_root_file", "romania_iptv_preferate.m3u")
    _write(ROOT / root_file, main_text)
    print(f"  wrote {ROOT / root_file}")

    # Strictly-working subset (working categories only, no test section).
    _write(out_dir / "romania_working.m3u", render(working, config, used))
    print(f"  wrote {out_dir / 'romania_working.m3u'} ({len(working)} working)")

    # Per-category playlists (working categories + the testing bucket).
    groups: dict[str, list[Channel]] = {}
    for c in working + testing:
        groups.setdefault(c.group or "Altele", []).append(c)
    for group, chans in groups.items():
        safe = group.lower().replace(" ", "-").replace("/", "-").replace("(", "").replace(")", "")
        _write(out_dir / "by-group" / f"{safe}.m3u", render(chans, config, used))
    print(f"  wrote {len(groups)} per-category playlists to {out_dir / 'by-group'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
