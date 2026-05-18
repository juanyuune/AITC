import urllib.request
import urllib.parse
import json
import sys
import os
from datetime import datetime
from pathlib import Path

# Fix Windows encoding issue
sys.stdout.reconfigure(encoding='utf-8')

questions = [
    "請給我台泥 2024年Q1 的現金及約當現金",
    "請給我聯電 2024年Q3 的應收帳款淨額",
    "請給我東元電機 2024年Q2 的負債總額與資產總額",
    "台泥 2024年的現金水位是否充足？",
    "東元電機 2024年的獲利能力與負債結構是否有風險？",
    "士林電機 2024年各季營收趨勢如何？是否持續成長？",
]

model = os.getenv("OLLAMA_MODEL") or os.getenv("OPENAI_MODEL_NAME", "unknown")
provider = "Ollama (On-Premise)" if os.getenv("OLLAMA_MODEL") else "OpenAI API"
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

output_file = Path(f"benchmark_results_{model.replace(':', '_').replace('/', '_')}_{timestamp}.txt")

print(f"Benchmarking model : {model}")
print(f"Provider           : {provider}")
print(f"Results saved to   : {output_file}")
print("=" * 60)

results = []
log_lines = []

def log(text=""):
    print(text)
    log_lines.append(text)

log(f"BENCHMARK RESULTS")
log(f"Model    : {model}")
log(f"Provider : {provider}")
log(f"Date     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log("=" * 60)

for i, q in enumerate(questions, 1):
    encoded = urllib.parse.quote(q)
    url = f"http://localhost:3001/chatbot/{encoded}?bypass_cache=true"
    log(f"Q{i}: {q}")
    print(f"     Waiting for response...")
    try:
        with urllib.request.urlopen(url, timeout=600) as r:
            data = json.loads(r.read())
            t = data["response_time_seconds"]
            cached = data["cached"]
            answer = data["answer"]
            log(f"     Time   : {t}s")
            log(f"     Cached : {cached}")
            log(f"     Answer :")
            log("-" * 60)
            for line in answer.splitlines():
                log(f"     {line}")
            log("-" * 60)
            results.append((i, q, t, answer))
    except Exception as e:
        log(f"     ERROR: {e}")
        results.append((i, q, "ERROR", str(e)))
    log()

log("=" * 60)
log("SUMMARY - Response Times")
log("=" * 60)
log(f"{'No.':<5} {'Time':<12} Question")
log("-" * 60)
for no, q, t, _ in results:
    log(f"Q{no:<4} {str(t)+'s':<12} {q[:50]}")
log()

log("=" * 60)
log("SUMMARY - Answer Preview")
log("=" * 60)
for no, q, t, answer in results:
    log(f"Q{no} [{t}s]: {q}")
    preview_lines = [l for l in answer.splitlines() if l.strip()][:2]
    for line in preview_lines:
        log(f"    {line[:100]}")
    log()

output_file.write_text("\n".join(log_lines), encoding="utf-8")
print(f"\nFull results saved to: {output_file}")