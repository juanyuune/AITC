---
description: 生成完整信用調查報告
argument-hint: "[公司代碼或名稱] [年份]"
---

# /credit-report — 完整信用調查報告

生成指定公司的完整信用調查報告，整合財務數據、比率分析、趨勢與風險評估。

## 流程

1. 確認公司代碼與最新可用期間
2. skill: "xbrl-query" — 取得近 4 季財務數字
3. skill: "ratio-analysis" — 計算關鍵財務比率
4. skill: "trend-analysis" — 分析多季趨勢
5. skill: "credit-memo" — 整合為完整報告，附複核聲明

## 範例

**輸入：** `/credit-report 台泥 2024`

**輸出：** 完整信用調查報告（基本資訊、財務概況、比率分析、趨勢、風險評估、複核聲明）
