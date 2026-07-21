"""Merging, de-duplication and candidate ranking."""
from __future__ import annotations

from collections import defaultdict

from models import Channel


def apply_blocklist(channels: list[Channel], patterns: list[str]) -> list[Channel]:
    """Drop channels whose URL contains any blocked substring."""
    active = [p for p in patterns if p]
    if not active:
        return channels
    return [ch for ch in channels if not any(p in ch.url for p in active)]


def group_candidates(channels: list[Channel]) -> dict[str, list[Channel]]:
    """Group channels by their canonical key, preserving insertion order."""
    groups: dict[str, list[Channel]] = defaultdict(list)
    for ch in channels:
        if not ch.key:
            continue
        groups[ch.key].append(ch)
    return groups


def _rank_key(ch: Channel) -> tuple[int, int]:
    # Lower priority number = more trusted; higher resolution preferred.
    return (ch.priority, -ch.resolution)


def choose_best(candidates: list[Channel]) -> Channel:
    """Pick the single best candidate for a channel key."""
    ranked = sorted(candidates, key=_rank_key)
    best = ranked[0]

    # Keep curated/best URL choice, but inherit official metadata if available.
    if not best.tvg_logo:
        for alt in ranked:
            if alt.tvg_logo:
                best.tvg_logo = alt.tvg_logo
                break

    if not best.tvg_name:
        for alt in ranked:
            if alt.tvg_name:
                best.tvg_name = alt.tvg_name
                break

    return best


def dedupe(channels: list[Channel]) -> tuple[list[Channel], dict[str, list[Channel]]]:
    """Return (one best channel per key, full candidate map)."""
    cand_map = group_candidates(channels)
    chosen = [choose_best(cands) for cands in cand_map.values()]
    return chosen, cand_map
