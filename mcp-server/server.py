"""
AITC XBRL MCP Server
====================
FastMCP SSE transport — port 8091
Schema: Taiwan FSC XBRL normalized DB (FinancialStatementXBRL.db)

Start:
    source ~/mcp-venv/bin/activate
    export XBRL_DB_PATH=/home/user/AITC/FinancialStatementXBRL.db
    export XBRL_PORT=8091
    python server.py
"""

import os
import json
import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("aitc-xbrl-mcp")

DB_PATH     = os.environ.get("XBRL_DB_PATH", "FinancialStatementXBRL.db")
SERVER_HOST = os.environ.get("XBRL_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("XBRL_PORT", "8091"))

mcp = FastMCP(
    name="aitc-xbrl",
    instructions=(
        "Taiwan FSC XBRL financial database for AITC credit investigation. "
        "ALWAYS query this MCP first — never use web search for financial data. "
        "Q4 data is cumulative (full-year), not standalone Q4. "
        "Always note unit and period in responses. "
        "Always include a human-review disclaimer on financial outputs."
    ),
)

@contextmanager
def get_db():
    db_path = os.path.expanduser(DB_PATH)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def rows_to_list(rows) -> list:
    return [dict(row) for row in rows]


# Stock code → Chinese company name mapping (Taiwan FSC institutions)
_COMPANY_NAMES = {
    "2801": "彰化商業銀行股份有限公司",
    "2809": "京城商業銀行股份有限公司",
    "2812": "台中商業銀行股份有限公司",
    "2834": "臺灣企銀股份有限公司",
    "2838": "聯邦商業銀行股份有限公司",
    "2845": "遠東國際商業銀行股份有限公司",
    "2850": "新光人壽保險股份有限公司",
    "2851": "寶瑞人壽保險股份有限公司",
    "2852": "第一金人壽保險股份有限公司",
    "2867": "三商美邦人壽保險股份有限公司",
    "2880": "華南金融控股股份有限公司",
    "2881": "富邦金融控股股份有限公司",
    "2882": "國泰金融控股股份有限公司",
    "2883": "開發金融控股股份有限公司",
    "2884": "玉山金融控股股份有限公司",
    "2885": "元大金融控股股份有限公司",
    "2886": "兆豐金融控股股份有限公司",
    "2887": "台新金融控股股份有限公司",
    "2888": "新光金融控股股份有限公司",
    "2889": "國票金融控股股份有限公司",
    "2890": "永豐金融控股股份有限公司",
    "2891": "中國信託金融控股股份有限公司",
    "2892": "第一金融控股股份有限公司",
    "2905": "三商金融控股股份有限公司",
    "5834": "保誠人壽保險股份有限公司",
    "5876": "上海商業儲蓄銀行股份有限公司",
}

def get_company_name(company_code: str) -> str:
    return _COMPANY_NAMES.get(company_code, f"股票代號{company_code}")


def parse_period(period: str) -> tuple:
    """
    Parse period string into (year, quarter_str).
    quarter is stored as 'Q1','Q2','Q3','Q4' or '' for annual.
    Accepts: '2024Q3', '2024q3', '2024'
    """
    period = period.strip().upper()
    if "Q" in period:
        parts = period.split("Q")
        return int(parts[0]), f"Q{parts[1]}"
    else:
        return int(period), ""


def fmt_period(year: int, quarter: str) -> str:
    return f"{year}{quarter}" if quarter else str(year)


# Single-quarter date windows for Taiwan XBRL
_Q_WINDOWS = {
    "Q1": ("01-01", "03-31"),
    "Q2": ("04-01", "06-30"),
    "Q3": ("07-01", "09-30"),
    "Q4": ("10-01", "12-31"),
}


def period_bounds(year: int, quarter: str):
    """Return (single_start, single_end, ytd_start, ytd_end) ISO date strings."""
    if quarter in _Q_WINDOWS:
        s_mmdd, e_mmdd = _Q_WINDOWS[quarter]
        single_start = f"{year}-{s_mmdd}"
        single_end = f"{year}-{e_mmdd}"
    else:
        single_start = f"{year}-01-01"
        single_end = f"{year}-12-31"
    ytd_start = f"{year}-01-01"
    ytd_end = single_end
    return single_start, single_end, ytd_start, ytd_end


def q4_caveat(quarters: list) -> Optional[str]:
    if "Q4" in quarters:
        return (
            "⚠️  Q4注意：Q4數據為全年累計值（非單季），"
            "如需計算單季Q4請以全年減去Q1+Q2+Q3。"
        )
    return None


@mcp.tool()
def get_schema() -> str:
    """Inspect the XBRL database — returns all table names, columns, and row counts."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cur.fetchall()]
            result = {}
            for table in tables:
                cur.execute(f"PRAGMA table_info('{table}')")
                columns = [{"name": r[1], "type": r[2]} for r in cur.fetchall()]
                cur.execute(f"SELECT COUNT(*) FROM '{table}'")
                count = cur.fetchone()[0]
                result[table] = {"columns": columns, "row_count": count}
            return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def list_companies(name_filter: Optional[str] = None) -> str:
    """
    List all companies in the database.
    Args:
        name_filter: Optional partial match on company_code. Example: "23"
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if name_filter:
                cur.execute(
                    """
                    SELECT company_code,
                           COUNT(DISTINCT year || quarter) AS period_count,
                           MIN(year) AS first_year,
                           MAX(year) AS last_year
                    FROM report_instance
                    WHERE company_code LIKE ?
                    GROUP BY company_code ORDER BY company_code
                    """,
                    (f"%{name_filter}%",),
                )
            else:
                cur.execute(
                    """
                    SELECT company_code,
                           COUNT(DISTINCT year || quarter) AS period_count,
                           MIN(year) AS first_year,
                           MAX(year) AS last_year
                    FROM report_instance
                    GROUP BY company_code ORDER BY company_code
                    """
                )
            rows = rows_to_list(cur.fetchall())
            return json.dumps({"count": len(rows), "companies": rows}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def list_periods(company_code: str) -> str:
    """
    List all available reporting periods for a company.
    Args:
        company_code: Taiwan stock code, e.g. "2303"
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT year, quarter, report_scope, industry_type
                FROM financial_metric_value
                WHERE company_code = ?
                ORDER BY year DESC, quarter DESC
                """,
                (company_code,),
            )
            rows = rows_to_list(cur.fetchall())
            periods = [
                {
                    "period": fmt_period(r["year"], r["quarter"]),
                    "year": r["year"],
                    "quarter": r["quarter"],
                    "report_scope": r["report_scope"],
                    "industry_type": r["industry_type"],
                }
                for r in rows
            ]
            return json.dumps(
                {"company_code": company_code, "periods": periods, "count": len(periods)},
                ensure_ascii=False, indent=2,
            )
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def list_fields(statement_type: Optional[str] = None, keyword: Optional[str] = None) -> str:
    """
    List available financial fields from field_dictionary.
    Args:
        statement_type: "BS", "IS", "CF", or "EQ"
        keyword: Partial match on zh_name or en_name. Example: "營收"
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()
            where, params = [], []
            if statement_type:
                where.append("statement_type = ?")
                params.append(statement_type.upper())
            if keyword:
                where.append("(zh_name LIKE ? OR en_name LIKE ? OR canonical_name LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
            sql = "SELECT field_id, canonical_name, zh_name, en_name, statement_type, value_type FROM field_dictionary"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY statement_type, field_id"
            cur.execute(sql, params)
            rows = rows_to_list(cur.fetchall())
            return json.dumps({"count": len(rows), "fields": rows}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def get_company_financials(
    company_code: str,
    period: str,
    fields: Optional[list] = None,
    statement_type: Optional[str] = None,
) -> str:
    """
    Get financial data for a single company in a single period.
    Args:
        company_code:   Taiwan stock code, e.g. "2303"
        period:         e.g. "2024Q3" or "2024Q4"
        fields:         Optional list of zh_name or canonical_name to filter.
        statement_type: Optional — "BS", "IS", "CF", "EQ"
    """
    try:
        year, quarter = parse_period(period)
        single_start, single_end, ytd_start, ytd_end = period_bounds(year, quarter)
        where = [
            "fmv.company_code = ?",
            "fmv.year = ?",
            "fmv.quarter = ?",
            "fmv.period_start >= ?",
            "(fmv.period_end = ? OR fmv.period_end IS NULL)",
        ]
        params = [company_code, year, quarter, f"{year}-01-01", single_end]

        if fields:
            ph = ",".join("?" * len(fields))
            where.append(f"(fd.canonical_name IN ({ph}) OR fd.zh_name IN ({ph}))")
            params.extend(fields)
            params.extend(fields)
        if statement_type:
            where.append("fd.statement_type = ?")
            params.append(statement_type.upper())

        sql = f"""
            SELECT fd.statement_type, fd.zh_name, fd.canonical_name,
                   fd.en_name, fmv.value, fd.value_type, fmv.report_scope
            FROM financial_metric_value fmv
            JOIN field_dictionary fd ON fmv.field_id = fd.field_id
            WHERE {' AND '.join(where)}
            ORDER BY fd.statement_type, fd.field_id
        """
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = rows_to_list(cur.fetchall())

        return json.dumps({
            "company_code": company_code,
            "period": period,
            "year": year,
            "quarter": quarter,
            "row_count": len(rows),
            "data": rows,
            "caveat": q4_caveat([quarter]),
            "disclaimer": "本資料僅供參考，財務決策請以公司正式公告及專業人員審查為準。",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def get_trend_data(
    company_code: str,
    fields: list,
    periods: Optional[list] = None,
    last_n_periods: Optional[int] = None,
) -> str:
    """
    Multi-period trend data for a company.
    Args:
        company_code:   Taiwan stock code, e.g. "2303"
        fields:         List of zh_name or canonical_name to track.
        periods:        Explicit list, e.g. ["2024Q1","2024Q2","2024Q3"]
        last_n_periods: Fetch most recent N periods (default 8).
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()

            if periods:
                parsed = [parse_period(p) for p in periods]
            else:
                n = last_n_periods or 8
                cur.execute(
                    """
                    SELECT DISTINCT year, quarter
                    FROM financial_metric_value
                    WHERE company_code = ?
                    ORDER BY year DESC, quarter DESC LIMIT ?
                    """,
                    (company_code, n),
                )
                parsed = [(r["year"], r["quarter"]) for r in cur.fetchall()]

            if not parsed:
                return json.dumps({"error": f"No periods found for {company_code}"})

            # Build per-period clauses that also pin the period dates, so we
            # never pick prior-year comparatives or the YTD duplicate.
            clauses, period_params = [], []
            for (yy, qq) in parsed:
                s_start, s_end, _, _ = period_bounds(yy, qq)
                clauses.append(
                    "(fmv.year = ? AND fmv.quarter = ? "
                    "AND fmv.period_start = ? AND fmv.period_end = ?)"
                )
                period_params.extend([yy, qq, s_start, s_end])
            period_filter = " OR ".join(clauses)
            ph = ",".join("?" * len(fields))
            field_params = fields + fields

            sql = f"""
                SELECT fmv.year, fmv.quarter,
                       fd.zh_name, fd.canonical_name, fd.value_type, fmv.value
                FROM financial_metric_value fmv
                JOIN field_dictionary fd ON fmv.field_id = fd.field_id
                WHERE fmv.company_code = ?
                  AND ({period_filter})
                  AND (fd.canonical_name IN ({ph}) OR fd.zh_name IN ({ph}))
                ORDER BY fmv.year, fmv.quarter, fd.field_id
            """
            params = [company_code] + period_params + field_params
            cur.execute(sql, params)
            rows = rows_to_list(cur.fetchall())

        pivot = {}
        quarters_seen = []
        for row in rows:
            key = row["canonical_name"] or row["zh_name"]
            if key not in pivot:
                pivot[key] = {"zh_name": row["zh_name"], "value_type": row["value_type"], "trend": []}
            p = fmt_period(row["year"], row["quarter"])
            pivot[key]["trend"].append({"period": p, "value": row["value"]})
            quarters_seen.append(row["quarter"])

        return json.dumps({
            "company_code": company_code,
            "periods_queried": [fmt_period(y, q) for y, q in parsed],
            "fields": pivot,
            "caveat": q4_caveat(quarters_seen),
            "disclaimer": "本資料僅供參考，財務決策請以公司正式公告及專業人員審查為準。",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def compare_companies(company_codes: list, field: str, period: str) -> str:
    """
    Cross-company comparison — same field, same period, multiple companies.
    Args:
        company_codes: e.g. ["2303", "2344", "2454"]
        field:         zh_name or canonical_name
        period:        e.g. "2024Q3"
    """
    try:
        year, quarter = parse_period(period)
        single_start, single_end, _, _ = period_bounds(year, quarter)
        ph = ",".join("?" * len(company_codes))
        sql = f"""
            SELECT fmv.company_code, fd.zh_name, fd.canonical_name,
                   fmv.value, fd.value_type, fmv.report_scope
            FROM financial_metric_value fmv
            JOIN field_dictionary fd ON fmv.field_id = fd.field_id
            WHERE fmv.company_code IN ({ph})
              AND fmv.year = ? AND fmv.quarter = ?
              AND fmv.period_start = ? AND fmv.period_end = ?
              AND (fd.canonical_name = ? OR fd.zh_name = ?)
            ORDER BY CAST(fmv.value AS REAL) DESC
        """
        params = company_codes + [year, quarter, single_start, single_end, field, field]
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = rows_to_list(cur.fetchall())

        return json.dumps({
            "field_queried": field,
            "period": period,
            "results": rows,
            "caveat": q4_caveat([quarter]),
            "disclaimer": "本資料僅供參考，財務決策請以公司正式公告及專業人員審查為準。",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def run_read_query(sql: str) -> str:
    """
    Run any read-only SELECT against the XBRL database.
    Tables: financial_metric_value, field_dictionary, report_instance, taxonomy_concept
    Args:
        sql: A SELECT statement only. INSERT/UPDATE/DELETE/DROP are blocked.
    """
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH"]
    sql_upper = sql.strip().upper()
    for kw in forbidden:
        if kw in sql_upper:
            return f"錯誤：不允許的操作 ({kw})。只接受 SELECT 查詢。"
    if not sql_upper.startswith("SELECT"):
        return "錯誤：只接受 SELECT 查詢。"
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = rows_to_list(cur.fetchall())
            return json.dumps({"row_count": len(rows), "data": rows}, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"SQL錯誤：{e}"



@mcp.tool()
def get_credit_summary(company_code: str, period: str) -> str:
    """
    Get a complete credit investigation summary for one company in one call.
    Returns all key metrics: assets, liabilities, equity, net income, debt ratio,
    EPS, and cash — everything needed for a credit assessment.
    Args:
        company_code: Taiwan stock code, e.g. "2881"
        period:       e.g. "2024Q3" or "2024"
    """
    try:
        year, quarter = parse_period(period)
        q = quarter if quarter else "Q4"
        with get_db() as conn:
            # Key concepts for credit assessment
            # Concept priority order matters:
            # - Balance sheet items: use instant_date filter
            # - Income/CF items: prefer YTD (period_start=year-01-01) over single quarter
            # - 本期淨利: ProfitLossFromContinuingOperations (net income) before ProfitLoss (comprehensive)
            concepts = {
                "總資產":        ["ifrs-full_Assets", "ifrs-full_EquityAndLiabilities"],
                "總負債":        ["ifrs-full_Liabilities"],
                "股東權益":      ["ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity"],
                "本期淨利":      ["ifrs-full_ProfitLossFromContinuingOperations", "ifrs-full_ProfitLoss"],
                "稅前淨利":      ["ifrs-full_ProfitLossBeforeTax"],
                "每股盈餘":      ["ifrs-full_BasicEarningsLossPerShare"],
                "現金及約當現金": ["ifrs-full_CashAndCashEquivalents"],
                "營業活動現金流": ["ifrs-full_CashFlowsFromUsedInOperatingActivities",
                                   "tifrs-SCF_CashFlowsFromUsedInOperatingActivities"],
            }
            # Period filters: balance sheet = instant, income/CF = YTD period_start
            _INSTANT_FIELDS = {"總資產", "總負債", "股東權益", "現金及約當現金"}
            _YTD_FIELDS = {"本期淨利", "稅前淨利", "每股盈餘", "營業活動現金流"}
            ytd_start = f"{year}-01-01"
            ytd_end   = f"{year}-{_Q_WINDOWS.get(q or 'Q4', ('01-01','12-31'))[1]}"
            results = {}
            for label, concept_list in concepts.items():
                is_instant = label in _INSTANT_FIELDS
                is_ytd     = label in _YTD_FIELDS
                for concept in concept_list:
                    if is_instant:
                        # Balance sheet: match instant_date = period_end
                        row = conn.execute("""
                            SELECT fmv.value, xf.decimals, xf.unit_id, ri.period_end
                            FROM financial_metric_value fmv
                            JOIN report_instance ri ON ri.report_id = fmv.report_id
                            LEFT JOIN xbrl_fact xf ON xf.fact_id = fmv.fact_id
                            WHERE ri.company_code = ? AND ri.year = ? AND ri.quarter = ?
                            AND fmv.concept_id = ? AND fmv.value IS NOT NULL
                            AND xf.instant_date = ri.period_end
                            AND xf.segment_json IS NULL
                            ORDER BY ABS(fmv.value) DESC LIMIT 1
                        """, (company_code, year, q or "Q4", concept)).fetchone()
                    else:
                        # Income/CF: match YTD period (period_start=year-01-01)
                        row = conn.execute("""
                            SELECT fmv.value, xf.decimals, xf.unit_id, ri.period_end
                            FROM financial_metric_value fmv
                            JOIN report_instance ri ON ri.report_id = fmv.report_id
                            LEFT JOIN xbrl_fact xf ON xf.fact_id = fmv.fact_id
                            WHERE ri.company_code = ? AND ri.year = ? AND ri.quarter = ?
                            AND fmv.concept_id = ? AND fmv.value IS NOT NULL
                            AND xf.period_start = ? AND xf.period_end = ?
                            AND xf.segment_json IS NULL
                            ORDER BY ABS(fmv.value) DESC LIMIT 1
                        """, (company_code, year, q or "Q4", concept, ytd_start, ytd_end)).fetchone()
                    if row:
                        raw, dec, unit, period_end = row
                        # Apply decimals scaling
                        if dec == -3:
                            val = raw / 1000
                            unit_label = "千元新台幣"
                        elif dec == -6:
                            val = raw / 1000000
                            unit_label = "百萬元新台幣"
                        else:
                            val = raw
                            # Clean up unit labels
                            if unit in ("EarningsPerShare", "shares", "Share"):
                                unit_label = "元"
                            else:
                                unit_label = "元" if unit in ("TWD", None, "") else unit
                        # Format value — strip trailing zeros for decimals
                        if val == int(val):
                            val_fmt = f"{val:,.0f}"
                        else:
                            # Remove trailing zeros (e.g. 8.6100 → 8.61)
                            val_fmt = f"{val:,.4f}".rstrip("0").rstrip(".")

                        # Add 億元 reference for large amounts in 千元
                        yi_ref = ""
                        if unit_label == "千元新台幣" and val >= 10000000:
                            yi = round(val / 100000)
                            yi_ref = f"（約 {yi:,} 億元）"

                        # Fix EPS unit
                        if label == "每股盈餘":
                            unit_label = "元／股"

                        results[label] = {
                            "value": round(val, 4),
                            "value_formatted": f"{val_fmt} {unit_label}{yi_ref}".strip(),
                            "unit": unit_label,
                            "period_end": period_end,
                        }
                        break  # found, stop trying alternatives

            # Compute debt ratio if both assets and liabilities available
            if "總資產" in results and "總負債" in results:
                assets = results["總資產"]["value"]
                liab   = results["總負債"]["value"]
                if assets > 0:
                    debt_ratio = round(liab / assets * 100, 2)
                    results["負債比率"] = {
                        "value": debt_ratio,
                        "value_formatted": f"{debt_ratio}%",
                        "unit": "",
                        "formula": "總負債 ÷ 總資產 × 100",
                    }

            # Get company name
            name_row = conn.execute("""
                SELECT ri.company_code FROM report_instance ri
                WHERE ri.company_code = ? LIMIT 1
            """, (company_code,)).fetchone()

            # Get industry type for this company
            industry_row = conn.execute(
                "SELECT industry_type FROM report_instance WHERE company_code=? AND year=? AND quarter=? LIMIT 1",
                (company_code, year, q or "Q4")
            ).fetchone()
            industry_type = industry_row[0] if industry_row else "FH"

            return json.dumps({
                "company_code": company_code,
                "company_name": get_company_name(company_code),
                "industry_type": industry_type,
                "period": fmt_period(year, quarter),
                "period_note": "損益表與現金流量表數值為年初至本期末之累計數（YTD）；資產負債表數值為期末時間點數。",
                "metrics": results,
                "caveat": q4_caveat([q] if q else ["Q4"]),
                "disclaimer": "本資料僅供參考，財務決策請以公司正式公告及專業人員審查為準。",
            }, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def get_risk_indicators(company_code: str, periods: list) -> str:
    """
    Compute credit risk indicators across multiple periods for trend analysis.
    Returns debt ratio, asset growth, net income trend, and cash flow coverage.
    Designed to give Claude everything needed for a credit risk assessment.
    Args:
        company_code: Taiwan stock code, e.g. "2881"
        periods:      List of periods e.g. ["2022Q4","2023Q4","2024Q3"]
    """
    try:
        period_data = {}
        with get_db() as conn:
            for period in periods:
                year, quarter = parse_period(period)
                q = quarter if quarter else "Q4"

                # Period bounds for this specific period
                _ytd_start = f"{year}-01-01"
                _q_end     = _Q_WINDOWS.get(q, ("01-01", "12-31"))[1]
                _ytd_end   = f"{year}-{_q_end}"
                _instant   = _ytd_end  # balance sheet snapshot date

                def fetch_instant(concepts):
                    """Balance sheet items — instant date snapshot."""
                    for concept in concepts:
                        row = conn.execute("""
                            SELECT fmv.value, xf.decimals
                            FROM financial_metric_value fmv
                            JOIN report_instance ri ON ri.report_id = fmv.report_id
                            LEFT JOIN xbrl_fact xf ON xf.fact_id = fmv.fact_id
                            WHERE ri.company_code = ? AND ri.year = ? AND ri.quarter = ?
                            AND fmv.concept_id = ? AND fmv.value IS NOT NULL
                            AND xf.instant_date = ?
                            AND xf.segment_json IS NULL
                            ORDER BY ABS(fmv.value) DESC LIMIT 1
                        """, (company_code, year, q, concept, _instant)).fetchone()
                        if row:
                            raw, dec = row
                            return raw / 1000 if dec == -3 else (raw / 1000000 if dec == -6 else raw)
                    return None

                def fetch_ytd(concepts):
                    """Income/CF items — YTD cumulative (period_start=year-01-01)."""
                    for concept in concepts:
                        row = conn.execute("""
                            SELECT fmv.value, xf.decimals
                            FROM financial_metric_value fmv
                            JOIN report_instance ri ON ri.report_id = fmv.report_id
                            LEFT JOIN xbrl_fact xf ON xf.fact_id = fmv.fact_id
                            WHERE ri.company_code = ? AND ri.year = ? AND ri.quarter = ?
                            AND fmv.concept_id = ? AND fmv.value IS NOT NULL
                            AND xf.period_start = ? AND xf.period_end = ?
                            AND xf.segment_json IS NULL
                            ORDER BY ABS(fmv.value) DESC LIMIT 1
                        """, (company_code, year, q, concept, _ytd_start, _ytd_end)).fetchone()
                        if row:
                            raw, dec = row
                            return raw / 1000 if dec == -3 else (raw / 1000000 if dec == -6 else raw)
                    return None

                assets  = fetch_instant(["ifrs-full_Assets", "ifrs-full_EquityAndLiabilities"])
                liab    = fetch_instant(["ifrs-full_Liabilities"])
                equity  = fetch_instant(["ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity"])
                income  = fetch_ytd(["ifrs-full_ProfitLossFromContinuingOperations", "ifrs-full_ProfitLoss"])
                cfo     = fetch_ytd(["ifrs-full_CashFlowsFromUsedInOperatingActivities",
                                     "tifrs-SCF_CashFlowsFromUsedInOperatingActivities"])

                entry = {"unit": "千元新台幣"}
                if assets:  entry["總資產"] = round(assets, 0)
                if liab:    entry["總負債"] = round(liab, 0)
                if equity:  entry["股東權益"] = round(equity, 0)
                if income:  entry["本期淨利"] = round(income, 0)
                if cfo:     entry["營業現金流"] = round(cfo, 0)
                if assets and liab and assets > 0:
                    entry["負債比率"] = f"{round(liab/assets*100, 2)}%"
                if assets and equity and assets > 0:
                    entry["權益比率"] = f"{round(equity/assets*100, 2)}%"

                period_data[period] = entry

        # Compute year-over-year asset growth
        period_keys = list(period_data.keys())
        growth = {}
        for i in range(1, len(period_keys)):
            prev_p = period_keys[i-1]
            curr_p = period_keys[i]
            prev_a = period_data[prev_p].get("總資產")
            curr_a = period_data[curr_p].get("總資產")
            if prev_a and curr_a and prev_a > 0:
                g = round((curr_a - prev_a) / prev_a * 100, 2)
                growth[f"{prev_p}→{curr_p}"] = f"{g:+.2f}%"

        return json.dumps({
            "company_code": company_code,
            "company_name": get_company_name(company_code),
            "periods_analysed": periods,
            "data": period_data,
            "asset_growth": growth,
            "note": "負債比率=總負債÷總資產×100。本期淨利與現金流為YTD累計數；資產負債表為期末快照。單位：千元新台幣。",
            "disclaimer": "本資料僅供參考，財務決策請以公司正式公告及專業人員審查為準。",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"錯誤：{e}"


@mcp.tool()
def generate_credit_report_prompt(
    company_code: str,
    company_name: str,
    period: str,
    report_type: str = "standard"
) -> str:
    """
    Generate a structured prompt template for Claude to write a Taiwan FSC-style
    credit investigation report. Call get_credit_summary and get_risk_indicators
    first to gather the data, then use this prompt to guide report generation.
    Args:
        company_code:  Taiwan stock code, e.g. "2881"
        company_name:  Full company name in Chinese, e.g. "富邦金融控股股份有限公司"
        period:        Reporting period, e.g. "2024Q3"
        report_type:   "standard" (default) or "detailed"
    """
    sections = {
        "standard": [
            "一、公司基本資料（公司名稱、股票代號、產業別、申報期間、報告編製日期）",
            "二、財務結構分析（資產規模、負債比率含FSC判定、股東權益、近三期同季趨勢）",
            "三、獲利能力分析（本期淨利YTD累計、EPS含年化估算、ROA、ROE年化）",
            "四、流動性與現金流分析（現金及約當現金、營業活動現金流、流動性評估）",
            "五、信用風險評估（主要風險因子、與同業比較、財務健康度綜合判斷）",
            "六、授信建議（明確給出：正常往來／加強注意／限制往來／婉拒授信，並說明主要依據）",
        ],
        "detailed": [
            "一、公司基本資料與產業背景",
            "二、財務結構深度分析（近三年趨勢）",
            "三、獲利能力與成長性分析",
            "四、流動性風險與現金流量分析",
            "五、資本適足性評估（適用於銀行與金控）",
            "六、跨公司比較分析（與同業平均比較）",
            "七、重大風險因子識別",
            "八、信用評等建議與授信參考",
            "九、免責聲明與資料來源",
        ],
    }

    chosen = sections.get(report_type, sections["standard"])
    sections_text = "\n".join(chosen)

    prompt = f"""你是一位台灣金融業資深信用徵審專員。
請根據以下財務資料，撰寫一份專業的信用調查報告。

【受查公司】
公司名稱：{company_name}
股票代號：{company_code}
申報期間：{period}
資料來源：台灣金管會MOPS XBRL財務報告（官方申報數據）

【報告格式】
請依照以下章節結構撰寫，使用繁體中文，語氣專業正式：

{sections_text}

【格式要求】
- 金額統一使用千元新台幣（NTD thousands）
- 億元換算公式：千元數值 ÷ 100,000 = 億元
- 比率保留兩位小數
- 每個章節需有具體數據支撐，不得僅作一般性描述
- 結尾必須包含免責聲明：本報告依據公開申報財務資料編製，僅供參考，
  不構成投資或授信建議，實際決策請以最新公告及專業人員審查為準
- 【重要】請勿使用 Markdown 格式：不得使用 #、##、###、**、`、---、| 等符號
- 章節標題直接以文字書寫（例如：一、公司基本資料）
- 表格資料以文字列點方式呈現，不使用管道符號（|）
- 不使用 emoji 符號（📌、⚠️ 等）
- 計算公式以文字方式呈現，不使用 LaTeX 或程式碼區塊
- 【嚴格禁止】不得在報告中提及、比較或警示任何其他公司，受查公司已明確指定，請勿自行推測或添加其他公司名稱、股票代號或備註說明
- 【嚴格禁止】不得添加任何「備註說明」章節提及其他公司與受查公司之差異
- 【必須執行】第六章授信建議必須給出明確等級（正常往來／加強注意／限制往來／婉拒授信）並說明依據
- 【必須執行】趨勢分析必須使用相同季度比較（如2022Q3、2023Q3、2024Q3），不得混用Q4全年與Q3前三季

【財務數據】
請將從 get_credit_summary 和 get_risk_indicators 工具取得的數據
填入各章節分析中。"""

    return json.dumps({
        "company_code": company_code,
        "company_name": company_name,
        "period": period,
        "report_type": report_type,
        "prompt_template": prompt,
        "instructions": [
            "Step 1: Call get_credit_summary(company_code, period) to get current metrics",
            "Step 2: Call get_risk_indicators(company_code, ['2022Q4','2023Q4','2024Q3']) for trends",
            "Step 3: Use this prompt template with the data from steps 1 and 2",
            "Step 4: Generate the report in Traditional Chinese following the section structure",
        ],
        "disclaimer": "本工具產生之報告架構僅供參考，正式信用徵審報告需經專業人員審核。",
    }, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    db_path = os.path.expanduser(DB_PATH)
    logger.info(f"XBRL DB path : {db_path}")
    logger.info(f"MCP transport: SSE")
    logger.info(f"Listening on : http://{SERVER_HOST}:{SERVER_PORT}")
    logger.info(f"SSE endpoint : http://{SERVER_HOST}:{SERVER_PORT}/sse")

    if not os.path.exists(db_path):
        logger.error(f"Database not found: {db_path} — set XBRL_DB_PATH and restart.")
        raise SystemExit(1)

    uvicorn.run(mcp.sse_app(), host=SERVER_HOST, port=SERVER_PORT, log_level="info")