from typing import Dict, List, Optional, Tuple

CONCEPT_MAP: Dict[str, Tuple[str, str]] = {
    "現金及約當現金":           ("ifrs-full_CashAndCashEquivalents",        "balance_sheet"),
    "應收帳款淨額":             ("tifrs-bsci-ci_AccountsReceivableNet",      "balance_sheet"),
    "應收帳款":                 ("tifrs-bsci-ci_AccountsReceivableNet",      "balance_sheet"),
    "應收票據淨額":             ("tifrs-bsci-ci_NotesReceivableNet",         "balance_sheet"),
    "應收票據":                 ("tifrs-bsci-ci_NotesReceivableNet",         "balance_sheet"),
    "存貨":                     ("ifrs-full_Inventories",                    "balance_sheet"),
    "流動資產":                 ("ifrs-full_CurrentAssets",                  "balance_sheet"),
    "非流動資產":               ("ifrs-full_NoncurrentAssets",               "balance_sheet"),
    "資產總計":                 ("ifrs-full_Assets",                         "balance_sheet"),
    "資產總額":                 ("ifrs-full_Assets",                         "balance_sheet"),
    "資產合計":                 ("ifrs-full_Assets",                         "balance_sheet"),
    "總資產":                   ("ifrs-full_Assets",                         "balance_sheet"),
    "不動產廠房及設備淨額":     ("ifrs-full_PropertyPlantAndEquipment",       "balance_sheet"),
    "不動產、廠房及設備淨額":   ("ifrs-full_PropertyPlantAndEquipment",       "balance_sheet"),
    "無形資產":                 ("ifrs-full_IntangibleAssetsOtherThanGoodwill", "balance_sheet"),
    "商譽":                     ("ifrs-full_Goodwill",                       "balance_sheet"),
    "使用權資產淨額":           ("ifrs-full_RightofuseAssets",               "balance_sheet"),
    "預付款項":                 ("ifrs-full_Prepayments",                    "balance_sheet"),
    "保留盈餘":                 ("ifrs-full_RetainedEarnings",               "balance_sheet"),
    "流動負債":                 ("ifrs-full_CurrentLiabilities",             "balance_sheet"),
    "非流動負債":               ("ifrs-full_NoncurrentLiabilities",          "balance_sheet"),
    "負債總計":                 ("ifrs-full_Liabilities",                    "balance_sheet"),
    "負債總額":                 ("ifrs-full_Liabilities",                    "balance_sheet"),
    "負債合計":                 ("ifrs-full_Liabilities",                    "balance_sheet"),
    "總負債":                   ("ifrs-full_Liabilities",                    "balance_sheet"),
    "應付帳款":                 ("ifrs-full_TradeAndOtherCurrentPayablesToTradeSuppliers", "balance_sheet"),
    "短期借款":                 ("ifrs-full_ShorttermBorrowings",            "balance_sheet"),
    "長期借款":                 ("ifrs-full_LongtermBorrowings",             "balance_sheet"),
    "租賃負債":                 ("ifrs-full_LeaseLiabilities",               "balance_sheet"),
    "權益總計":                 ("ifrs-full_Equity",                         "balance_sheet"),
    "權益總額":                 ("ifrs-full_Equity",                         "balance_sheet"),
    "股東權益":                 ("ifrs-full_Equity",                         "balance_sheet"),
    "股本":                     ("ifrs-full_IssuedCapital",                  "balance_sheet"),
    "資本公積":                 ("ifrs-full_SharePremium",                   "balance_sheet"),
    "營業收入":                 ("ifrs-full_Revenue",                        "comprehensive_income_statement"),
    "營收":                     ("ifrs-full_Revenue",                        "comprehensive_income_statement"),
    "收入":                     ("ifrs-full_Revenue",                        "comprehensive_income_statement"),
    "營業成本":                 ("tifrs-bsci-ci_OperatingCosts",             "comprehensive_income_statement"),
    "營業毛利":                 ("tifrs-bsci-ci_GrossProfitLossFromOperations", "comprehensive_income_statement"),
    "毛利":                     ("tifrs-bsci-ci_GrossProfitLossFromOperations", "comprehensive_income_statement"),
    "營業費用":                 ("ifrs-full_OperatingExpense",               "comprehensive_income_statement"),
    "營業利益":                 ("ifrs-full_ProfitLossFromOperatingActivities", "comprehensive_income_statement"),
    "營業損益":                 ("ifrs-full_ProfitLossFromOperatingActivities", "comprehensive_income_statement"),
    "稅前損益":                 ("ifrs-full_ProfitLossBeforeTax",            "comprehensive_income_statement"),
    "稅前淨利":                 ("ifrs-full_ProfitLossBeforeTax",            "comprehensive_income_statement"),
    "繼續營業單位本期淨利":     ("ifrs-full_ProfitLossFromContinuingOperations", "comprehensive_income_statement"),
    "本期淨利":                 ("ifrs-full_ProfitLossFromContinuingOperations", "comprehensive_income_statement"),
    "稅後淨利":                 ("ifrs-full_ProfitLossFromContinuingOperations", "comprehensive_income_statement"),
    "淨利":                     ("ifrs-full_ProfitLossFromContinuingOperations", "comprehensive_income_statement"),
    "本期損益":                 ("ifrs-full_ProfitLossFromContinuingOperations", "comprehensive_income_statement"),
    "每股盈餘":                 ("ifrs-full_BasicEarningsLossPerShare",      "comprehensive_income_statement"),
    "EPS":                      ("ifrs-full_BasicEarningsLossPerShare",      "comprehensive_income_statement"),
    "所得稅費用":               ("ifrs-full_IncomeTaxExpenseContinuingOperations", "comprehensive_income_statement"),
    "利息費用":                 ("ifrs-full_FinanceCosts",                   "comprehensive_income_statement"),
    "利息收入":                 ("ifrs-full_InterestIncome",                 "comprehensive_income_statement"),
    "營業活動現金流量":         ("ifrs-full_CashFlowsFromUsedInOperatingActivities", "statement_of_cash_flows"),
    "營業活動現金流":           ("ifrs-full_CashFlowsFromUsedInOperatingActivities", "statement_of_cash_flows"),
    "投資活動現金流量":         ("ifrs-full_CashFlowsFromUsedInInvestingActivities", "statement_of_cash_flows"),
    "投資活動現金流":           ("ifrs-full_CashFlowsFromUsedInInvestingActivities", "statement_of_cash_flows"),
    "籌資活動現金流量":         ("ifrs-full_CashFlowsFromUsedInFinancingActivities", "statement_of_cash_flows"),
    "籌資活動現金流":           ("ifrs-full_CashFlowsFromUsedInFinancingActivities", "statement_of_cash_flows"),
    "資本支出":                 ("ifrs-full_PurchaseOfPropertyPlantAndEquipment", "statement_of_cash_flows"),
    "期末現金及約當現金":       ("ifrs-full_CashAndCashEquivalents",         "statement_of_cash_flows"),
}

ALIAS_MAP: Dict[str, str] = {
    "現金水位": "現金及約當現金", "約當現金": "現金及約當現金", "現金部位": "現金及約當現金",
    "總收入": "營業收入", "銷售收入": "營業收入", "本期營業收入": "營業收入",
    "資產規模": "資產總計", "負債規模": "負債總計", "負債水位": "負債總計",
    "獲利": "本期淨利", "淨損": "本期淨利", "純益": "本期淨利", "盈利": "本期淨利",
    "盈餘": "本期淨利", "本期稅後淨利": "本期淨利", "稅後損益": "本期淨利",
    "毛損": "營業毛利", "營業損失": "營業利益", "營業盈餘": "營業利益",
    "自由現金流": "營業活動現金流量", "經營現金流": "營業活動現金流量",
    "淨值": "權益總計", "每股淨值": "權益總計", "帳面價值": "權益總計",
    "應收款項": "應收帳款淨額", "應收款": "應收帳款淨額",
    "借款": "短期借款", "貸款": "短期借款",
    "獲利能力": "本期淨利",
}

def lookup_concept(field_name: str):
    if field_name in CONCEPT_MAP:
        return CONCEPT_MAP[field_name]
    canonical = ALIAS_MAP.get(field_name)
    if canonical and canonical in CONCEPT_MAP:
        return CONCEPT_MAP[canonical]
    for key, value in CONCEPT_MAP.items():
        if len(field_name) >= 2 and (field_name in key or key in field_name):
            return value
    return None

def get_statement_type(field_name: str):
    r = lookup_concept(field_name)
    return r[1] if r else None

def get_concept_id(field_name: str):
    r = lookup_concept(field_name)
    return r[0] if r else None
