# Cyclewatch

iPhones show a single battery health percentage under Settings and keep no
history of it: no cycle-count trend, no capacity-over-time graph, no way to
see how fast your battery is degrading or to compare devices. The raw data
exists buried in Apple's analytics files, but iOS gives you no viewer for it.

Cyclewatch is the self-hosted solution: feed it your `.ips` analytics files
and it extracts dated battery readings (cycle count, charge capacity,
health %) and plots them over time. Your data stays on your machine — no
cloud account, just a Docker container and a local volume.

## Quick start (copy-paste)

You don't need this repo. Create an empty folder, save this as
`docker-compose.yml`, and run `docker compose up -d`:

```yaml
services:
  cyclewatch:
    image: maraudermarauder/cyclewatch:latest
    container_name: cyclewatch
    ports:
      - "3344:8000"
    volumes:
      - cyclewatch-data:/app/data
    environment:
      - DATA_DIR=/app/data
    restart: unless-stopped

volumes:
  cyclewatch-data:
```

```bash
docker compose up -d
# then open http://<docker-host-ip>:3344/
```

The named volume avoids Linux file-permission issues out of the box (the
container runs as a non-root user). Prefer a visible folder instead? Swap the
`volumes:` entry for `- ./data:/app/data` — then back up by copying that
folder. With the named volume, back up from the app's Backup tab, or:

```bash
docker run --rm -v cyclewatch-data:/data -v "$PWD":/backup busybox \
  tar czf /backup/cyclewatch-data-backup.tar.gz -C /data .
```

Update later with:

```bash
docker compose pull && docker compose up -d
```

Pin a version instead of `latest` by changing the image tag to e.g.
`maraudermarauder/cyclewatch:v0.1.0`.

## How it works

1. On your iPhone/iPad open **Settings > Privacy & Security > Analytics &
   Improvements > Analytics Data**.
2. Find files named **Analytics-yyyy-mm-dd-*.ips** (battery data is included
   in these since iOS 16) and share/save them (e.g. via AirDrop, Files, or
   email) to a computer.
3. Open the web UI, pick (or create) the device the files belong to, and
   upload one or more `.ips` files.
4. The app extracts dated battery readings and plots them.

## Run from this repo (contributors)

```bash
# Pulls maraudermarauder/cyclewatch from DockerHub (published on git tags via CI).
docker compose up -d
# then open http://<docker-host-ip>:3344/
```

```bash
docker compose pull && docker compose up -d   # update
```

Pin a version instead of `latest`:

```bash
# .env
TAG=v0.1.0
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

## Publishing to DockerHub (maintainers)

Images publish automatically from the `JasonXiao127/cyclewatch` GitHub repo
via `.github/workflows/docker-publish.yml` (multi-arch `linux/amd64` +
`linux/arm64`). One-time setup and first publish:

```bash
# 1. Push the repo (first time only)
git push -u origin main

# 2. In GitHub: Settings → Secrets → Actions → add
#      DOCKERHUB_USERNAME = maraudermarauder
#      DOCKERHUB_TOKEN    = <DockerHub access token, Read+Write>
#    (hub.docker.com → Account Settings → Security → New Access Token)

# 3. Cut a release — CI builds and pushes latest + versioned tags
git tag v0.1.0 && git push origin v0.1.0

# 4. Confirm on hub.docker.com/r/maraudermarauder/cyclewatch/tags,
#    then smoke-test from a clean machine:
docker pull maraudermarauder/cyclewatch:latest
```

Notes:

- Only tags matching `v*` trigger a push; pull requests build without
  pushing as a smoke test.
- `latest` moves on every version tag. Pin `v0.1.0`-style tags for
  reproducible deploys.

### Manual fallback (if CI is red)

```bash
docker login
docker buildx build --platform linux/amd64,linux/arm64 \
  -t maraudermarauder/cyclewatch:v0.1.0 \
  -t maraudermarauder/cyclewatch:latest \
  --push .
```
