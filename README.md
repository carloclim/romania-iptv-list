# Romania IPTV List

An auto-updating, de-duplicated **M3U playlist of Romanian TV channels**, built by merging
a hand-curated list with public community sources, health-checking every stream, and
publishing a single playlist you can drop into any IPTV player (Arvio, VLC, TiviMate, etc.).

- **Curated + aggregated** — your preferred channels take priority, gaps are filled from public sources.
- **Self-healing** — a daily GitHub Action re-checks every stream and swaps in a working URL when one dies.
- **EPG-ready** — the playlist header carries an `x-tvg-url` so the program guide loads automatically.

## Use it

Paste one of these URLs into your player:

| Playlist | URL |
| --- | --- |
| All channels, auto-healed (recommended) | `https://raw.githubusercontent.com/carloclim/romania-iptv-list/main/romania_iptv_preferate.m3u` |
| Same list (dist copy for Pages) | `https://raw.githubusercontent.com/carloclim/romania-iptv-list/main/dist/romania.m3u` |
| Only confirmed-working (diagnostic) | `https://raw.githubusercontent.com/carloclim/romania-iptv-list/main/dist/romania_working.m3u` |

> Use the **recommended** URL in Arvio. It keeps every channel and swaps broken stream URLs
> for working alternates. The *confirmed-working* list is validated by the CI runner, which
> sits outside Romania, so it may omit RO-only channels that actually work for you.

Once GitHub Pages is enabled (see below) these are also served from
`https://carloclim.github.io/romania-iptv-list/romania.m3u` and `.../romania_working.m3u`.

### In Arvio

Add a playlist and choose **M3U URL**, then paste the *recommended* URL above. The EPG
loads automatically from the `x-tvg-url` in the file — no separate guide URL needed.

## How it works

```
sources/curated.m3u  ─┐
remotes.json sources ─┼─▶ parse ─▶ blocklist ─▶ dedupe/rank ─▶ overrides ─▶ validate + heal ─▶ playlists
                      ─┘
```

- **`sources/curated.m3u`** — your hand-picked list (highest priority, `priority: 0`).
- **`sources/remotes.json`** — public M3U sources to pull from, each with a `priority` (lower = more trusted).
- **`data/overrides.json`** — force a specific URL/headers for a channel; always wins.
- **`data/blocklist.txt`** — substrings of URLs to always exclude (dead hosts, takedowns).
- **`data/config.json`** — title, EPG guide URL(s), group order, validation timeout/concurrency.

De-duplication matches channels by canonical `tvg-id` (e.g. `PROTV.ro@SD` == `ProTV.ro`),
falling back to a normalized name. When several sources offer the same channel, the most
trusted / highest-resolution one wins; if it fails validation, a working alternate is used.

### Outputs

| File | Contents |
| --- | --- |
| `romania_iptv_preferate.m3u` | Every unique channel, de-duplicated, broken URLs auto-healed (repo root so the Arvio URL stays stable) |
| `dist/romania.m3u` | Same as above (served via GitHub Pages) |
| `dist/romania_working.m3u` | Only streams that passed the health check (may under-report RO-only channels) |
| `dist/by-group/*.m3u` | One playlist per category |

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\build.py            # full build with validation
.\.venv\Scripts\python.exe scripts\build.py --no-validate   # fast dedupe only
```

## Customize

- **Add a source:** edit `sources/remotes.json`, set the raw `.m3u` `url`, `enabled: true`, and a `priority`.
- **Pin a channel's stream:** add an entry to `data/overrides.json` with the channel `key`
  (canonical tvg-id, e.g. `protv.ro`) and the `url` (+ optional `headers`).
- **Remove a source/host:** add a URL substring to `data/blocklist.txt`.
- **Change the guide or categories:** edit `data/config.json`.

## Automation

`.github/workflows/build.yml` runs daily (and on demand via **Run workflow**), rebuilds the
playlists, commits any changes, and deploys `dist/` to GitHub Pages.

To enable Pages: **Settings → Pages → Build and deployment → Source: GitHub Actions.**

## Geo-blocking note

Many Romanian streams are only reachable from Romanian IPs. GitHub's runners are outside
Romania, so such channels may be marked `geo`/`dead` during validation and dropped from the
*working* list even though they play fine for you. They remain in `dist/romania.m3u`. To
validate from a Romanian IP, set an HTTP proxy in the workflow or use a self-hosted runner in RO.

## Legal

No video files are stored in this repository. It only contains links to publicly available
streams that, to the best of our knowledge, were made public by their broadcasters. If a link
infringes your rights as a copyright holder, open an issue and it will be removed from the
list — but note that removing a link here does not remove the content from its host. This
project does not decrypt, capture, or redistribute paid/subscription streams, and does not
bypass any DRM or access control.

## License

Code is released under the [MIT License](LICENSE). Playlist entries are links to third-party
streams and are not covered by that license.
