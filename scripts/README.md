# AITC Pipeline Scripts

Scripts for downloading, importing, and maintaining the XBRL financial database.

## Core Scripts

| Script | Purpose | When to Run |
|--------|---------|-------------|
| mops_downloader_v2.py | Downloads XBRL filings from MOPS | Daily via cron or manually |
| build_xbrl_sql.py | Parses XBRL and imports to database | After downloading |
| build_xbrl_embeddings.py | Rebuilds AI search index | After importing |
| auto_update.sh | Runs all three in sequence | Daily 6:00 AM via cron |

## Taxonomy Tools

| Script | Purpose |
|--------|---------|
| build_xbrl_mapping_json.py | Builds concept mapping from taxonomy |
| parse_xbrl_dictionary.py | Parses XBRL dictionary files |
| split_xbrl_dictionary.py | Splits dictionary by statement type |

## Cron Schedule

    0 6 * * * /home/user/AITC/scripts/auto_update.sh

## Add a New Company

    source ~/mcp-venv/bin/activate
    python mops_downloader_v2.py --stock 2886
    python build_xbrl_sql.py --taxonomy-root ~/AITC/taxonomy/tifrs-20250630/tifrs-20250630 --instance-dir ~/AITC/xbrl_downloads/2886 --sql-output ~/AITC/output/2886_import.sql --db-path ~/AITC/FinancialStatementXBRL.db
    source ~/AITC/agent-env/bin/activate
    python build_xbrl_embeddings.py
