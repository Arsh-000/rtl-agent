"""A small web server for rtl-agent: one page in the browser instead of the terminal.

    python -m rtl_agent web                      then open http://localhost:8501

It uses only Python's standard library (no extra packages to install). It is meant for one
person or a small team on one machine (Stage 1); by default it only listens on this computer.

The page (static/index.html) talks to a small JSON API:

    GET  /api/status                 tools found? AI key on the server? built-in examples
    POST /api/inspect                {files, top?}        -> modules, top guess, ports (via Yosys)
    POST /api/runs                   {kind, action, ...}  -> starts a run, returns its id
    GET  /api/runs                   all runs, newest first
    GET  /api/runs/<id>              progress: status, rounds, baseline vs best, log
    POST /api/runs/<id>/stop         pause (the run can be resumed)
    POST /api/runs/<id>/resume       continue a paused run
    GET  /api/runs/<id>/report       the HTML report
    GET  /api/runs/<id>/best.zip     the best design found
    GET  /api/runs/<id>/all.zip      everything: inputs, config, every round, report
"""

from __future__ import annotations

import json
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .jobs import JobError, JobManager, detect_modules, inspect_files, server_status

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 25_000_000
_RUN = re.compile(r"^/api/runs/([a-z0-9_]+)(?:/(stop|resume|report|best\.zip|all\.zip))?$")


def make_handler(jobs: JobManager):
    class Handler(BaseHTTPRequestHandler):
        server_version = "rtl-agent-web"

        # ------------------------------------------------------------ plumbing
        def log_message(self, fmt, *args):          # keep the terminal quiet: one line per action
            if self.command != "GET" or "/api/runs/" not in self.path:
                super().log_message(fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise JobError(f"upload too large (limit {MAX_BODY // 1_000_000} MB)")
            if n == 0:
                return {}
            try:
                data = json.loads(self.rfile.read(n))
            except ValueError:
                raise JobError("request is not valid JSON")
            if not isinstance(data, dict):
                raise JobError("request must be a JSON object")
            return data

        def _handle(self, fn):
            try:
                fn()
            except JobError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:                 # never leak a stack trace to the page
                traceback.print_exc()
                self._json(500, {"error": f"internal error: {exc.__class__.__name__}: {exc}"})

        # ------------------------------------------------------------ routes
        def do_GET(self):
            self._handle(self._get)

        def do_POST(self):
            self._handle(self._post)

        def _get(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/status":
                return self._json(200, server_status())
            if path == "/api/runs":
                return self._json(200, {"runs": jobs.list()})
            m = _RUN.match(path)
            if m:
                job_id, action = m.group(1), m.group(2)
                if action is None:
                    return self._json(200, jobs.detail(job_id))
                if action == "report":
                    return self._send(200, jobs.report_html(job_id).encode(), "text/html; charset=utf-8")
                if action in ("best.zip", "all.zip"):
                    name, data = (jobs.zip_best if action == "best.zip" else jobs.zip_all)(job_id)
                    return self._send(200, data, "application/zip",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
            self._json(404, {"error": "not found"})

        def _post(self):
            path = self.path.split("?")[0]
            body = self._body()
            if path == "/api/inspect":
                files = body.get("files") or []
                found = detect_modules(files)
                top = body.get("top") or found["top"]
                out = dict(found, top=top, info=None, error=None)
                if top:
                    try:
                        out["info"] = inspect_files(files, top)
                    except JobError as exc:
                        out["error"] = str(exc)
                return self._json(200, out)
            if path == "/api/runs":
                key = (body.pop("api_key", None) or "").strip() or None
                job_id = jobs.create(body, api_key=key)
                return self._json(200, {"id": job_id})
            m = _RUN.match(path)
            if m and m.group(2) == "stop":
                jobs.stop(m.group(1))
                return self._json(200, {"ok": True})
            if m and m.group(2) == "resume":
                jobs.resume(m.group(1), api_key=(body.get("api_key") or "").strip() or None)
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

    return Handler


def make_server(host: str, port: int, workdir: Path) -> ThreadingHTTPServer:
    jobs = JobManager(workdir)
    srv = ThreadingHTTPServer((host, port), make_handler(jobs))
    srv.daemon_threads = True
    srv.jobs = jobs
    return srv


def serve(host: str = "127.0.0.1", port: int = 8501, workdir: Path = Path("webruns")) -> None:
    srv = make_server(host, port, workdir)
    shown = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    print(f"rtl-agent web interface: open http://{shown}:{port} in your browser")
    print(f"runs are saved in {Path(workdir).resolve() / 'jobs'}   (Ctrl+C to stop the server)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nserver stopped (runs that were still going keep running in the background until they finish)")
    finally:
        srv.server_close()


def serve_in_thread(host: str, port: int, workdir: Path) -> ThreadingHTTPServer:
    """For tests: start the server in a background thread and return it."""
    srv = make_server(host, port, workdir)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
