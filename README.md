# AITC Credit Investigation Platform
## 授信調查系統

On-premise AI platform for Taiwan financial institution credit investigation.
Built on DGX Spark (192.168.20.169) using Taiwan FSC XBRL financial data.

## Quick Start

    bash ~/start.sh
    Web interface: http://192.168.20.169:3000

## Repository Structure

    AITC/
    scripts/          Pipeline scripts - download, import, update
    mcp-server/       MCP server for Claude Code plugin
    plugins/          Claude Code plugin for analysts
    src/              Core source code
    docs/             Documentation and benchmarks
    taxonomy/         Taiwan FSC XBRL taxonomy (gitignored)
    xbrl_downloads/   Downloaded XBRL files (gitignored)

## Data Coverage

    26 financial institutions
    503 quarterly filings (2019 Q1 to 2026 Q1)
    1,074,588 financial metric rows
    Auto-updated daily at 6:00 AM from MOPS

## Services

    vLLM           port 8000   Qwen2.5-14B LLM inference
    FastAPI        port 3001   Agent backend
    Next.js        port 3000   Web interface
    MCP Server     port 8091   Claude Code plugin bridge
    Ollama         port 11434  Embeddings qwen3.6:27b

## Data Source

    Taiwan FSC MOPS: https://mopsov.twse.com.tw/mops/web/t203sb01

---
Internal use only - AITC Development Team
