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
        where = ["fmv.company_code = ?", "fmv.year = ?", "fmv.quarter = ?"]
        params = [company_code, year, quarter]

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

            period_filter = " OR ".join(["(fmv.year = ? AND fmv.quarter = ?)"] * len(parsed))
            period_params = [v for yq in parsed for v in yq]
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
        ph = ",".join("?" * len(company_codes))
        sql = f"""
            SELECT fmv.company_code, fd.zh_name, fd.canonical_name,
                   fmv.value, fd.value_type, fmv.report_scope
            FROM financial_metric_value fmv
            JOIN field_dictionary fd ON fmv.field_id = fd.field_id
            WHERE fmv.company_code IN ({ph})
              AND fmv.year = ? AND fmv.quarter = ?
              AND (fd.canonical_name = ? OR fd.zh_name = ?)
            ORDER BY CAST(fmv.value AS REAL) DESC
        """
        params = company_codes + [year, quarter, field, field]
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