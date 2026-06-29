"""export_dashboard_data.py - CSV + 전략 → dashboard_data.json"""
import json, sys, shutil, glob
from pathlib import Path
import pandas as pd

BASE   = Path(__file__).parent.parent
MASTER = BASE / "data" / "master"
sys.path.insert(0, str(BASE))

from analyzer.strategy_recommender import get_all_strategies
from analyzer.risk_predictor import run_predictor

INDUSTRY_STRUCTURE = {
    "배터리": {
        "type": "원가형",
        "description": "원가가 수익성을 결정하는 산업",
        "flow": ["국제정세", "원자재가격", "생산원가", "가동률", "수출"],
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
        "flow": ["국제정세", "철광석·유연탄가격", "생산원가", "생산능력", "수출"],
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
        "flow": ["국제정세", "관세·경기변화", "소비수요", "생산조정", "수출"],
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
        "flow": ["국제정세", "수출규제발표", "공급망차질", "재고조정", "수출변화"],
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

def get_current_stage(industry, ew_data, ecos_current):
    alerts = ew_data.get("alerts", [])
    grade, stage, detail = "🟢", "정상", ""
    if industry == "배터리":
        mat = [a for a in alerts if any(k in a.get("indicator","") for k in ["니켈","흑연","구리"])]
        cap = [a for a in alerts if "배터리_가동률" in a.get("indicator","")]
        if mat:
            grade, stage = "🔴", "원자재 압박 진행 중"
            detail = mat[0].get("message","")[:30]
        elif cap:
            grade, stage = "🟡", "가동률 하락 중"
    elif industry == "철강":
        mat = [a for a in alerts if any(k in a.get("indicator","") for k in ["철광석","유연탄","PPI_열연"])]
        if mat:
            grade = "🔴" if any("위험" in a.get("grade","") for a in mat) else "🟡"
            stage, detail = "생산원가 상승 중", mat[0].get("message","")[:30]
    elif industry == "자동차":
        tar = [a for a in alerts if "관세" in a.get("type","")]
        if tar:
            grade, stage, detail = "🟡", "관세 압박 지속", "미국 25% 관세 영향"
    elif industry == "반도체":
        reg = [a for a in alerts if "수출규제" in a.get("type","")]
        if reg:
            grade, stage = "🟡", "수출규제 모니터링"
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
        latest = ecos_df.iloc[-1]
        for col in ecos_df.columns:
            if col != "month":
                try:
                    ecos_current[col] = round(float(latest[col]),1)
                except:
                    pass

    industry_flow = {}
    for industry, structure in INDUSTRY_STRUCTURE.items():
        current_stage = get_current_stage(industry, ew_data, ecos_current)
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

    result = {
        "scores": scores, "risk_dist": risk_dist, "trend": trend,
        "news": news_list, "strategies": strategies, "predictions": predictions,
        "industry_flow": industry_flow, "stats": stats,
    }

    out = Path(__file__).parent / "dashboard_data.json"
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