#!/bin/bash
LOG="$HOME/AITC/logs/warmup.log"
BASE="http://localhost:3001"
log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG"; }

ask() {
    local q="$1" label="$2"
    log "Warming: $label"
    # Use GET endpoint — matches web UI cache key format
    local encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$q")
    curl -s "$BASE/chatbot/$encoded" --max-time 420 > /dev/null 2>&1
    log "Done: $label"
}

log "=== Warmup Started ==="
ask "哪幾家金控超過警示線" "MCP Screen"
ask "富邦金控和國泰金控財務比較" "MCP Compare"
ask "富邦金控 2024Q3 速動比率" "Fubon quick ratio"
ask "國泰金控 2024Q3 負債比率" "Cathay debt ratio"
ask "兆豐金控 2024Q3 ROA" "Mega ROA"
ask "中信金控 2024Q3 ROE" "CTBC ROE"
ask "玉山金控 2024Q3 流動比率" "E.Sun current ratio"
ask "元大金控 2024Q3 財務槓桿倍數" "Yuanta leverage"
ask "台新金控 2024Q3 股東權益比率" "Taishin equity ratio"
ask "第一金控 2024Q3 速動比率" "First Financial quick ratio"
ask "中央再保險 2024Q3 流動比率" "Central Re current ratio"
ask "新光產物保險 2024Q3 現金比率" "Shin Kong cash ratio"
ask "富邦金控整體財務體質怎麼樣，有沒有值得長期信賴？" "Fubon overall analysis"
ask "國泰金控的財務結構偏保守還是偏積極，跟業界比起來如何？" "Cathay structure"
ask "中信金控目前主要的財務風險點在哪裡，有什麼需要特別留意的？" "CTBC risk"
ask "元大金控有沒有財務壓力的跡象，從報表上能看出來嗎？" "Yuanta pressure"
ask "華南金控財報裡面有沒有哪個數字特別不尋常，需要特別注意？" "Hua Nan anomaly"
ask "新光金控從財報來看，公司整體經營狀況算穩定嗎？" "Shin Kong stability"
log "=== Warmup Complete ==="
