# AITC 台灣信用調查外掛程式

Traditional Chinese Claude Code plugin for Taiwan credit investigation.
Connects to an on-premise XBRL database via MCP — no data leaves the DGX Spark.

---

## Install & Run

```bash
# Make sure the MCP server is running first
source ~/mcp-venv/bin/activate
export XBRL_DB_PATH=/home/user/AITC/FinancialStatementXBRL.db
export XBRL_PORT=8091
python /home/user/AITC/mcp-server/server.py

# Then launch Claude Code from this directory
cd plugins/aitc-credit-investigation
claude
```

---

## Skills (10)

Skills fire automatically when relevant. Claude draws on them without you needing to name them.

| Skill | Fires when you ask about... |
|---|---|
| `data-sources` | Any financial data query — enforces MCP-first rule |
| `xbrl-query` | Specific financial figures, account balances, single-period data |
| `company-profile` | Company background, available periods, industry classification |
| `balance-sheet` | Assets, liabilities, equity, cash position, debt structure |
| `income-statement` | Revenue, gross profit, operating income, net income, EPS |
| `cash-flow` | Operating/investing/financing cash flows, free cash flow |
| `ratio-analysis` | Financial ratios, debt ratio, current ratio, ROE, margins |
| `trend-analysis` | Multi-quarter trends, growth rates, YoY comparisons |
| `peer-comparison` | Industry benchmarking, comparing multiple companies |
| `credit-memo` | Full credit investigation report,徵信報告, credit assessment |

---

## Commands (6)

Invoke explicitly by typing the command name.

| Command | Usage | Description |
|---|---|---|
| `xbrl` | `xbrl 台泥 2024Q3 營業收入` | Single financial figure lookup |
| `credit-report` | `credit-report 2303 2024` | Full 6-section credit report |
| `trend` | `trend 台泥 營業收入 2024` | Multi-quarter trend with YoY |
| `ratio` | `ratio 2303 2024Q3` | Key financial ratio calculation |
| `peer` | `peer 1101 1102 1103 資產總計 2024Q3` | Cross-company comparison |
| `risk` | `risk 2303 2024` | Full-year risk scan, traffic-light rating |

---

## Data Source

All financial data comes from `FinancialStatementXBRL.db` — Taiwan FSC XBRL filings.
198,511 rows across 10 companies, covering 2024Q1–2025Q3.

**Web search is never used for financial figures.**

---

## Key Rules Built into Every Skill

1. **MCP first** — always query xbrl-taiwan MCP before anything else
2. **No web search** for financial data
3. **Q4 caveat** — Q4 figures are full-year cumulative, not standalone Q4
4. **Disclaimer** — every financial output ends with human-review disclaimer
5. **Traditional Chinese** — all output in 繁體中文
6. **Confirm before continuing** — show data to user after each major step

---

## Validator

```bash
python3 aitc-check.py
```

Expected output: `Checked N item(s). OK — all checks passed, 0 issues.`
