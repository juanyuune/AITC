#!/usr/bin/env python3
"""
MOPS Importer
==============
Imports downloaded XBRL files into the database using the existing
build_xbrl_sql.py script. This is a wrapper that handles finding
the taxonomy, finding the downloaded files, and running the import.

Usage:
    python mops_import.py --stock 2330
    python mops_import.py --stock 2330 2303 2881
    python mops_import.py --all
"""

import os
import sys
import glob
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime

DB_PATH      = Path(os.environ.get("XBRL_DB_PATH",      "/home/user/AITC/FinancialStatementXBRL.db"))
DOWNLOAD_DIR = Path(os.environ.get("XBRL_DOWNLOAD_DIR", "/home/user/AITC/xbrl_downloads"))
TAXONOMY_DIR = Path(os.environ.get("XBRL_TAXONOMY_DIR", "/home/user/AITC/taxonomy"))
SCRIPTS_DIR  = Path(__file__).parent
BUILD_SCRIPT = SCRIPTS_DIR / "build_xbrl_sql.py"
LOG_FILE     = Path("/home/user/AITC/logs/mops_importer.log")


def log(msg, level="INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_taxonomy():
    """Finds the taxonomy folder on disk."""
    # Check common locations
    search_paths = [
        TAXONOMY_DIR,
        Path("/home/user/AITC"),
        Path("/home/user"),
        Path.home(),
    ]
    for base in search_paths:
        if not base.exists():
            continue
        for folder in base.iterdir():
            if folder.is_dir() and "tifrs" in folder.name.lower():
                log(f"Found taxonomy: {folder}")
                return folder
    return None


def find_xbrl_files(stock_code):
    """Finds all downloaded XBRL files for a stock code."""
    stock_dir = DOWNLOAD_DIR / stock_code
    if not stock_dir.exists():
        return []
    files = []
    for pattern in ["*.xbrl", "*.xml", "**/*.xbrl", "**/*.xml"]:
        files.extend(stock_dir.glob(pattern))
    # Filter out non-XBRL xml files (docx templates etc)
    files = [f for f in files if "template" not in str(f).lower()
             and "docx" not in str(f).lower()]
    return sorted(set(files))


def already_imported(stock_code, xbrl_file):
    """Check if this file is already in the database."""
    if not DB_PATH.exists():
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.execute(
            "SELECT COUNT(*) FROM report_instance WHERE file_name = ?",
            (xbrl_file.name,)
        )
        count = cur.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def import_file(xbrl_file, taxonomy_root, stock_code):
    """Runs build_xbrl_sql.py on one XBRL file."""
    if already_imported(stock_code, xbrl_file):
        log(f"  Already imported: {xbrl_file.name} — skipping.")
        return True

    sql_output = xbrl_file.parent / f"{xbrl_file.stem}_import.sql"

    cmd = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--taxonomy-root", str(taxonomy_root),
        "--instance",      str(xbrl_file),
        "--sql-output",    str(sql_output),
        "--db-path",       str(DB_PATH),
    ]

    log(f"  Importing: {xbrl_file.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        log(f"  FAILED: {result.stderr.strip()}", "ERROR")
        return False

    # Print key stats from output
    for line in result.stdout.strip().split("\n"):
        if any(k in line for k in ["reports=", "facts=", "financial_metric_value_rows="]):
            log(f"    {line}")

    log(f"  Done: {xbrl_file.name}")
    return True


def import_stock(stock_code, taxonomy_root):
    """Imports all downloaded files for one stock code."""
    files = find_xbrl_files(stock_code)
    if not files:
        log(f"[{stock_code}] No XBRL files found in {DOWNLOAD_DIR / stock_code}", "WARN")
        log(f"  Run: python mops_downloader.py --stock {stock_code}", "WARN")
        return 0

    log(f"[{stock_code}] Found {len(files)} file(s) to import.")
    success = 0
    for f in files:
        if import_file(f, taxonomy_root, stock_code):
            success += 1
    return success


def rebuild_embeddings():
    """Rebuilds the embeddings cache after import."""
    embeddings_script = SCRIPTS_DIR / "build_xbrl_embeddings.py"
    if not embeddings_script.exists():
        log("build_xbrl_embeddings.py not found, skipping.", "WARN")
        return
    log("Rebuilding embeddings cache...")
    result = subprocess.run(
        [sys.executable, str(embeddings_script)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        log("Embeddings rebuilt successfully.")
    else:
        log(f"Embeddings rebuild failed: {result.stderr[:200]}", "WARN")


def show_summary(stock_codes):
    """Shows database summary after import."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    print("\n" + "=" * 55)
    print("  Database summary after import")
    print("=" * 55)
    cur = conn.execute("""
        SELECT company_code,
               COUNT(DISTINCT year||quarter) as periods,
               COUNT(*) as reports
        FROM   report_instance
        WHERE  company_code IN ({})
        GROUP  BY company_code
        ORDER  BY company_code
    """.format(",".join("?" * len(stock_codes))), stock_codes)
    rows = cur.fetchall()
    if rows:
        print(f"  {'Code':<8} {'Periods':>8} {'Reports':>8}")
        print(f"  {'────':<8} {'───────':>8} {'───────':>8}")
        for code, periods, reports in rows:
            print(f"  {code:<8} {periods:>8} {reports:>8}")
    else:
        print("  No data found for these companies.")

    total_rows = conn.execute("SELECT COUNT(*) FROM financial_metric_value").fetchone()[0]
    total_cos  = conn.execute("SELECT COUNT(DISTINCT company_code) FROM report_instance").fetchone()[0]
    print(f"\n  Total companies in DB : {total_cos:,}")
    print(f"  Total metric rows     : {total_rows:,}")
    print("=" * 55)
    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MOPS XBRL Importer")
    parser.add_argument("--stock", nargs="+", help="Stock code(s) to import")
    parser.add_argument("--all",   action="store_true", help="Import all downloaded companies")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Skip rebuilding embeddings (faster, do manually after batch import)")
    args = parser.parse_args()

    # Find taxonomy
    taxonomy = find_taxonomy()
    if not taxonomy:
        print("\nERROR: Taxonomy folder not found.")
        print("Download it first:")
        print("  python mops_downloader.py --taxonomy")
        sys.exit(1)

    # Collect stock codes
    codes = []
    if args.stock:
        codes.extend(args.stock)
    elif args.all:
        if DOWNLOAD_DIR.exists():
            codes = [d.name for d in DOWNLOAD_DIR.iterdir() if d.is_dir()]
        else:
            print(f"Download directory not found: {DOWNLOAD_DIR}")
            sys.exit(1)
    else:
        print("Specify --stock 2330 or --all")
        sys.exit(1)

    if not codes:
        print("No companies found to import.")
        sys.exit(1)

    print(f"\nImporting {len(codes)} company/companies using taxonomy: {taxonomy.name}")
    print(f"Database: {DB_PATH}\n")

    total = 0
    for code in codes:
        count = import_stock(code, taxonomy)
        total += count

    print(f"\n{total} file(s) imported.")

    # Rebuild embeddings unless skipped
    if not args.no_embeddings:
        rebuild_embeddings()

    show_summary(codes)

    print("\nNext step — test in Claude Code:")
    for code in codes[:3]:
        print(f"  xbrl {code} 最新 營業收入")
