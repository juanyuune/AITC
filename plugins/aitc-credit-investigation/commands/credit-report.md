---
description: 生成台灣上市公司完整信用調查報告
argument-hint: "[公司代碼或名稱] [年份]"
---

# /credit-report — 完整信用調查報告

生成指定公司的完整信用調查報告，整合四季財務數據、比率、趨勢與風險評估。

## 流程

1. 確認公司代碼與最新可用期間（`list_periods`）
2. skill: "xbrl-query" — 取得近四季財務數字（分四次呼叫）
3. skill: "ratio-analysis" — 計算關鍵財務比率
4. skill: "trend-analysis" — 分析多季趨勢，正確處理 Q4
5. skill: "credit-memo" — 整合為六章節完整報告，附複核聲明

## 範例

**輸入：** `credit-report 台泥 2024`

**輸出：** 完整六章節信用調查報告（基本資料、財務概況、比率、趨勢、風險評估、聲明）

## 預期執行時間

約 3–5 分鐘（需取得四季資料並整合分析）
