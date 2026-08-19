# Zwift → Komoot sync (macOS)

Personal CLI for **macOS** that makes it easier to sync Zwift rides to Komoot: FIT upload, `ZWIFT - titles`, and matching screenshots staged for manual photo add.

Built and tested on Mac only (Zwift’s default folders under `~/Documents`, `~/Pictures`). It is not aimed at Windows or Linux.

## How it works

1. Reads `.fit` files from `~/Documents/Zwift/Activities`
2. Pulls the real title and activity ID from Zwift logs (`~/Documents/Zwift/Logs`)
3. Matches screenshots in `~/Pictures/Zwift` to each ride’s time window (skips `*_clean.jpg`)
4. Uploads the FIT to Komoot (unofficial v007 API) with a `ZWIFT - …` title
5. Copies matched photos into `data/pending_photos/<tour_id>/` — Komoot’s API can’t upload images
6. Tracks what’s already synced in SQLite

FYI: Screenshots on the Zwift activity page are the same files already on disk. No scraping needed.

Paths are resolved from your home directory (`Path.home()`).

## Setup

```bash
cd zwift-komoot-synchronizer
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# fill in KOMOOT_EMAIL / KOMOOT_PASSWORD
```

## Commands

```bash
zwift-komoot doctor          # check paths + .env
zwift-komoot list            # pending rides
zwift-komoot list --all
zwift-komoot status

zwift-komoot sync --dry-run  # no upload
zwift-komoot sync
zwift-komoot sync --open           # open pending photo folders after sync
zwift-komoot sync --limit 1
zwift-komoot sync --force

zwift-komoot stage-photos          # create missing pending folders only
zwift-komoot stage-photos --open   # open all pending folders in Finder

zwift-komoot clean-photos              # marks rides as done — only step that does
zwift-komoot clean-photos --tour-id 123
zwift-komoot clean-photos --dry-run
```

After `sync`, rides with photos are `pending` until you run `clean-photos` (which marks them `done`).

If the `zwift-komoot` script isn’t on your PATH:

```bash
python -m zwift_komoot_sync sync --dry-run
```

## Config (`.env`)

| Variable | Default | Notes |
|----------|---------|--------|
| `KOMOOT_EMAIL` | — | Komoot login |
| `KOMOOT_PASSWORD` | — | Komoot password |
| `KOMOOT_PRIVACY` | `private` | `private`, `friends`, or `public` |
| `MIN_FIT_KB` | `5` | Skip tiny/incomplete FITs (`inProgressActivity.fit` is always ignored) |

Optional overrides if your Zwift folders live elsewhere: `ZWIFT_ACTIVITIES_DIR`, `ZWIFT_LOGS_DIR`, `ZWIFT_PHOTOS_DIR`.

## Title examples

| Zwift name (logs) | Komoot title |
|-------------------|--------------|
| `Your First Workout` | `ZWIFT - Your First Workout` |
| `Tempus Fugit in Watopia` | `ZWIFT - Tempus Fugit in Watopia` |

## Limitations

- macOS only
- Komoot’s API is unofficial and can change without notice
- Photo upload isn’t available via API for now (read-only). Matched files land in `data/pending_photos/<tour_id>/`. Add them on the tour page yourself using the build-in helpers.
- Zwift tracks use virtual-world GPS, same as a manual FIT import

## Contributing

PRs welcome — especially around reliability, matching edge cases, CLI UX, and anything that makes day-to-day sync smoother.

## Layout

```
src/zwift_komoot_sync/
  cli.py
  sync.py
  fit_scanner.py
  zwift_log.py
  photos.py
  komoot_client.py
  db.py
data/sync.db              # created on first sync (gitignored)
data/pending_photos/      # staged photos for manual upload (gitignored)
```

Example output on komoot : 
<img width="1123" height="866" alt="komoot-sync-example" src="https://github.com/user-attachments/assets/64b4cecc-c43e-47ac-90aa-8e8b0a2efbe2" />

