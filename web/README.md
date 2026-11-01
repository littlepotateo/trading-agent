# TradingAgents web app

A browser front end for TradingAgents. You pick a ticker and a date, choose the analysts and models, and the app runs the same multi-agent pipeline the command-line tool runs. It shows progress as it goes, then the rating and each agent's report.

The app is two files:

- `server.py`: a small local server. It runs the analysis and serves the page. It uses only the Python standard library.
- `index.html`: the page itself. It needs no internet connection beyond the server.

> **This is a research tool.** Runs call paid language-model APIs, so each one costs money. Results are not financial advice.

---

## 1. Before you start

You need:

- **Python 3.11 or later.** The upstream project recommends 3.13.
- **An API key for at least one model provider**, such as OpenAI, Anthropic, or Google. Ollama needs no key if you run it locally.
- **The TradingAgents package installed.** The web app imports it, so it must be installed in the same Python environment you use to run the server.

### Install the TradingAgents package

The package comes from the upstream project, [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents). Clone it, then install it:

```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install .
```

This installs the dependencies listed in the project's `pyproject.toml`, including LangGraph, yfinance, and the model provider clients.

### Add your API keys

Put keys in a `.env` file in the folder where you start the server, or export them in your shell. For example, a `.env` file:

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
FRED_API_KEY=...          # optional: macro data
ALPHA_VANTAGE_API_KEY=... # optional: alternative data vendor
```

Only the key for the provider you select is required. The page shows a warning under the provider list when the key for that provider is missing.

---

## 2. Start the server

From the folder that contains `web/` (this repository's root):

```bash
python -m web.server
```

You should see:

```
TradingAgents web app on http://127.0.0.1:8765
```

Open **http://127.0.0.1:8765** in your browser.

Options:

| Flag | Default | What it does |
|------|---------|--------------|
| `--port` | `8765` | Port to listen on, for example `--port 9000` |
| `--host` | `127.0.0.1` | Address to listen on. Keep the default so only your machine can reach it. |

Stop the server with `Ctrl+C`.

---

## 3. Run an analysis

The form has four sections.

**1. What to analyse**
- **Ticker:** Any Yahoo Finance symbol. Examples: `NVDA`, `AAPL`, `0700.HK`, `7203.T`, `AZN.L`, `600519.SS`, `BTC-USD`.
- **Analysis date:** The date to analyse, in the past or today. Future dates are rejected. Past dates use only data available on that day.

**2. Analysts**
Pick one or more. Fewer analysts means faster runs and lower cost.
- **Market:** price action and technical indicators
- **Sentiment:** StockTwits and Reddit chatter
- **News:** company and macro headlines
- **Fundamentals:** filings and financial statements

**3. Models**
- **LLM provider:** the provider to call. Providers without a key are marked "(no key set)".
- **Quick model:** used by the analysts, researchers, and trader. Choose a cheaper, faster model.
- **Deep model:** used by the research and portfolio managers. Choose a stronger model.

The model fields suggest names for the chosen provider, but you can type any model ID that your provider serves.

**4. Depth**
- **Research debate rounds** and **Risk debate rounds** (1 to 5 each): how many times the bull and bear researchers, and the risk analysts, argue before a decision. More rounds mean more calls and more cost.
- **Save reports to disk:** on by default. See "Where results go" below.

Press **Run analysis**. The right-hand panel then shows:

- **The rating:** Buy, Overweight, Hold, Underweight, or Sell. "Review" means no rating could be read from the final decision, so read the text yourself.
- **Progress steps:** Analysts, Research debate, Trader, Risk debate, Portfolio manager. Each turns green when it finishes.
- **A live log** of what has finished, including any error.
- **Report tabs:** one tab per agent, with its full write-up. Click a tab to read it.

A run typically takes several minutes, depending on the provider and the number of analysts and debate rounds.

### Recent runs

The **Recent runs** list on the left shows up to the last 50 runs since the server started. Click one to reopen its progress and reports. The list is kept in memory, so it is cleared when you restart the server. Saved reports stay on disk.

### One run at a time

The server runs one analysis at a time. If you start another while one is running, it waits its turn, so the second run may sit at "queued" for a while.

---

## 4. Where results go

Results are written under the TradingAgents data folder, which defaults to `~/.tradingagents/`:

| What | Where |
|------|-------|
| Saved reports (when "Save reports to disk" is on) | `~/.tradingagents/logs/` |
| Decision memory, used by later runs | `~/.tradingagents/memory/trading_memory.md` |
| Checkpoints (not used by the web app) | `~/.tradingagents/cache/` |

Each saved run includes the per-agent markdown files, a combined `complete_report.md`, and a `complete_report.html` page you can open in a browser. The server prints the saved location in the run panel.

Change the folder with environment variables before starting the server:

```bash
export TRADINGAGENTS_RESULTS_DIR=/path/to/reports
export TRADINGAGENTS_MEMORY_LOG_PATH=/path/to/trading_memory.md
```

---

## 5. Other settings

Any `TRADINGAGENTS_*` environment variable works, because the server starts from the project's default configuration. The most useful ones:

| Variable | Effect |
|----------|--------|
| `TRADINGAGENTS_LLM_PROVIDER` | Default provider shown in the form |
| `TRADINGAGENTS_QUICK_THINK_LLM` / `TRADINGAGENTS_DEEP_THINK_LLM` | Default models shown in the form |
| `TRADINGAGENTS_OUTPUT_LANGUAGE` | Language for reports, for example `Japanese` |
| `TRADINGAGENTS_TEMPERATURE` | Sampling temperature. Lower values give more repeatable output on models that honour it. |
| `TRADINGAGENTS_LLM_BACKEND_URL` | Endpoint for a local or self-hosted model server |

The form's own choices (provider, models, rounds, analysts) override the defaults for each run.

The default provider and models are `openai` with `gpt-6-sol` (deep) and `gpt-6-luna` (quick). Check the upstream README for current model names, and see `tradingagents/default_config.py` for every option.

---

## 6. Using the hosted artifact

There is also a hosted copy of the page on claude.ai, linked from the PR. It cannot run analyses by itself: a claude.ai page is not allowed to call `127.0.0.1` in your browser. To use it, keep this server running on your machine, and enter `http://127.0.0.1:8765` in the **API address** box at the top right, then press **Connect**. Treat the hosted copy as a preview unless your browser allows the connection.

The server only accepts calls from `localhost` and from claude.ai origins. Other websites are refused, so a page you visit cannot start runs with your keys.

---

## 7. Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Red dot and "Can't reach the TradingAgents server" | The server is not running, or it is on a different port. Start it with `python -m web.server`, then press **Connect**. |
| "API key for provider 'openai' is not set" | Add the key to `.env` in the folder you started the server from, or export it, then restart the server. |
| `ModuleNotFoundError: No module named 'tradingagents'` | The package is not installed in this Python environment. Run `pip install .` in the TradingAgents clone, using the same environment. |
| Run ends with "Review" or an error about the decision | The model's final answer did not contain a readable rating. Re-run, or use a stronger deep model. |
| A run is stuck on "Running…" for a long time | Slow providers and multiple debate rounds take time. Check the live log for progress. Provider rate limits can also slow it. |
| Port 8765 already in use | Start on another port with `python -m web.server --port 9000`, then enter that address in **API address**. |
| Data for a date returns nothing or is withheld | Some sources only give current data. Runs dated in the past read statements as filed on that day, and some insider data is withheld for past dates. The reports say what was unavailable. |

When something fails, the server's console shows the full traceback, which is the best place to look.

---

## 8. How it is built

The server calls the same functions as the command-line tool, in the same order: it creates the run state, streams the graph to get progress, records the decision in the memory log, and writes the report tree. The page polls the server every two seconds for progress.

The app is intentionally simple: no database, no build step, no front-end dependencies, and no new Python packages beyond the TradingAgents package itself.
