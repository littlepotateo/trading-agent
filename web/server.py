"""Local web app for TradingAgents.

Serves the single-page frontend in this folder and a small JSON API that runs
the same pipeline the CLI runs. Standard library only, so it adds no
dependencies beyond the package itself.

    python -m web.server                # http://127.0.0.1:8765
    python -m web.server --port 9000

API keys are read from the environment or a .env file, as the CLI does. The
server never returns key values, only whether a provider's key is set.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import re
import threading
import traceback
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from tradingagents.agents.rating import run_rating
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.reporting import _report_parts, analyst_names

HERE = Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"

# Providers offered in the UI. "env" is the variable the key is read from;
# None means the provider needs no key (local Ollama).
PROVIDERS = {
    "openai":    {"label": "OpenAI",     "env": "OPENAI_API_KEY",
                  "models": ["gpt-6-sol", "gpt-6-luna", "gpt-6-astra"]},
    "anthropic": {"label": "Anthropic",  "env": "ANTHROPIC_API_KEY",
                  "models": ["claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"]},
    "google":    {"label": "Google",     "env": "GOOGLE_API_KEY",
                  "models": ["gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-3.1-flash-lite"]},
    "xai":       {"label": "xAI",        "env": "XAI_API_KEY",
                  "models": ["grok-4.6", "grok-4.5"]},
    "deepseek":  {"label": "DeepSeek",   "env": "DEEPSEEK_API_KEY",
                  "models": ["deepseek-v4-pro", "deepseek-flash"]},
    "openrouter": {"label": "OpenRouter", "env": "OPENROUTER_API_KEY", "models": []},
    "groq":      {"label": "Groq",       "env": "GROQ_API_KEY", "models": []},
    "mistral":   {"label": "Mistral",    "env": "MISTRAL_API_KEY", "models": []},
    "ollama":    {"label": "Ollama (local)", "env": None, "models": []},
}

ANALYSTS = [
    {"key": "market",       "label": "Market",       "hint": "Price action and technical indicators"},
    {"key": "social",       "label": "Sentiment",    "hint": "StockTwits and Reddit chatter"},
    {"key": "news",         "label": "News",         "hint": "Company and macro headlines"},
    {"key": "fundamentals", "label": "Fundamentals", "hint": "Filings and financial statements"},
]
ANALYST_KEYS = {a["key"] for a in ANALYSTS}

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,20}$")
# Pages allowed to call the API from a browser: localhost, and claude.ai (where
# artifacts run). Anything else is refused, so a random website cannot start runs
# with your API keys.
ALLOWED_SUFFIXES = ("claude.ai", "claudeusercontent.com")

RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()
# The graph reads process-wide config, so runs go one at a time.
RUN_SLOT = threading.Lock()


class RequestError(ValueError):
    pass


def _key_status() -> dict[str, bool]:
    return {name: bool(info["env"] is None or os.environ.get(info["env"]))
            for name, info in PROVIDERS.items()}


def _validate(body: dict) -> dict:
    ticker = str(body.get("ticker", "")).strip().upper()
    if not TICKER_RE.match(ticker):
        raise RequestError("Ticker must be 1-20 letters, digits, '.', '-', '^' or '='.")

    raw_date = str(body.get("date", "")).strip()
    try:
        trade_date = date.fromisoformat(raw_date)
    except ValueError:
        raise RequestError("Date must be YYYY-MM-DD.") from None
    if trade_date > date.today():
        raise RequestError("Date cannot be in the future.")

    analysts = body.get("analysts") or []
    if not analysts or not set(analysts) <= ANALYST_KEYS:
        raise RequestError("Pick at least one analyst.")

    provider = str(body.get("provider", "openai"))
    if provider not in PROVIDERS:
        raise RequestError(f"Unknown provider '{provider}'.")

    deep = str(body.get("deep_model", "")).strip() or DEFAULT_CONFIG["deep_think_llm"]
    quick = str(body.get("quick_model", "")).strip() or DEFAULT_CONFIG["quick_think_llm"]

    def small_int(name, default, lo, hi):
        try:
            value = int(body.get(name, default))
        except (TypeError, ValueError):
            raise RequestError(f"{name} must be a whole number.") from None
        if not lo <= value <= hi:
            raise RequestError(f"{name} must be between {lo} and {hi}.")
        return value

    return {
        "ticker": ticker,
        "date": trade_date.isoformat(),
        "analysts": [a for a in ANALYST_KEYS if a in analysts],  # stable order
        "provider": provider,
        "deep_model": deep,
        "quick_model": quick,
        "debate_rounds": small_int("debate_rounds", 1, 1, 5),
        "risk_rounds": small_int("risk_rounds", 1, 1, 5),
        "save": bool(body.get("save", True)),
    }


def _build_config(req: dict) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["llm_provider"] = req["provider"]
    config["deep_think_llm"] = req["deep_model"]
    config["quick_think_llm"] = req["quick_model"]
    config["max_debate_rounds"] = req["debate_rounds"]
    config["max_risk_discuss_rounds"] = req["risk_rounds"]
    return config


def _set_status(run_id: str, **fields) -> None:
    with RUNS_LOCK:
        RUNS[run_id].update(fields)


def _log(run_id: str, kind: str, text: str) -> None:
    with RUNS_LOCK:
        events = RUNS[run_id]["events"]
        events.append({"t": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": text})
        del events[:-300]  # keep the feed short


# Report fields are filled as each node finishes, so their first appearance
# is a useful progress signal.
STEP_LABELS = {
    "market_report": "Market analyst finished",
    "sentiment_report": "Sentiment analyst finished",
    "news_report": "News analyst finished",
    "fundamentals_report": "Fundamentals analyst finished",
    "investment_plan": "Research manager finished",
    "trader_investment_plan": "Trader finished",
    "final_trade_decision": "Portfolio manager finished",
}


def _execute(run_id: str, req: dict) -> None:
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    with RUN_SLOT:
        try:
            _set_status(run_id, state="running")
            _log(run_id, "info", f"Starting {req['ticker']} on {req['date']} "
                                 f"with {', '.join(analyst_names(req['analysts']))}")
            config = _build_config(req)
            graph = TradingAgentsGraph(selected_analysts=req["analysts"], debug=False, config=config)
            init_state = graph.create_run_state(req["ticker"], req["date"])
            args = graph.propagator.get_graph_args()

            final_state: dict = {}
            seen: set[str] = set()
            for _messages, chunk in graph.stream_run(graph.checkpoint_input(init_state), **args):
                if not chunk:
                    continue
                final_state.update(chunk)
                for key, label in STEP_LABELS.items():
                    if chunk.get(key) and key not in seen:
                        seen.add(key)
                        _log(run_id, "step", label)

            graph.record_decision(req["ticker"], req["date"], final_state)
            rating = run_rating(final_state)
            sections = [
                {"heading": heading, "files": [{"agent": agent, "markdown": text}
                                               for agent, _file, text in written]}
                for heading, _folder, written in _report_parts(final_state)
            ]

            saved_to = None
            if req["save"]:
                saved_to = str(graph.save_reports(final_state, req["ticker"], html=True))

            _set_status(run_id, state="done", rating=rating, decision=final_state.get("final_trade_decision"),
                        sections=sections, saved_to=saved_to, finished=datetime.now().isoformat(timespec="seconds"))
            _log(run_id, "done", f"Finished: {rating}")
        except Exception as exc:  # surface any failure to the UI instead of dying silently
            traceback.print_exc()
            _set_status(run_id, state="error", error=f"{type(exc).__name__}: {exc}")
            _log(run_id, "error", f"{type(exc).__name__}: {exc}")


def start_run(req: dict) -> str:
    run_id = uuid.uuid4().hex[:12]
    with RUNS_LOCK:
        RUNS[run_id] = {
            "id": run_id, "state": "queued", "request": req, "events": [],
            "rating": None, "decision": None, "sections": [], "error": None,
            "saved_to": None, "started": datetime.now().isoformat(timespec="seconds"), "finished": None,
        }
    threading.Thread(target=_execute, args=(run_id, req), daemon=True).start()
    return run_id


def _public_run(run: dict) -> dict:
    return copy.deepcopy(run)


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingAgentsWeb/1.0"

    # --- helpers -----------------------------------------------------------
    def _cors_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        host = urlparse(origin).hostname or ""
        if host in ("localhost", "127.0.0.1") or host.endswith(ALLOWED_SUFFIXES):
            return origin
        return None

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64_000:
            raise RequestError("Request too large.")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise RequestError("Body must be JSON.") from None

    # --- routes ------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Chrome's Private Network Access: a public page may call 127.0.0.1 only if allowed.
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/config":
            return self._json(200, {
                "analysts": ANALYSTS,
                "providers": [{"key": k, "label": v["label"], "has_key": _key_status()[k],
                               "env": v["env"], "models": v["models"]} for k, v in PROVIDERS.items()],
                "defaults": {"provider": DEFAULT_CONFIG["llm_provider"],
                             "deep_model": DEFAULT_CONFIG["deep_think_llm"],
                             "quick_model": DEFAULT_CONFIG["quick_think_llm"],
                             "debate_rounds": DEFAULT_CONFIG["max_debate_rounds"],
                             "risk_rounds": DEFAULT_CONFIG["max_risk_discuss_rounds"]},
                "today": date.today().isoformat(),
                "results_dir": DEFAULT_CONFIG["results_dir"],
            })
        if path == "/api/runs":
            with RUNS_LOCK:
                runs = [{"id": r["id"], "state": r["state"], "ticker": r["request"]["ticker"],
                         "date": r["request"]["date"], "rating": r["rating"], "started": r["started"]}
                        for r in reversed(list(RUNS.values()))]
            return self._json(200, runs[:50])
        if path.startswith("/api/runs/"):
            run_id = path.rsplit("/", 1)[-1]
            with RUNS_LOCK:
                run = RUNS.get(run_id)
                payload = _public_run(run) if run else None
            if not payload:
                return self._json(404, {"error": "Run not found."})
            return self._json(200, payload)
        self._json(404, {"error": "Not found."})

    def do_POST(self):
        if urlparse(self.path).path != "/api/runs":
            return self._json(404, {"error": "Not found."})
        try:
            req = _validate(self._read_json())
        except RequestError as exc:
            return self._json(400, {"error": str(exc)})
        run_id = start_run(req)
        self._json(202, {"id": run_id})

    def log_message(self, fmt, *args):  # quieter console
        print(f"{self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingAgents web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TradingAgents web app on http://{args.host}:{args.port}")
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()


if __name__ == "__main__":
    main()
