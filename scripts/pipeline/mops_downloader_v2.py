#!/usr/bin/env python3
import os, re, sys, time, argparse, requests
from pathlib import Path
from datetime import datetime

DOWNLOAD_DIR = Path(os.environ.get("XBRL_DOWNLOAD_DIR", "/home/user/AITC/xbrl_downloads"))
LOG_FILE     = Path("/home/user/AITC/logs/mops_downloader.log")
MOPS_BASE    = "https://mopsov.twse.com.tw"
MOPS_SEARCH  = f"{MOPS_BASE}/mops/web/ajax_t203sb01"
MOPS_DL      = f"{MOPS_BASE}/server-java/FileDownLoad"
HEADERS      = {"User-Agent": "Mozilla/5.0", "Referer": f"{MOPS_BASE}/mops/web/t203sb01"}
SEASON_MAP   = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
START_YEAR   = 2019
RATE_LIMIT   = 1.5

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    open(LOG_FILE, "a").write(line + "\n")

def get_available_filings(stock_code):
    data = {"step":"1","firstin":"1","off":"1","keyword4":"","code1":"",
            "TYPEK2":"","checkbtn":"","queryName":"co_id","inpuType":"co_id",
            "TYPEK":"all","co_id": stock_code}
    try:
        resp = requests.post(MOPS_SEARCH, data=data, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        matches = re.findall(r'year=(\d{4})&season=(\d)&report_id=C', resp.text)
        seen, result = set(), []
        for y, s in matches:
            key = (int(y), int(s))
            if key not in seen:
                seen.add(key)
                result.append(key)
        result.sort()
        return result
    except Exception as e:
        log(f"  [{stock_code}] Failed to get filing list: {e}", "ERROR")
        return []

def download_filing(stock_code, year, season_int, dest_dir):
    quarter  = f"Q{season_int}"
    filename = f"{stock_code}_{year}{quarter}.xml"
    dest_path = dest_dir / filename
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        log(f"  [{stock_code}] {year}{quarter} already exists — skipping.")
        return dest_path
    url = (f"{MOPS_DL}?functionName=t164sb01&step=9"
           f"&co_id={stock_code}&year={year}&season={season_int}&report_id=C")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "html" in content_type.lower() and len(resp.content) < 5000:
            log(f"  [{stock_code}] {year}{quarter} — no filing available.", "WARN")
            return None
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_kb = dest_path.stat().st_size // 1024
        log(f"  [{stock_code}] Downloaded {year}{quarter} → {filename} ({size_kb}KB)")
        return dest_path
    except Exception as e:
        log(f"  [{stock_code}] Download failed for {year}{quarter}: {e}", "ERROR")
        return None

def download_company(stock_code, year_filter=None, quarter_filter=None):
    log(f"Processing {stock_code}...")
    dest_dir = DOWNLOAD_DIR / stock_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    available = get_available_filings(stock_code)
    if not available:
        log(f"  [{stock_code}] No filings found.", "WARN")
        return []
    log(f"  [{stock_code}] {len(available)} filing(s) available.")
    downloaded = []
    for year, season_int in available:
        if year_filter and year != year_filter:
            continue
        if quarter_filter and season_int != SEASON_MAP[quarter_filter]:
            continue
        if year < START_YEAR:
            continue
        path = download_filing(stock_code, year, season_int, dest_dir)
        if path:
            downloaded.append(path)
        time.sleep(RATE_LIMIT)
    log(f"  [{stock_code}] Done. {len(downloaded)} file(s) downloaded.")
    return downloaded

def get_all_listed_companies():
    log("Fetching all listed companies from TWSE API...")
    try:
        resp = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers=HEADERS, timeout=30)
        data = resp.json()
        codes = list({item["Code"] for item in data if item.get("Code","").isdigit()})
        log(f"Found {len(codes)} companies.")
        return sorted(codes)
    except Exception as e:
        log(f"Failed to fetch company list: {e}", "ERROR")
        return []

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock",   nargs="+")
    parser.add_argument("--list",    help="Text file with stock codes")
    parser.add_argument("--all",     action="store_true", help="Download ALL listed companies")
    parser.add_argument("--year",    type=int)
    parser.add_argument("--quarter", choices=["Q1","Q2","Q3","Q4"])
    args = parser.parse_args()

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    codes = []

    if args.all:
        codes = get_all_listed_companies()
    if args.stock:
        codes.extend(args.stock)
    if args.list:
        with open(args.list, encoding="utf-8") as f:
            for line in f:
                code = line.split("#")[0].strip().split()[0] if line.strip() else ""
                if code:
                    codes.append(code)

    if not codes:
        print("Use --stock 2330  or  --list company_list.txt  or  --all")
        sys.exit(1)

    print(f"\nCompanies: {len(codes)}  |  Source: mopsov.twse.com.tw\n")
    total = 0
    for code in codes:
        files = download_company(code, year_filter=int(args.year) if args.year else None, quarter_filter=args.quarter)
        total += len(files)
    print(f"\nDone. {total} filing(s) downloaded.")
