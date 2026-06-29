#!/usr/bin/env python3
"""
Add Company
============
The simplest way to add a new company into the AITC system.

Usage:
    # Add one company by stock code
    python add_company.py 2330

    # Add multiple companies
    python add_company.py 2330 2303 2317

    # Add from a list file (one stock code per line)
    python add_company.py --file my_companies.txt

What this does:
    1. Downloads all available XBRL filings for the company from FSC
    2. Imports them into the database
    3. Shows a summary of what was added
"""

import os
import sys
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime

DB_PATH     = Path(os.environ.get("XBRL_DB_PATH", "/home/user/AITC/FinancialStatementXBRL.db"))
SCRIPTS_DIR = Path(__file__).parent


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run_script(script, args):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script)] + args,
        capture_output=False,   # show output in real time
        text=True
    )
    return result.returncode == 0


def company_summary(stock_code):
    """Shows what was imported for this company."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute("""
        SELECT r.year, r.quarter, r.report_type,
               COUNT(f.id) as facts
        FROM   report_instance r
        LEFT   JOIN financial_metric_value f ON f.report_id = r.id
        WHERE  r.stock_code = ?
        GROUP  BY r.year, r.quarter, r.report_type
        ORDER  BY r.year, r.quarter
    """, (stock_code,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        log(f"  No data found for {stock_code} in database.")
        return

    print()
    print(f"  {'Period':<12} {'Type':<6} {'Facts':>8}")
    print(f"  {'──────':<12} {'────':<6} {'─────':>8}")
    for year, quarter, rtype, facts in rows:
        print(f"  {year}Q{quarter:<8} {rtype:<6} {facts:>8,}")
    total = sum(r[3] for r in rows)
    print(f"  {'──────':<12} {'────':<6} {'─────':>8}")
    print(f"  {'TOTAL':<12} {'':6} {total:>8,}")
    print()


def add_companies(stock_codes):
    print()
    print("=" * 55)
    print("  AITC — Add Company")
    print("=" * 55)
    print(f"  Companies to add: {', '.join(stock_codes)}")
    print(f"  Database: {DB_PATH}")
    print("=" * 55)
    print()

    for code in stock_codes:
        print(f"  ── Processing {code} ──────────────────────────")
        print()

        # Step 1: Download
        log(f"Step 1/2  Downloading XBRL filings for {code} from FSC...")
        ok = run_script("01_fsc_downloader.py", ["--stock", code])
        if not ok:
            log(f"  WARNING: Download had issues for {code}. Attempting import anyway.")

        # Step 2: Import
        log(f"Step 2/2  Importing into database...")
        ok = run_script("02_xbrl_importer.py", ["--stock", code])
        if not ok:
            log(f"  ERROR: Import failed for {code}.")
            continue

        # Summary
        log(f"Done! Here is what was added for {code}:")
        company_summary(code)

    print("=" * 55)
    print("  All companies processed.")
    print()
    print("  To verify, open Claude Code and try:")
    for code in stock_codes[:3]:
        print(f"    xbrl {code} 最新 營業收入")
    print("=" * 55)
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--file":
        # Read from file
        if len(sys.argv) < 3:
            print("Usage: python add_company.py --file companies.txt")
            sys.exit(1)
        filepath = Path(sys.argv[2])
        if not filepath.exists():
            print(f"File not found: {filepath}")
            sys.exit(1)
        codes = []
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                code = line.strip().split()[0]
                if code and not code.startswith("#"):
                    codes.append(code)
        if not codes:
            print("No stock codes found in file.")
            sys.exit(1)
        add_companies(codes)
    else:
        # Stock codes passed directly as arguments
        codes = [c.strip() for c in sys.argv[1:] if c.strip()]
        if not codes:
            print("No stock codes provided.")
            sys.exit(1)
        add_companies(codes)
