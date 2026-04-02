# IMAP Sync Service

Small self-hosted HTTP service for mailbox migrations between two IMAP servers.

This service does not implement IMAP copying itself. It schedules and runs [`imapsync`](https://imapsync.lamiral.info/) jobs, stores job state in SQLite, and exposes simple endpoints for creating migrations, checking status, and reading logs.

## Why this design

`imapsync` is the pragmatic engine for this job. It already handles folder discovery, message flags, retries, duplicates, and the many IMAP compatibility issues that make direct reimplementation expensive and fragile.

## Features

- Token-protected HTTP API
- Background job execution
- SQLite job persistence
- Per-job logs on disk
- Single-binary Python runtime from the standard library
- systemd unit for direct server hosting

## Requirements

- Python 3.11+
- `imapsync` installed on the host and available in `$PATH`

On Debian or Ubuntu, installation is typically:

```bash
sudo apt update
sudo apt install imapsync
```

## Configuration

Environment variables:

- `IMAP_SYNC_HOST`: bind address, default `0.0.0.0`
- `IMAP_SYNC_PORT`: listen port, default `8080`
- `IMAP_SYNC_API_TOKEN`: bearer token for API auth; if empty, auth is disabled
- `IMAPSYNC_BIN`: path to `imapsync`, default `imapsync`
- `IMAP_SYNC_DATA_DIR`: data directory, default `./data`
- `IMAP_SYNC_DB`: SQLite database path, default `./data/jobs.db`
- `IMAP_SYNC_POLL_INTERVAL`: queue polling interval in seconds, default `2`

## Run locally

```bash
export IMAP_SYNC_API_TOKEN='replace-this'
python3 app.py
```

Health check:

```bash
curl -H "Authorization: Bearer $IMAP_SYNC_API_TOKEN" http://127.0.0.1:8080/health
```

## Create a migration job

```bash
curl \
  -X POST \
  -H "Authorization: Bearer $IMAP_SYNC_API_TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8080/jobs \
  -d '{
    "source_host": "old.mail.example.com",
    "source_port": 993,
    "source_user": "alice@example.com",
    "source_password": "old-password",
    "destination_host": "new.mail.example.com",
    "destination_port": 993,
    "destination_user": "alice@example.com",
    "destination_password": "new-password",
    "automap": true,
    "sync_internal_dates": true,
    "delete2duplicates": false,
    "extra_args": ["--ssl1", "--ssl2"]
  }'
```

## API

- `GET /health`
- `GET /jobs`
- `GET /jobs/{id}`
- `GET /jobs/{id}/log`
- `POST /jobs`
- `POST /jobs/{id}/stop`

## systemd setup

1. Copy the project to your server, for example `/opt/imap-sync-service`.
2. Create an environment file from `.env.example`.
3. Copy `imap-sync.service` to `/etc/systemd/system/`.
4. Adjust paths in the service file if needed.
5. Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now imap-sync.service
sudo systemctl status imap-sync.service
```

## Security notes

- This service stores source and destination passwords in SQLite so queued jobs can run later. Keep the database on encrypted storage and restrict filesystem permissions.
- Put the service behind HTTPS, either with a reverse proxy or a private network.
- Use a strong `IMAP_SYNC_API_TOKEN`.
- Consider binding to `127.0.0.1` and exposing it only through Nginx or Tailscale.
