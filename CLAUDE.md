# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the backend server
python app.py                  # starts FastAPI on port 3001

# Run the MCP server (separate venv)
source ~/mcp-venv/activate
export XBRL_DB_PATH=/home/user/AITC/FinancialStatementXBRL.db
export XBRL_PORT=8091
python mcp-server/server.py

# Build XBRL embeddings cache (run after DB updates)
python scripts/build_xbrl_embeddings.py

# Build XBRL SQL inserts from taxonomy + instance file
python3 scripts/build_xbrl_sql.py \
  --taxonomy-root /path/to/tifrs-20200630 \
  --instance /path/to/report.xbrl \
  --sql-output ./output/report_import.sql

# Query the chatbot directly (no cache)
curl "http://localhost:3001/chatbot/聯電2024Q3現金?bypass_cache=true"
```

There is no test suite in this repository.

## Architecture

The system is a **LangGraph-based financial Q&A agent** for Taiwan credit investigation, backed by a local SQLite XBRL database. All LLM calls go to an on-premise vLLM endpoint (Breeze2-8B by default); there is no cloud LLM call unless all retrieved data sources are classified as public.

### Request flow

```
GET /chatbot/{user_input}
  → query_cache (MD5 keyed, src/services/query_result_cache.json)
  → LangGraph graph.invoke()
      → unified_question_analyzer   # 1 LLM call: rephrase + classify type + classify statement
          ↓ EXACT_QUERY                         ↓ SEMANTIC/ANALYSIS/DECISION
      exact_query                       semantic_retrieval
        (rule-based extraction,           (multi-period LLM plan,
         concept_map → vector → keyword,   same candidate search stack,
         SQL on XBRL.db)                   SQL on XBRL.db)
          ↓                                     ↓
      dispatch_node   ← checks retrieved_sources vs PRIVATE_SOURCES / PUBLIC_SOURCES
          ↓ "private"                           ↓ "cloud"
      generate_answer_onpremise       generate_answer_cloud
      (pass-through, tags state)      (pass-through, tags state — cloud model not yet wired)
```

### Candidate resolution stack (both exact_query and semantic_retrieval)

For each requested financial field, the system tries three paths in order:
1. **concept_map** (`src/services/concept_map.py`) — hardcoded zh→`ifrs-full_*` / `tifrs-bsci-ci_*` lookup; score = 100, no LLM needed.
2. **vector search** (`src/services/vector_candidate_search.py`) — cosine similarity on prebuilt embeddings cache; provider set by `EMBEDDING_PROVIDER` env var (`ollama` uses `nomic-embed-text`, `openai` uses `text-embedding-3-small`).
3. **keyword/SequenceMatcher** (`src/services/account_title_matcher.py`) — fallback when embeddings cache is empty.

After candidate collection: `MIN_CANDIDATE_SCORE = 45.0` filters out weak matches; if top candidate has `score >= CHINESE_TRUST_SCORE (50.0)` and the query is Chinese, LLM disambiguation is skipped.

### Database

`FinancialStatementXBRL.db` (SQLite, project root, **not version-controlled**) is the single source of all financial data. Key tables: `report_instance`, `field_dictionary`, `financial_metric_value`, `xbrl_fact`. Both the main app and the MCP server open it in read-only mode.

After updating the DB, call `clear_cache()` from `src/services/query_cache.py` and rebuild embeddings.

### Data privacy / dispatch

`src/services/data_classifier_config.py` is the single source of truth. `FinancialStatementXBRL.db` is in `PRIVATE_SOURCES` → all queries against it route to `generate_answer_onpremise`. Only sources explicitly listed in `PUBLIC_SOURCES` (e.g. `published_xbrl_reports`) can route to cloud.

### LLM provider

`src/providers/chat_openAI_provider.py` instantiates a `ChatOpenAI` client pointing at the local vLLM endpoint. Controlled by:

| Env var | Default |
|---------|---------|
| `VLLM_BASE_URL` | `http://localhost:8080/v1` |
| `VLLM_MODEL` | `/home/user/models/breeze2-8b` |
| `VLLM_TEMPERATURE` | `0.1` |
| `VLLM_MAX_TOKENS` | `2048` |

### MCP server

`mcp-server/server.py` exposes the same XBRL database via FastMCP SSE (port 8091) for Claude Code tool use. It runs in its own venv (`~/mcp-venv`). The `.mcp.json` in `plugins/aitc-credit-investigation/` registers it.

### CORS origins

Hardcoded in `app.py`: `localhost:3000`, `127.0.0.1:3000`, `192.168.20.169:3000/3001`. Add new frontend origins there.

## Plugin Commands

These shortcuts are for analysts using Claude Code with the `xbrl-taiwan` MCP connected. Every command must:
- Query `xbrl-taiwan` MCP as the data source — never use web search for financial figures.
- Respond entirely in **繁體中文 (Traditional Chinese)**.
- End every response with: `⚠️ 本資料僅供參考，財務決策請以公司正式公告及專業人員審查為準。`
- **Q4 data is cumulative full-year**, not a standalone fourth quarter. Always state the period clearly (e.g., 累計至 2024/12/31).

---

### `xbrl [company] [period] [field]`

Query a single financial field for a company and period.

- `company`: stock code (e.g. `2303`) or company name (e.g. `聯電`)
- `period`: `2024Q1` … `2024Q4` (Q4 = cumulative full-year)
- `field`: Chinese field name, e.g. `現金及約當現金`, `營業收入`, `負債總計`

Call `mcp__xbrl-taiwan__get_company_financials` with the resolved code and period. Filter to the requested field. Report the value with unit and period date. If the field is not found, say so explicitly rather than returning zero.

---

### `credit-report [company] [year]`

Generate a full credit investigation report covering all four quarters of the given year.

Call `mcp__xbrl-taiwan__get_company_financials` for Q1–Q4 of the year (four calls). Organise the output into these sections, each populated from the retrieved data:

1. **公司基本資料** — name, stock code, period coverage
2. **資產負債結構** — 資產總計, 負債總計, 股東權益, 負債比率, 自有資本率
3. **獲利能力** — 營業收入, 毛利率, 營業利益率, 本期淨利, EPS
4. **現金流量** — 營業/投資/籌資活動現金流量, 期末現金餘額
5. **季度趨勢摘要** — one-sentence characterisation of Q1→Q4 trajectory
6. **徵信風險提示** — flag any quarters with negative operating cash flow, negative net income, or debt ratio above 60%

---

### `trend [company] [field] [year]`

Show the value of a single financial field across all four quarters of the year.

Call `mcp__xbrl-taiwan__get_trend_data` or four `get_company_financials` calls (Q1–Q4). Present results as a table with columns 季度 | 數值 | 單位. Add a one-line trend observation (成長／衰退／持平) below the table. Remind the reader that Q4 is cumulative full-year when applicable.

---

### `ratio [company] [period]`

Calculate key financial ratios from raw XBRL figures for one period.

Call `mcp__xbrl-taiwan__get_company_financials`. Derive and present the following ratios (show formula and computed value):

| 比率 | 公式 |
|------|------|
| 負債比率 | 負債總計 ÷ 資產總計 |
| 流動比率 | 流動資產 ÷ 流動負債 |
| 毛利率 | 營業毛利 ÷ 營業收入 |
| 營業利益率 | 營業利益 ÷ 營業收入 |
| 淨利率 | 本期淨利 ÷ 營業收入 |
| ROE（當期） | 本期淨利 ÷ 股東權益 |

If any input field is missing from the data, skip that ratio and note it as 資料不足.

---

### `peer [company1] [company2...] [field] [period]`

Compare the same financial field across multiple companies for one period.

Call `mcp__xbrl-taiwan__compare_companies` or one `get_company_financials` per company. Present results as a ranked table: 公司 | 股票代碼 | 數值 | 單位. Rank by value descending. Add a one-sentence observation on the spread. All companies must share the same period; if a company has no data for that period, mark it as 無資料.

---

### `risk [company] [year]`

Quick risk assessment: scan all four quarters and flag adverse signals.

Call `get_company_financials` for Q1–Q4. Check each quarter against these thresholds and list every triggered flag:

| 指標 | 風險門檻 |
|------|---------|
| 負債比率 | > 60% |
| 流動比率 | < 1.0 |
| 營業活動現金流量 | < 0 |
| 本期淨利 | < 0 |
| 營業收入季增率 | < −20% (連續兩季) |

Output a 交通燈號 summary — 🟢 無明顯風險 / 🟡 需關注 (1–2 flags) / 🔴 高風險 (3+ flags) — followed by the flagged items with the quarter and value that triggered each one.
