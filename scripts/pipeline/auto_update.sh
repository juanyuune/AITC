#!/bin/bash
# =============================================================================
# auto_update.sh  —  AITC MOPS Auto-Download & Update Pipeline  (FIXED v2)
# =============================================================================
# Runs daily at 6:00 AM via cron.
#
# CRITICAL FIX (2026-06-30): The previous version re-imported EVERY XML file
# in EVERY company folder on EVERY run, regardless of whether it was already
# in the database. Since financial_metric_value uses plain INSERT (no
# OR REPLACE / unique constraint), this silently duplicated data on every
# single cron run. A one-time cleanup removed 1,066,369 duplicate rows
# (47% of the database) on 2026-06-30.
#
# This version only imports files that are NEWER than a marker file updated
# at the end of each successful run — so only genuinely new MOPS filings
# get imported, exactly like mops_downloader_v2.py already does for downloads.
# =============================================================================

# ── configuration ─────────────────────────────────────────────────────────────
AITC_DIR="/home/user/AITC"
SCRIPTS_DIR="$AITC_DIR/scripts/pipeline"
LOG_DIR="$AITC_DIR/logs"
LOG="$LOG_DIR/auto_update.log"
SUMMARY_FILE="$LOG_DIR/last_run_summary.txt"
DB_PATH="$AITC_DIR/FinancialStatementXBRL.db"
TAXONOMY="$AITC_DIR/taxonomy/tifrs-20250630/tifrs-20250630"
XBRL_DOWNLOAD_DIR="$AITC_DIR/xbrl_downloads"
OUTPUT_DIR="$AITC_DIR/output"

# Marker file — its modification time marks "last successful import".
# Only XML files newer than this get re-processed on the next run.
IMPORT_MARKER="$AITC_DIR/.last_import_marker"

MCP_VENV="/home/user/mcp-venv"
AGENT_VENV="$AITC_DIR/agent-env"

mkdir -p "$LOG_DIR" "$OUTPUT_DIR"

RUN_START=$(date "+%Y-%m-%d %H:%M:%S")
RUN_DATE=$(date "+%Y-%m-%d")

# ── logging helpers ───────────────────────────────────────────────────────────
log() { echo "[$(date '+%H:%M:%S')]  $1" >> "$LOG"; }

# ── counters ──────────────────────────────────────────────────────────────────
ERRORS=()
FILES_NEW_ON_DISK=0
COMPANIES_WITH_NEW_FILES=0
COMPANIES_IMPORTED=0
NEW_ROWS=0

echo "======================================" >> "$LOG"
echo "$(date) — Auto update started" >> "$LOG"

# ── step 1: pre-flight checks ─────────────────────────────────────────────────
log "Step 1: Pre-flight checks"

if [ ! -f "$DB_PATH" ]; then
    log "ERROR: Database not found at $DB_PATH"
    ERRORS+=("Database missing")
fi

DISK_FREE_GB=$(df "$AITC_DIR" | awk 'NR==2 {printf "%.1f", $4/1048576}')
log "Disk free: ${DISK_FREE_GB} GB"
if (( $(echo "$DISK_FREE_GB < 5.0" | bc -l 2>/dev/null || echo 0) )); then
    log "WARNING: Low disk space — ${DISK_FREE_GB} GB free"
    ERRORS+=("Low disk: ${DISK_FREE_GB}GB free")
fi

if curl -s --max-time 10 "https://mopsov.twse.com.tw" > /dev/null 2>&1; then
    log "MOPS reachable: OK"
else
    log "WARNING: Cannot reach MOPS"
    ERRORS+=("MOPS unreachable at start")
fi

DB_ROWS_BEFORE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM financial_metric_value;" 2>/dev/null || echo "0")
DB_SIZE_BEFORE=$(du -sh "$DB_PATH" 2>/dev/null | cut -f1)
log "DB rows before: $DB_ROWS_BEFORE  (size: $DB_SIZE_BEFORE)"

# ── step 2: download new filings from MOPS ───────────────────────────────────
log "Step 2: Download new filings from MOPS"

COMPANIES=$(sqlite3 "$DB_PATH" \
  "SELECT DISTINCT company_code FROM report_instance ORDER BY company_code;" \
  | tr '\n' ' ')
log "Tracking companies: $COMPANIES"

source "$MCP_VENV/bin/activate"
python "$SCRIPTS_DIR/mops_downloader_v2.py" --stock $COMPANIES >> "$LOG" 2>&1
DOWNLOAD_EXIT=$?
deactivate

if [ "$DOWNLOAD_EXIT" -ne 0 ]; then
    log "WARNING: Downloader exited with code $DOWNLOAD_EXIT"
    ERRORS+=("Downloader exit code: $DOWNLOAD_EXIT")
fi

# ── step 3: import ONLY files newer than the last successful import ─────────
log "Step 3: Import new files only (marker-based, prevents re-import duplication)"

if [ ! -f "$IMPORT_MARKER" ]; then
    log "No import marker found — creating one now (first run of fixed script)."
    log "NOTE: existing files will NOT be re-imported. Only files downloaded"
    log "      AFTER this point will be picked up on the next run."
    touch "$IMPORT_MARKER"
fi

for dir in "$XBRL_DOWNLOAD_DIR"/*/; do
    code=$(basename "$dir")

    NEW_FILES=$(find "$dir" -name "*.xml" -newer "$IMPORT_MARKER" 2>/dev/null)

    if [ -z "$NEW_FILES" ]; then
        continue
    fi

    NUM_NEW=$(echo "$NEW_FILES" | wc -l)
    FILES_NEW_ON_DISK=$((FILES_NEW_ON_DISK + NUM_NEW))
    COMPANIES_WITH_NEW_FILES=$((COMPANIES_WITH_NEW_FILES + 1))

    log "  [$code] $NUM_NEW new file(s) — importing"

    STAGE_DIR=$(mktemp -d)
    echo "$NEW_FILES" | while read -r f; do
        [ -n "$f" ] && cp "$f" "$STAGE_DIR/"
    done

    python3 "$SCRIPTS_DIR/build_xbrl_sql.py" \
        --taxonomy-root "$TAXONOMY" \
        --instance-dir "$STAGE_DIR" \
        --sql-output "$OUTPUT_DIR/${code}_import_${RUN_DATE}.sql" \
        --db-path "$DB_PATH" >> "$LOG" 2>&1

    IMPORT_EXIT=$?
    rm -rf "$STAGE_DIR"

    if [ "$IMPORT_EXIT" -eq 0 ]; then
        COMPANIES_IMPORTED=$((COMPANIES_IMPORTED + 1))
        log "  [$code] Import OK"
    else
        log "  [$code] Import FAILED (exit $IMPORT_EXIT)"
        ERRORS+=("Import failed: $code")
    fi
done

if [ "$FILES_NEW_ON_DISK" -eq 0 ]; then
    log "No new files found since last run — nothing to import today."
fi

# ── step 4: rebuild search index — only if something actually changed ───────
log "Step 4: Rebuild search index"

if [ "$COMPANIES_IMPORTED" -gt 0 ]; then
    source "$AGENT_VENV/bin/activate"
    export XBRL_DB_PATH="$DB_PATH"
    cd /home/user/AITC-CreditInvestigationChatBotAgent && SQLITE_DB_PATH="$DB_PATH" $AGENT_VENV/bin/python3 build_xbrl_embeddings.py >> "$LOG" 2>&1
    EMBED_EXIT=$?
    deactivate

    if [ "$EMBED_EXIT" -eq 0 ]; then
        log "Embeddings rebuilt OK"
    else
        log "WARNING: Embeddings rebuild failed (exit $EMBED_EXIT)"
        ERRORS+=("Embeddings rebuild failed")
    fi
else
    log "No new imports — skipping embeddings rebuild"
fi

# ── step 5: update the marker — only AFTER a fully successful run ───────────
if [ ${#ERRORS[@]} -eq 0 ]; then
    touch "$IMPORT_MARKER"
    log "Import marker updated — next run will only look for files newer than now."
else
    log "WARNING: Errors occurred — marker NOT updated, so affected files will be retried tomorrow."
fi

# ── step 6: post-run verification ────────────────────────────────────────────
log "Step 6: Post-run verification"

DB_ROWS_AFTER=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM financial_metric_value;" 2>/dev/null || echo "0")
DB_SIZE_AFTER=$(du -sh "$DB_PATH" 2>/dev/null | cut -f1)
NEW_ROWS=$((DB_ROWS_AFTER - DB_ROWS_BEFORE))

log "DB rows after:  $DB_ROWS_AFTER  (size: $DB_SIZE_AFTER)"
log "New rows added: $NEW_ROWS"

if [ "$NEW_ROWS" -gt 100000 ]; then
    log "WARNING: $NEW_ROWS new rows is unusually high for a daily delta run."
    log "         Verify no duplication has been reintroduced — run the"
    log "         duplicate-check query from the system report before trusting this."
    ERRORS+=("Abnormally large row growth: $NEW_ROWS — possible duplication regression")
fi

if curl -s --max-time 5 "http://localhost:3001/chatbot/test" > /dev/null 2>&1; then
    log "Backend (port 3001): UP"
else
    log "WARNING: Backend not responding after update"
    ERRORS+=("Backend down after update")
fi

# ── step 7: write summary ────────────────────────────────────────────────────
RUN_END=$(date "+%Y-%m-%d %H:%M:%S")
ERROR_COUNT=${#ERRORS[@]}
STATUS="SUCCESS"
[ "$ERROR_COUNT" -gt 0 ] && STATUS="WARNING"

cat > "$SUMMARY_FILE" << SUMMARY
AITC AUTO-UPDATE SUMMARY
========================
Date          : $RUN_DATE
Started       : $RUN_START
Completed     : $RUN_END
Status        : $STATUS

DATA CHANGES
------------
New files found on disk : $FILES_NEW_ON_DISK
Companies with new data : $COMPANIES_WITH_NEW_FILES
Companies imported OK   : $COMPANIES_IMPORTED
New DB rows added       : $NEW_ROWS
DB size                 : $DB_SIZE_AFTER  (was: $DB_SIZE_BEFORE)
DB total rows           : $DB_ROWS_AFTER

$([ "$ERROR_COUNT" -gt 0 ] && echo "WARNINGS / ERRORS
-----------------
$(printf '%s\n' "${ERRORS[@]}")")

NEXT SCHEDULED RUN
------------------
Tomorrow 06:00 AM
SUMMARY

log "Status: $STATUS  |  New rows: $NEW_ROWS  |  Errors: $ERROR_COUNT"
cat "$SUMMARY_FILE" >> "$LOG"

echo "$(date) — Auto update complete" >> "$LOG"
echo "======================================" >> "$LOG"

exit 0
