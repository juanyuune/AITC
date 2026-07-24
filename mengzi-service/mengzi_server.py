"""
Mengzi-BERT-base-fin Classifier Service
Port 3002 — CPU only — <100ms response
"""
import time
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
import uvicorn

app = FastAPI(title="Mengzi Financial Classifier")

print("[mengzi] Loading Mengzi-BERT-base-fin...")
t0 = time.time()

classifier = pipeline(
    "text-classification",
    model="Langboat/mengzi-bert-base-fin",
    device=-1,  # CPU only
    top_k=None
)

print(f"[mengzi] ✅ Model loaded in {time.time()-t0:.1f}s")

# ── Financial health thresholds ───────────────────────────────
METRIC_THRESHOLDS = {
    "負債比率": {"high": 90, "low": 30, "unit": "%"},
    "流動比率": {"high": 300, "low": 100, "unit": "%"},
    "速動比率": {"high": 200, "low": 80, "unit": "%"},
    "ROA":     {"high": 2.0, "low": 0.3, "unit": "%"},
    "ROE":     {"high": 15.0, "low": 3.0, "unit": "%"},
}

# ── FHC non-applicable metrics ────────────────────────────────
FHC_NOT_APPLICABLE = [
    "存貨", "存貨周轉", "應收帳款周轉", "營業週期",
    "原料", "製造", "產品", "庫存"
]

class ClassifyRequest(BaseModel):
    text: str
    metric: str = ""
    value: float = None
    is_fhc: bool = False

class ClassifyResponse(BaseModel):
    intent: str          # lookup / reasoning / analysis
    health: str          # 偏高 / 偏低 / 正常 / N/A
    fhc_applicable: bool
    confidence: float
    response_time_ms: float

@app.get("/health")
def health():
    return {"status": "ok", "model": "Langboat/mengzi-bert-base-fin"}

@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest):
    t0 = time.time()

    # ── 1. FHC applicability check ────────────────────────────
    fhc_applicable = True
    if req.is_fhc:
        fhc_applicable = not any(kw in req.text for kw in FHC_NOT_APPLICABLE)

    # ── 2. Health assessment ──────────────────────────────────
    health = "N/A"
    if req.metric and req.value is not None and req.metric in METRIC_THRESHOLDS:
        thresholds = METRIC_THRESHOLDS[req.metric]
        if req.value > thresholds["high"]:
            health = "偏高"
        elif req.value < thresholds["low"]:
            health = "偏低"
        else:
            health = "正常"

    # ── 3. Intent classification via Mengzi ──────────────────
    result = classifier(req.text[:512])
    top = sorted(result[0], key=lambda x: x["score"], reverse=True)[0]
    confidence = top["score"]

    # Map to routing intent
    ANALYSIS_KEYWORDS = ["分析", "報告", "評估", "風險", "趨勢", "整體", "信用"]
    LOOKUP_KEYWORDS = ["多少", "是什麼", "查詢", "數值", "金額"]

    if any(kw in req.text for kw in ANALYSIS_KEYWORDS):
        intent = "analysis"
    elif any(kw in req.text for kw in LOOKUP_KEYWORDS):
        intent = "lookup"
    else:
        intent = "reasoning"

    elapsed = (time.time() - t0) * 1000

    print(f"[mengzi] text={req.text[:40]} intent={intent} health={health} fhc={fhc_applicable} {elapsed:.1f}ms")

    return ClassifyResponse(
        intent=intent,
        health=health,
        fhc_applicable=fhc_applicable,
        confidence=confidence,
        response_time_ms=elapsed
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3002)
