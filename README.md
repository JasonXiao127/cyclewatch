# Self-Hosted iPhone Battery Tracker

A self-hosted Docker app that extracts battery health data (cycle count,
charge capacity, battery health %) from Apple's iOS/iPadOS analytics files
and tracks it over time with graphs.

## How it works

1. On your iPhone/iPad open **Settings > Privacy & Security > Analytics &
   Improvements > Analytics Data**.
2. Find files named **Analytics-yyyy-mm-dd-*.ips** (battery data is included
   in these since iOS 16) and share/save them (e.g. via AirDrop, Files, or
   email) to a computer.
3. Open the web UI, pick (or create) the device the files belong to, and
   upload one or more `.ips` files.
4. The app extracts dated battery readings and plots them.

## Run (published image)

```bash
# Pulls maraudermarauder/cyclewatch from DockerHub (published on git tags via CI).
docker compose up -d
```

Then open **http://<docker-host-ip>:3344/**

Update later with:

```bash
docker compose pull && docker compose up -d
```

Pin a version instead of `latest`:

```bash
# .env
TAG=v1.0.0
```

Local build (dev) without pulling:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

## Features

- Multi-device: attribute uploads to a device, create devices from the
  Devices tab or the small "+ New" button next to the upload selector.
- Works for iPhones and iPads alike.
- Historical backfill: reading dates come from inside the file; entries
  without a usable timestamp fall back to today's date (marked "fallback").
- Duplicate uploads (same file, same device) are skipped via SHA-256.
- Overlapping files merge: readings are upserted per device + timestamp.
- Temporarily hide any reading (or a whole device) from the graphs without
  deleting it; toggle "Show hidden points" on the dashboard to find them.
- Delete data at any level: single readings, whole uploads, or a device
  (cascades to its readings).

## Data & backups

All state lives in `./data` on the host (SQLite database + a copy of every
uploaded file). Back it up by copying that folder.

## iOS version changes

Apple occasionally moves the battery keys inside analytics files. The parser
searches every JSON entry recursively for known key names (see
`app/parser.py`); unrecognized battery-ish keys are reported in the upload
result so new key names are easy to spot and add to `BATTERY_KEY_ALIASES`.

## Development

```bash
pip install -r requirements.txt
pytest
uvicorn app.main:app --reload --port 3344
```
