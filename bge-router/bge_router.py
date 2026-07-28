"""
AITC BGE-M3 Semantic Router Service
=====================================
Port 3002 — GPU accelerated — <100ms routing decision

Architecture:
  BGE-M3 (568M, BAAI) — state-of-the-art multilingual embedding model
  Native Traditional Chinese support
  Contrastive training for semantic similarity — perfect for routing
  
  4 routing labels covering all analyst question types:
    analysis    → Claude Sonnet 4.6 (credit reports)
    lookup      → Qwen 2.5-14B (data retrieval)
    ratio_calc  → Qwen 2.5-3B (ratio calculation + FSC judgment)
    screening   → MCP Screen (all 26 institutions)

  Mengzi-BERT-fin removed as direct answer agent — Qwen3B covers
  all ratio and health classification questions with better quality.
"""
import time
import torch
import torch.nn.functional as F
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModel
import uvicorn

app = FastAPI(title="AITC BGE-M3 Semantic Router")

BGE_M3_PATH = "/home/user/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"

print("[router] Loading BGE-M3...")
t0 = time.time()

tokenizer = AutoTokenizer.from_pretrained(BGE_M3_PATH)
model = AutoModel.from_pretrained(BGE_M3_PATH)

# Use GPU if available — DGX Spark has unified memory
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = model.to(device)
model.eval()

print(f"[router] BGE-M3 loaded on {device} in {time.time()-t0:.1f}s")

# ── Routing label prototypes (Traditional Chinese) ─────────────────────
# Each label has diverse examples covering real analyst question patterns
# BGE-M3 encodes these into high-quality semantic embeddings
LABEL_PROTOTYPES = {
    "analysis": [
        "國泰金控2024Q3信用調查報告",
        "富邦金融控股信用調查報告",
        "兆豐金控完整信用分析報告",
        "彰化銀行授信調查報告",
        "三商美邦人壽信用報告",
        "請出具金融機構信用調查報告",
        "幫我產生授信報告",
        "製作完整財務信用分析",
        "金融控股公司授信評估報告",
        "信用徵信報告分析",
        "玉山金控財務信用分析報告",
        "中信金控完整信用調查",
    ],
    "lookup": [
        "國泰金控2024Q3總資產是多少",
        "富邦金負債總計金額",
        "兆豐金控股東權益數值查詢",
        "彰化銀行本期淨利是多少",
        "每股盈餘數字是多少",
        "現金及約當現金金額",
        "查詢特定財務數據",
        "列出近三年資產規模",
        "營業活動現金流數值",
        "稅前淨利金額查詢",
        "顯示財務報表數字",
        "告訴我總負債金額",
    ],
    "ratio_calc": [
        "國泰金控2024Q3負債比率是否在正常範圍",
        "計算ROA和ROE財務比率",
        "負債比率FSC判定結果",
        "資產報酬率是否達標",
        "每股盈餘年化估算計算",
        "負債比率92.95%算正常嗎",
        "這個負債比率偏高嗎",
        "ROE14%對金控業算好嗎",
        "EPS6.78元算偏低嗎",
        "財務比率計算及FSC合規判定",
        "流動比率1.2倍算健康嗎",
        "ROA1%是否低於標準",
        "負債比率97%是高風險嗎",
        "這個財務指標正常嗎",
        "93.5%的負債比率偏高嗎",
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
        "全台26家FSC機構財務篩選",
        "所有銀行負債比率由高到低排名",
        "找出高風險金融機構",
        "哪些保險公司超過FSC門檻",
    ],
}

LABEL_TO_AGENT = {
    "analysis":   "claude",
    "lookup":     "qwen14b",
    "ratio_calc": "qwen3b",
    "screening":  "screen",
}

def _encode(texts: list) -> torch.Tensor:
    """Encode texts using BGE-M3 — production quality multilingual embeddings."""
    encoded = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt"
    ).to(device)
    with torch.no_grad():
        output = model(**encoded)
    # BGE-M3 uses CLS token embedding (first token)
    embeddings = output.last_hidden_state[:, 0, :]
    return F.normalize(embeddings, p=2, dim=1)

# Pre-compute label prototype embeddings at startup
print("[router] Computing BGE-M3 prototype embeddings...")
t1 = time.time()
LABEL_EMBEDDINGS = {}
for label, sentences in LABEL_PROTOTYPES.items():
    embs = _encode(sentences)
    LABEL_EMBEDDINGS[label] = F.normalize(embs.mean(dim=0, keepdim=True), p=2, dim=1).squeeze(0)
print(f"[router] Prototype embeddings ready in {time.time()-t1:.2f}s")
print(f"[router] BGE-M3 router active — 4 agents: {list(LABEL_TO_AGENT.values())}")

class ClassifyRequest(BaseModel):
    text: str
    metric: str = ""
    value: float = None
    is_fhc: bool = False

class ClassifyResponse(BaseModel):
    intent: str
    agent: str
    confidence: float
    complexity: float
    all_scores: dict
    response_time_ms: float

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "model": "BAAI/bge-m3",
        "device": str(device),
        "router": "BGE-M3 semantic similarity",
        "agents": list(LABEL_TO_AGENT.values())
    }

@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest):
    t0 = time.time()

    # Encode query with BGE-M3
    query_emb = _encode([req.text[:512]]).squeeze(0)

    # Cosine similarity to each label prototype
    scores = {}
    for label, proto_emb in LABEL_EMBEDDINGS.items():
        scores[label] = float(F.cosine_similarity(
            query_emb.unsqueeze(0),
            proto_emb.unsqueeze(0)
        ))

    # Best label
    best_label = max(scores, key=scores.get)
    best_score = scores[best_label]
    agent = LABEL_TO_AGENT[best_label]

    # Complexity score — margin between top two scores
    sorted_scores = sorted(scores.values(), reverse=True)
    complexity = round(1.0 - (sorted_scores[0] - sorted_scores[1]), 3)
    complexity = max(0.0, min(1.0, complexity))

    elapsed = (time.time() - t0) * 1000
    print(f"[router] {req.text[:60]}")
    print(f"[router] → {best_label} ({agent}) conf={best_score:.3f} complexity={complexity:.3f} {elapsed:.1f}ms")
    print(f"[router] scores: {', '.join(f'{k}={v:.3f}' for k,v in sorted(scores.items(), key=lambda x:-x[1]))}")

    return ClassifyResponse(
        intent=best_label,
        agent=agent,
        confidence=best_score,
        complexity=complexity,
        all_scores={k: round(v, 4) for k, v in scores.items()},
        response_time_ms=elapsed,
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3002)
