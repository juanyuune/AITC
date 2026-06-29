#!/usr/bin/env python3
"""
Auto Update — runs downloader then importer
============================================
This is the script the cron job calls.
It downloads any new XBRL filings and imports them into the database.

Cron setup (runs every day at 6:00 AM):
    crontab -e
    0 6 * * * /home/user/AITC/scripts/03_auto_update.py >> /home/user/AITC/logs/auto_update.log 2>&1

Manual run:
    python 03_auto_update.py                  # update all companies already in DB
    python 03_auto_update.py --stock 2330     # update one company only
    python 03_auto_update.py --list my_list.txt
"""

import os
import sys
import argparse
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime

DB_PATH      = Path(os.environ.get("XBRL_DB_PATH", "/home/user/AITC/FinancialStatementXBRL.db"))
SCRIPTS_DIR  = Path(__file__).parent
LOG_FILE     = Path("/home/user/AITC/logs/auto_update.log")


def log(msg, level="INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_existing_companies():
    """Returns list of stock codes already in the database."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute("SELECT DISTINCT stock_code FROM report_instance ORDER BY stock_code")
    codes = [r[0] for r in cur.fetchall()]
    conn.close()
    return codes


def run(cmd, label):
    log(f"Running: {label}")
    result = subprocess.run(
        [sys.executable] + cmd,
        capture_output=True, text=True
    )
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            log(f"  {line}")
    if result.returncode != 0:
        log(f"  FAILED: {result.stderr.strip()}", "ERROR")
        return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AITC Auto Update")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--stock", help="Update one company only")
    group.add_argument("--list",  help="Update companies from a text file")
    group.add_argument("--all",   action="store_true",
                       help="Update ALL listed companies (slow, use sparingly)")
    args = parser.parse_args()

    log("=" * 60)
    log("AITC Auto Update started")
    log("=" * 60)

    downloader = str(SCRIPTS_DIR / "01_fsc_downloader.py")
    importer   = str(SCRIPTS_DIR / "02_xbrl_importer.py")

    if args.stock:
        # Single company
        run([downloader, "--stock", args.stock, "--incremental"],
            f"Download {args.stock}")
        run([importer,   "--stock", args.stock, "--incremental"],
            f"Import {args.stock}")

    elif args.list:
        # From a list file
        run([downloader, "--list", args.list, "--incremental"],
            f"Download from {args.list}")
        run([importer,   "--all",  "--incremental"],
            "Import all new files")

    elif args.all:
        # All listed companies — use only when expanding coverage
        run([downloader, "--all", "--incremental"],
            "Download all listed companies")
        run([importer,   "--all", "--incremental"],
            "Import all new files")

    else:
        # Default: update only companies already in the database
        companies = get_existing_companies()
        if not companies:
            log("No companies in DB yet. Run with --list or --all to add companies.", "WARN")
            sys.exit(1)
        log(f"Updating {len(companies)} existing companies: {', '.join(companies[:10])}{'...' if len(companies) > 10 else ''}")

        # Write a temp list file
        tmp_list = Path("/tmp/aitc_update_list.txt")
        tmp_list.write_text("\n".join(companies))

        run([downloader, "--list", str(tmp_list), "--incremental"],
            "Download new filings for existing companies")
        run([importer,   "--all",  "--incremental"],
            "Import new filings")

    log("=" * 60)
    log("Auto Update complete.")
    log("=" * 60)
