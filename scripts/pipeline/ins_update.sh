#!/bin/bash
# =============================================================================
# ins_update.sh  —  AITC Insurance Company Daily Update
# =============================================================================
# Runs daily at 7:00 AM via cron (one hour after main banking pipeline).
# Step 1: Download new XBRL filings for 4 insurance companies
# Step 2: Import any new files into the database
# Step 3: Rebuild embeddings if new data was imported
#
# FIX (August 4, 2026):
#   Previously used -newer "$DB_PATH" as the import marker.
#   This was unreliable because SQLite updates the DB file mtime on every
#   write — any API request resets it, causing ins_update.sh to silently
#   skip genuinely new filings on the next run. In the opposite case, if
#   the DB mtime was stale, it would re-import everything.
#   Now uses a dedicated marker file (.last_ins_import_marker), consistent
#   with auto_update.sh. Marker is only updated on a fully clean run.
# =============================================================================

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

# Dedicated marker file — modification time = last successful import.
# Only XML files newer than this are imported on each run.
# This is separate from auto_update.sh's marker so the two pipelines
# do not interfere with each other.
IMPORT_MARKER="$AITC_DIR/.last_ins_import_marker"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"; }

mkdir -p "$OUTPUT_DIR"
RUN_DATE=$(date +%Y%m%d_%H%M%S)

log "============================================================"
log "INS UPDATE STARTED"
log "============================================================"

ERRORS=()

# ── Step 1: Download ──────────────────────────────────────────────────────────
log "Step 1: Downloading insurance filings..."

$MCP_VENV/bin/python3 "$SCRIPTS_DIR/mops_ins_downloader.py" >> "$LOG" 2>&1
DOWNLOAD_EXIT=$?

if [ "$DOWNLOAD_EXIT" -ne 0 ]; then
    log "ERROR: Download failed (exit $DOWNLOAD_EXIT) — aborting import"
    exit 1
fi
log "Step 1: Download complete"

# ── Step 2: Import new files only ────────────────────────────────────────────
log "Step 2: Importing new files into database..."

# Initialise marker on first run — existing files are already in the DB
# (or will be caught by the report_id guard in build_xbrl_sql.py).
if [ ! -f "$IMPORT_MARKER" ]; then
    log "No import marker found — first run of fixed script."
    log "Creating marker now. Only files downloaded AFTER this point"
    log "will be picked up on future runs. Existing files are already"
    log "in the database or will be skipped by the report_id guard."
    touch "$IMPORT_MARKER"
fi

ROWS_BEFORE=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM financial_metric_value;" 2>/dev/null || echo "0")
NEW_FILES_TOTAL=0
COMPANIES_IMPORTED=0

for CODE in $INS_COMPANIES; do
    DIR="$DOWNLOAD_DIR/$CODE"

    if [ ! -d "$DIR" ]; then
        log "  [$CODE] Download directory not found — skipping"
        continue
    fi

    # Use dedicated marker file — NOT DB mtime.
    NEW_FILES=$(find "$DIR" -name "*.xml" -newer "$IMPORT_MARKER" 2>/dev/null)

    if [ -z "$NEW_FILES" ]; then
        log "  [$CODE] No new files since last run — skipping"
        continue
    fi

    FILE_COUNT=$(echo "$NEW_FILES" | wc -l | tr -d ' ')
    log "  [$CODE] Found $FILE_COUNT new file(s) — importing..."

    STAGE=$(mktemp -d)
    echo "$NEW_FILES" | while read -r f; do
        [ -n "$f" ] && cp "$f" "$STAGE/"
    done

    $AGENT_VENV/bin/python3 "$SCRIPTS_DIR/build_xbrl_sql.py" \
        --taxonomy-root "$TAXONOMY" \
        --instance-dir  "$STAGE" \
        --sql-output    "$OUTPUT_DIR/${CODE}_ins_import_${RUN_DATE}.sql" \
        --db-path       "$DB_PATH" >> "$LOG" 2>&1
    IMPORT_EXIT=$?

    rm -rf "$STAGE"

    if [ "$IMPORT_EXIT" -eq 0 ]; then
        log "  [$CODE] Import OK"
        NEW_FILES_TOTAL=$((NEW_FILES_TOTAL + FILE_COUNT))
        COMPANIES_IMPORTED=$((COMPANIES_IMPORTED + 1))
    else
        log "  [$CODE] Import FAILED (exit $IMPORT_EXIT)"
        ERRORS+=("Import failed: $CODE")
    fi
done

ROWS_AFTER=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM financial_metric_value;" 2>/dev/null || echo "0")
NET_NEW=$((ROWS_AFTER - ROWS_BEFORE))
log "Step 2: Import complete — $NEW_FILES_TOTAL file(s) processed, +$NET_NEW rows"

# Sanity check — flag abnormal row growth (same threshold as auto_update.sh)
if [ "$NET_NEW" -gt 50000 ]; then
    log "WARNING: $NET_NEW new rows is unusually high for a daily INS delta run."
    log "         Verify no duplication — check xbrl_fact duplicate count."
    ERRORS+=("Abnormally large row growth: $NET_NEW — possible duplication regression")
fi

# ── Step 3: Rebuild embeddings if new data ────────────────────────────────────
if [ "$NET_NEW" -gt 0 ]; then
    log "Step 3: Rebuilding embeddings (new data detected)..."
    cd /home/user/AITC-CreditInvestigationChatBotAgent && \
        SQLITE_DB_PATH="$DB_PATH" \
        $AGENT_VENV/bin/python3 build_xbrl_embeddings.py >> "$LOG" 2>&1
    EMB_EXIT=$?
    if [ "$EMB_EXIT" -eq 0 ]; then
        log "Step 3: Embeddings rebuilt OK"
    else
        log "Step 3: Embeddings rebuild FAILED (exit $EMB_EXIT)"
        ERRORS+=("Embeddings rebuild failed")
    fi
else
    log "Step 3: Skipped — no new data"
fi

# ── Step 4: Update marker — only on fully clean run ──────────────────────────
if [ ${#ERRORS[@]} -eq 0 ]; then
    touch "$IMPORT_MARKER"
    log "Import marker updated — next run picks up only files newer than now."
else
    log "WARNING: Errors occurred — marker NOT updated."
    log "         Affected files will be retried on the next run."
fi

log "============================================================"
log "INS UPDATE COMPLETE"
log "  DB rows      : $ROWS_AFTER  (+$NET_NEW new)"
log "  Files imported: $NEW_FILES_TOTAL"
log "  Errors        : ${#ERRORS[@]}"
log "============================================================"

# Exit non-zero if any errors so cron can detect failures
[ ${#ERRORS[@]} -eq 0 ] && exit 0 || exit 1
