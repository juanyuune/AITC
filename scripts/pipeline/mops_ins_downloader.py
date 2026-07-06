#!/usr/bin/env python3
"""
mops_ins_downloader.py
----------------------
Downloads XBRL filings for the 5 INS-taxonomy insurance companies
from Taiwan FSC MOPS. Mirrors the logic of mops_downloader_v2.py
but uses TYPEK=sii and handles INS-specific filing patterns.

INS Companies:
  5834  合庫金控    (Fu Hwa Financial — insurance taxonomy)
  2850  新光產物保險
  2851  中央再保險
  2852  第一產物保險
  2867  三商美邦人壽

Usage:
  python mops_ins_downloader.py                    # all 5 INS companies
  python mops_ins_downloader.py --stock 2850 2851  # specific companies
  python mops_ins_downloader.py --stock 5834 --typek-override otc
"""

import os
import sys
import re
import time
import logging
import argparse
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime

# ── paths ──────────────────────────────────────────────────────────────────
BASE_DIR      = Path.home() / "AITC"
DOWNLOAD_DIR  = BASE_DIR / "xbrl_downloads"
LOG_DIR       = BASE_DIR / "logs"
LOG_FILE      = LOG_DIR  / "ins_downloader.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── INS company registry ────────────────────────────────────────────────────
INS_COMPANIES = {
    "5834": {"name": "合庫金控",    "typek": "sii"},
    "2850": {"name": "新光產物保險", "typek": "sii"},
    "2851": {"name": "中央再保險",  "typek": "sii"},
    "2852": {"name": "第一產物保險", "typek": "sii"},
    "2867": {"name": "三商美邦人壽", "typek": "sii"},
}

# ── MOPS endpoints ──────────────────────────────────────────────────────────
MOPS_SEARCH_URL   = "https://mopsov.twse.com.tw/mops/web/ajax_t203sb01"
MOPS_DOWNLOAD_URL = "https://mopsov.twse.com.tw/server-java/FileDownLoad"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://mopsov.twse.com.tw/mops/web/t203sb01",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── helpers ─────────────────────────────────────────────────────────────────

def get_available_filings(stock_code: str, typek: str) -> list[tuple[str, str, str]]:
    """
    Call MOPS Endpoint 1 to get the list of available year/quarter filings.

    IMPORTANT: For INS-taxonomy companies, MOPS does NOT return year/season
    in plain table cells. Instead:
      - The visible cell shows ROC-calendar year+quarter combined, e.g. "115Q1"
        (ROC year = Gregorian year - 1911)
      - The actual Gregorian year, season, and report_id needed for download
        live inside the onclick JavaScript of the '下載' (Download) button:
        onclick="window.open('/server-java/FileDownLoad?functionName=t164sb01
                  &step=9&co_id=2850&year=2026&season=1&report_id=A','new1');"

    So we parse the onclick attribute directly with regex instead of reading
    table cells as plain text. This also captures report_id, which for INS
    companies is 'A' (individual/individual report), not 'C' (consolidated)
    as used for banking holding companies.

    Returns list of (year, season, report_id) tuples, e.g. [("2026","1","A"), ...].
    """
    typek_options = [typek]
    for alt in ["sii", "otc", "rotc"]:
        if alt not in typek_options:
            typek_options.append(alt)

    # Matches: co_id=2850&year=2026&season=1&report_id=A
    download_pattern = re.compile(
        r"co_id=(\d+)&year=(\d{4})&season=([1-4])&report_id=([A-Z])"
    )

    for tk in typek_options:
        log.info(f"  [{stock_code}] Searching MOPS with TYPEK={tk} ...")
        try:
            resp = SESSION.post(
                MOPS_SEARCH_URL,
                data={
                    "co_id":   stock_code,
                    "step":    "1",
                    "firstin": "1",
                    "off":     "1",
                    "TYPEK":   tk,
                },
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning(f"  [{stock_code}] Search request failed (TYPEK={tk}): {e}")
            continue

        # Parse download button onclick attributes directly from raw HTML —
        # do NOT rely on <td> text content, it's the wrong place for this data.
        matches = download_pattern.findall(resp.text)

        # Filter to only this company's own filings (in case of stray matches)
        filings = []
        seen = set()
        for co_id, year, season, report_id in matches:
            if co_id != stock_code:
                continue
            key = (year, season)
            if key in seen:
                continue
            seen.add(key)
            filings.append((year, season, report_id))

        if filings:
            # Sort newest first for readability in logs
            filings.sort(key=lambda x: (x[0], x[1]), reverse=True)
            log.info(f"  [{stock_code}] Found {len(filings)} filings via TYPEK={tk} "
                      f"(range: {filings[-1][0]}Q{filings[-1][1]} \u2192 {filings[0][0]}Q{filings[0][1]})")
            return filings
        else:
            log.warning(f"  [{stock_code}] No filings found with TYPEK={tk}")

    log.error(
        f"  [{stock_code}] No filings found with any TYPEK. "
        f"Company may file PDF-only or under a different code."
    )
    return []


def download_xbrl_file(
    stock_code: str,
    year: str,
    season: str,
    report_id: str,
    out_dir: Path,
) -> bool:
    """
    Call MOPS Endpoint 2 to download one XBRL XML file.
    report_id varies by company type:
      - 'C' = consolidated (used for banking/FH holding companies)
      - 'A' = individual/standalone (used for INS taxonomy companies)
    Returns True on success, False on failure/skip.
    """
    filename = f"{stock_code}_{year}Q{season}.xml"
    out_path = out_dir / filename

    if out_path.exists() and out_path.stat().st_size > 0:
        log.info(f"    SKIP  {filename}  (already exists)")
        return False  # False = skipped, not an error

    params = {
        "functionName": "t164sb01",
        "step":         "9",
        "co_id":        stock_code,
        "year":         year,
        "season":       season,
        "report_id":    report_id,
    }

    try:
        resp = SESSION.get(MOPS_DOWNLOAD_URL, params=params, timeout=60)
        resp.raise_for_status()

        # MOPS returns HTML error pages instead of XML for missing filings
        content_type = resp.headers.get("Content-Type", "")
        if "html" in content_type.lower() or resp.text.strip().startswith("<!DOCTYPE"):
            log.warning(f"    SKIP  {filename}  (MOPS returned HTML — filing not available)")
            return False

        if len(resp.content) < 500:
            log.warning(f"    SKIP  {filename}  (response too small — {len(resp.content)} bytes)")
            return False

        out_path.write_bytes(resp.content)
        log.info(f"    OK    {filename}  ({len(resp.content):,} bytes)")
        return True

    except requests.RequestException as e:
        log.error(f"    FAIL  {filename}  ({e})")
        return False


def process_company(stock_code: str, typek_override: str | None = None) -> dict:
    """
    Full download pipeline for one INS company.
    Returns a result summary dict.
    """
    info   = INS_COMPANIES.get(stock_code, {"name": "Unknown", "typek": "sii"})
    name   = info["name"]
    typek  = typek_override or info["typek"]

    log.info(f"[{stock_code}] {name} — starting INS download")

    out_dir = DOWNLOAD_DIR / stock_code
    out_dir.mkdir(parents=True, exist_ok=True)

    filings  = get_available_filings(stock_code, typek)
    result   = {
        "stock_code":  stock_code,
        "name":        name,
        "available":   len(filings),
        "downloaded":  0,
        "skipped":     0,
        "failed":      0,
        "no_filings":  len(filings) == 0,
    }

    if not filings:
        return result

    for year, season, report_id in filings:
        time.sleep(0.8)   # be polite to MOPS — avoid rate-limiting
        ok = download_xbrl_file(stock_code, year, season, report_id, out_dir)
        if ok is True:
            result["downloaded"] += 1
        elif ok is False:
            # Could be skipped or soft-failed — check file exists
            fname = out_dir / f"{stock_code}_{year}Q{season}.xml"
            if fname.exists():
                result["skipped"] += 1
            else:
                result["failed"] += 1

    log.info(
        f"[{stock_code}] Done — "
        f"available: {result['available']}, "
        f"downloaded: {result['downloaded']}, "
        f"skipped: {result['skipped']}, "
        f"failed: {result['failed']}"
    )
    return result


def print_summary(results: list[dict]) -> None:
    """Print a clean summary table and 5834-specific note."""
    log.info("")
    log.info("=" * 60)
    log.info("  INS DOWNLOADER — SUMMARY")
    log.info("=" * 60)
    log.info(f"  {'Code':<6}  {'Company':<12}  {'Avail':>5}  {'DL':>5}  {'Skip':>5}  {'Fail':>5}")
    log.info(f"  {'-'*6}  {'-'*12}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}")

    total_dl = 0
    for r in results:
        status = "⚠ no filings" if r["no_filings"] else ""
        log.info(
            f"  {r['stock_code']:<6}  {r['name']:<12}  "
            f"{r['available']:>5}  {r['downloaded']:>5}  "
            f"{r['skipped']:>5}  {r['failed']:>5}  {status}"
        )
        total_dl += r["downloaded"]

    log.info(f"  {'-'*6}  {'-'*12}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}")
    log.info(f"  Total new files downloaded: {total_dl}")
    log.info("=" * 60)

    # Specific note about 5834 if it returned nothing
    for r in results:
        if r["stock_code"] == "5834" and r["no_filings"]:
            log.info("")
            log.info("  NOTE — 合庫金控 (5834):")
            log.info("  MOPS returned no XBRL filings for 5834 with any TYPEK value.")
            log.info("  Likely causes:")
            log.info("    1. Files under financial holding FH taxonomy, not INS")
            log.info("    2. PDF-only filing — no XBRL available on MOPS")
            log.info("    3. Filed under a different stock code on MOPS")
            log.info("  Action: manually check https://mopsov.twse.com.tw/mops/web/t203sb01")
            log.info("          Search 5834, note what format the filing is listed as.")


# ── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download INS-taxonomy XBRL filings from MOPS for insurance companies."
    )
    parser.add_argument(
        "--stock",
        nargs="+",
        default=list(INS_COMPANIES.keys()),
        help="Stock codes to process (default: all 5 INS companies)",
    )
    parser.add_argument(
        "--typek-override",
        default=None,
        help="Force a specific TYPEK value for all companies (sii / otc / rotc)",
    )
    args = parser.parse_args()

    log.info(f"INS Downloader started — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Target companies: {', '.join(args.stock)}")
    log.info("")

    results = []
    for code in args.stock:
        if code not in INS_COMPANIES:
            log.warning(f"Unknown company code: {code} — skipping")
            continue
        result = process_company(code, typek_override=args.typek_override)
        results.append(result)
        time.sleep(2)   # pause between companies

    print_summary(results)

    log.info("")
    log.info("Next step: run build_xbrl_sql.py with --taxonomy-root pointing to")
    log.info("the INS taxonomy directory, then rebuild embeddings.")
    log.info("See Priority 1 instructions in the system report.")


if __name__ == "__main__":
    main()