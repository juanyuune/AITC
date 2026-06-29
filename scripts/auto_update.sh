#!/bin/bash
cd /home/user/AITC
export XBRL_DB_PATH=/home/user/AITC/FinancialStatementXBRL.db
export XBRL_DOWNLOAD_DIR=/home/user/AITC/xbrl_downloads
TAXONOMY=/home/user/AITC/taxonomy/tifrs-20250630/tifrs-20250630
LOG=/home/user/AITC/logs/auto_update.log
SCRIPTS=/home/user/AITC/scripts

echo "======================================" >> $LOG
echo "$(date) — Auto update started" >> $LOG

COMPANIES=$(sqlite3 $XBRL_DB_PATH \
  "SELECT DISTINCT company_code FROM report_instance ORDER BY company_code;" \
  | tr '\n' ' ')
echo "Updating: $COMPANIES" >> $LOG

source /home/user/mcp-venv/bin/activate
python $SCRIPTS/mops_downloader_v2.py --stock $COMPANIES >> $LOG 2>&1

for dir in $XBRL_DOWNLOAD_DIR/*/; do
  code=$(basename $dir)
  python3 $SCRIPTS/build_xbrl_sql.py \
    --taxonomy-root $TAXONOMY \
    --instance-dir $dir \
    --sql-output /home/user/AITC/output/${code}_import.sql \
    --db-path $XBRL_DB_PATH >> $LOG 2>&1
done

source /home/user/AITC/agent-env/bin/activate
python $SCRIPTS/build_xbrl_embeddings.py >> $LOG 2>&1

echo "$(date) — Auto update complete" >> $LOG
echo "======================================" >> $LOG
