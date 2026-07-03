"""export_dashboard_data.py - CSV + 전략 → dashboard_data.json"""
import json, sys, shutil, glob, math
from pathlib import Path
import pandas as pd


def fix_nan(obj):
    """JSON 표준은 NaN/Infinity를 지원하지 않으므로 0으로 치환 (재귀적)"""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0
        return obj
    elif isinstance(obj, dict):
        return {k: fix_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_nan(v) for v in obj]
    return obj

BASE   = Path(__file__).parent.parent
MASTER = BASE / "data" / "master"
sys.path.insert(0, str(BASE))

from analyzer.strategy_recommender import get_all_strategies
from analyzer.risk_predictor import run_predictor

INDUSTRY_STRUCTURE = {
    "배터리": {
        "type": "원가형",
        "description": "원가가 수익성을 결정하는 산업",
        "flow": ["국제정세", "원자재가격", "생산원가", "가동률", "수출영향"],
        "key_countries": [
            {"code":"ID","name":"인도네시아","role":"니켈 공급","channel":"원자재"},
            {"code":"CL","name":"칠레","role":"리튬 공급","channel":"원자재"},
            {"code":"CN","name":"중국","role":"흑연·소재","channel":"공급망"},
        ],
        "key_indicators": ["수입물가_니켈괴","수입물가_흑연","배터리_가동률"],
        "validation": "니켈→가동률 r=0.673, 가동률→수출 r=0.594",
    },
    "철강": {
        "type": "사이클형",
        "description": "원자재 사이클이 산업을 지배하는 산업",
        "flow": ["국제정세", "철광석·유연탄가격", "생산원가", "생산능력", "수출영향"],
        "key_countries": [
            {"code":"AU","name":"호주","role":"철광석 공급","channel":"원자재"},
            {"code":"BR","name":"브라질","role":"철광석 공급","channel":"원자재"},
            {"code":"CN","name":"중국","role":"수요·감산","channel":"지정학"},
            {"code":"RU","name":"러시아","role":"유연탄 공급","channel":"원자재"},
        ],
        "key_indicators": ["수입물가_철광석","수입물가_유연탄","철강_생산능력"],
        "validation": "유연탄→수출 r=-0.601, 생산능력→수출 r=-0.916",
    },
    "자동차": {
        "type": "수요형",
        "description": "수요가 즉시 생산을 바꾸는 산업",
        "flow": ["국제정세", "관세·경기변화", "소비수요", "생산조정", "수출영향"],
        "key_countries": [
            {"code":"US","name":"미국","role":"최대 수출시장","channel":"관세"},
            {"code":"MX","name":"멕시코","role":"USMCA 생산기지","channel":"공급망"},
            {"code":"DE","name":"독일","role":"EU 경쟁국","channel":"수출규제"},
        ],
        "key_indicators": ["환율_원달러","미국산업생산","미국정책금리"],
        "validation": "관세리스크→수출 그랜저 F=8.224",
    },
    "반도체": {
        "type": "정책형",
        "description": "정책이 직접 생산을 건드리는 산업",
        "flow": ["국제정세", "수출규제발표", "공급망차질", "재고조정", "수출영향"],
        "key_countries": [
            {"code":"US","name":"미국","role":"규제 주체","channel":"수출규제"},
            {"code":"NL","name":"네덜란드","role":"ASML 장비","channel":"공급망"},
            {"code":"CN","name":"중국","role":"수요·규제대상","channel":"수출규제"},
            {"code":"TW","name":"대만","role":"파운드리","channel":"공급망"},
        ],
        "key_indicators": ["수입물가_실리콘웨이퍼","수입물가_웨이퍼가공장비"],
        "validation": "수출규제→수출 lag2 r=0.512",
    },
}

def get_current_stage(industry, ew_data, ecos_current, score=None, top_risk=None):
    # 등급은 산업카드(industry_risk_score.csv)와 동일한 기준(60/35)으로 통일
    if score is not None:
        grade = "🔴" if score >= 60 else "🟡" if score >= 35 else "🟢"
    else:
        grade = "🟢"

    alerts = ew_data.get("alerts", [])
    stage, detail = None, ""

    if industry == "배터리":
        mat = [a for a in alerts if any(k in a.get("indicator","") for k in ["니켈","흑연","구리"])]
        cap = [a for a in alerts if "배터리_가동률" in a.get("indicator","")]
        if mat:
            stage = "원자재 압박 진행 중"
            detail = mat[0].get("message","")[:30]
        elif cap:
            stage = "가동률 하락 중"
    elif industry == "철강":
        mat = [a for a in alerts if any(k in a.get("indicator","") for k in ["철광석","유연탄","PPI_열연"])]
        if mat:
            stage, detail = "생산원가 상승 중", mat[0].get("message","")[:30]
    elif industry == "자동차":
        tar = [a for a in alerts if "관세" in a.get("type","")]
        pol = sorted(
            [a for a in alerts if a.get("type")=="정치리스크"
             and a.get("indicator","").split(" ")[0] in ("US","MX","DE")],
            key=lambda a: a.get("value", 0), reverse=True
        )
        risk_label = top_risk if top_risk else "통상리스크"
        if tar:
            stage, detail = "관세 압박 지속", "미국 25% 관세 영향"
        elif pol:
            country = pol[0].get("indicator","").split(" ")[0]
            country_name = {"US":"미국","MX":"멕시코","DE":"독일"}.get(country, country)
            stage = f"{country_name}발 {risk_label} 모니터링"
            detail = pol[0].get("message","")[:30]
    elif industry == "반도체":
        reg = [a for a in alerts if "수출규제" in a.get("type","")]
        pol = sorted(
            [a for a in alerts if a.get("type")=="정치리스크"
             and a.get("indicator","").split(" ")[0] in ("US","NL","CN","TW")],
            key=lambda a: a.get("value", 0), reverse=True
        )
        risk_label = top_risk if top_risk else "통상리스크"
        if reg:
            stage = "수출규제 모니터링"
        elif pol:
            country = pol[0].get("indicator","").split(" ")[0]
            country_name = {"US":"미국","NL":"네덜란드","CN":"중국","TW":"대만"}.get(country, country)
            stage = f"{country_name}발 {risk_label} 모니터링"
            detail = pol[0].get("message","")[:30]

    # alerts 기반 구체적 단계가 없으면 점수대에 맞는 일반 문구로 대체
    if stage is None:
        if grade == "🔴":
            stage = "위험 수준 지속"
        elif grade == "🟡":
            stage = "모니터링 필요"
        else:
            stage = "정상"

    return {"grade": grade, "stage": stage, "detail": detail}

def build_json():
    score_df = pd.read_csv(MASTER / "industry_risk_score.csv")
    news_df  = pd.read_csv(MASTER / "news_analyzed.csv")
    trend_df = pd.read_csv(MASTER / "risk_trend.csv")

    scores = {}
    for _, row in score_df.iterrows():
        scores[row["industry"]] = {
            "score": round(float(row["risk_score"]),1),
            "grade": str(row["risk_grade"]),
            "top_risk": str(row["top_risk"]),
            "news_count": int(row["news_count"]),
            "관세리스크": int(row.get("관세리스크",0)),
            "공급망리스크": int(row.get("공급망리스크",0)),
            "수출규제리스크": int(row.get("수출규제리스크",0)),
            "원자재리스크": int(row.get("원자재리스크",0)),
            "지정학리스크": int(row.get("지정학리스크",0)),
        }

    risk_dist = {}
    for col in ["관세리스크","공급망리스크","수출규제리스크","원자재리스크","지정학리스크"]:
        if col in score_df.columns:
            risk_dist[col] = int(score_df[col].sum())

    trend_df = trend_df.sort_values("month")
    risk_cols = ["관세리스크","원자재리스크","공급망리스크","지정학리스크","수출규제리스크"]
    trend = {"months": trend_df["month"].tolist()}
    for col in risk_cols:
        trend[col] = trend_df[col].fillna(0).astype(int).tolist() if col in trend_df.columns else [0]*len(trend_df)

    news_list = []
    risk_news = news_df[news_df["primary_risk"]!="기타"].copy().sort_values("date",ascending=False)
    for cat in ["관세리스크","원자재리스크","공급망리스크","지정학리스크","수출규제리스크"]:
        for _, row in risk_news[risk_news["primary_risk"]==cat].head(2).iterrows():
            title = str(row["title"])
            for ent in ["&middot;","&amp;","&lt;","&gt;","&#39;"]:
                title = title.replace(ent," ")
            news_list.append({"title":title[:70],"date":str(row["date"]),"country":str(row["country"]),"risk":str(row["primary_risk"])})

    strategies = get_all_strategies(scores)
    pred_df = run_predictor(str(MASTER), str(MASTER))
    predictions = {}
    for _, row in pred_df.iterrows():
        predictions[row["industry"]] = {
            "current": float(row["current_score"]),
            "pred_1m": float(row["pred_1m"]),
            "pred_2m": float(row["pred_2m"]),
            "pred_3m": float(row["pred_3m"]),
            "direction": str(row["direction"]),
            "change_str": str(row["change_str"]),
            "future_months": row["future_months"] if isinstance(row["future_months"],list) else [],
        }

    ew_data = json.load(open(MASTER/"early_warning.json",encoding="utf-8")) if (MASTER/"early_warning.json").exists() else {}
    pol_ts_data = json.load(open(MASTER/"country_pol_timeseries.json",encoding="utf-8")) if (MASTER/"country_pol_timeseries.json").exists() else {}

    ecos_files = sorted(glob.glob(str(BASE/"data/raw/ecos/*.csv")))
    ecos_current = {}
    if ecos_files:
        ecos_df = pd.read_csv(ecos_files[-1], encoding="utf-8-sig")
        for col in ecos_df.columns:
            if col != "month":
                try:
                    # 최신 월이 NaN(미발표)일 수 있으므로 NaN 제외 후 마지막 유효값 사용
                    valid = ecos_df[col].dropna()
                    if len(valid) > 0:
                        ecos_current[col] = round(float(valid.iloc[-1]), 1)
                except:
                    pass

    industry_flow = {}
    for industry, structure in INDUSTRY_STRUCTURE.items():
        industry_score = scores.get(industry, {}).get("score")
        industry_top_risk = scores.get(industry, {}).get("top_risk")
        current_stage = get_current_stage(industry, ew_data, ecos_current, industry_score, industry_top_risk)
        ts_records = pol_ts_data.get("timeseries",[])
        ts_df = pd.DataFrame(ts_records) if ts_records else pd.DataFrame()

        country_status = []
        for country in structure["key_countries"]:
            code = country["code"]
            status = {**country, "score":0,"change":0,"grade":"🟢","keywords":""}
            if not ts_df.empty:
                c_df = ts_df[ts_df["country_code"]==code].sort_values("month")
                recent = c_df[c_df["month"]<"2026-06"].tail(1)
                if not recent.empty:
                    row = recent.iloc[0]
                    score  = float(row.get("pol_score",0))
                    change = float(row.get("score_change",0)) if not pd.isna(row.get("score_change")) else 0
                    status.update({
                        "score": round(score,1), "change": round(change,1),
                        "grade": "🔴" if score>80 else "🟡" if score>30 else "🟢",
                        "keywords": str(row.get("keywords",""))
                    })
            country_status.append(status)

        indicators = {ind: ecos_current[ind] for ind in structure["key_indicators"] if ind in ecos_current}

        industry_flow[industry] = {
            "type": structure["type"],
            "description": structure["description"],
            "flow": structure["flow"],
            "current_stage": current_stage,
            "countries": country_status,
            "indicators": indicators,
            "validation": structure["validation"],
        }

    total = len(news_df)
    stats = {
        "total_news": total,
        "classified_rate": round((news_df["primary_risk"]!="기타").sum()/total*100,1),
        "countries": int(news_df["country"].nunique()),
        "updated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "data_period": "2022.01 ~ 2026.06",
    }

    # 지역 산업 영향 분석 결과 로드
    region_analysis = {}
    region_path = MASTER / "region_hypothesis_results.json"
    if region_path.exists():
        with open(region_path, encoding="utf-8") as f:
            region_data = json.load(f)
        # 대시보드용 핵심 데이터만 추출
        best = region_data.get("best_per_industry", {})
        region_analysis = {
            "배터리": {
                "cluster": "충북 오창·충남 천안",
                "r_pct": best.get("배터리", {}).get("abs_pct", 55.7),
                "lag": best.get("배터리", {}).get("lag", 12),
                "risk_col": best.get("배터리", {}).get("risk_col", "관세리스크"),
                "description": "관세리스크 감지 → 12개월 후 배터리 가동률 하락",
            },
            "철강": {
                "cluster": "경북 포항·전남 광양",
                "r_pct": best.get("철강", {}).get("abs_pct", 65.3),
                "lag": best.get("철강", {}).get("lag", 6),
                "risk_col": best.get("철강", {}).get("risk_col", "관세리스크"),
                "description": "관세리스크 감지 → 6개월 후 경북 수출 감소",
            },
            "자동차": {
                "cluster": "울산·경기 평택",
                "r_pct": best.get("자동차", {}).get("abs_pct", 78.4),
                "lag": best.get("자동차", {}).get("lag", 6),
                "risk_col": best.get("자동차", {}).get("risk_col", "관세리스크"),
                "description": "관세리스크 감지 → 6개월 내 선수출 급증 후 감소",
            },
            "반도체": {
                "cluster": "경기 용인·평택·충남 천안",
                "r_pct": best.get("반도체", {}).get("abs_pct", 48.5),
                "lag": best.get("반도체", {}).get("lag", 3),
                "risk_col": best.get("반도체", {}).get("risk_col", "관세리스크"),
                "description": "관세리스크 감지 → 3개월 후 충남 수출 감소",
            },
        }

    result = {
        "scores": scores, "risk_dist": risk_dist, "trend": trend,
        "news": news_list, "strategies": strategies, "predictions": predictions,
        "industry_flow": industry_flow, "stats": stats,
        "region_analysis": region_analysis,
    }

    out = Path(__file__).parent / "dashboard_data.json"
    result = fix_nan(result)
    with open(out,"w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print("✅ dashboard_data.json 생성 완료")

    dashboard_dir = Path(__file__).parent
    for fname in ["early_warning.json","support_programs.json","industry_risk_matrix.json",
                  "political_risk.json","spillover_analysis.json","korisk_cli.json",
                  "anomaly_detection.json","network_analysis.json","country_pol_timeseries.json"]:
        src = MASTER/fname
        if src.exists():
            shutil.copy(src, dashboard_dir/fname)
            print(f"✅ {fname} 복사 완료")

if __name__ == "__main__":
    build_json()
