# AITC Credit Investigation Chatbot

On-premise AI system for Taiwan FSC credit investigation, built on NVIDIA DGX Spark.
Uses Breeze2-8B (MediaTek Research) via vLLM — no data leaves the machine.

I built this because our analysts were spending 20–30 minutes per company pulling numbers
from MOPS, copying them into Excel, and manually computing ratios. The XBRL data was
already there in a structured database. This connects it to an AI that can query it,
reason about it, and write the report.

---

## How it actually works

Two separate systems share the same SQLite database:

```
Chatbot (internal UI)                    Claude Code Plugin
─────────────────────────────            ─────────────────────────────
React frontend (port 3000)               claude (in plugin directory)
    ↓                                        ↓
FastAPI + LangGraph (port 3001)          MCP server (port 8091)
    ↓ direct SQLite                          ↓ read-only SQLite
FinancialStatementXBRL.db  ←────────────────┘
    ↑
Breeze2-8B via vLLM (port 8080)
```

The chatbot uses Breeze2 for everything — fully on-prem, zero API calls.
The Claude Code plugin uses Claude Sonnet via Anthropic API for reasoning,
but the database stays local. Only the query text goes out.

---

## Setup

### Prerequisites

- NVIDIA DGX Spark with CUDA 13
- Breeze2-8B model weights at `~/models/breeze2-8b`
- Node.js 20+ (already on DGX)
- Python 3.12

### Install

```bash
git clone https://github.com/juanyuune/AITC.git
cd AITC
pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum set VLLM_MODEL to your actual model path
```

### Start everything

```bash
./start.sh
```

This starts all 4 services in the right order. vLLM takes 2–3 minutes to load
the model into GPU memory — the script waits for it before continuing.

# Validate the plugin after install
python3 plugins/aitc-credit-investigation/aitc-check.py

If you need to start manually (debugging, or if start.sh fails):

```bash
# Terminal 1 — vLLM (start this first, takes 2-3 min)
source ~/vllm-install/.vllm/bin/activate
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
export CUDA_HOME=/usr/local/cuda-13.0
python -m vllm.entrypoints.openai.api_server \
  --model ~/models/breeze2-8b \
  --host 0.0.0.0 --port 8080 \
  --trust-remote-code --dtype bfloat16 \
  --max-model-len 32768 --gpu-memory-utilization 0.85

# Terminal 2 — MCP server (independent of vLLM)
source ~/mcp-venv/bin/activate
export XBRL_DB_PATH=/home/user/AITC/FinancialStatementXBRL.db
python mcp-server/server.py

# Terminal 3 — backend
python app.py

# Terminal 4 — frontend
yarn dev
```

Chatbot UI: http://192.168.20.169:3000

---

## Claude Code Plugin

The plugin lives in `plugins/aitc-credit-investigation/`. It gives Claude Code
direct access to the XBRL database via MCP — Traditional Chinese, on-premise.

```bash
# MCP server must be running first (Terminal 2 above)
cd plugins/aitc-credit-investigation
claude
```

Commands available inside Claude Code:

```
credit-report 台泥 2024          # full 6-section credit report
xbrl 2303 2024Q3 營業收入        # single financial figure
trend 台泥 營業收入 2024          # multi-quarter trend with YoY
ratio 2303 2024Q3                # financial ratio analysis
peer 1101 1102 1103 資產總計 2024Q3  # peer comparison
risk 2303 2024                   # full-year risk scan
```

Skills fire automatically from natural language — you don't have to use the commands.
"聯電最近財務怎樣" will trigger the right skill without any explicit command.

---

## Project layout

```
AITC/
├── app.py                    # FastAPI entry point
├── start.sh                  # Start all 4 services
├── requirements.txt          # Python deps (see note on mcp-server/)
├── .env.example              # All env vars documented here
├── CLAUDE.md                 # Architecture notes for Claude Code
├── AITC_Startup_Guide.md     # Startup reference card
│
├── mcp-server/               # Standalone MCP server
│   ├── server.py             # FastMCP SSE, port 8091
│   └── requirements.txt      # Separate venv (mcp-venv)
│
├── plugins/
│   └── aitc-credit-investigation/
│       ├── README.md         # Plugin-specific docs
│       ├── aitc-check.py     # Validator (80 checks)
│       ├── skills/           # 10 SKILL.md files
│       └── commands/         # 6 command files
│
└── src/
    ├── agent/
    │   ├── graph.py          # LangGraph pipeline
    │   └── nodes/
    │       ├── unified_question_analyzer.py  # 1 LLM call does what 4 used to
    │       ├── exact_query.py                # concept_map → vector → keyword
    │       ├── semantic_retrieval.py
    │       ├── dispatch_node.py              # routes private vs cloud
    │       ├── generate_answer_onpremise.py
    │       └── generate_answer_cloud.py      # stub — not yet wired
    ├── api/
    │   └── chatbot.py        # /chatbot/{input}, 180s timeout, 24h cache
    └── services/
        ├── query_cache.py    # TTL-based cache, auto-purges expired entries
        ├── concept_map.py    # direct XBRL concept ID lookup (score=100, no LLM)
        ├── data_classifier_config.py   # what's private vs public
        ├── vector_candidate_search.py
        └── account_title_matcher.py    # SequenceMatcher fallback
```

---

## The query pipeline

When an analyst asks something, here's what happens:

```
user question
    ↓
unified_question_analyzer   1 LLM call: classify type + statement type
    ↓
    ├── EXACT_QUERY → exact_query.py
    │       ↓
    │   concept_map lookup (score=100, no LLM needed for known terms)
    │       ↓ miss
    │   vector search (semantic similarity on prebuilt embeddings)
    │       ↓ miss
    │   SequenceMatcher keyword fallback
    │       ↓
    │   fetch from FinancialStatementXBRL.db
    │       ↓
    │   LLM formats the answer
    │
    └── SEMANTIC → semantic_retrieval.py → same candidate stack → LLM
            ↓
        dispatch_node   checks retrieved_sources against PRIVATE_SOURCES
            ↓
        generate_answer_onpremise (all current queries go here)
```

One thing that took a while to figure out: the XBRL database stores quarters as
the string "Q3", not the integer 3. Every query that filters by quarter has to
match against `'Q3'` not `3`. The concept map and vector search both handle this,
but it's worth knowing if you're writing raw SQL.

---

## Database

`FinancialStatementXBRL.db` — SQLite, ~95MB. Not in git (too large, changes frequently).
Get the latest copy from the team or import using `scripts/build_xbrl_sql.py`.

Current coverage: 33 companies, 2022Q1–2025Q3.

| Table | Rows | What it stores |
|---|---|---|
| `financial_metric_value` | 198,511 | Core financial data — query this first |
| `field_dictionary` | 1,149 | Chinese/English field names and statement type |
| `field_concept_mapping` | 8,604 | Maps fields to XBRL concept IDs |
| `report_instance` | 102 | One row per filed report |
| `taxonomy_concept` | 10,351 | Full XBRL taxonomy |

The main query pattern is always a JOIN between `financial_metric_value`
and `field_dictionary` on `field_id`. Don't query `xbrl_fact` directly
unless you need raw XBRL values — `financial_metric_value` is the pre-processed version.

After importing new data, clear the answer cache:

```bash
python3 -c "from src.services.query_cache import clear_cache; print(clear_cache(), 'entries cleared')"
```

---

## Things that aren't obvious

**Q4 is full-year cumulative, not Q4 standalone.**
Taiwan XBRL income statement and cash flow for Q4 = Jan–Dec total.
To get Q4 standalone: subtract Q1+Q2+Q3 from Q4. The MCP server and
plugin both handle this automatically and label it as a derived value.

**Two copies of the DB exist on disk.**
`~/AITC/FinancialStatementXBRL.db` is the active one (newer timestamp).
`~/AITC-CreditInvestigationChatBotAgent/FinancialStatementXBRL.db` is older.
Always use the one in `~/AITC/`.

**Port 8091 can ghost after a crash.**
If `server.py` dies hard, the port can stay in TIME_WAIT with nothing actually
listening. `lsof -i :8091` shows nothing, but the port rejects new connections.
Fix: `fuser -k 8091/tcp` or just restart the DGX terminal session.

**33 fields in field_dictionary have empty zh_name.**
MCP tools filter by zh_name, so these fields do not appear in normal tool
results and require SQL fallback. Run:
  sqlite3 ~/AITC/FinancialStatementXBRL.db \
  "SELECT field_id, canonical_name FROM field_dictionary WHERE zh_name IS NULL OR zh_name = '';"
to see which ones. Fixing them is a data task — no code changes needed.

**The vLLM venv and the mcp-venv are separate.**
Don't install mcp packages into the vLLM venv or vice versa. The MCP server
runs in `~/mcp-venv` and needs to stay isolated.

---

## Environment variables

Full list in `.env.example`. The ones that actually matter:

| Variable | Default | Notes |
|---|---|---|
| `VLLM_MODEL` | `/home/user/models/breeze2-8b` | Path to model weights |
| `XBRL_DB_PATH` | `FinancialStatementXBRL.db` | Absolute path recommended |
| `PIPELINE_TIMEOUT` | `180` | Complex queries can take 2-3 min |
| `CACHE_TTL_HOURS` | `24` | Set to 0 to disable caching |
| `XBRL_PORT` | `8091` | Change if port is stuck |

---

## Tech stack

| Layer | Technology | Why |
|---|---|---|
| LLM | Breeze2-8B via vLLM | Best Traditional Chinese performance on local hardware |
| Agent | LangGraph | Good for multi-step pipelines with conditional routing |
| Backend | FastAPI | Fast, async, good for streaming responses |
| Frontend | Next.js | Team already knew it |
| DB | SQLite | XBRL data is read-heavy, single-file, no concurrent writes |
| MCP server | FastMCP SSE | Standard Claude Code MCP transport |
| Plugin | Claude Code | Analyst-facing interface with skill auto-triggering |
| Hardware | NVIDIA DGX Spark | ~40 tokens/sec on Breeze2-8B at bfloat16 |
