# AITC Credit Investigation Chatbot

An on-premise AI system for Taiwan credit investigation, running on NVIDIA DGX Spark.
Queries Taiwan FSC XBRL financial data using Breeze2-8B (MediaTek Research) via vLLM.
All processing is 100% on-premise — no data leaves the network.

---

## System Architecture

```
React/Next.js (port 3000)
    ↓ HTTP
FastAPI + LangGraph (port 3001)       ← app.py
    ↓ direct query
FinancialStatementXBRL.db             ← Taiwan FSC XBRL data (SQLite)

Claude Code Plugin (separate)
    ↓ MCP SSE
FastMCP Server (port 8091)            ← mcp-server/server.py
    ↓ read-only query
FinancialStatementXBRL.db

Breeze2-8B via vLLM (port 8080)      ← on-premise LLM
```

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/juanyuune/AITC.git
cd AITC
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual paths
```

### 3. Start all services

```bash
./start.sh
```

Or manually in separate terminals:

```bash
# Terminal 1 — Breeze2-8B model server
source ~/vllm-install/.vllm/bin/activate
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
export CUDA_HOME=/usr/local/cuda-13.0
python -m vllm.entrypoints.openai.api_server \
  --model ~/models/breeze2-8b \
  --host 0.0.0.0 --port 8080 \
  --trust-remote-code --dtype bfloat16 \
  --max-model-len 32768 --gpu-memory-utilization 0.85

# Terminal 2 — FastAPI backend
python app.py

# Terminal 3 — React frontend
yarn dev

# Terminal 4 — MCP server (for Claude Code plugin)
source ~/mcp-venv/bin/activate
export XBRL_DB_PATH=/home/user/AITC/FinancialStatementXBRL.db
export XBRL_PORT=8091
python mcp-server/server.py
```

### 4. Access the chatbot

Open `http://192.168.20.169:3000` in your browser (LAN only).

---

## Project Structure

```
AITC/
├── app.py                          # FastAPI entry point (port 3001)
├── start.sh                        # One-command startup script
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variable template
├── CLAUDE.md                       # Full architecture docs for Claude Code
│
├── mcp-server/                     # XBRL MCP server for Claude Code plugin
│   ├── server.py                   # FastMCP SSE server (port 8091)
│   ├── requirements.txt
│   └── README.md
│
├── plugins/                        # Claude Code plugin
│   └── aitc-credit-investigation/
│       ├── .mcp.json               # Points to localhost:8091
│       ├── skills/                 # 10 Traditional Chinese skill files
│       └── commands/               # 6 slash commands
│
├── src/
│   ├── agent/
│   │   ├── graph.py                # LangGraph pipeline definition
│   │   └── nodes/
│   │       ├── unified_question_analyzer.py  # 1 LLM call replaces 4
│   │       ├── exact_query.py                # XBRL exact value lookup
│   │       ├── semantic_retrieval.py         # Semantic search fallback
│   │       ├── dispatch_node.py              # Private vs cloud routing
│   │       ├── generate_answer_onpremise.py  # On-premise answer path
│   │       └── generate_answer_cloud.py      # Cloud answer path (future)
│   ├── api/
│   │   └── chatbot.py              # /chatbot/{user_input} endpoint
│   ├── services/
│   │   ├── query_cache.py          # Answer cache with 24h TTL
│   │   ├── concept_map.py          # XBRL concept ID fast lookup
│   │   ├── data_classifier_config.py  # Private vs public source rules
│   │   ├── vector_candidate_search.py
│   │   └── account_title_matcher.py
│   └── types/
│       └── langgraph_state_types.py
│
└── scripts/
    └── build_xbrl_sql.py           # Import XBRL reports into the DB
```

---

## Claude Code Plugin

The `plugins/aitc-credit-investigation/` folder is a Traditional Chinese Claude Code plugin
that connects to the XBRL database via the MCP server.

To use it:

```bash
# Make sure the MCP server is running (Terminal 4 above)
cd plugins/aitc-credit-investigation
claude
```

Available commands inside Claude Code:

| Command | Description |
|---|---|
| `xbrl [company] [period] [field]` | Query a specific financial figure |
| `credit-report [company] [year]` | Generate full credit investigation report |
| `trend [company] [field] [year]` | Multi-quarter trend analysis |
| `ratio [company] [period]` | Calculate key financial ratios |
| `peer [companies...] [field] [period]` | Cross-company comparison |
| `risk [company] [year]` | Full-year risk assessment |

---

## Importing XBRL Data

To import a new XBRL report into the database:

```bash
python3 scripts/build_xbrl_sql.py \
  --taxonomy-root /path/to/tifrs-20200630 \
  --instance /path/to/report.xbrl \
  --sql-output ./output/report_import.sql
```

After importing new data, clear the answer cache so fresh results are generated:

```python
from src.services.query_cache import clear_cache
clear_cache()
```

---

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `VLLM_BASE_URL` | `http://localhost:8080/v1` | vLLM server URL |
| `VLLM_MODEL` | `/home/user/models/breeze2-8b` | Model path |
| `XBRL_DB_PATH` | `FinancialStatementXBRL.db` | XBRL database path |
| `XBRL_PORT` | `8091` | MCP server port |
| `CACHE_TTL_HOURS` | `24` | Answer cache TTL |
| `PIPELINE_TIMEOUT` | `180` | Max seconds per query |

---

## Database

`FinancialStatementXBRL.db` — SQLite, ~95MB, Taiwan FSC XBRL data.

| Table | Rows | Description |
|---|---|---|
| `report_instance` | 102 | One row per filed report |
| `financial_metric_value` | 198,511 | Core financial data |
| `field_dictionary` | 1,149 | Financial field definitions |
| `field_concept_mapping` | 8,604 | Field → XBRL concept mappings |
| `taxonomy_concept` | 10,351 | XBRL taxonomy concepts |

> The database is excluded from git (see `.gitignore`). Contact the team for the latest copy.

---

## Tech Stack

| Component | Technology |
|---|---|
| LLM | Breeze2-8B (MediaTek Research) via vLLM |
| Agent framework | LangGraph |
| Backend | FastAPI (Python) |
| Frontend | React / Next.js |
| Database | SQLite (FinancialStatementXBRL.db) |
| MCP server | FastMCP (SSE transport) |
| Claude plugin | Claude Code v2.1.185 |
| Hardware | NVIDIA DGX Spark |
| Network | LAN only (192.168.20.169) |
