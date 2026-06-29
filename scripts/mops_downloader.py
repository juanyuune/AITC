#!/usr/bin/env python3
"""
MOPS XBRL Downloader
=====================
Downloads XBRL filings directly from Taiwan MOPS using the t203sb01 endpoint.
This is the same page as: https://mopsov.twse.com.tw/mops/web/t203sb01

Requires only ONE domain to be whitelisted on DGX firewall:
    mopsov.twse.com.tw

Usage:
    # Add one company - downloads all available quarters
    python mops_downloader.py --stock 2330

    # Add multiple companies
    python mops_downloader.py --stock 2330 2303 2881

    # Add from list file
    python mops_downloader.py --list company_list.txt

    # Download specific year/quarter only
    python mops_downloader.py --stock 2330 --year 2024 --quarter Q3

After downloading, run the importer:
    python mops_import.py --stock 2330
"""

import os
import re
import sys
import time
import zipfile
import argparse
import requests
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────
DOWNLOAD_DIR = Path(os.environ.get("XBRL_DOWNLOAD_DIR", "/home/user/AITC/xbrl_downloads"))
TAXONOMY_DIR = Path(os.environ.get("XBRL_TAXONOMY_DIR", "/home/user/AITC/taxonomy"))
LOG_FILE     = Path("/home/user/AITC/logs/mops_downloader.log")

# MOPS endpoints — only need mopsov.twse.com.tw whitelisted
MOPS_BASE    = "https://mopsov.twse.com.tw"
MOPS_SEARCH  = f"{MOPS_BASE}/mops/web/t203sb01"
MOPS_XBRL    = f"{MOPS_BASE}/server-java/t57sb01"

HEADERS = {
    "User-Agent":  "Mozilla/5.0 (compatible; AITC-Research/1.0)",
    "Referer":     MOPS_SEARCH,
    "Content-Type": "application/x-www-form-urlencoded",
}

# Taiwan ROC year = Western year - 1911
# 2024 = 民國113年
RATE_LIMIT = 2.0   # seconds between requests — respect MOPS servers

# Quarters available on MOPS
# season: Q1=01, Q2=02, Q3=03, Q4=04
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
SEASON_MAP = {"Q1": "01", "Q2": "02", "Q3": "03", "Q4": "04"}

# Years to try (adjust range as needed)
# Going back to 2019 for full business cycle
START_YEAR = 2019
END_YEAR   = datetime.now().year


# ── Logging ───────────────────────────────────────────────────
def log(msg, level="INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── ROC year conversion ───────────────────────────────────────
def to_roc_year(western_year):
    return str(western_year - 1911)


# ── Download one XBRL filing ──────────────────────────────────
def search_filing(stock_code, year, quarter):
    """
    Searches MOPS t203sb01 for a filing.
    Returns the download URL or None if not found.
    """
    roc_year = to_roc_year(year)
    season   = SEASON_MAP[quarter]

    data = {
        "encodeURIComponent": "1",
        "step":               "1",
        "firstin":            "1",
        "co_id":              stock_code,
        "year":               roc_year,
        "season":             season,
    }

    try:
        resp = requests.post(MOPS_SEARCH, data=data, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Look for XBRL download link in the response
        # MOPS returns links like: t57sb01?id=XXXXXX&step=2&filetype=xbrl
        patterns = [
            r't57sb01\?[^"\']*filetype=xbrl[^"\']*',
            r'server-java/t57sb01\?[^"\']*',
            r'href="([^"]*xbrl[^"]*)"',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                match = matches[0]
                if match.startswith("http"):
                    return match
                elif match.startswith("/"):
                    return f"{MOPS_BASE}{match}"
                else:
                    return f"{MOPS_BASE}/server-java/{match}"

        # Also try looking for zip download
        zip_patterns = [r'href="([^"]*\.zip[^"]*)"']
        for pattern in zip_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                url = matches[0]
                return url if url.startswith("http") else f"{MOPS_BASE}{url}"

        return None

    except requests.RequestException as e:
        log(f"  [{stock_code}] Search failed for {year}{quarter}: {e}", "ERROR")
        return None


def download_filing(stock_code, year, quarter, dest_dir):
    """
    Downloads one XBRL filing for a company/period.
    Returns path to downloaded file or None.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded
    existing = list(dest_dir.glob(f"*{year}*{quarter}*")) + \
               list(dest_dir.glob(f"*{to_roc_year(year)}*{SEASON_MAP[quarter]}*"))
    if existing:
        log(f"  [{stock_code}] {year}{quarter} already downloaded: {existing[0].name}")
        return existing[0]

    log(f"  [{stock_code}] Searching MOPS for {year}{quarter}...")
    url = search_filing(stock_code, year, quarter)

    if not url:
        log(f"  [{stock_code}] {year}{quarter} not found on MOPS.")
        return None

    log(f"  [{stock_code}] Downloading {year}{quarter} from {url[:60]}...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()

        # Determine filename
        content_disp = resp.headers.get("Content-Disposition", "")
        fname_match  = re.search(r'filename[^;=\n]*=([^;\n]*)', content_disp)
        if fname_match:
            filename = fname_match.group(1).strip().strip('"\'')
        else:
            ext      = ".zip" if "zip" in url.lower() else ".xbrl"
            filename = f"{stock_code}_{year}{quarter}{ext}"

        dest_path = dest_dir / filename
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        log(f"  [{stock_code}] Saved: {dest_path.name} ({dest_path.stat().st_size:,} bytes)")

        # If zip, extract it
        if dest_path.suffix.lower() == ".zip":
            dest_path = extract_zip(dest_path, dest_dir, stock_code, year, quarter)

        return dest_path

    except requests.RequestException as e:
        log(f"  [{stock_code}] Download failed: {e}", "ERROR")
        return None


def extract_zip(zip_path, dest_dir, stock_code, year, quarter):
    """Extracts a zip file and returns the main XBRL file inside."""
    extract_dir = dest_dir / f"{stock_code}_{year}{quarter}_extracted"
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
        # Find the main XBRL/XML file
        xbrl_files = list(extract_dir.rglob("*.xbrl")) + \
                     list(extract_dir.rglob("*.xml"))
        if xbrl_files:
            # Prefer files with the stock code in name
            main = next((f for f in xbrl_files if stock_code in f.name), xbrl_files[0])
            log(f"  [{stock_code}] Extracted: {main.name}")
            return main
    return zip_path


# ── Download all quarters for a company ──────────────────────
def download_company(stock_code, year=None, quarter=None):
    """Downloads all available filings for one company."""
    log(f"Processing company: {stock_code}")
    dest_dir  = DOWNLOAD_DIR / stock_code
    downloaded = []

    years    = [year] if year else range(START_YEAR, END_YEAR + 1)
    quarters = [quarter] if quarter else QUARTERS

    for y in years:
        for q in quarters:
            # Skip future quarters
            now = datetime.now()
            if y == now.year and int(SEASON_MAP[q]) > (now.month - 1) // 3 + 1:
                continue

            path = download_filing(stock_code, y, q, dest_dir)
            if path:
                downloaded.append(path)
            time.sleep(RATE_LIMIT)

    log(f"  [{stock_code}] Done. {len(downloaded)} filing(s) downloaded.")
    return downloaded


# ── Download taxonomy (one time) ──────────────────────────────
def download_taxonomy():
    """
    Downloads the TIFRS taxonomy from MOPS.
    Only needs to be done once — taxonomy is reused for all companies.
    """
    TAXONOMY_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded
    existing = list(TAXONOMY_DIR.glob("tifrs-*"))
    if existing:
        log(f"Taxonomy already exists: {existing[0]}")
        return existing[0]

    log("Downloading TIFRS taxonomy from MOPS...")
    taxonomy_url = f"{MOPS_BASE}/mops/web/t147sb01"

    try:
        resp = requests.get(taxonomy_url, headers=HEADERS, timeout=30)
        html = resp.text

        # Find taxonomy download links
        zip_links = re.findall(r'href="([^"]*tifrs[^"]*\.zip[^"]*)"', html, re.IGNORECASE)
        if not zip_links:
            zip_links = re.findall(r'href="([^"]*taxonomy[^"]*\.zip[^"]*)"', html, re.IGNORECASE)

        if not zip_links:
            log("Could not find taxonomy download link. Please download manually from:", "WARN")
            log(f"  {taxonomy_url}", "WARN")
            return None

        # Get the latest taxonomy
        url = zip_links[-1]
        if not url.startswith("http"):
            url = f"{MOPS_BASE}{url}"

        log(f"Downloading taxonomy from: {url}")
        resp = requests.get(url, headers=HEADERS, timeout=120, stream=True)
        resp.raise_for_status()

        zip_path = TAXONOMY_DIR / "tifrs_taxonomy.zip"
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extract
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(TAXONOMY_DIR)

        # Find extracted folder
        folders = [d for d in TAXONOMY_DIR.iterdir() if d.is_dir() and "tifrs" in d.name.lower()]
        if folders:
            log(f"Taxonomy ready: {folders[0]}")
            return folders[0]

    except Exception as e:
        log(f"Taxonomy download failed: {e}", "ERROR")
        log("Please download manually from https://mopsov.twse.com.tw/mops/web/t147sb01", "WARN")
        return None


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MOPS XBRL Downloader")
    parser.add_argument("--stock",    nargs="+", help="Stock code(s), e.g. 2330 2303")
    parser.add_argument("--list",     help="Text file with one stock code per line")
    parser.add_argument("--year",     type=int,  help="Specific year, e.g. 2024")
    parser.add_argument("--quarter",  choices=["Q1","Q2","Q3","Q4"], help="Specific quarter")
    parser.add_argument("--taxonomy", action="store_true", help="Download taxonomy only")
    args = parser.parse_args()

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if args.taxonomy:
        download_taxonomy()
        sys.exit(0)

    # Collect stock codes
    codes = []
    if args.stock:
        codes.extend(args.stock)
    if args.list:
        with open(args.list, encoding="utf-8") as f:
            for line in f:
                code = line.strip().split("#")[0].strip().split()[0] if line.strip() else ""
                if code:
                    codes.append(code)

    if not codes:
        print("No stock codes provided. Use --stock 2330 or --list company_list.txt")
        sys.exit(1)

    print(f"\nDownloading XBRL filings for: {', '.join(codes)}")
    print(f"Years: {args.year or f'{START_YEAR}–{END_YEAR}'}")
    print(f"Quarter: {args.quarter or 'all'}")
    print(f"Destination: {DOWNLOAD_DIR}\n")

    total = 0
    for code in codes:
        files = download_company(code, year=args.year, quarter=args.quarter)
        total += len(files)

    print(f"\nDone. {total} filing(s) downloaded across {len(codes)} company/companies.")
    print(f"\nNext step — import into database:")
    for code in codes[:3]:
        print(f"  python mops_import.py --stock {code}")
