#!/usr/bin/env python3
"""AITC Benchmark Runner v3 — natural analyst questions, years 2021-2025, numeric ground truth validation"""
import json, sqlite3, urllib.request, datetime, sys, time, re

BASE_URL  = "http://localhost:3001"
import os as _os
NO_CACHE  = _os.environ.get("BENCH_NO_CACHE") == "1"   # BENCH_NO_CACHE=1: backend skips its response cache
ASK_TIMEOUT = 480 if NO_CACHE else 120                 # a cold analysis answer can take several minutes
DB_PATH   = "/home/user/AITC/logs/observability.db"
LOG_PATH  = "/home/user/AITC/logs/benchmark.log"
BASELINE  = 0.70

QUESTIONS = [
  {
    "id": "L01",
    "cat": "lookup",
    "q": "國泰金控 2025年第三季的資產總規模有多大？",
    "company": "國泰金控",
    "code": "2882",
    "period": "2025Q3",
    "field": "總資產",
    "ground_truth_numeric": 14242656104.0,
    "ground_truth_display": "14,242,656,104 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L02",
    "cat": "lookup",
    "q": "富邦金控 2025年第三季帳上的資產總額為何？",
    "company": "富邦金控",
    "code": "2881",
    "period": "2025Q3",
    "field": "總資產",
    "ground_truth_numeric": 12402055230.0,
    "ground_truth_display": "12,402,055,230 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L03",
    "cat": "lookup",
    "q": "兆豐金控 2024年第三季的負債合計是多少？",
    "company": "兆豐金控",
    "code": "2886",
    "period": "2024Q3",
    "field": "總負債",
    "ground_truth_numeric": 4251145439.0,
    "ground_truth_display": "4,251,145,439 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L04",
    "cat": "lookup",
    "q": "中信金控 2024年第三季股東權益的金額為何？",
    "company": "中信金控",
    "code": "2891",
    "period": "2024Q3",
    "field": "股東權益",
    "ground_truth_numeric": 510469390.0,
    "ground_truth_display": "510,469,390 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L05",
    "cat": "lookup",
    "q": "玉山金控 2024年第三季帳上現金部位有多少？",
    "company": "玉山金控",
    "code": "2884",
    "period": "2024Q3",
    "field": "現金及約當現金",
    "ground_truth_numeric": 64854359.0,
    "ground_truth_display": "64,854,359 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L06",
    "cat": "lookup",
    "q": "元大金控 2024年第二季的負債總額是多少？",
    "company": "元大金控",
    "code": "2885",
    "period": "2024Q2",
    "field": "總負債",
    "ground_truth_numeric": 3273971411.0,
    "ground_truth_display": "3,273,971,411 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L07",
    "cat": "lookup",
    "q": "台新金控 2024年第三季的現金及約當現金為何？",
    "company": "台新金控",
    "code": "2887",
    "period": "2024Q3",
    "field": "現金及約當現金",
    "ground_truth_numeric": 29537379.0,
    "ground_truth_display": "29,537,379 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L08",
    "cat": "lookup",
    "q": "第一金控 2024年第三季的資產規模為何？",
    "company": "第一金控",
    "code": "2892",
    "period": "2024Q3",
    "field": "總資產",
    "ground_truth_numeric": 4647219029.0,
    "ground_truth_display": "4,647,219,029 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L09",
    "cat": "lookup",
    "q": "國泰金控 2023年第三季的資產總額是多少？",
    "company": "國泰金控",
    "code": "2882",
    "period": "2023Q3",
    "field": "總資產",
    "ground_truth_numeric": 12897826562.0,
    "ground_truth_display": "12,897,826,562 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L10",
    "cat": "lookup",
    "q": "華南金控 2023年第三季的負債合計為何？",
    "company": "華南金控",
    "code": "2880",
    "period": "2023Q3",
    "field": "總負債",
    "ground_truth_numeric": 3599505758.0,
    "ground_truth_display": "3,599,505,758 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L11",
    "cat": "lookup",
    "q": "富邦金控 2023年第一季的負債總額是多少？",
    "company": "富邦金控",
    "code": "2881",
    "period": "2023Q1",
    "field": "總負債",
    "ground_truth_numeric": 9907429668.0,
    "ground_truth_display": "9,907,429,668 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L12",
    "cat": "lookup",
    "q": "玉山金控 2023年第三季的資產規模有多大？",
    "company": "玉山金控",
    "code": "2884",
    "period": "2023Q3",
    "field": "總資產",
    "ground_truth_numeric": 3590762229.0,
    "ground_truth_display": "3,590,762,229 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L13",
    "cat": "lookup",
    "q": "元大金控 2023年第三季的負債合計為何？",
    "company": "元大金控",
    "code": "2885",
    "period": "2023Q3",
    "field": "總負債",
    "ground_truth_numeric": 2889416818.0,
    "ground_truth_display": "2,889,416,818 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L14",
    "cat": "lookup",
    "q": "國泰金控 2022年第三季的資產總規模是多少？",
    "company": "國泰金控",
    "code": "2882",
    "period": "2022Q3",
    "field": "總資產",
    "ground_truth_numeric": 11889627463.0,
    "ground_truth_display": "11,889,627,463 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L15",
    "cat": "lookup",
    "q": "富邦金控 2022年第四季的股東權益金額為何？",
    "company": "富邦金控",
    "code": "2881",
    "period": "2022Q4",
    "field": "股東權益",
    "ground_truth_numeric": 573609207.0,
    "ground_truth_display": "573,609,207 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L16",
    "cat": "lookup",
    "q": "元大金控 2022年第三季的負債總額是多少？",
    "company": "元大金控",
    "code": "2885",
    "period": "2022Q3",
    "field": "總負債",
    "ground_truth_numeric": 2710362778.0,
    "ground_truth_display": "2,710,362,778 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L17",
    "cat": "lookup",
    "q": "華南金控 2022年第一季的資產總額為何？",
    "company": "華南金控",
    "code": "2880",
    "period": "2022Q1",
    "field": "總資產",
    "ground_truth_numeric": 3549844724.0,
    "ground_truth_display": "3,549,844,724 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L18",
    "cat": "lookup",
    "q": "國泰金控 2021年第三季的資產規模有多大？",
    "company": "國泰金控",
    "code": "2882",
    "period": "2021Q3",
    "field": "總資產",
    "ground_truth_numeric": 11383850222.0,
    "ground_truth_display": "11,383,850,222 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L19",
    "cat": "lookup",
    "q": "富邦金控 2021年第四季的現金部位是多少？",
    "company": "富邦金控",
    "code": "2881",
    "period": "2021Q4",
    "field": "現金及約當現金",
    "ground_truth_numeric": 284688160.0,
    "ground_truth_display": "284,688,160 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "L20",
    "cat": "lookup",
    "q": "玉山金控 2021年第三季的股東權益為何？",
    "company": "玉山金控",
    "code": "2884",
    "period": "2021Q3",
    "field": "股東權益",
    "ground_truth_numeric": 189660849.0,
    "ground_truth_display": "189,660,849 千元",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R01",
    "cat": "ratio",
    "q": "富邦金控 2025年第三季的負債比率是多少，是否在正常範圍？",
    "company": "富邦金控",
    "code": "2881",
    "period": "2025Q3",
    "field": "負債比率",
    "ground_truth_numeric": 92.28,
    "ground_truth_display": "92.28%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R02",
    "cat": "ratio",
    "q": "國泰金控 2025年第三季的ROA表現如何？",
    "company": "國泰金控",
    "code": "2882",
    "period": "2025Q3",
    "field": "ROA",
    "ground_truth_numeric": 0.7,
    "ground_truth_display": "0.7%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R03",
    "cat": "ratio",
    "q": "兆豐金控 2024年第二季的ROE是多少？",
    "company": "兆豐金控",
    "code": "2886",
    "period": "2024Q2",
    "field": "ROE",
    "ground_truth_numeric": 11.66,
    "ground_truth_display": "11.66%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R04",
    "cat": "ratio",
    "q": "元大金控 2024年第三季的負債比率是否偏高？",
    "company": "元大金控",
    "code": "2885",
    "period": "2024Q3",
    "field": "負債比率",
    "ground_truth_numeric": 91.13,
    "ground_truth_display": "91.13%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R05",
    "cat": "ratio",
    "q": "玉山金控 2024年第三季的ROA水準如何？",
    "company": "玉山金控",
    "code": "2884",
    "period": "2024Q3",
    "field": "ROA",
    "ground_truth_numeric": 0.71,
    "ground_truth_display": "0.71%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R06",
    "cat": "ratio",
    "q": "台新金控 2024年第三季的負債比率符合FSC標準嗎？",
    "company": "台新金控",
    "code": "2887",
    "period": "2024Q3",
    "field": "負債比率",
    "ground_truth_numeric": 93.16,
    "ground_truth_display": "93.16%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R07",
    "cat": "ratio",
    "q": "第一金控 2024年第三季的資產報酬率是多少？",
    "company": "第一金控",
    "code": "2892",
    "period": "2024Q3",
    "field": "ROA",
    "ground_truth_numeric": 0.6,
    "ground_truth_display": "0.6%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R08",
    "cat": "ratio",
    "q": "華南金控 2024年第三季的負債比率是否超過警示線？",
    "company": "華南金控",
    "code": "2880",
    "period": "2024Q3",
    "field": "負債比率",
    "ground_truth_numeric": 94.79,
    "ground_truth_display": "94.79%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R09",
    "cat": "ratio",
    "q": "新光金控 2023年第三季的ROA表現如何？",
    "company": "新光金控",
    "code": "2888",
    "period": "2023Q3",
    "field": "ROA",
    "ground_truth_numeric": -0.02,
    "ground_truth_display": "-0.02%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R10",
    "cat": "ratio",
    "q": "中信金控 2023年第三季的ROE是多少？",
    "company": "中信金控",
    "code": "2891",
    "period": "2023Q3",
    "field": "ROE",
    "ground_truth_numeric": 15.21,
    "ground_truth_display": "15.21%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R11",
    "cat": "ratio",
    "q": "國泰金控 2023年第三季的負債比率為何？",
    "company": "國泰金控",
    "code": "2882",
    "period": "2023Q3",
    "field": "負債比率",
    "ground_truth_numeric": 94.69,
    "ground_truth_display": "94.69%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R12",
    "cat": "ratio",
    "q": "富邦金控 2022年第三季的ROE水準如何？",
    "company": "富邦金控",
    "code": "2881",
    "period": "2022Q3",
    "field": "ROE",
    "ground_truth_numeric": 19.78,
    "ground_truth_display": "19.78%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R13",
    "cat": "ratio",
    "q": "元大金控 2022年第三季的ROA是多少？",
    "company": "元大金控",
    "code": "2885",
    "period": "2022Q3",
    "field": "ROA",
    "ground_truth_numeric": 0.92,
    "ground_truth_display": "0.92%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R14",
    "cat": "ratio",
    "q": "國泰金控 2021年第三季的負債比率是多少？",
    "company": "國泰金控",
    "code": "2882",
    "period": "2021Q3",
    "field": "負債比率",
    "ground_truth_numeric": 92.36,
    "ground_truth_display": "92.36%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "R15",
    "cat": "ratio",
    "q": "富邦金控 2021年第三季的ROE是多少？",
    "company": "富邦金控",
    "code": "2881",
    "period": "2021Q3",
    "field": "ROE",
    "ground_truth_numeric": 20.18,
    "ground_truth_display": "20.18%",
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ]
  },
  {
    "id": "C01",
    "cat": "compare",
    "q": "富邦金控和國泰金控 2024年第三季的財務結構有何差異？",
    "must_contain": [
      "富邦",
      "國泰"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Both in response"
  },
  {
    "id": "C02",
    "cat": "compare",
    "q": "兆豐金控和中信金控的財務體質比較如何？",
    "must_contain": [
      "兆豐",
      "中國信託"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Both in response"
  },
  {
    "id": "C03",
    "cat": "compare",
    "q": "玉山金控和台新金控的獲利能力誰比較強？",
    "must_contain": [
      "玉山",
      "臺新"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Both in response"
  },
  {
    "id": "C04",
    "cat": "compare",
    "q": "元大金控和永豐金控的資產規模與風險比較",
    "must_contain": [
      "元大",
      "永豐"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Both in response"
  },
  {
    "id": "C05",
    "cat": "compare",
    "q": "第一金控和華南金控的財務狀況比較",
    "must_contain": [
      "第一",
      "華南"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Both in response"
  },
  {
    "id": "S01",
    "cat": "screen",
    "q": "目前哪幾家金控的負債比率超過FSC警示標準？",
    "must_contain": [
      "警示"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Warning institutions"
  },
  {
    "id": "S02",
    "cat": "screen",
    "q": "所有金控的負債比率排名，哪家最高？",
    "must_contain": [
      "負債"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Ranked list"
  },
  {
    "id": "S03",
    "cat": "screen",
    "q": "26家FSC機構中，哪家的ROA表現最好？",
    "must_contain": [
      "ROA"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Top ROA"
  },
  {
    "id": "S04",
    "cat": "screen",
    "q": "各金控最新一季的每股盈餘比較，哪家最高？",
    "must_contain": [
      "每股"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "EPS comparison"
  },
  {
    "id": "S05",
    "cat": "screen",
    "q": "哪幾家金控的負債比率已超過95%的高風險門檻？",
    "must_contain": [
      "負債"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "High risk"
  },
  {
    "id": "A01",
    "cat": "analysis",
    "q": "富邦金控整體財務體質如何？適合授信往來嗎？",
    "must_contain": [
      "富邦"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Analysis — human review"
  },
  {
    "id": "A02",
    "cat": "analysis",
    "q": "國泰金控的財務結構偏保守還是偏積極，跟同業相比如何？",
    "must_contain": [
      "國泰"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Analysis — human review"
  },
  {
    "id": "A03",
    "cat": "analysis",
    "q": "中信金控目前有哪些主要的財務風險需要注意？",
    "must_contain": [
      "中國信託"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Analysis — human review"
  },
  {
    "id": "A04",
    "cat": "analysis",
    "q": "從財報數字判斷，元大金控是否有財務壓力的跡象？",
    "must_contain": [
      "元大"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Analysis — human review"
  },
  {
    "id": "A05",
    "cat": "analysis",
    "q": "新光金控近期的財務表現是否穩定，授信風險評估為何？",
    "must_contain": [
      "新光"
    ],
    "must_not_contain": [
      "系統錯誤",
      "逾時",
      "ERROR"
    ],
    "ground_truth_numeric": None,
    "ground_truth_display": "Analysis — human review"
  }
]

def ask(q, timeout=None):
    timeout = timeout or ASK_TIMEOUT
    try:
        body = {"question": q, "mode": "auto"}
        if NO_CACHE:
            body["no_cache"] = True
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            BASE_URL + "/chatbot", data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"answer": "ERROR:" + str(e), "model": "error", "response_time_seconds": 0}

def check_numeric(answer, ground_truth, cat):
    if ground_truth is None: return None, "no_ground_truth"
    answer_clean = answer.replace(",", "")
    # Try multiple formats the system might output
    formats = []
    if ground_truth >= 1000:
        formats += [f"{ground_truth:,.0f}".replace(",",""), f"{ground_truth:.0f}"]
        yi = ground_truth / 100000
        formats += [f"{yi:,.0f}".replace(",",""), f"{yi:.1f}".replace(",","")]
    else:
        formats += [str(ground_truth), f"{ground_truth:.2f}", f"{ground_truth:.4f}"]
    for fmt in formats:
        if fmt.replace(",","") in answer_clean:
            return True, f"match:{fmt}"
    # Numeric tolerance fallback
    nums = re.findall(r"\d+\.?\d*", answer_clean)
    tolerance = 1.0 if cat == "lookup" else 0.05
    for n in nums:
        try:
            if abs(float(n) - ground_truth) <= tolerance:
                return True, f"numeric_close:{n}"
        except: pass
    return False, f"not_found:expected_{ground_truth}"

# Company name aliases — maps any short name to all valid forms in system output
# Root fix: system returns full legal names; benchmark checks short names
# This single mapping handles all companies across all question types (C, A, S)
COMPANY_ALIASES = {
    "富邦":  ["富邦","富邦金融控股","富邦金控"],
    "國泰":  ["國泰","國泰金融控股","國泰金控"],
    "兆豐":  ["兆豐","兆豐金融控股","兆豐金控"],
    "中信":  ["中信","中國信託","中國信託金融控股"],
    "玉山":  ["玉山","玉山金融控股","玉山金控"],
    "元大":  ["元大","元大金融控股","元大金控"],
    "台新":  ["台新","臺新","台新金融控股","臺新金融控股"],
    "第一":  ["第一","第一金控","第一金融控股"],
    "第一金": ["第一","第一金控","第一金融控股"],
    "華南":  ["華南","華南金融控股","華南金控"],
    "新光":  ["新光","新光金融控股","新光金控"],
    "永豐":  ["永豐","永豐金融控股","永豐金控"],
    "合庫":  ["合庫","合作金庫","合庫金控"],
    "開發":  ["開發","中華開發","開發金控"],
    "開發金": ["開發","中華開發","開發金控"],
    "國票":  ["國票","國際票券","國票金控"],
    "台灣企銀": ["台灣企銀","臺灣企銀","台企銀"],
    "彰銀":  ["彰銀","彰化銀行"],
    "臺新":  ["台新","臺新","台新金融控股","臺新金融控股"],
    "中國信託": ["中信","中國信託","中國信託金融控股"],
}

# ── sanity checks added after the 5 Nov review (see patch_benchmark.py) ───────────────────────────────
import os as _os
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_END_OK = tuple("。！？；：）)」』】.!?%")
_UNIT_PAIR = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)億元（(-?\d[\d,]*)千元）")
try:
    sys.path.insert(0, _os.path.expanduser(_os.environ.get("AITC_VALIDATOR_DIR", "~/AITC-CreditInvestigationChatBotAgent")))
    from src.api.answer_validator import check_analysis_claims as _check_analysis_claims
except Exception as _e:                      # validator not importable: the analysis checks are skipped, not faked
    _check_analysis_claims = None
    print(f"[benchmark] analysis claim checks unavailable: {_e}")


def _is_template_report(answer):
    """Screen / compare reports are written by Python templates (title on the first line)."""
    first = (answer.strip().split("\n") or [""])[0]
    return "財務篩選報告】" in first or "機構財務比較分析】" in first


def sanity(result, tc):
    """Extra failures (each prefixed 'sanity:') that the keyword / number check cannot see."""
    answer = result.get("answer", "") or ""
    cat, model = tc.get("cat"), result.get("model", "")
    out = []
    if answer.lstrip().startswith("【系統提醒】本題原本的處理模組"):
        out.append("sanity:fallback:answer came from the fallback flow (a model or module was unavailable)")
    if cat == "ratio" and "【財務比率分析報告】" in answer:
        for ln in answer.split("\n"):
            s = ln.strip()
            if len(_CJK_RE.findall(s)) >= 20 and not s.endswith(_END_OK):
                out.append("sanity:cutoff:" + s[-18:])
                break
    for m in _UNIT_PAIR.finditer(answer):
        try:
            yi, qian = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if abs(yi - qian / 100000.0) > 1.0:
            out.append(f"sanity:units:{m.group(1)}億元≠{m.group(2)}千元")
            break
    if cat == "analysis" and not _is_template_report(answer) and _check_analysis_claims:
        j = re.search(r"(正常範圍|警示範圍|高風險範圍)", answer)
        for it in _check_analysis_claims(answer, j.group(1) if j else "", "", tc.get("q", "")):
            out.append(f"sanity:{it['kind']}:{it['sentence'][:24]}")
    return out


def _roe_from(answer, who):
    """ROE (annualised, %) for one company from a ratio-route or compare-route answer; None if not found."""
    m = re.search(r"ROE（年化）：\s*(-?\d+\.\d+)%", answer)
    if m and who is None:
        return float(m.group(1))
    m = re.search(r"ROE年化[^\n]*?" + re.escape(who or "") + r"[：:]\s*(-?\d+\.\d+)%", answer) if who else None
    return float(m.group(1)) if m else None


def consistency():
    """Same company, same period, same metric must give the same number on the ratio route and the compare route."""
    r1 = ask("國泰金控 2024年第三季的ROE是多少？")
    r2 = ask("富邦金控和國泰金控 2024年第三季的財務結構有何差異？")
    a, b = _roe_from(r1.get("answer", ""), None), _roe_from(r2.get("answer", ""), "國泰金融")
    ok = a is not None and b is not None and abs(a - b) <= 0.05
    print(f"[consistency] 國泰金控 2024Q3 ROE  ratio route={a}  compare route={b}  -> {'OK' if ok else 'MISMATCH'}")
    return ok

def check(result, tc):
    answer = result.get("answer","")
    failures = []
    for term in tc.get("must_not_contain",[]):
        if term in answer: failures.append(f"forbidden:{term}")
    if tc.get("cat") not in ("lookup","ratio"):
        for term in tc.get("must_contain",[]):
            # Check aliases — if any alias matches, consider it found
            aliases = COMPANY_ALIASES.get(term, [term])
            if not any(a in answer for a in aliases):
                failures.append(f"missing:{term}")
    if tc.get("cat") in ("lookup","ratio") and tc.get("ground_truth_numeric") is not None:
        num_pass, num_reason = check_numeric(answer, tc["ground_truth_numeric"], tc["cat"])
        if not num_pass:   # a question may list other accepted values (e.g. total vs parent-owners equity)
            for _alt in tc.get("ground_truth_alt", []):
                _ok, _why = check_numeric(answer, _alt, tc["cat"])
                if _ok:
                    num_pass, num_reason = True, _why + "(alt)"
                    break
        if not num_pass: failures.append(f"numeric_fail:{num_reason}")
    failures += sanity(result, tc)
    return len(failures)==0, failures

def wait_ready(max_wait=420):
    """Do not ask a single question before the models answer: a request that arrives while TAIDE is still loading is
    served by a fallback model and fails for a reason that has nothing to do with the code."""
    import os as _os, time as _time
    if _os.environ.get("BENCH_SKIP_READY") == "1":
        return
    pending = [("TAIDE (taide-8b)", _os.environ.get("AITC_TAIDE_URL", "http://localhost:33001/v1/models"), "taide-8b"),
               ("Qwen3-30B", _os.environ.get("AITC_QWEN_URL", "http://localhost:33000/v1/models"), "")]
    deadline = _time.time() + max_wait
    while pending and _time.time() < deadline:
        for t in list(pending):
            try:
                with urllib.request.urlopen(t[1], timeout=5) as r:
                    if t[2] in r.read().decode("utf-8", "replace"):
                        pending.remove(t)
                        print(f"[benchmark] {t[0]} is ready")
            except Exception:
                pass
        if pending:
            _time.sleep(5)
    if pending:
        print("[benchmark] STOP: not ready after %d s: %s - nothing was asked." % (max_wait, ", ".join(t[0] for t in pending)))
        sys.exit(2)

def main():
    wait_ready()
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS benchmark_results (
        id INTEGER PRIMARY KEY, run_id TEXT, timestamp TEXT,
        question_id TEXT, question TEXT, category TEXT,
        model_used TEXT, response_time_seconds REAL,
        passed INTEGER, failures TEXT, ground_truth TEXT, answer_preview TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS benchmark_runs (
        id INTEGER PRIMARY KEY, run_id TEXT, started_at TEXT,
        completed_at TEXT, total INTEGER, passed INTEGER, failed INTEGER,
        pass_rate REAL, alert INTEGER, lookup_rate REAL, ratio_rate REAL,
        compare_rate REAL, screen_rate REAL, analysis_rate REAL)""")
    conn.commit()
    passed_count=0; failed_ids=[]; cat_results={"lookup":[],"ratio":[],"compare":[],"screen":[],"analysis":[]}
    started=datetime.datetime.now().isoformat(); total=len(QUESTIONS)
    print(f"[benchmark] Run {run_id} — {total} questions (v2 numeric validation)")
    for i,tc in enumerate(QUESTIONS,1):
        print(f"[{i:02d}/{total}] {tc['id']} ({tc['cat']}) {tc['q'][:40]}", end="", flush=True)
        result=ask(tc["q"]); passed,fails=check(result,tc); elapsed=result.get("response_time_seconds",0)
        print(f" -> {'OK' if passed else 'FAIL'} {elapsed:.1f}s")
        if not passed:
            print(f"         Failures: {fails}")
            print(f"         GT: {tc.get('ground_truth_display','N/A')}")
            print(f"         Answer: {result.get('answer','')[:80]}")
        if passed: passed_count+=1
        else: failed_ids.append(tc["id"])
        cat_results[tc["cat"]].append(1 if passed else 0)
        conn.execute("""INSERT INTO benchmark_results
            (run_id,timestamp,question_id,question,category,model_used,response_time_seconds,passed,failures,ground_truth,answer_preview)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id,datetime.datetime.now().isoformat(),tc["id"],tc["q"],tc["cat"],
             result.get("model","?"),elapsed,1 if passed else 0,str(fails),
             tc.get("ground_truth_display","N/A"),result.get("answer","")[:200]))
        conn.commit()
    def rate(cat): r=cat_results[cat]; return sum(r)/len(r) if r else 0
    _cons_ok = consistency()
    pass_rate=passed_count/total; alert=1 if (pass_rate<BASELINE or not _cons_ok) else 0
    conn.execute("""INSERT INTO benchmark_runs
        (run_id,started_at,completed_at,total,passed,failed,pass_rate,alert,lookup_rate,ratio_rate,compare_rate,screen_rate,analysis_rate)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id,started,datetime.datetime.now().isoformat(),total,passed_count,total-passed_count,pass_rate,alert,
         rate("lookup"),rate("ratio"),rate("compare"),rate("screen"),rate("analysis")))
    conn.commit(); conn.close()
    print(); print("="*60)
    summary=("OK" if not alert else "ALERT")+f" {passed_count}/{total} ({round(pass_rate*100)}%)"+("" if _cons_ok else " [consistency MISMATCH]")
    print(f"[benchmark] {summary}")
    print(f"  Lookup:{int(rate('lookup')*100)}% Ratio:{int(rate('ratio')*100)}% Compare:{int(rate('compare')*100)}% Screen:{int(rate('screen')*100)}% Analysis:{int(rate('analysis')*100)}%")
    if failed_ids: print(f"  Failed: {failed_ids}")
    print("="*60)
    open(LOG_PATH,"a").write(run_id+" "+summary+"\n")

if __name__=="__main__": sys.exit(main())
