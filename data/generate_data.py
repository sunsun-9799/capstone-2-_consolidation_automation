# -*- coding: utf-8 -*-
"""
4주차: 가상 데이터 생성 스크립트
연결회계 자동화 프로그램 — 캡스톤 디자인 2

이 스크립트는 다음 4개 CSV 파일을 생성합니다.
  1. company_master.csv        — 회사 마스터 (회사코드, 회사명, 구분)
  2. financial_statements.csv  — P/A/B 3사 재무제표 (2025.12.31 결산)
  3. journal_entries.csv       — 내부거래 분개 데이터 (Trading Partner 포함)
  4. ownership.csv             — 지분율 테이블 (취득시점 자본 포함)

재현성을 위해 모든 숫자는 하드코딩된 값이며, 스크립트 실행 시
각 회사별 대차균형(총차변=총대변)과 FR-10(투자-자본-영업권 매칭)을
자동으로 검증합니다. 검증 실패 시 AssertionError를 발생시킵니다.
"""

import pandas as pd
import os

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 0. 계정과목 마스터 (17개)
# ---------------------------------------------------------------------------
ACCOUNT_MASTER = [
    # (계정과목, 구분, 정상잔액)
    ("현금및현금성자산", "자산", "차"),
    ("매출채권",         "자산", "차"),
    ("재고자산",         "자산", "차"),
    ("대여금",           "자산", "차"),
    ("유형자산",         "자산", "차"),
    ("종속기업투자주식",  "자산", "차"),
    ("영업권",           "자산", "차"),   # 연결조정에서만 발생 (개별 재무제표엔 없음)
    ("매입채무",         "부채", "대"),
    ("미지급배당금",      "부채", "대"),
    ("차입금",           "부채", "대"),
    ("자본금",           "자본", "대"),
    ("이익잉여금",        "자본", "대"),
    ("비지배지분",        "자본", "대"),   # 연결조정에서만 발생
    ("매출",             "손익", "대"),
    ("매출원가",          "손익", "차"),
    ("판관비",           "손익", "차"),
    ("배당금수익",        "손익", "대"),
]
account_df = pd.DataFrame(ACCOUNT_MASTER, columns=["계정과목", "구분", "정상잔액"])

# ---------------------------------------------------------------------------
# 0-1. 회사 마스터 (Company Entity)
#      financial_statements/journal_entries/ownership 세 파일 모두 이 회사코드를
#      참조(FK)한다. FR-9("TP가 지분율 테이블/마스터에 없는 회사코드")의 기준 테이블.
# ---------------------------------------------------------------------------
COMPANY_MASTER = [
    # (회사코드, 회사명, 구분)
    ("P", "모회사", "지배기업"),
    ("A", "자회사A", "종속기업(100%)"),
    ("B", "자회사B", "종속기업(80%)"),
]
company_df = pd.DataFrame(COMPANY_MASTER, columns=["회사코드", "회사명", "구분"])

# ---------------------------------------------------------------------------
# 1. 회사별 기초 잔액 (그룹 내부거래 반영 "전" 외부 활동 결과)
#    B의 경우, 배당 200 중 비지배지분(NCI) 귀속분 40은 내부거래(Trading Partner)로
#    잡히지 않으므로 여기 기초 잔액에 이미 반영해 둔다 (현금 -40, 이잉 -40).
# ---------------------------------------------------------------------------
# 외부거래(일반매출/일반매입/판관비) 분개를 별도로 추가하면서, 아래 매출/매출원가/판관비는
# 더 이상 기초값으로 직접 넣지 않고 전부 외부거래 분개(거래유형=일반매출/일반매입/판관비)의
# 합계로 대체한다. 최종 잔액이 바뀌지 않도록 현금은 그 분개들의 순현금효과만큼 미리 빼둔다.
#   P: 순현금효과 = 2500-1500-500 = +500  -> 현금 기초값 2000-500=1500
#   A: 순현금효과 = 1750-1100-300 = +350  -> 현금 기초값 800-350=450
#   B: 순현금효과 = 1700-1300-350 = +50   -> 현금 기초값 860-50=810
BASE = {
    "P": {
        "현금및현금성자산": 1500, "매출채권": 300, "재고자산": 1200, "대여금": 0,
        "유형자산": 3000, "종속기업투자주식": 3700, "매입채무": 400, "차입금": 300,
        "자본금": 5000, "이익잉여금": 4000,
        "매출": 0, "매출원가": 0, "판관비": 0, "배당금수익": 0,
    },
    "A": {
        "현금및현금성자산": 450, "매출채권": 200, "재고자산": 600, "대여금": 0,
        "유형자산": 1000, "종속기업투자주식": 0, "매입채무": 250, "차입금": 100,
        "자본금": 1000, "이익잉여금": 900,
        "매출": 0, "매출원가": 0, "판관비": 0, "배당금수익": 0,
    },
    "B": {
        "현금및현금성자산": 810, "매출채권": 300, "재고자산": 700, "대여금": 0,
        "유형자산": 1200, "종속기업투자주식": 0, "매입채무": 300, "차입금": 150,
        "자본금": 1500, "이익잉여금": 1060,
        "매출": 0, "매출원가": 0, "판관비": 0, "배당금수익": 0,
    },
}

# ---------------------------------------------------------------------------
# 2. 내부거래 분개 데이터 (Trading Partner 포함)
#    분개번호 - 회사코드 - 계정과목 - 차대구분 - 금액 - TP - 거래일자 - 거래유형 - 마진율
#    (마진율은 거래유형=재고 인 라인에만 채워짐 → FR-6 미실현이익 계산용)
# ---------------------------------------------------------------------------
JE_ROWS = [
    # --- P -> A 매출/매입 #1 (전량 외부 재판매, 재고 미실현이익 없음) ---
    ("JE001", "P", "매출채권", "차", 300, "A", "2025-03-15", "매출", None),
    ("JE001", "P", "매출",     "대", 300, "A", "2025-03-15", "매출", None),
    ("JE002", "A", "매출원가", "차", 300, "P", "2025-03-15", "매입", None),
    ("JE002", "A", "매입채무", "대", 300, "P", "2025-03-15", "매입", None),

    # --- P -> A 매출/매입 #2 ---
    ("JE003", "P", "매출채권", "차", 200, "A", "2025-06-01", "매출", None),
    ("JE003", "P", "매출",     "대", 200, "A", "2025-06-01", "매출", None),
    ("JE004", "A", "매출원가", "차", 200, "P", "2025-06-01", "매입", None),
    ("JE004", "A", "매입채무", "대", 200, "P", "2025-06-01", "매입", None),

    # --- A -> B 매출/매입 #1 (전량 외부 재판매) ---
    ("JE005", "A", "매출채권", "차", 600, "B", "2025-04-10", "매출", None),
    ("JE005", "A", "매출",     "대", 600, "B", "2025-04-10", "매출", None),
    ("JE006", "B", "매출원가", "차", 600, "A", "2025-04-10", "매입", None),
    ("JE006", "B", "매입채무", "대", 600, "A", "2025-04-10", "매입", None),

    # --- A -> B 매출/매입 #2 (마진율 20%, 기말재고 40% 잔존 -> 미실현이익 80) ---
    ("JE007", "A", "매출채권", "차", 1000, "B", "2025-07-20", "매출", None),
    ("JE007", "A", "매출",     "대", 1000, "B", "2025-07-20", "매출", None),
    ("JE008", "B", "매출원가", "차", 600,  "A", "2025-07-20", "매입", None),
    ("JE008", "B", "재고자산", "차", 400,  "A", "2025-07-20", "재고", 0.20),
    ("JE008", "B", "매입채무", "대", 1000, "A", "2025-07-20", "매입", None),

    # --- A -> B 매출/매입 #3 (전량 외부 재판매) ---
    ("JE009", "A", "매출채권", "차", 400, "B", "2025-09-05", "매출", None),
    ("JE009", "A", "매출",     "대", 400, "B", "2025-09-05", "매출", None),
    ("JE010", "B", "매출원가", "차", 400, "A", "2025-09-05", "매입", None),
    ("JE010", "B", "매입채무", "대", 400, "A", "2025-09-05", "매입", None),

    # --- B -> P 배당금 (지분율 80% 해당분만; NCI 20%분은 BASE에 선반영) ---
    ("JE011", "P", "현금및현금성자산",     "차", 160, "B", "2025-11-20", "배당", None),
    ("JE011", "P", "배당금수익", "대", 160, "B", "2025-11-20", "배당", None),
    ("JE012", "B", "이익잉여금", "차", 160, "P", "2025-11-20", "배당", None),
    ("JE012", "B", "현금및현금성자산",     "대", 160, "P", "2025-11-20", "배당", None),

    # --- P -> A 대여금/차입금 ---
    ("JE013", "P", "대여금",   "차", 500, "A", "2025-02-01", "대여금", None),
    ("JE013", "P", "현금및현금성자산",     "대", 500, "A", "2025-02-01", "대여금", None),
    ("JE014", "A", "현금및현금성자산",     "차", 500, "P", "2025-02-01", "대여금", None),
    ("JE014", "A", "차입금",   "대", 500, "P", "2025-02-01", "대여금", None),

    # --- A -> B 대여금/차입금 ---
    ("JE015", "A", "대여금",   "차", 300, "B", "2025-04-01", "대여금", None),
    ("JE015", "A", "현금및현금성자산",     "대", 300, "B", "2025-04-01", "대여금", None),
    ("JE016", "B", "현금및현금성자산",     "차", 300, "A", "2025-04-01", "대여금", None),
    ("JE016", "B", "차입금",   "대", 300, "A", "2025-04-01", "대여금", None),
]

# ---------------------------------------------------------------------------
# 2-1. 외부거래(일반) 분개 — FR-3("TP가 채워진 거래를 내부거래로 자동 식별") 검증용 노이즈.
#      거래유형을 매출/매입/배당/재고/대여금(내부거래 전용)과 다르게
#      일반매출/일반매입/판관비로 둬서 FR-9 완결성 체크(내부거래 전용 유형에만 적용)가
#      외부거래를 결함으로 오탐하지 않게 한다. TradingPartner는 전부 공란(외부 상대방).
#      각 회사의 매출/매출원가/판관비 합계와 정확히 일치하도록 분해했다 (BASE는 0으로 비움).
# ---------------------------------------------------------------------------
EXTERNAL_JE_ROWS = [
    # --- P: 매출 2500 = 1000+900+600 ---
    ("EX-P01", "P", "현금및현금성자산", "차", 1000, "", "2025-01-10", "일반매출", None),
    ("EX-P01", "P", "매출",           "대", 1000, "", "2025-01-10", "일반매출", None),
    ("EX-P02", "P", "현금및현금성자산", "차", 900,  "", "2025-05-20", "일반매출", None),
    ("EX-P02", "P", "매출",           "대", 900,  "", "2025-05-20", "일반매출", None),
    ("EX-P03", "P", "현금및현금성자산", "차", 600,  "", "2025-08-15", "일반매출", None),
    ("EX-P03", "P", "매출",           "대", 600,  "", "2025-08-15", "일반매출", None),
    # --- P: 매출원가 1500 = 900+600 ---
    ("EX-P04", "P", "매출원가",        "차", 900, "", "2025-02-10", "일반매입", None),
    ("EX-P04", "P", "현금및현금성자산", "대", 900, "", "2025-02-10", "일반매입", None),
    ("EX-P05", "P", "매출원가",        "차", 600, "", "2025-07-05", "일반매입", None),
    ("EX-P05", "P", "현금및현금성자산", "대", 600, "", "2025-07-05", "일반매입", None),
    # --- P: 판관비 500 = 300+200 ---
    ("EX-P06", "P", "판관비",          "차", 300, "", "2025-03-01", "판관비", None),
    ("EX-P06", "P", "현금및현금성자산", "대", 300, "", "2025-03-01", "판관비", None),
    ("EX-P07", "P", "판관비",          "차", 200, "", "2025-09-01", "판관비", None),
    ("EX-P07", "P", "현금및현금성자산", "대", 200, "", "2025-09-01", "판관비", None),

    # --- A: 매출 1750 = 700+650+400 ---
    ("EX-A01", "A", "현금및현금성자산", "차", 700, "", "2025-01-15", "일반매출", None),
    ("EX-A01", "A", "매출",           "대", 700, "", "2025-01-15", "일반매출", None),
    ("EX-A02", "A", "현금및현금성자산", "차", 650, "", "2025-05-10", "일반매출", None),
    ("EX-A02", "A", "매출",           "대", 650, "", "2025-05-10", "일반매출", None),
    ("EX-A03", "A", "현금및현금성자산", "차", 400, "", "2025-08-20", "일반매출", None),
    ("EX-A03", "A", "매출",           "대", 400, "", "2025-08-20", "일반매출", None),
    # --- A: 매출원가 1100 = 600+500 ---
    ("EX-A04", "A", "매출원가",        "차", 600, "", "2025-02-15", "일반매입", None),
    ("EX-A04", "A", "현금및현금성자산", "대", 600, "", "2025-02-15", "일반매입", None),
    ("EX-A05", "A", "매출원가",        "차", 500, "", "2025-07-10", "일반매입", None),
    ("EX-A05", "A", "현금및현금성자산", "대", 500, "", "2025-07-10", "일반매입", None),
    # --- A: 판관비 300 = 200+100 ---
    ("EX-A06", "A", "판관비",          "차", 200, "", "2025-03-05", "판관비", None),
    ("EX-A06", "A", "현금및현금성자산", "대", 200, "", "2025-03-05", "판관비", None),
    ("EX-A07", "A", "판관비",          "차", 100, "", "2025-09-05", "판관비", None),
    ("EX-A07", "A", "현금및현금성자산", "대", 100, "", "2025-09-05", "판관비", None),

    # --- B: 매출 1700 = 700+600+400 ---
    ("EX-B01", "B", "현금및현금성자산", "차", 700, "", "2025-01-20", "일반매출", None),
    ("EX-B01", "B", "매출",           "대", 700, "", "2025-01-20", "일반매출", None),
    ("EX-B02", "B", "현금및현금성자산", "차", 600, "", "2025-05-15", "일반매출", None),
    ("EX-B02", "B", "매출",           "대", 600, "", "2025-05-15", "일반매출", None),
    ("EX-B03", "B", "현금및현금성자산", "차", 400, "", "2025-08-25", "일반매출", None),
    ("EX-B03", "B", "매출",           "대", 400, "", "2025-08-25", "일반매출", None),
    # --- B: 매출원가 1300 = 700+600 ---
    ("EX-B04", "B", "매출원가",        "차", 700, "", "2025-02-20", "일반매입", None),
    ("EX-B04", "B", "현금및현금성자산", "대", 700, "", "2025-02-20", "일반매입", None),
    ("EX-B05", "B", "매출원가",        "차", 600, "", "2025-07-15", "일반매입", None),
    ("EX-B05", "B", "현금및현금성자산", "대", 600, "", "2025-07-15", "일반매입", None),
    # --- B: 판관비 350 = 200+150 ---
    ("EX-B06", "B", "판관비",          "차", 200, "", "2025-03-10", "판관비", None),
    ("EX-B06", "B", "현금및현금성자산", "대", 200, "", "2025-03-10", "판관비", None),
    ("EX-B07", "B", "판관비",          "차", 150, "", "2025-09-10", "판관비", None),
    ("EX-B07", "B", "현금및현금성자산", "대", 150, "", "2025-09-10", "판관비", None),
]

journal_df = pd.DataFrame(
    JE_ROWS + EXTERNAL_JE_ROWS,
    columns=["분개번호", "회사코드", "계정과목", "차대구분", "금액", "TradingPartner", "거래일자", "거래유형", "마진율"],
)

# ---------------------------------------------------------------------------
# 3. 지분율 테이블 (취득시점 자본 포함 — FR-10 계산용)
# ---------------------------------------------------------------------------
OWNERSHIP_ROWS = [
    ("P", "A", 1.00, "2022-01-01", 1000, 500),   # 자본금, 이익잉여금(취득시점)
    ("P", "B", 0.80, "2022-01-01", 1500, 800),
]
ownership_df = pd.DataFrame(
    OWNERSHIP_ROWS,
    columns=["모회사코드", "자회사코드", "지분율", "취득일", "취득시점자본금", "취득시점이익잉여금"],
)

# ---------------------------------------------------------------------------
# 4. 기초 잔액 + 분개 반영 -> 최종 재무제표 산출
# ---------------------------------------------------------------------------
SIGN = {"차": 1, "대": -1}
NORMAL_SIGN = {"차": 1, "대": -1}  # 정상잔액이 "차"인 계정은 +가 증가, "대"인 계정은 +가 증가(표시상 절대값)

def build_financial_statements():
    company_codes = ["P", "A", "B"]
    balances = {c: dict(BASE[c]) for c in company_codes}

    # 분개 반영: 계정과목 정상잔액 방향에 맞춰 "증가"로 누적
    acc_normal = dict(zip(account_df["계정과목"], account_df["정상잔액"]))
    for _, row in journal_df.iterrows():
        comp = row["회사코드"]
        acct = row["계정과목"]
        dc = row["차대구분"]
        amt = row["금액"]
        normal = acc_normal[acct]
        delta = amt if dc == normal else -amt
        balances[comp][acct] = balances[comp].get(acct, 0) + delta

    records = []
    for comp in company_codes:
        for acct, amt in balances[comp].items():
            if amt == 0:
                continue
            records.append({
                "회사코드": comp,
                "계정과목": acct,
                "금액": amt,
                "기간": "FY2025",
                "결산일": "2025-12-31",
            })
    fs_df = pd.DataFrame(records)
    return fs_df, balances


fs_df, final_balances = build_financial_statements()

# ---------------------------------------------------------------------------
# 5. 검증
# ---------------------------------------------------------------------------
def validate():
    acc_type = dict(zip(account_df["계정과목"], account_df["구분"]))
    errors = []

    # (1) 회사별 총차변계정 = 총대변계정 (자산+매출원가+판관비 = 부채+자본+매출+배당금수익)
    for comp, bal in final_balances.items():
        debit_side = sum(v for k, v in bal.items() if acc_type[k] in ("자산",) or k in ("매출원가", "판관비"))
        credit_side = sum(v for k, v in bal.items() if acc_type[k] in ("부채", "자본") or k in ("매출", "배당금수익"))
        if abs(debit_side - credit_side) > 0.01:
            errors.append(f"[대차불일치] {comp}: 차변합={debit_side}, 대변합={credit_side}")
        else:
            print(f"[OK] {comp} 대차일치: {debit_side:,.0f}")

    # (2) 내부거래 매출채권/매입채무 상계 -> 0 확인 (P-A, A-B)
    def intercompany_pair_check(sales_je_list, comp_a, comp_b):
        total_ar = journal_df[
            (journal_df["회사코드"] == comp_a) & (journal_df["계정과목"] == "매출채권") &
            (journal_df["TradingPartner"] == comp_b)
        ]["금액"].sum()
        total_ap = journal_df[
            (journal_df["회사코드"] == comp_b) & (journal_df["계정과목"] == "매입채무") &
            (journal_df["TradingPartner"] == comp_a)
        ]["금액"].sum()
        return total_ar, total_ap

    ar_pa, ap_pa = intercompany_pair_check(None, "P", "A")
    if ar_pa != ap_pa:
        errors.append(f"[P-A 상계불일치] 매출채권={ar_pa}, 매입채무={ap_pa}")
    else:
        print(f"[OK] P-A 매출채권/매입채무 상계: {ar_pa:,.0f} = {ap_pa:,.0f}")

    ar_ab, ap_ab = intercompany_pair_check(None, "A", "B")
    if ar_ab != ap_ab:
        errors.append(f"[A-B 상계불일치] 매출채권={ar_ab}, 매입채무={ap_ab}")
    else:
        print(f"[OK] A-B 매출채권/매입채무 상계: {ar_ab:,.0f} = {ap_ab:,.0f}")

    # (3) 대여금/차입금 상계
    loan_pa = journal_df[(journal_df["회사코드"] == "P") & (journal_df["계정과목"] == "대여금")]["금액"].sum()
    borrow_a_from_p = journal_df[(journal_df["회사코드"] == "A") & (journal_df["계정과목"] == "차입금") & (journal_df["TradingPartner"] == "P")]["금액"].sum()
    if loan_pa != borrow_a_from_p:
        errors.append(f"[P-A 대여차입 불일치] {loan_pa} vs {borrow_a_from_p}")
    else:
        print(f"[OK] P->A 대여금/차입금 상계: {loan_pa:,.0f}")

    loan_ab = journal_df[(journal_df["회사코드"] == "A") & (journal_df["계정과목"] == "대여금") & (journal_df["TradingPartner"] == "B")]["금액"].sum()
    borrow_b_from_a = journal_df[(journal_df["회사코드"] == "B") & (journal_df["계정과목"] == "차입금") & (journal_df["TradingPartner"] == "A")]["금액"].sum()
    if loan_ab != borrow_b_from_a:
        errors.append(f"[A-B 대여차입 불일치] {loan_ab} vs {borrow_b_from_a}")
    else:
        print(f"[OK] A->B 대여금/차입금 상계: {loan_ab:,.0f}")

    # (4) 배당 상계 (P 배당금수익 == B 이익잉여금 감소분, TP 매칭)
    div_income_p = journal_df[(journal_df["회사코드"] == "P") & (journal_df["계정과목"] == "배당금수익")]["금액"].sum()
    div_re_b = journal_df[(journal_df["회사코드"] == "B") & (journal_df["계정과목"] == "이익잉여금") & (journal_df["TradingPartner"] == "P")]["금액"].sum()
    if div_income_p != div_re_b:
        errors.append(f"[배당 상계불일치] P배당금수익={div_income_p}, B이잉감소={div_re_b}")
    else:
        print(f"[OK] 배당금 상계: {div_income_p:,.0f}")

    # (5) FR-10: 투자주식 - 취득시점 순자산 지분율 해당액 = 영업권 (양수여야 함, B는 영업권 발생)
    for _, row in ownership_df.iterrows():
        parent, sub, pct = row["모회사코드"], row["자회사코드"], row["지분율"]
        net_assets_acq = row["취득시점자본금"] + row["취득시점이익잉여금"]
        parent_share = net_assets_acq * pct
        nci_share = net_assets_acq * (1 - pct)
        # P의 종속기업투자주식 중 해당 자회사분 금액은 BASE에 합산되어 있으므로 별도 매핑 필요
        inv_map = {"A": 1500, "B": 2200}
        investment = inv_map[sub]
        goodwill = investment - parent_share
        print(f"[FR-10] {parent}->{sub}: 취득시점순자산={net_assets_acq:,.0f}, "
              f"지배지분해당액={parent_share:,.0f}, NCI={nci_share:,.0f}, "
              f"투자주식={investment:,.0f}, 영업권={goodwill:,.0f}")
        if sub == "A" and goodwill != 0:
            errors.append(f"[FR-10] A는 영업권 0이어야 하는데 {goodwill}")
        if sub == "B" and goodwill <= 0:
            errors.append(f"[FR-10] B는 영업권이 양수여야 하는데 {goodwill}")

    # (6) FR-6: 재고 미실현이익 계산 확인
    inv_rows = journal_df[journal_df["거래유형"] == "재고"]
    for _, r in inv_rows.iterrows():
        unrealized = r["금액"] * r["마진율"]
        print(f"[FR-6] {r['분개번호']} {r['회사코드']} 기말재고잔존={r['금액']:,.0f}, "
              f"마진율={r['마진율']:.0%}, 미실현이익={unrealized:,.0f}")

    # (7) 참조 무결성: journal_entries/ownership의 회사코드가 company_master에 존재하는가
    #     (TradingPartner의 공란("")은 외부거래를 뜻하므로 검사 대상에서 제외)
    valid_codes = set(company_df["회사코드"])
    tp_codes = set(journal_df["TradingPartner"]) - {""}
    used_codes = set(journal_df["회사코드"]) | tp_codes | \
        set(ownership_df["모회사코드"]) | set(ownership_df["자회사코드"])
    unknown = used_codes - valid_codes
    if unknown:
        errors.append(f"[회사코드 참조무결성] company_master에 없는 코드: {unknown}")
    else:
        print(f"[OK] 회사코드 참조 무결성: {sorted(used_codes)} 전부 company_master에 존재")

    # (8) FR-3: TP 유무로 내부/외부거래가 실제로 갈리는지 확인 (식별이 no-op이 아니어야 함)
    total_entries = journal_df["분개번호"].nunique()
    internal_entries = journal_df.loc[journal_df["TradingPartner"] != "", "분개번호"].nunique()
    external_entries = total_entries - internal_entries
    print(f"[FR-3] 전체 분개 {total_entries}건 중 내부거래(TP 있음) {internal_entries}건, "
          f"외부거래(TP 없음) {external_entries}건")
    if external_entries == 0:
        errors.append("[FR-3] 외부거래(TP 없음)가 0건 — 식별 로직이 실제로 거를 대상이 없음")
    if internal_entries == 0:
        errors.append("[FR-3] 내부거래(TP 있음)가 0건")

    # (9) FR-9 완결성 체크가 외부거래를 오탐하지 않는지 확인: 완결성 체크 대상 거래유형
    #     (매출/매입/배당/재고)에는 외부거래 라벨(일반매출/일반매입/판관비)이 섞이면 안 됨
    fr9_scope = {"매출", "매입", "배당", "재고"}
    scoped_rows = journal_df[journal_df["거래유형"].isin(fr9_scope)]
    blank_tp_in_scope = scoped_rows[scoped_rows["TradingPartner"] == ""]
    if len(blank_tp_in_scope) > 0:
        errors.append(f"[FR-9] 내부거래 전용 거래유형인데 TP가 빈 라인이 {len(blank_tp_in_scope)}건 존재 "
                      f"(외부거래가 오탐될 수 있음)")
    else:
        print(f"[OK] FR-9 스코프({sorted(fr9_scope)}) 내 모든 라인에 TP 존재 — 외부거래 오탐 없음")

    # (10) 회귀 확인: 외부거래 분개를 추가하기 전과 매출/매출원가/판관비/현금 최종잔액이 동일한지
    expected_pnl = {
        "P": {"매출": 3000, "매출원가": 1500, "판관비": 500, "현금및현금성자산": 1660},
        "A": {"매출": 3750, "매출원가": 1600, "판관비": 300, "현금및현금성자산": 1000},
        "B": {"매출": 1700, "매출원가": 2900, "판관비": 350, "현금및현금성자산": 1000},
    }
    for comp, accts in expected_pnl.items():
        for acct, expected in accts.items():
            actual = final_balances[comp].get(acct, 0)
            if actual != expected:
                errors.append(f"[회귀] {comp}.{acct} = {actual} (기존 {expected}과 다름 — "
                              f"외부거래 분해가 기존 재무제표를 바꿔버림)")
    if not errors:
        print("[OK] 외부거래 분개 추가 후에도 기존 재무제표 최종잔액 전부 동일")

    if errors:
        raise AssertionError("검증 실패:\n" + "\n".join(errors))
    print("\n=== 모든 검증 통과 ===")


validate()

# ---------------------------------------------------------------------------
# 6. 파일 출력
# ---------------------------------------------------------------------------
company_df.to_csv(os.path.join(OUT_DIR, "company_master.csv"), index=False, encoding="utf-8-sig")
account_df.to_csv(os.path.join(OUT_DIR, "account_master.csv"), index=False, encoding="utf-8-sig")
fs_df.to_csv(os.path.join(OUT_DIR, "financial_statements.csv"), index=False, encoding="utf-8-sig")
journal_df.to_csv(os.path.join(OUT_DIR, "journal_entries.csv"), index=False, encoding="utf-8-sig")
ownership_df.to_csv(os.path.join(OUT_DIR, "ownership.csv"), index=False, encoding="utf-8-sig")

print("\n파일 생성 완료:")
for f in ["company_master.csv", "account_master.csv", "financial_statements.csv", "journal_entries.csv", "ownership.csv"]:
    print(" -", os.path.join(OUT_DIR, f))
