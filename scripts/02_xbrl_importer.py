#!/usr/bin/env python3
"""
XBRL Importer
==============
Parses downloaded XBRL .xml files and loads them into the SQLite database.
Designed to work with files downloaded by 01_fsc_downloader.py.

Usage:
    # Import one file
    python 02_xbrl_importer.py --file xbrl_downloads/2330/2330_2024Q3.xml

    # Import all files for one company
    python 02_xbrl_importer.py --stock 2330

    # Import everything in the download folder
    python 02_xbrl_importer.py --all

    # Import and skip files already in the database
    python 02_xbrl_importer.py --all --incremental
"""

import os
import re
import sqlite3
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────
DB_PATH      = Path(os.environ.get("XBRL_DB_PATH", "/home/user/AITC/FinancialStatementXBRL.db"))
DOWNLOAD_DIR = Path(os.environ.get("XBRL_DOWNLOAD_DIR", "/home/user/AITC/xbrl_downloads"))
LOG_FILE     = Path("/home/user/AITC/logs/importer.log")

# XBRL namespaces used in Taiwan FSC filings
XBRL_NS = {
    "xbrli":     "http://www.xbrl.org/2003/instance",
    "ifrs-full": "http://xbrl.ifrs.org/taxonomy/2014-03-05/ifrs-full",
    "twse":      "http://xbrl.twse.com.tw/taxonomy/",
    "link":      "http://www.xbrl.org/2003/linkbase",
    "xlink":     "http://www.w3.org/1999/xlink",
}


# ── Logging ───────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── Database setup ────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn):
    """Creates tables if they don't exist — safe to run on existing DB."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS report_instance (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code      TEXT    NOT NULL,
            company_name    TEXT,
            year            INTEGER NOT NULL,
            quarter         INTEGER NOT NULL,
            industry_type   TEXT,
            report_type     TEXT,   -- CR=consolidated, IR=individual
            filed_date      TEXT,
            source_file     TEXT,
            imported_at     TEXT    DEFAULT (datetime('now')),
            UNIQUE(stock_code, year, quarter, report_type)
        );

        CREATE TABLE IF NOT EXISTS financial_metric_value (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id       INTEGER NOT NULL REFERENCES report_instance(id),
            field_id        TEXT    NOT NULL,
            stock_code      TEXT    NOT NULL,
            year            INTEGER NOT NULL,
            quarter         INTEGER NOT NULL,
            period_type     TEXT,   -- instant / duration
            start_date      TEXT,
            end_date        TEXT,
            value           REAL,
            unit            TEXT    DEFAULT 'TWD',
            decimals        INTEGER,
            UNIQUE(report_id, field_id, period_type, end_date)
        );

        CREATE TABLE IF NOT EXISTS field_dictionary (
            field_id        TEXT    PRIMARY KEY,
            canonical_name  TEXT    NOT NULL,
            zh_name         TEXT,
            en_name         TEXT,
            statement_type  TEXT,   -- BS/IS/CF/NOTE
            data_type       TEXT,
            created_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_fmv_stock_period
            ON financial_metric_value(stock_code, year, quarter);

        CREATE INDEX IF NOT EXISTS idx_fmv_field
            ON financial_metric_value(field_id);

        CREATE INDEX IF NOT EXISTS idx_report_stock
            ON report_instance(stock_code, year, quarter);
    """)
    conn.commit()


# ── XBRL parsing ─────────────────────────────────────────────
def parse_xbrl_file(filepath):
    """
    Parses a Taiwan FSC XBRL file.
    Returns (metadata_dict, list_of_facts).

    Each fact is a dict:
    {
        field_id:    'ifrs-full_Assets',
        period_type: 'instant' | 'duration',
        start_date:  '2024-01-01' or None,
        end_date:    '2024-09-30',
        value:       574292437000.0,
        unit:        'TWD',
        decimals:    -3,
    }
    """
    log(f"  Parsing {filepath.name}...")
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        log(f"  XML parse error in {filepath}: {e}", "ERROR")
        return None, []

    # ── Extract contexts (period definitions) ─────────────────
    contexts = {}
    for ctx in root.iter("{http://www.xbrl.org/2003/instance}context"):
        ctx_id = ctx.get("id", "")
        period  = ctx.find("{http://www.xbrl.org/2003/instance}period")
        if period is None:
            continue
        instant = period.find("{http://www.xbrl.org/2003/instance}instant")
        start   = period.find("{http://www.xbrl.org/2003/instance}startDate")
        end     = period.find("{http://www.xbrl.org/2003/instance}endDate")
        if instant is not None:
            contexts[ctx_id] = {"type": "instant", "start": None, "end": instant.text}
        elif start is not None and end is not None:
            contexts[ctx_id] = {"type": "duration", "start": start.text, "end": end.text}

    # ── Extract units ─────────────────────────────────────────
    units = {}
    for unit in root.iter("{http://www.xbrl.org/2003/instance}unit"):
        uid    = unit.get("id", "")
        measure = unit.find("{http://www.xbrl.org/2003/instance}measure")
        if measure is not None:
            units[uid] = measure.text.split(":")[-1]   # "iso4217:TWD" → "TWD"

    # ── Extract metadata from entity ─────────────────────────
    entity_name  = ""
    stock_code   = ""
    for entity in root.iter("{http://www.xbrl.org/2003/instance}entity"):
        identifier = entity.find("{http://www.xbrl.org/2003/instance}identifier")
        if identifier is not None:
            stock_code = identifier.text.strip()
    # Try to get company name from XBRL header or entity segment
    for tag in root.iter():
        if "EntityName" in tag.tag or "CompanyName" in tag.tag:
            entity_name = tag.text or ""
            break

    # ── Determine report type (CR/IR) from filename or context ─
    fname = filepath.stem.upper()
    report_type = "CR" if "CR" in fname or "CONSOL" in fname else "IR"

    # ── Parse year/quarter from filename ──────────────────────
    # Filename format: {stock_code}_{year}Q{quarter}.xml
    m = re.search(r"_(\d{4})Q(\d)", filepath.stem)
    year    = int(m.group(1)) if m else 0
    quarter = int(m.group(2)) if m else 0

    metadata = {
        "stock_code":   stock_code or filepath.stem.split("_")[0],
        "company_name": entity_name,
        "year":         year,
        "quarter":      quarter,
        "report_type":  report_type,
        "source_file":  str(filepath),
    }

    # ── Extract facts ─────────────────────────────────────────
    facts = []
    skip_tags = {
        "{http://www.xbrl.org/2003/instance}context",
        "{http://www.xbrl.org/2003/instance}unit",
        "{http://www.xbrl.org/2003/instance}schemaRef",
        "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation",
    }
    for elem in root:
        tag = elem.tag
        if tag in skip_tags:
            continue
        if elem.text is None or not elem.text.strip():
            continue

        ctx_ref  = elem.get("contextRef", "")
        unit_ref = elem.get("unitRef", "")
        decimals = elem.get("decimals", "")
        value_str = elem.text.strip()

        # Only numeric facts
        try:
            value = float(value_str.replace(",", ""))
        except ValueError:
            continue

        ctx = contexts.get(ctx_ref, {})
        if not ctx:
            continue

        # Extract field_id from tag (strip namespace)
        # e.g. "{http://xbrl.ifrs.org/...}Assets" → "ifrs-full_Assets"
        local_name = tag.split("}")[-1] if "}" in tag else tag
        ns_part    = tag.split("}")[0].lstrip("{") if "}" in tag else ""
        if "ifrs" in ns_part.lower():
            prefix = "ifrs-full"
        elif "twse" in ns_part.lower():
            prefix = "twse"
        else:
            prefix = "other"
        field_id = f"{prefix}_{local_name}"

        facts.append({
            "field_id":    field_id,
            "period_type": ctx.get("type", "instant"),
            "start_date":  ctx.get("start"),
            "end_date":    ctx.get("end"),
            "value":       value,
            "unit":        units.get(unit_ref, "TWD"),
            "decimals":    int(decimals) if decimals.lstrip("-").isdigit() else 0,
        })

    log(f"  Parsed {len(facts)} facts from {filepath.name}.")
    return metadata, facts


# ── Database write ────────────────────────────────────────────
def import_to_db(conn, metadata, facts):
    """Inserts one filing's metadata and facts into the database."""
    stock_code  = metadata["stock_code"]
    year        = metadata["year"]
    quarter     = metadata["quarter"]
    report_type = metadata["report_type"]

    # Insert or get report_instance
    try:
        conn.execute("""
            INSERT OR IGNORE INTO report_instance
              (stock_code, company_name, year, quarter, report_type, source_file)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (stock_code, metadata["company_name"], year, quarter,
              report_type, metadata["source_file"]))
        conn.commit()
    except sqlite3.IntegrityError:
        pass   # already exists

    cur = conn.execute("""
        SELECT id FROM report_instance
        WHERE  stock_code = ? AND year = ? AND quarter = ? AND report_type = ?
    """, (stock_code, year, quarter, report_type))
    row = cur.fetchone()
    if not row:
        log(f"  Could not find/create report_instance for {stock_code} {year}Q{quarter}.", "ERROR")
        return 0

    report_id = row[0]

    # Insert facts
    inserted = 0
    for fact in facts:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO financial_metric_value
                  (report_id, field_id, stock_code, year, quarter,
                   period_type, start_date, end_date, value, unit, decimals)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (report_id, fact["field_id"], stock_code, year, quarter,
                  fact["period_type"], fact["start_date"], fact["end_date"],
                  fact["value"], fact["unit"], fact["decimals"]))
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    # Auto-register any new fields in field_dictionary
    field_ids = {f["field_id"] for f in facts}
    for fid in field_ids:
        conn.execute("""
            INSERT OR IGNORE INTO field_dictionary (field_id, canonical_name)
            VALUES (?, ?)
        """, (fid, fid.split("_", 1)[-1] if "_" in fid else fid))

    conn.commit()
    log(f"  Imported {inserted} facts for {stock_code} {year}Q{quarter} ({report_type}).")
    return inserted


def import_file(filepath, conn=None, incremental=False):
    """Imports a single XBRL file into the database."""
    filepath = Path(filepath)
    if not filepath.exists():
        log(f"File not found: {filepath}", "ERROR")
        return 0

    close_after = conn is None
    if conn is None:
        conn = get_db()
        ensure_tables(conn)

    if incremental:
        # Check if already imported
        m = re.search(r"_(\d{4})Q(\d)", filepath.stem)
        if m:
            year, quarter = int(m.group(1)), int(m.group(2))
            stock_code = filepath.stem.split("_")[0]
            cur = conn.execute("""
                SELECT COUNT(*) FROM report_instance
                WHERE  stock_code = ? AND year = ? AND quarter = ?
            """, (stock_code, year, quarter))
            if cur.fetchone()[0] > 0:
                log(f"  Already imported {filepath.name}, skipping.")
                return 0

    metadata, facts = parse_xbrl_file(filepath)
    if metadata is None:
        return 0

    count = import_to_db(conn, metadata, facts)
    if close_after:
        conn.close()
    return count


def import_stock(stock_code, incremental=False):
    """Imports all downloaded files for one stock code."""
    stock_dir = DOWNLOAD_DIR / stock_code
    if not stock_dir.exists():
        log(f"No downloads found for {stock_code}. Run 01_fsc_downloader.py first.", "WARN")
        return 0
    conn = get_db()
    ensure_tables(conn)
    total = 0
    for xbrl_file in sorted(stock_dir.glob("*.xml")):
        total += import_file(xbrl_file, conn=conn, incremental=incremental)
    conn.close()
    log(f"Done. {total} facts imported for {stock_code}.")
    return total


def import_all(incremental=False):
    """Imports all downloaded XBRL files in the download directory."""
    conn = get_db()
    ensure_tables(conn)
    total = 0
    companies = 0
    for stock_dir in sorted(DOWNLOAD_DIR.iterdir()):
        if not stock_dir.is_dir():
            continue
        companies += 1
        for xbrl_file in sorted(stock_dir.glob("*.xml")):
            total += import_file(xbrl_file, conn=conn, incremental=incremental)
    conn.close()
    log(f"Done. {total} facts imported across {companies} companies.")
    return total


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XBRL Importer")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file",  help="Import a single .xml file")
    group.add_argument("--stock", help="Import all files for one stock code")
    group.add_argument("--all",   action="store_true", help="Import all downloaded files")
    parser.add_argument("--incremental", action="store_true",
                        help="Skip files already in the database")
    args = parser.parse_args()

    if args.file:
        import_file(args.file, incremental=args.incremental)
    elif args.stock:
        import_stock(args.stock, incremental=args.incremental)
    elif args.all:
        import_all(incremental=args.incremental)
