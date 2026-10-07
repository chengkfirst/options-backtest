"""The local backtest service.

    python3 app/server.py

Then open http://localhost:8765.

**Why it has to be a local service**: a backtest runs Python (numpy/scipy) and reads
your data, and both of those are on your machine. A purely static page cannot do it, so
this is a service running locally with the browser as its interface, not a website.

The standard library is the only dependency. The core endpoints:

    GET  /api/health   is the service up (the front end decides from this whether it can
                       run backtests or only view exported results)
    GET  /api/meta     the available signals, symbols and defaults
    POST /api/run      run one backtest and return the trade log
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from meta import _describe_gaps, _meta_payload, _tradeoff  # noqa: E402
from runner import (  # noqa: E402
    STORE, SYNTHETIC_WARNING, VALIDATIONS, _finite, run_backtest, validate,
    validations_for,
)
from store import VERDICTS  # noqa: E402

#: The port, overridable so a second instance can run beside the first — reviewing an
#: interface change while a long backtest holds the usual port is the case that wanted it.
PORT = int(os.environ.get("BENCH_PORT", "8765"))
STATIC = ROOT / "static"

#: The columns sent to the front end. All of them would be heavy (1,700 trades x 44
#: columns over ten years); these are enough to chart and group by.

TEXT_TYPES = {"text/html", "text/css", "text/javascript", "application/javascript",
              "application/json", "text/plain"}


class Handler(SimpleHTTPRequestHandler):
    #: The standard library defaults to HTTP/1.0, which opens a new TCP connection per
    #: request. One page load is five or six static files plus several /api calls, and
    #: that churn is unnecessary. Declaring 1.1 lets the browser reuse the connection —
    #: on condition that **every response carries a correct Content-Length**, or the
    #: browser waits for ever, the page spins, and neither side raises. Every exit
    #: currently satisfies it: ``_json`` sets it explicitly, static files go through
    #: SimpleHTTPRequestHandler, and errors go through send_error. Any future branch
    #: that writes to wfile directly has to set Content-Length too.
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def _serve_index(self):
        """Stamp every js/css reference with the file's modification time when serving
        the index.

        Browsers cache js/css heuristically, so new HTML meets old JS and the error
        reported is "an element is null" or "a function is undefined" — symptoms that
        point at the code when the cause is a file that was never reloaded.

        Cache-Control only affects future loads and cannot rescue what is already cached;
        renaming files has to be maintained by hand. A ``?v=`` from the mtime changes the
        URL whenever the file changes, so the browser must refetch.
        """
        index = STATIC / "index.html"
        if not index.exists():
            return self.send_error(404, "index.html is not in %s" % STATIC)
        html = index.read_text(encoding="utf-8")

        def stamp(match):
            attr, name = match.group(1), match.group(2)
            target = STATIC / name
            if not target.exists():
                # A reference to a file that does not exist. Left silent, the page is
                # missing a script and nothing says so.
                sys.stderr.write(f"  index.html references a file that does not exist: "
                                 f"{name}\n")
                return match.group(0)
            return f'{attr}="{name}?v={int(target.stat().st_mtime)}"'

        html = re.sub(r'(src|href)="([\w.\-/]+\.(?:js|css))"', stamp, html)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def end_headers(self):
        """Every static file is revalidated against the server.

        Without this, Chrome caches js/css heuristically and a reload after an edit still
        runs the old code, raising nothing. A local tool does not care about the
        bandwidth; correctness comes first.
        """
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def guess_type(self, path):
        """Add charset=utf-8 to the text types.

        The standard library gives ``text/html`` from the extension with no encoding. One
        wrong guess by the browser garbles the page — and it is a silent failure: the page
        opens, nothing raises, and it simply cannot be read.
        """
        base = super().guess_type(path)
        main = base.split(";")[0].strip()
        return f"{main}; charset=utf-8" if main in TEXT_TYPES else base

    def log_message(self, fmt, *args):  # only print failures; successes would flood
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---- API ----
    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path in ("/", "/index.html"):
            return self._serve_index()
        if url.path == "/api/health":
            return self._json({"ok": True, **STORE.overview()})
        if url.path == "/api/tradeoff":
            try:
                return self._json(_tradeoff(q))
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"{type(exc).__name__}: {exc}",
                                   "detail": traceback.format_exc()}, 400)
        if url.path == "/api/meta":
            return self._json(self._meta())
        if url.path == "/api/runs":
            return self._json({"runs": STORE.runs(
                strategy_name=q.get("strategy"), symbol=q.get("symbol"),
                verdict=q.get("verdict"), limit=int(q.get("limit", 200)))})
        if url.path == "/api/run":
            try:
                run_id = int(q.get("id", ""))
            except ValueError:
                return self._json({"error": "the id parameter is missing or invalid"}, 400)
            record = STORE.run(run_id)
            if record is None:
                return self._json({"error": f"there is no run {run_id}"}, 404)
            try:
                trades = STORE.trades(run_id)
            except (FileNotFoundError, KeyError) as exc:
                return self._json({"error": str(exc)}, 410)
            record["rows"] = json.loads(trades.to_json(orient="records")) if len(trades) else []
            record["notes_list"] = STORE.notes(run_id)
            record["notes"] = list(_describe_gaps())
            if record["synthetic"]:
                record["notes"].insert(0, SYNTHETIC_WARNING)
            # The same shape a fresh run returns. Until this, opening a stored run gave a
            # record without `engine`, `boundary`, `problems`, `diagnostics`, `anomalies`
            # or `validations`, so every section that reads them would have rendered
            # undefined — which is why the interface never used this endpoint at all.
            #
            # `engine` and `validations` are **derived** from the stored configuration
            # rather than kept twice; the rest were recorded by the run and come back as
            # `null` when it predates the columns, which the interface shows as "not
            # recorded" rather than as "nothing happened".
            record["engine"] = record["config"].get("engine", "trade_table")
            record["validations"] = validations_for(record["engine"])
            record.setdefault("boundary", "")
            # The daily curve, if this run kept one. `None` where it did not, which the
            # analysis page distinguishes from a flat account.
            curve = STORE.equity(run_id)
            record["equity"] = (json.loads(curve.reset_index().to_json(
                orient="records", date_format="iso")) if curve is not None else None)
            # The event-driven extras live inside `analysis` in the store; the interface
            # reads them at the top level, as it does for a fresh run.
            for key in ("equity_metrics", "scenarios", "attribution"):
                if isinstance(record.get("analysis"), dict) and key in record["analysis"]:
                    record[key] = record["analysis"][key]
            # A run stored before a part of the analysis existed has no key for it, and an
            # absent key is not the same thing as a recorded `null`: the first makes the
            # interface's contract check report the backend as out of step, the second says
            # "this run does not have that" and lets the page fall back. Every part the
            # contract declares is present, so only one of those two can happen.
            if isinstance(record.get("analysis"), dict):
                for key in ("baselines", "trade_stats"):
                    record["analysis"].setdefault(key, None)
            return self._json(record)
        if url.path == "/api/strategies":
            name = q.get("name")
            return self._json({"strategies":
                STORE.strategy_versions(name) if name else STORE.strategies()})
        if url.path == "/api/notes":
            return self._json({"notes": STORE.notes(), "verdicts": VERDICTS})
        if url.path.startswith("/api/"):
            return self._json({"error": f"unknown endpoint {url.path}"}, 404)
        return super().do_GET()

    def do_HEAD(self):
        """HEAD on an API path goes through the same routing.

        Otherwise HEAD /api/meta is treated as a static file and returns 404: the path
        exists and is reported as missing.
        """
        if self.path.startswith("/api/"):
            self._head_only = True
            try:
                return self.do_GET()
            finally:
                self._head_only = False
        return super().do_HEAD()

    def do_POST(self):
        if self.path not in ("/api/run", "/api/note", "/api/run/delete",
                             "/api/run/archive", "/api/run/deletion", "/api/validate"):
            return self._json({"error": f"unknown endpoint {self.path}"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": f"the request body is not valid JSON: {exc}"}, 400)

        if self.path == "/api/note":
            try:
                note = STORE.add_note(
                    self._run_id(payload),
                    payload.get("body", ""),
                    payload.get("verdict", "open"),
                )
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"note": note})

        # Archiving and deleting are different operations with different consequences for
        # the record, so they are different endpoints rather than a flag on one.
        if self.path == "/api/run/archive":
            try:
                STORE.archive_run(self._run_id(payload),
                                  bool(payload.get("archived", True)))
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"ok": True})

        # What a delete would take, without taking it. The interface asks this before it
        # asks the researcher, so the question it puts to them names what is at stake
        # rather than only asking whether they meant it.
        if self.path == "/api/run/deletion":
            try:
                return self._json(STORE.deletion_takes(self._run_id(payload)))
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)

        if self.path == "/api/run/delete":
            try:
                took = STORE.delete_run(self._run_id(payload))
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"ok": True, "took": took})

        if self.path == "/api/validate":
            try:
                out = validate(self._run_id(payload), payload.get("kind", ""),
                               payload.get("params") or {})
            except (ValueError, KeyError, AttributeError) as exc:
                # A refusal, not a crash: the levels were not given, the run has aged out
                # of memory, the engine cannot answer it. The message says which, and the
                # interface shows it where the report would have gone.
                return self._json({"error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc(limit=6)
                sys.stderr.write(detail)
                return self._json({"error": f"{type(exc).__name__}: {exc}",
                                   "detail": detail}, 400)
            return self._json(out)

        try:
            record = run_backtest(payload)
        except Exception as exc:  # noqa: BLE001
            # The whole error chain goes back to the front end. A silent failure is the
            # least acceptable kind of error in this project — seeing "it failed" in the
            # browser without knowing where is worse than a crash.
            detail = traceback.format_exc(limit=6)
            sys.stderr.write(detail)
            return self._json(
                {"error": f"{type(exc).__name__}: {exc}", "detail": detail}, 400
            )

        return self._json(record)

    # ---- helpers ----
    def _meta(self) -> dict[str, Any]:
        return _meta_payload()

    def _run_id(self, payload) -> int:
        """Take the run id out of the request body.

        Failing to find it raises; it never falls back to 0. Writing
        ``payload.get("run_id", 0)`` would quietly turn "the front end sent no id" into
        "operate on run 0" — a note attached to the wrong run, the wrong record deleted,
        and nothing raised anywhere.
        """
        raw = payload.get("id", payload.get("run_id"))
        if raw is None:
            raise ValueError("the request body has no run id (the field is `id`)")
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"the run id is not an integer: {raw!r}") from None

    def _json(self, obj, status: int = 200):
        """Serialise and reply.

        Serialisation can itself fail (a function object finding its way into the
        configuration, say). The connection must never simply drop then — the front end
        would see "the request failed" and nothing about where. So a plain error comes
        back instead, stating the cause.
        """
        try:
            # allow_nan=False is the point. By default this emits NaN / Infinity
            # literals, which Python reads back and **JSON does not allow**, so the
            # browser's JSON.parse fails outright. What that looks like: 200 from the
            # server, a clean log, and a front end that says only "the response was not
            # JSON", with nowhere to start. The classic trigger is a NaN Sharpe on zero
            # trades, which destroys the whole response. So non-finite values become null
            # first ("cannot be computed" should be null anyway), with allow_nan=False
            # behind it — if anything slips through, raising loudly beats emitting
            # invalid JSON.
            body = json.dumps(_finite(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            status = 500
            body = json.dumps(
                {"error": f"the server failed to serialise the response: {exc}",
                 "detail": "This is a server bug; please report it with the steps you "
                           "took."},
                ensure_ascii=False,
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)


def main() -> int:
    if not STATIC.exists():
        sys.stderr.write(f"the static directory is missing: {STATIC}\n")
        return 1
    try:
        import backtest  # noqa: F401
    except ImportError as exc:
        sys.stderr.write(
            f"importing backtest failed: {exc}\n"
            f"Start it with a Python that has the dependencies, for example:\n"
            f"  /opt/anaconda3/bin/python3 {Path(__file__).relative_to(Path.cwd())}\n"
        )
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"the bench is running  ->  http://localhost:{PORT}")
    print("Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
