# IMAP Sync Service

Small self-hosted HTTP service for mailbox migrations between two IMAP servers.

This service does not implement IMAP copying itself. It schedules and runs [`imapsync`](https://imapsync.lamiral.info/) jobs, stores job state in SQLite, and exposes simple endpoints for creating migrations, checking status, and reading logs.

The service forces `imapsync` to run with `--nolog` because job output is already captured into per-job log files under the service data directory.

## Why this design

`imapsync` is the pragmatic engine for this job. It already handles folder discovery, message flags, retries, duplicates, and the many IMAP compatibility issues that make direct reimplementation expensive and fragile.

## Features

- Token-protected HTTP API
- Built-in web dashboard for bulk job creation
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

Dashboard:

```bash
open http://127.0.0.1:8080/
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
- `POST /jobs/bulk`
- `POST /jobs/{id}/stop`

## Bulk dashboard workflow

Open `/` in your browser and use the built-in dashboard:

- enter the API token once
- set the shared source and destination server values
- paste inbox rows in this format:

```text
source_user,source_password,destination_user,destination_password
```

- preview the generated jobs
- queue the batch with one click

## Proxmox LXC deployment

For this project, an unprivileged Debian LXC on Proxmox is a good default:

- low overhead
- easy snapshots and backups
- systemd works normally
- this service does not need special kernel features

Recommended container settings:

- Debian 12
- Unprivileged container
- 1 vCPU minimum
- 512 MB RAM minimum
- 4-8 GB disk minimum
- Static IP or DHCP reservation

If you want the simplest possible isolation model, a VM also works, but it is usually unnecessary for this workload.

### Automated install on Debian LXC

From inside the container:

```bash
apt update
apt install -y git
git clone https://github.com/mbandritz/imap-sync.git /opt/imap-sync-service
bash /opt/imap-sync-service/install-debian-lxc.sh
```

The install script will:

- install `python3`
- verify that `imapsync` is already installed and available in `PATH`
- create the `imap-sync` service account
- create `/var/lib/imap-sync-service`
- copy the systemd unit
- create `/opt/imap-sync-service/.env` if it does not already exist

If `imapsync` is not in the Debian repo you are using, install it manually first and then run the script.

After that, edit `/opt/imap-sync-service/.env` and set a strong token:

```bash
nano /opt/imap-sync-service/.env
```

Then start the service:

```bash
systemctl restart imap-sync.service
systemctl enable imap-sync.service
systemctl status imap-sync.service
```

### Nginx reverse proxy

Keep the app bound to `127.0.0.1` and expose Nginx instead.

Install Nginx:

```bash
apt install -y nginx
```

Copy the included config:

```bash
cp /opt/imap-sync-service/nginx-imap-sync.conf /etc/nginx/sites-available/imap-sync
ln -s /etc/nginx/sites-available/imap-sync /etc/nginx/sites-enabled/imap-sync
rm -f /etc/nginx/sites-enabled/default
```

Edit the `server_name` in `/etc/nginx/sites-available/imap-sync`, then test and reload:

```bash
nginx -t
systemctl reload nginx
```

If you want HTTPS, terminate TLS in Nginx with Let's Encrypt or keep the container reachable only over your VPN or private network.

### Manual systemd setup

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
- In Proxmox, prefer an unprivileged LXC and do not expose the API directly to the public internet.
