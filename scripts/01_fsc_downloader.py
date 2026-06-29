#!/usr/bin/env python3
"""
FSC XBRL Downloader
====================
Downloads XBRL filings from Taiwan FSC open data portal for any company.

Usage:
    # Download one company
    python 01_fsc_downloader.py --stock 2330

    # Download a list of companies
    python 01_fsc_downloader.py --list company_list.txt

    # Download ALL Taiwan listed companies
    python 01_fsc_downloader.py --all

    # Download only new filings since last run
    python 01_fsc_downloader.py --all --incremental

FSC Open Data API: https://data.fsc.gov.tw/data/api/
"""

import os
import json
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────
DB_PATH       = Path(os.environ.get("XBRL_DB_PATH", "/home/user/AITC/FinancialStatementXBRL.db"))
DOWNLOAD_DIR  = Path(os.environ.get("XBRL_DOWNLOAD_DIR", "/home/user/AITC/xbrl_downloads"))
LOG_FILE      = Path("/home/user/AITC/logs/downloader.log")

FSC_API_BASE  = "https://data.fsc.gov.tw/data/api"
FSC_XBRL_URL  = "https://doc.twse.com.tw/server-java/t57sb01"   # TWSE XBRL filing endpoint

HEADERS = {
    "User-Agent": "AITC-CreditInvestigation/1.0 (internal research tool)",
    "Accept": "application/json",
}

RATE_LIMIT_DELAY = 1.5   # seconds between API calls — be polite to FSC servers


# ── Logging ───────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── FSC API helpers ───────────────────────────────────────────
def get_all_listed_companies():
    """
    Fetches the full list of TWSE/TPEx listed companies from FSC open data.
    Returns list of dicts: [{stock_code, company_name, market}, ...]
    """
    log("Fetching full company list from FSC...")
    url = f"{FSC_API_BASE}/getCompanyInfo"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        companies = []
        for item in data.get("data", []):
            companies.append({
                "stock_code":   item.get("SecuritiesCode", "").strip(),
                "company_name": item.get("CompanyName", "").strip(),
                "market":       item.get("MarketType", "").strip(),
            })
        log(f"Found {len(companies)} listed companies.")
        return companies
    except Exception as e:
        log(f"Failed to fetch company list: {e}", "ERROR")
        return []


def get_filing_list(stock_code, year=None, quarter=None):
    """
    Returns list of available XBRL filings for a given stock code.
    If year/quarter not specified, returns all available filings.
    """
    url = f"{FSC_API_BASE}/getXBRLList"
    params = {"stockCode": stock_code}
    if year:
        params["year"] = year
    if quarter:
        params["quarter"] = quarter
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        log(f"  [{stock_code}] Failed to get filing list: {e}", "WARN")
        return []


def download_xbrl_file(stock_code, year, quarter, filing_id, dest_dir):
    """
    Downloads a single XBRL filing (.xml) to dest_dir.
    Returns the local file path, or None if failed.
    """
    filename = f"{stock_code}_{year}Q{quarter}.xml"
    dest_path = dest_dir / stock_code / filename

    # Skip if already downloaded
    if dest_path.exists():
        log(f"  [{stock_code}] {year}Q{quarter} already exists, skipping.")
        return dest_path

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"{FSC_XBRL_URL}?id={filing_id}&step=2&filetype=xbrl"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        log(f"  [{stock_code}] Downloaded {year}Q{quarter} → {dest_path}")
        return dest_path
    except Exception as e:
        log(f"  [{stock_code}] Failed to download {year}Q{quarter}: {e}", "ERROR")
        return None


def get_already_imported(stock_code):
    """
    Checks the database to see which periods are already imported
    for this company — avoids re-importing existing data.
    """
    import sqlite3
    if not DB_PATH.exists():
        return set()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT year || 'Q' || quarter
            FROM   report_instance
            WHERE  stock_code = ?
        """, (stock_code,))
        rows = {r[0] for r in cur.fetchall()}
        conn.close()
        return rows
    except Exception:
        return set()


# ── Main download logic ───────────────────────────────────────
def download_company(stock_code, incremental=False):
    """
    Downloads all available XBRL filings for one company.
    If incremental=True, skips periods already in the database.
    """
    log(f"Processing {stock_code}...")
    already_imported = get_already_imported(stock_code) if incremental else set()

    filings = get_filing_list(stock_code)
    if not filings:
        log(f"  [{stock_code}] No filings found.", "WARN")
        return []

    downloaded = []
    for filing in filings:
        year    = str(filing.get("year", ""))
        quarter = str(filing.get("quarter", "")).replace("Q", "")
        fid     = filing.get("filingId", "")
        period  = f"{year}Q{quarter}"

        if incremental and period in already_imported:
            log(f"  [{stock_code}] {period} already in DB, skipping.")
            continue

        path = download_xbrl_file(stock_code, year, quarter, fid, DOWNLOAD_DIR)
        if path:
            downloaded.append(path)
        time.sleep(RATE_LIMIT_DELAY)

    return downloaded


def download_from_list(company_list_file, incremental=False):
    """Reads stock codes from a text file (one per line) and downloads each."""
    codes = []
    with open(company_list_file, encoding="utf-8") as f:
        for line in f:
            code = line.strip().split()[0]   # handle "2330  TSMC" format too
            if code and not code.startswith("#"):
                codes.append(code)
    log(f"Company list: {len(codes)} companies to process.")
    results = {}
    for code in codes:
        results[code] = download_company(code, incremental=incremental)
        time.sleep(RATE_LIMIT_DELAY)
    return results


def download_all(incremental=False):
    """Downloads filings for every listed company on TWSE/TPEx."""
    companies = get_all_listed_companies()
    if not companies:
        log("No companies returned. Aborting.", "ERROR")
        return
    log(f"Starting download for {len(companies)} companies...")
    for c in companies:
        code = c["stock_code"]
        if not code:
            continue
        download_company(code, incremental=incremental)
        time.sleep(RATE_LIMIT_DELAY)


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FSC XBRL Downloader")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stock",  help="Single stock code, e.g. 2330")
    group.add_argument("--list",   help="Text file with one stock code per line")
    group.add_argument("--all",    action="store_true", help="All listed companies")
    parser.add_argument("--incremental", action="store_true",
                        help="Skip periods already in the database")
    args = parser.parse_args()

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if args.stock:
        paths = download_company(args.stock, incremental=args.incremental)
        log(f"Done. {len(paths)} file(s) downloaded for {args.stock}.")

    elif args.list:
        results = download_from_list(args.list, incremental=args.incremental)
        total = sum(len(v) for v in results.values())
        log(f"Done. {total} file(s) downloaded across {len(results)} companies.")

    elif args.all:
        download_all(incremental=args.incremental)
        log("Full download complete.")
