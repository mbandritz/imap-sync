#!/usr/bin/env python3
import json
import mimetypes
import os
import secrets
import shlex
import signal
import sqlite3
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("IMAP_SYNC_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("IMAP_SYNC_DB", DATA_DIR / "jobs.db"))
STATIC_DIR = BASE_DIR / "static"
HOST = os.environ.get("IMAP_SYNC_HOST", "0.0.0.0")
PORT = int(os.environ.get("IMAP_SYNC_PORT", "8090"))
API_TOKEN = os.environ.get("IMAP_SYNC_API_TOKEN", "")
IMAPSYNC_BIN = os.environ.get("IMAPSYNC_BIN", "imapsync")
POLL_INTERVAL = float(os.environ.get("IMAP_SYNC_POLL_INTERVAL", "2.0"))


CREATE_JOBS_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    source_host TEXT NOT NULL,
    source_port INTEGER,
    source_user TEXT NOT NULL,
    destination_host TEXT NOT NULL,
    destination_port INTEGER,
    destination_user TEXT NOT NULL,
    sync_internal_dates INTEGER NOT NULL DEFAULT 1,
    automap INTEGER NOT NULL DEFAULT 1,
    delete2duplicates INTEGER NOT NULL DEFAULT 0,
    extra_args TEXT NOT NULL DEFAULT '[]',
    command_preview TEXT,
    log_path TEXT,
    pid INTEGER,
    exit_code INTEGER,
    error TEXT
)
"""


CREATE_SECRETS_SQL = """
CREATE TABLE IF NOT EXISTS job_secrets (
    job_id TEXT PRIMARY KEY,
    source_password TEXT NOT NULL,
    destination_password TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
)
"""


db_lock = threading.Lock()
runner_lock = threading.Lock()
active_jobs: dict[str, subprocess.Popen[str]] = {}
shutdown_event = threading.Event()


def now_ts() -> int:
    return int(time.time())


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(CREATE_JOBS_SQL)
        conn.execute(CREATE_SECRETS_SQL)
        conn.commit()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def require_auth(handler: BaseHTTPRequestHandler) -> bool:
    if not API_TOKEN:
        return True
    header = handler.headers.get("Authorization", "")
    expected = f"Bearer {API_TOKEN}"
    if secrets.compare_digest(header, expected):
        return True
    respond_json(handler, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
    return False


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def respond_json(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def respond_file(handler: BaseHTTPRequestHandler, file_path: Path) -> None:
    if not file_path.exists() or not file_path.is_file():
        respond_json(handler, HTTPStatus.NOT_FOUND, {"error": "not found"})
        return

    content = file_path.read_bytes()
    content_type, _encoding = mimetypes.guess_type(str(file_path))
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type or "application/octet-stream")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def build_imapsync_command(job: sqlite3.Row, secrets_row: sqlite3.Row) -> list[str]:
    args = [
        IMAPSYNC_BIN,
        "--nolog",
        "--host1",
        job["source_host"],
        "--user1",
        job["source_user"],
        "--password1",
        secrets_row["source_password"],
        "--host2",
        job["destination_host"],
        "--user2",
        job["destination_user"],
        "--password2",
        secrets_row["destination_password"],
        "--syncinternaldates" if job["sync_internal_dates"] else "--nosyncinternaldates",
        "--automap" if job["automap"] else "--noautomap",
    ]

    if job["source_port"]:
        args.extend(["--port1", str(job["source_port"])])
    if job["destination_port"]:
        args.extend(["--port2", str(job["destination_port"])])
    if job["delete2duplicates"]:
        args.append("--delete2duplicates")

    extra_args = json.loads(job["extra_args"])
    args.extend(extra_args)
    return args


def redact_command(args: list[str]) -> str:
    redacted: list[str] = []
    secret_flags = {"--password1", "--password2"}
    skip_value = False
    for part in args:
        if skip_value:
            redacted.append("********")
            skip_value = False
            continue
        redacted.append(shlex.quote(part))
        if part in secret_flags:
            skip_value = True
    return " ".join(redacted)


def create_job(payload: dict[str, Any]) -> str:
    required = [
        "source_host",
        "source_user",
        "source_password",
        "destination_host",
        "destination_user",
        "destination_password",
    ]
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")

    extra_args = payload.get("extra_args", [])
    if not isinstance(extra_args, list) or not all(isinstance(item, str) for item in extra_args):
        raise ValueError("extra_args must be a list of strings")

    job_id = secrets.token_hex(8)
    ts = now_ts()
    log_path = DATA_DIR / f"{job_id}.log"

    with db_lock, db() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, status, created_at, updated_at, source_host, source_port, source_user,
                destination_host, destination_port, destination_user, sync_internal_dates,
                automap, delete2duplicates, extra_args, log_path
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                ts,
                ts,
                payload["source_host"],
                payload.get("source_port"),
                payload["source_user"],
                payload["destination_host"],
                payload.get("destination_port"),
                payload["destination_user"],
                1 if payload.get("sync_internal_dates", True) else 0,
                1 if payload.get("automap", True) else 0,
                1 if payload.get("delete2duplicates", False) else 0,
                json.dumps(extra_args),
                str(log_path),
            ),
        )
        conn.execute(
            """
            INSERT INTO job_secrets (job_id, source_password, destination_password)
            VALUES (?, ?, ?)
            """,
            (job_id, payload["source_password"], payload["destination_password"]),
        )
        conn.commit()
    return job_id


def create_bulk_jobs(payload: dict[str, Any]) -> list[str]:
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("jobs must be a non-empty list")
    job_ids: list[str] = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("each job must be an object")
        job_ids.append(create_job(job))
    return job_ids


def fetch_job(job_id: str) -> dict[str, Any] | None:
    with db_lock, db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs() -> list[dict[str, Any]]:
    with db_lock, db() as conn:
        rows = conn.execute(
            """
            SELECT id, status, created_at, updated_at, started_at, finished_at,
                   source_host, source_user, destination_host, destination_user,
                   exit_code, error
            FROM jobs
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_ts()
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [job_id]
    with db_lock, db() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)
        conn.commit()


def run_job(job_id: str) -> None:
    with db_lock, db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        secrets_row = conn.execute("SELECT * FROM job_secrets WHERE job_id = ?", (job_id,)).fetchone()
    if not job or not secrets_row:
        return

    args = build_imapsync_command(job, secrets_row)
    command_preview = redact_command(args)
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    update_job(job_id, status="running", started_at=now_ts(), command_preview=command_preview, error=None)

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"$ {command_preview}\n")
        log_file.flush()
        try:
            process = subprocess.Popen(
                args,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            update_job(
                job_id,
                status="failed",
                finished_at=now_ts(),
                exit_code=127,
                error=f"imapsync binary not found at '{IMAPSYNC_BIN}'",
            )
            log_file.write(f"\nERROR: imapsync binary not found at '{IMAPSYNC_BIN}'\n")
            return

        with runner_lock:
            active_jobs[job_id] = process
        update_job(job_id, pid=process.pid)
        exit_code = process.wait()
        with runner_lock:
            active_jobs.pop(job_id, None)

    status = "completed" if exit_code == 0 else "failed"
    update_job(job_id, status=status, finished_at=now_ts(), exit_code=exit_code, pid=None)


def runner_loop() -> None:
    while not shutdown_event.is_set():
        with db_lock, db() as conn:
            row = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            shutdown_event.wait(POLL_INTERVAL)
            continue
        run_job(row["id"])


def stop_job(job_id: str) -> bool:
    with runner_lock:
        process = active_jobs.get(job_id)
    if not process:
        return False
    process.terminate()
    update_job(job_id, status="stopping", error="termination requested")
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "IMAPSyncService/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]

        if parsed.path == "/":
            respond_file(self, STATIC_DIR / "index.html")
            return

        if parts and parts[0] == "static":
            requested = Path(*parts[1:]) if len(parts) > 1 else Path("")
            file_path = (STATIC_DIR / requested).resolve()
            if STATIC_DIR.resolve() not in file_path.parents and file_path != STATIC_DIR.resolve():
                respond_json(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            respond_file(self, file_path)
            return

        if not require_auth(self):
            return

        if parsed.path == "/health":
            respond_json(self, HTTPStatus.OK, {"status": "ok"})
            return

        if parsed.path == "/jobs":
            respond_json(self, HTTPStatus.OK, {"jobs": list_jobs()})
            return

        if len(parts) == 2 and parts[0] == "jobs":
            job = fetch_job(parts[1])
            if not job:
                respond_json(self, HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            respond_json(self, HTTPStatus.OK, {"job": job})
            return

        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "log":
            job = fetch_job(parts[1])
            if not job:
                respond_json(self, HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            log_path = Path(job["log_path"])
            content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            respond_json(self, HTTPStatus.OK, {"id": parts[1], "log": content})
            return

        respond_json(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if not require_auth(self):
            return

        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]

        if parsed.path == "/jobs":
            try:
                payload = read_json(self)
                job_id = create_job(payload)
            except json.JSONDecodeError:
                respond_json(self, HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                return
            except ValueError as exc:
                respond_json(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            respond_json(self, HTTPStatus.CREATED, {"id": job_id, "status": "queued"})
            return

        if parsed.path == "/jobs/bulk":
            try:
                payload = read_json(self)
                job_ids = create_bulk_jobs(payload)
            except json.JSONDecodeError:
                respond_json(self, HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                return
            except ValueError as exc:
                respond_json(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            respond_json(self, HTTPStatus.CREATED, {"ids": job_ids, "count": len(job_ids), "status": "queued"})
            return

        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "stop":
            job = fetch_job(parts[1])
            if not job:
                respond_json(self, HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            if stop_job(parts[1]):
                respond_json(self, HTTPStatus.ACCEPTED, {"id": parts[1], "status": "stopping"})
                return
            respond_json(self, HTTPStatus.CONFLICT, {"error": "job is not running"})
            return

        respond_json(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def install_signal_handlers(server: ThreadingHTTPServer) -> None:
    def handle_shutdown(signum: int, _frame: Any) -> None:
        shutdown_event.set()
        with runner_lock:
            for process in active_jobs.values():
                process.terminate()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)


def main() -> None:
    ensure_storage()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    install_signal_handlers(server)
    worker = threading.Thread(target=runner_loop, daemon=True)
    worker.start()
    print(f"listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
