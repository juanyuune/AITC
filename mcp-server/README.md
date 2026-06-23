# AITC XBRL MCP Server

FastMCP SSE server wrapping `FinancialStatementXBRL.db` on port 8090.
Runs 100% on-prem on the DGX Spark — no data leaves the machine.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set the database path

```bash
export XBRL_DB_PATH=/your/path/to/FinancialStatementXBRL.db
```

Or copy `.env.example` to `.env` and fill it in, then load it:

```bash
cp .env.example .env
# edit .env
set -a && source .env && set +a
```

### 3. Adjust column names (if needed)

Open `server.py` and find the `COL` dict near the top. If your table or
column names differ from the defaults, change them here — no other edits needed.

```python
COL = {
    "table":          "financial_data",   # ← your main table name
    "stock_code":     "stock_code",       # ← your stock code column
    ...
}
```

**Not sure of your schema?** Start the server and call the `get_schema` tool first —
it returns all table names, column names, types, and row counts.

### 4. Start the server

```bash
python server.py
```

You should see:
```
XBRL DB path : /your/path/FinancialStatementXBRL.db
MCP transport: SSE
Listening on : http://0.0.0.0:8090
SSE endpoint : http://0.0.0.0:8090/sse
```

---

## Updating .mcp.json

Your plugin's `.mcp.json` should already point to this server.
If it doesn't, update the connector entry:

```json
{
  "mcpServers": {
    "xbrl-taiwan": {
      "type": "sse",
      "url": "http://localhost:8090/sse"
    }
  }
}
```

---

## Tools Exposed

| Tool | Purpose |
|---|---|
| `get_schema` | Inspect DB tables, columns, row counts |
| `list_companies` | List all companies (optional name filter) |
| `list_periods` | List available periods for a company |
| `get_company_financials` | Single company × single period query |
| `get_trend_data` | Multi-period trend for a company |
| `compare_companies` | Cross-company comparison, single concept |
| `run_read_query` | Escape hatch — raw SELECT query |

---

## Architecture

```
Terminal 1: python server.py           → port 8090 (MCP SSE, this server)
Terminal 2: vLLM Breeze2-8B            → port 8080 (AI model)
Terminal 3: python app.py              → port 3001 (FastAPI + LangGraph)
Terminal 4: yarn dev                   → port 3000 (React/Next.js frontend)
```

---

## Notes

- **Q4 caveat:** Q4 data is cumulative (full-year), not standalone Q4.
  The server automatically adds a warning when Q4 periods appear in results.
- **Read-only:** The server opens SQLite in read-only mode (`?mode=ro`).
  Write operations are impossible by design.
- **run_read_query:** Only `SELECT` is allowed — INSERT/UPDATE/DELETE/DROP are blocked.
