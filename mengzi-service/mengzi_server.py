"""
Mengzi-BERT-base-fin Classifier Service — Production Router
Port 3002 — CPU only — <100ms response

Architecture:
  Intent routing: Mengzi embeddings + cosine similarity to label prototypes
  Health assessment: FSC threshold rules (deterministic)
  FHC applicability: domain rule check

This is genuine AI-based routing using Mengzi's financial domain
understanding, not keyword matching. The model encodes the question
into a 768-dim financial domain embedding, then finds the closest
routing label via cosine similarity.

Phase 2: replace prototype embeddings with fine-tuned classification
head trained on AITC routing examples (300-500 samples).
"""
import time
import torch
import torch.nn.functional as F
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModel
import uvicorn

app = FastAPI(title="Mengzi Financial Router")

print("[mengzi] Loading Mengzi-BERT-base-fin...")
t0 = time.time()

MODEL_NAME = "Langboat/mengzi-bert-base-fin"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModel.from_pretrained(MODEL_NAME)
model.eval()

print(f"[mengzi] Model loaded in {time.time()-t0:.1f}s")

# ── Label prototype sentences ──────────────────────────────────────────
# Each label is represented by multiple prototype sentences in Traditional Chinese
# Mengzi encodes these into embeddings; routing is cosine similarity to mean prototype
# Phase 2: replace with fine-tuned classification head

LABEL_PROTOTYPES = {
    "analysis": [
        "請出具國泰金控2024Q3信用調查報告",
        "富邦金融控股財務信用分析報告",
        "幫我產生兆豐金控的完整授信報告",
        "彰化銀行信用評估完整分析",
        "製作三商美邦人壽的信用調查報告",
        "國泰金控財務風險評估報告",
    ],
    "lookup": [
        "國泰金控2024Q3總資產是多少",
        "富邦金2024Q3負債總計金額",
        "兆豐金控股東權益數值查詢",
        "彰化銀行本期淨利是多少",
        "每股盈餘數字是多少",
        "現金及約當現金金額查詢",
    ],
    "ratio_calc": [
        "負債比率是否在正常範圍",
        "計算國泰金控ROA和ROE",
        "富邦金負債比率FSC判定結果",
        "兆豐金控資產報酬率是否達標",
        "每股盈餘年化估算計算",
        "負債比率92.95%是否偏高",
    ],
    "reasoning": [
        "為什麼國泰金控負債比率下降",
        "比較富邦金與國泰金的財務結構",
        "分析兆豐金控三期趨勢",
        "哪家金控財務結構更穩健",
        "國泰金控ROE下降的原因分析",
        "影響負債比率變動的主要因素",
    ],
    "classification": [
        "這家銀行財務健康嗎",
        "負債比率91%算正常嗎",
        "EPS 1.98元是否偏低",
        "流動比率1.2倍風險高嗎",
        "財務狀況是否良好",
        "這個比率算偏高還是偏低",
    ],
    "screening": [
        "哪幾家金控負債比率超過FSC警示線",
        "台灣所有銀行2024Q3負債比率排名",
        "ROA最高的金融機構是哪家",
        "哪些機構財務風險較高",
        "全體金融機構負債比率比較",
        "哪家金控財務最穩健",
        "篩選出警示或高風險機構",
        "比較所有金控的獲利能力",
    ],
}

# Route label to agent mapping
LABEL_TO_AGENT = {
    "analysis":       "claude",
    "lookup":         "qwen",
    "ratio_calc":     "finr1",
    "reasoning":      "finr1",
    "classification": "mengzi",
    "screening":      "screen",
}

def _mean_pool(model_output, attention_mask):
    """Mean pooling over token embeddings — standard sentence embedding."""
    token_embeddings = model_output.last_hidden_state
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)

def _encode(texts: list) -> torch.Tensor:
    """Encode list of texts into normalized embeddings using Mengzi."""
    encoded = tokenizer(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
    with torch.no_grad():
        output = model(**encoded)
    embeddings = _mean_pool(output, encoded["attention_mask"])
    return F.normalize(embeddings, p=2, dim=1)

# Pre-compute label prototype embeddings at startup
print("[mengzi] Computing label prototype embeddings...")
t1 = time.time()
LABEL_EMBEDDINGS = {}
for label, sentences in LABEL_PROTOTYPES.items():
    embs = _encode(sentences)
    LABEL_EMBEDDINGS[label] = embs.mean(dim=0)  # mean prototype
    LABEL_EMBEDDINGS[label] = F.normalize(LABEL_EMBEDDINGS[label].unsqueeze(0), p=2, dim=1).squeeze(0)
print(f"[mengzi] Prototype embeddings ready in {time.time()-t1:.2f}s")

# ── FSC Financial health thresholds ───────────────────────────────────
METRIC_THRESHOLDS = {
    "負債比率": {"high": 90, "low": 30, "unit": "%"},
    "流動比率": {"high": 300, "low": 100, "unit": "%"},
    "速動比率": {"high": 200, "low": 80, "unit": "%"},
    "ROA":     {"high": 2.0, "low": 0.3, "unit": "%"},
    "ROE":     {"high": 15.0, "low": 3.0, "unit": "%"},
}

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
    intent: str
    agent: str
    health: str
    fhc_applicable: bool
    confidence: float
    complexity: float          # 0.0=simple/clear, 1.0=complex/ambiguous
    all_scores: dict           # cosine similarity to all label prototypes
    response_time_ms: float

@app.get("/health")
def health_check():
    return {"status": "ok", "model": MODEL_NAME, "router": "embedding-similarity"}

@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest):
    t0 = time.time()

    # ── 1. FHC applicability check ─────────────────────────────
    fhc_applicable = True
    if req.is_fhc:
        fhc_applicable = not any(kw in req.text for kw in FHC_NOT_APPLICABLE)

    # ── 2. Health assessment (FSC rules — deterministic) ────────
    health = "N/A"
    if req.metric and req.value is not None and req.metric in METRIC_THRESHOLDS:
        thresholds = METRIC_THRESHOLDS[req.metric]
        if req.value > thresholds["high"]:
            health = "偏高"
        elif req.value < thresholds["low"]:
            health = "偏低"
        else:
            health = "正常"

    # ── 3. AI intent routing via Mengzi embeddings ──────────────
    # Encode the question using Mengzi financial domain encoder
    query_emb = _encode([req.text[:512]]).squeeze(0)

    # Compute cosine similarity to each label prototype
    scores = {}
    for label, proto_emb in LABEL_EMBEDDINGS.items():
        scores[label] = float(F.cosine_similarity(query_emb.unsqueeze(0), proto_emb.unsqueeze(0)))

    # Select highest scoring label
    best_label = max(scores, key=scores.get)
    best_score = scores[best_label]
    agent = LABEL_TO_AGENT[best_label]

    # Complexity score — measures question ambiguity
    # Low complexity = strong signal to one label (simple, clear question)
    # High complexity = spread across labels (ambiguous, multi-faceted question)
    sorted_scores = sorted(scores.values(), reverse=True)
    top_score = sorted_scores[0]
    second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
    complexity = round(1.0 - (top_score - second_score), 3)
    complexity = max(0.0, min(1.0, complexity))  # clamp to [0,1]

    elapsed = (time.time() - t0) * 1000
    print(f"[mengzi] {req.text[:50]}")
    print(f"[mengzi] intent={best_label} agent={agent} conf={best_score:.3f} complexity={complexity:.3f} {elapsed:.1f}ms")
    print(f"[mengzi] scores: {', '.join(f'{k}={v:.3f}' for k,v in sorted(scores.items(), key=lambda x: -x[1]))}")

    return ClassifyResponse(
        intent=best_label,
        agent=agent,
        health=health,
        fhc_applicable=fhc_applicable,
        confidence=best_score,
        complexity=complexity,
        all_scores={k: round(v, 4) for k, v in scores.items()},
        response_time_ms=elapsed,
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3002)
