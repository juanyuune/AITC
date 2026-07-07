#!/bin/bash
# =============================================================================
# ins_update.sh  —  AITC Insurance Company Daily Update
# =============================================================================
# Runs daily at 7:00 AM via cron (one hour after main banking pipeline).
# Step 1: Download new XBRL filings for 4 insurance companies
# Step 2: Import any new files into the database
# Step 3: Rebuild embeddings if new data was imported

AITC_DIR="/home/user/AITC"
SCRIPTS_DIR="$AITC_DIR/scripts/pipeline"
LOG_DIR="$AITC_DIR/logs"
LOG="$LOG_DIR/ins_update.log"
DB_PATH="$AITC_DIR/FinancialStatementXBRL.db"
TAXONOMY="$AITC_DIR/taxonomy/tifrs-20250630/tifrs-20250630"
DOWNLOAD_DIR="$AITC_DIR/xbrl_downloads"
OUTPUT_DIR="$AITC_DIR/output"
MCP_VENV="/home/user/mcp-venv"
AGENT_VENV="$AITC_DIR/agent-env"
INS_COMPANIES="2850 2851 2852 2867"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"; }

mkdir -p "$OUTPUT_DIR"
RUN_DATE=$(date +%Y%m%d_%H%M%S)
log "============================================================"
log "INS UPDATE STARTED"
log "============================================================"

# ── Step 1: Download ──────────────────────────────────────────
log "Step 1: Downloading insurance filings..."
$MCP_VENV/bin/python3 "$SCRIPTS_DIR/mops_ins_downloader.py" >> "$LOG" 2>&1
DOWNLOAD_EXIT=$?
if [ "$DOWNLOAD_EXIT" -ne 0 ]; then
    log "ERROR: Download failed (exit $DOWNLOAD_EXIT) — aborting import"
    exit 1
fi
log "Step 1: Download complete"

# ── Step 2: Import new files only ────────────────────────────
log "Step 2: Importing new files into database..."
ROWS_BEFORE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM financial_metric_value;" 2>/dev/null)
NEW_FILES_TOTAL=0

for CODE in $INS_COMPANIES; do
    STAGE=$(mktemp -d)
    # Only files newer than last run (use DB modification time as marker)
    NEW_FILES=$(find "$DOWNLOAD_DIR/$CODE" -name "*.xml" -newer "$DB_PATH" 2>/dev/null)
    if [ -z "$NEW_FILES" ]; then FILE_COUNT=0; else FILE_COUNT=$(echo "$NEW_FILES" | wc -l | tr -d ' '); fi

    if [ "$FILE_COUNT" -eq 0 ]; then
        log "  [$CODE] No new files — skipping import"
        rm -rf "$STAGE"
        continue
    fi

    log "  [$CODE] Found $FILE_COUNT new file(s) — importing..."
    echo "$NEW_FILES" | while read -r f; do
        [ -n "$f" ] && cp "$f" "$STAGE/"
    done

    $AGENT_VENV/bin/python3 "$SCRIPTS_DIR/build_xbrl_sql.py" \
        --taxonomy-root "$TAXONOMY" \
        --instance-dir "$STAGE" \
        --sql-output "$OUTPUT_DIR/${CODE}_ins_import_${RUN_DATE}.sql" \
        --db-path "$DB_PATH" >> "$LOG" 2>&1
    IMPORT_EXIT=$?
    rm -rf "$STAGE"

    if [ "$IMPORT_EXIT" -eq 0 ]; then
        log "  [$CODE] Import OK"
        NEW_FILES_TOTAL=$((NEW_FILES_TOTAL + ${FILE_COUNT:-0}))
    else
        log "  [$CODE] Import FAILED (exit $IMPORT_EXIT)"
    fi
done

ROWS_AFTER=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM financial_metric_value;" 2>/dev/null)
NET_NEW=$((ROWS_AFTER - ROWS_BEFORE))
log "Step 2: Import complete — $NEW_FILES_TOTAL file(s) processed, +$NET_NEW rows"

# ── Step 3: Rebuild embeddings if new data ────────────────────
if [ "$NET_NEW" -gt 0 ]; then
    log "Step 3: Rebuilding embeddings (new data detected)..."
    cd /home/user/AITC-CreditInvestigationChatBotAgent && SQLITE_DB_PATH="$DB_PATH" $AGENT_VENV/bin/python3 build_xbrl_embeddings.py >> "$LOG" 2>&1
    EMB_EXIT=$?
    if [ "$EMB_EXIT" -eq 0 ]; then
        log "Step 3: Embeddings rebuilt OK"
    else
        log "Step 3: Embeddings rebuild FAILED (exit $EMB_EXIT)"
    fi
else
    log "Step 3: Skipped — no new data"
fi

log "============================================================"
log "INS UPDATE COMPLETE — DB rows: $ROWS_AFTER (+$NET_NEW new)"
log "============================================================"
