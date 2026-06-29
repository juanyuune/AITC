#!/usr/bin/env python3
"""
Add Company — MOPS Version
===========================
The simplest way to add any Taiwan listed company to the AITC system.
Downloads directly from MOPS (mopsov.twse.com.tw) and imports into DB.

PREREQUISITE: DGX must have outbound HTTPS access to mopsov.twse.com.tw
              Ask IT to whitelist this domain.

Usage:
    python add_company.py 2330
    python add_company.py 2330 2303 2881 2882
    python add_company.py --file company_list.txt
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = Path(__file__).parent


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def run(script, args, label):
    log(label)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script)] + args,
        capture_output=False, text=True
    )
    return result.returncode == 0


def add_companies(codes):
    print()
    print("=" * 60)
    print("  AITC — Add Company from MOPS")
    print("=" * 60)
    print(f"  Companies : {', '.join(codes)}")
    print(f"  Source    : mopsov.twse.com.tw (Taiwan FSC MOPS)")
    print("=" * 60)
    print()

    # Step 0 — Make sure taxonomy exists
    import os
    from pathlib import Path
    taxonomy_dir = Path(os.environ.get("XBRL_TAXONOMY_DIR", "/home/user/AITC/taxonomy"))
    has_taxonomy = taxonomy_dir.exists() and any(
        d.is_dir() and "tifrs" in d.name.lower() for d in taxonomy_dir.iterdir()
    ) if taxonomy_dir.exists() else False

    if not has_taxonomy:
        print("  ── Step 0: Downloading taxonomy (first time only) ──")
        ok = run("mops_downloader.py", ["--taxonomy"], "Downloading TIFRS taxonomy from MOPS...")
        if not ok:
            print()
            print("  ERROR: Could not download taxonomy.")
            print("  This usually means mopsov.twse.com.tw is not accessible.")
            print("  Please ask IT to whitelist: mopsov.twse.com.tw")
            sys.exit(1)
        print()

    for code in codes:
        print(f"  ── Processing {code} ──────────────────────────────")
        print()

        # Step 1: Download from MOPS
        ok = run("mops_downloader.py", ["--stock", code],
                 f"Step 1/2  Downloading all XBRL filings for {code} from MOPS...")
        if not ok:
            log(f"  WARNING: Download had issues for {code}. Will try importing what we have.")

        print()

        # Step 2: Import into database
        ok = run("mops_import.py", ["--stock", code],
                 f"Step 2/2  Importing into database...")
        if not ok:
            log(f"  ERROR: Import failed for {code}.")
            continue

        print()

    print("=" * 60)
    print("  All done. Test in Claude Code:")
    print()
    print("  cd ~/AITC/plugins/aitc-credit-investigation")
    print("  claude")
    for code in codes[:3]:
        print(f"  ❯ xbrl {code} 最新 營業收入")
    print("=" * 60)
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--file":
        if len(sys.argv) < 3:
            print("Usage: python add_company.py --file companies.txt")
            sys.exit(1)
        with open(sys.argv[2], encoding="utf-8") as f:
            codes = []
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    code = line.split()[0]
                    if code:
                        codes.append(code)
        if not codes:
            print("No stock codes found in file.")
            sys.exit(1)
    else:
        codes = [c.strip() for c in sys.argv[1:] if c.strip() and not c.startswith("-")]

    if not codes:
        print("No stock codes provided.")
        sys.exit(1)

    add_companies(codes)
