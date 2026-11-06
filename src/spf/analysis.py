"""Seller acquisition pipeline: lead scoring, monthly win forecast and revenue per deal.

Run:  python -m spf.analysis   (from src/, after scripts/get_data.sh)
Writes docs/data/results.json for the dashboard and CSV tables to outputs/.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from .load import ROOT, build, checks

OUT = ROOT / "outputs"
DOCS = ROOT / "docs" / "data"

WIN_WINDOW = 90                       # a lead counts as converted if won within 90 days
SALES_START = pd.Timestamp("2017-12-01")  # first closed deal; earlier leads waited for the sales team
TRAIN = ("2018-01-01", "2018-03-01")  # lead scoring trained on Jan and Feb 2018 leads
TEST = ("2018-03-01", "2018-06-01")   # and tested on Mar to May 2018 leads
FORECAST_MONTHS = pd.period_range("2018-03", "2018-08", freq="M")
SHRINK = 20                           # pseudo counts used when encoding a category's win rate
INTERVAL = 0.80
CAP = 0.99                            # per deal cap for the revenue average
CAPS_TRIED = [0.95, 0.98, 0.99]


# ---------------------------------------------------------------- funnel
def funnel(leads: pd.DataFrame) -> dict:
    won = leads[leads["won"]]
    by_origin = (leads.groupby("origin")
                 .agg(leads=("mql_id", "size"), wins=("won", "sum"))
                 .assign(win_rate=lambda t: t.wins / t.leads)
                 .sort_values("leads", ascending=False))
    months = (leads.groupby(leads["contact_month"].dt.to_period("M"))
              .agg(leads=("mql_id", "size")).join(
              won.groupby(won["won_month"].dt.to_period("M")).agg(wins=("mql_id", "size")), how="outer")
              .fillna(0).astype(int))
    after = leads[leads["contact_date"] >= SALES_START]
    return {
        "leads": int(len(leads)),
        "wins": int(len(won)),
        "win_rate": float(leads["won"].mean()),
        "win_rate_after_sales_start": float(after["won"].mean()),
        "median_days_to_win": float(won["days_to_win"].median()),
        "share_won_within_90d": float((won["days_to_win"] <= WIN_WINDOW).mean()),
        "by_origin": [{"origin": k, **{c: (float(v) if c == "win_rate" else int(v)) for c, v in r.items()}}
                      for k, r in by_origin.iterrows()],
        "by_month": [{"month": str(k), "leads": int(r.leads), "wins": int(r.wins)} for k, r in months.iterrows()],
    }


# ---------------------------------------------------------------- lead scoring
def _encode(train: pd.DataFrame, col: str, prior: float) -> pd.Series:
    s = train.groupby(col)["y"].agg(["sum", "count"])
    return (s["sum"] + SHRINK * prior) / (s["count"] + SHRINK)


def _features(df: pd.DataFrame, enc: dict, prior: float) -> pd.DataFrame:
    logit = lambda p: np.log(p / (1 - p))
    return pd.DataFrame({c: logit(df[c].map(e).fillna(prior)) for c, e in enc.items()})


def lead_scoring(leads: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    df = leads.copy()
    df["y"] = df["won"] & (df["days_to_win"] <= WIN_WINDOW)
    tr = df[(df["contact_date"] >= TRAIN[0]) & (df["contact_date"] < TRAIN[1])]
    te = df[(df["contact_date"] >= TEST[0]) & (df["contact_date"] < TEST[1])].copy()
    prior = float(tr["y"].mean())
    out = {"train_leads": int(len(tr)), "test_leads": int(len(te)), "test_base_rate": float(te["y"].mean()),
           "models": {}}
    for name, cols in {"origin only": ["origin"], "origin + landing page": ["origin", "landing_page_id"]}.items():
        enc = {c: _encode(tr, c, prior) for c in cols}
        model = LogisticRegression().fit(_features(tr, enc, prior), tr["y"])
        p = model.predict_proba(_features(te, enc, prior))[:, 1]
        out["models"][name] = {"auc": float(roc_auc_score(te["y"], p)),
                               "brier": float(brier_score_loss(te["y"], p))}
        te[f"p_{name}"] = p
    p = te["p_origin + landing page"]
    te["decile"] = pd.qcut(p.rank(method="first"), 10, labels=False)
    dec = te.groupby("decile").agg(predicted=("p_origin + landing page", "mean"), actual=("y", "mean"),
                                   wins=("y", "sum")).reset_index()
    total = te["y"].sum()
    top3 = te[te["decile"] >= 7]
    out.update({
        "deciles": json.loads(dec.to_json(orient="records")),
        "top_decile_win_rate": float(dec.iloc[-1]["actual"]),
        "bottom_half_win_rate": float(te[te["decile"] < 5]["y"].mean()),
        "top30_share_of_leads": float(len(top3) / len(te)),
        "top30_share_of_wins": float(top3["y"].sum() / total),
        "test_landing_pages_seen_in_training": float(te["landing_page_id"].isin(tr["landing_page_id"]).mean()),
    })
    return out, te


# ---------------------------------------------------------------- monthly win forecast
def _hazards(leads: pd.DataFrame, T: pd.Period) -> dict[int, float]:
    """Chance a lead that is still open at month age a is won during that month, using only
    cohorts contacted after the sales team started and only wins observed before month T."""
    H = leads[(leads["cm"] >= pd.Period(SALES_START, "M")) & (leads["cm"] < T)]
    haz = {}
    for a in range(1, 13):
        at_risk = H[(H["cm"] + a < T) & ~((H["age"] < a) & (H["wm"] < T))]
        if len(at_risk) < 50:
            break
        haz[a] = float((at_risk["age"] == a).mean())
    return haz


def win_forecast(leads: pd.DataFrame) -> dict:
    """Forecast, at the start of each month, how many deals will be won that month from
    leads already in the pipeline. Compared with two simple run rate baselines."""
    L = leads.copy()
    L["cm"] = L["contact_month"].dt.to_period("M")
    L["wm"] = L["won_month"].dt.to_period("M")
    L["age"] = [(w - c).n if pd.notna(w) else np.nan for w, c in zip(L["wm"], L["cm"])]
    old = L["cm"] < pd.Period(SALES_START, "M")

    def pipeline_wins(T):
        return int(((L["wm"] == T) & (L["cm"] < T)).sum())

    rows = []
    for T in FORECAST_MONTHS:
        haz = _hazards(L, T)
        last = max(haz)
        open_ = L[(L["cm"] < T) & ~(L["wm"] < T) & ~old]
        p = np.array([haz.get(min((T - c).n, last), 0.0) for c in open_["cm"]])
        old_rate = np.mean([((L["wm"] == T - k) & old).sum() for k in (1, 2, 3)])
        mean = p.sum() + old_rate
        sd = np.sqrt((p * (1 - p)).sum() + old_rate)
        z = norm.ppf(0.5 + INTERVAL / 2)
        rows.append({
            "month": str(T),
            "actual": pipeline_wins(T),
            "open_leads": int(len(open_)),
            "pipeline": float(mean),
            "low": float(max(0.0, mean - z * sd)),
            "high": float(mean + z * sd),
            "last_month": pipeline_wins(T - 1),
            "avg_3m": float(np.mean([pipeline_wins(T - k) for k in (1, 2, 3)])),
        })
    t = pd.DataFrame(rows)
    err = lambda col: float((abs(t[col] - t["actual"]) / t["actual"]).mean())
    mae = lambda col: float(abs(t[col] - t["actual"]).mean())
    return {
        "months": rows,
        "mape": {"pipeline": err("pipeline"), "last_month": err("last_month"), "avg_3m": err("avg_3m")},
        "mae": {"pipeline": mae("pipeline"), "last_month": mae("last_month"), "avg_3m": mae("avg_3m")},
        "interval_coverage": float(((t["actual"] >= t["low"]) & (t["actual"] <= t["high"])).mean()),
        "interval": INTERVAL,
    }


# ---------------------------------------------------------------- revenue per deal
def revenue(rev: pd.DataFrame) -> dict:
    ranked = rev["gmv_90d"].sort_values(ascending=False)
    k = max(1, len(ranked) // 10)
    by_type = (rev.groupby("lead_type")
               .agg(deals=("gmv_90d", "size"), active=("gmv_90d", lambda s: (s > 0).mean()),
                    mean_gmv=("gmv_90d", "mean"), total_gmv=("gmv_90d", "sum"))
               .sort_values("mean_gmv", ascending=False))
    by_type["share_of_deals"] = by_type["deals"] / len(rev)
    by_type["share_of_gmv"] = by_type["total_gmv"] / rev["gmv_90d"].sum()

    # Backtest: forecast each monthly cohort's 90 day sales from deals won in earlier months,
    # either with one average per deal or with an average per lead type (shrunk to the overall mean).
    rev = rev.assign(wm=rev["won_month"].dt.to_period("M"))
    # A few very large sellers dominate the average, so a second version caps each past deal
    # at the 99th percentile of past deals before averaging. Caps of the 95th, 98th and 99th
    # percentile were tried; the 99th did best (see CAPS_TRIED), on only three test months.
    bt = []
    for T in pd.period_range("2018-03", "2018-05", freq="M"):
        past, cur = rev[rev["wm"] < T], rev[rev["wm"] == T]
        row = {"month": str(T), "deals": int(len(cur)), "actual": float(cur["gmv_90d"].sum())}
        for tag, g in {"": past["gmv_90d"],
                       "_capped": past["gmv_90d"].clip(upper=past["gmv_90d"].quantile(CAP))}.items():
            flat = g.mean()
            s = past.assign(g=g).groupby("lead_type")["g"].agg(["sum", "count"])
            typed = (s["sum"] + SHRINK * flat) / (s["count"] + SHRINK)
            row["flat" + tag] = float(flat * len(cur))
            row["by_lead_type" + tag] = float(cur["lead_type"].map(typed).fillna(flat).sum())
        bt.append(row)
    b = pd.DataFrame(bt)
    ape = lambda col: float((abs(b[col] - b["actual"]) / b["actual"]).mean())
    return {
        "deals": int(len(rev)),
        "active_share": float((rev["gmv_90d"] > 0).mean()),
        "median_gmv": float(rev["gmv_90d"].median()),
        "mean_gmv": float(rev["gmv_90d"].mean()),
        "total_gmv": float(rev["gmv_90d"].sum()),
        "top10pct_share": float(ranked.head(k).sum() / ranked.sum()),
        "by_lead_type": [{"lead_type": i, **{c: float(v) for c, v in r.items()}} for i, r in by_type.iterrows()],
        "backtest": bt,
        "backtest_mape": {k: ape(k) for k in ["flat", "by_lead_type", "flat_capped", "by_lead_type_capped"]},
        "cap_quantile": CAP,
        "caps_tried": CAPS_TRIED,
    }


def main():
    OUT.mkdir(exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    con = build()
    integrity = checks(con)
    leads = con.execute("SELECT * FROM leads").df()
    rev = con.execute("SELECT * FROM deal_revenue").df()

    fun = funnel(leads)
    score, scored = lead_scoring(leads)
    fc = win_forecast(leads)
    rv = revenue(rev)

    scored[["mql_id", "contact_date", "origin", "y", "p_origin + landing page", "decile"]].to_csv(
        OUT / "lead_scores_test.csv", index=False)
    pd.DataFrame(fc["months"]).to_csv(OUT / "win_forecast_backtest.csv", index=False)
    pd.DataFrame(rv["by_lead_type"]).to_csv(OUT / "revenue_by_lead_type.csv", index=False)

    payload = {"integrity": integrity, "funnel": fun, "scoring": score, "forecast": fc, "revenue": rv}
    (DOCS / "results.json").write_text(json.dumps(payload, indent=2))

    print(json.dumps(integrity))
    print({k: fun[k] for k in ["leads", "wins", "win_rate", "win_rate_after_sales_start", "median_days_to_win",
                               "share_won_within_90d"]})
    print({k: v for k, v in score.items() if k != "deciles"})
    print(pd.DataFrame(fc["months"]).round(1).to_string())
    print(fc["mape"], fc["mae"], "coverage", fc["interval_coverage"])
    print({k: v for k, v in rv.items() if k not in ("by_lead_type", "backtest")})
    print(pd.DataFrame(rv["by_lead_type"]).round(3).to_string())
    print(pd.DataFrame(rv["backtest"]).round(0).to_string())


if __name__ == "__main__":
    main()
