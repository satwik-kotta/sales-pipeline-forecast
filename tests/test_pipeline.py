import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spf import analysis  # noqa: E402


def tiny_db():
    """Three leads, two won, with planted sales inside and outside the 90 day window."""
    con = duckdb.connect()
    con.execute("""CREATE TABLE raw_mql AS SELECT * FROM (VALUES
        ('a', '2018-01-10', 'p1', 'organic_search'),
        ('b', '2018-01-20', 'p2', NULL),
        ('c', '2018-02-01', 'p1', 'social')) t(mql_id, first_contact_date, landing_page_id, origin)""")
    con.execute("""CREATE TABLE raw_deals AS SELECT * FROM (VALUES
        ('a', 's1', 'sdr1', 'sr1', '2018-01-15 10:00:00', 'pet', 'online_big', 0.0),
        ('b', 's2', 'sdr1', 'sr2', '2018-03-01 10:00:00', 'pet', NULL, 0.0))
        t(mql_id, seller_id, sdr_id, sr_id, won_date, business_segment, lead_type, declared_monthly_revenue)""")
    con.execute("""CREATE TABLE raw_orders AS SELECT * FROM (VALUES
        ('o1', '2018-02-01 00:00:00', 'delivered'),
        ('o2', '2018-02-02 00:00:00', 'canceled'),
        ('o3', '2018-06-01 00:00:00', 'delivered'),
        ('o4', '2018-03-05 00:00:00', 'delivered'))
        t(order_id, order_purchase_timestamp, order_status)""")
    con.execute("""CREATE TABLE raw_items AS SELECT * FROM (VALUES
        ('o1', 's1', 100.0), ('o2', 's1', 999.0), ('o3', 's1', 50.0), ('o4', 's2', 20.0))
        t(order_id, seller_id, price)""")
    con.execute((ROOT / "sql" / "pipeline.sql").read_text())
    return con


def test_leads_table():
    con = tiny_db()
    rows = con.execute("SELECT mql_id, won, days_to_win, origin, lead_type FROM leads ORDER BY mql_id").fetchall()
    assert rows == [("a", True, 5, "organic_search", "online_big"),
                    ("b", True, 40, "missing", "unknown"),
                    ("c", False, None, "social", "unknown")]


def test_revenue_window_excludes_canceled_and_late_sales():
    con = tiny_db()
    rev = dict(con.execute("SELECT seller_id, gmv_90d FROM deal_revenue").fetchall())
    # s1: 100 inside the window, the canceled 999 and the sale 137 days later are excluded
    assert rev["s1"] == pytest.approx(100.0)
    assert rev["s2"] == pytest.approx(20.0)


def test_encoding_shrinks_small_groups_to_prior():
    tr = pd.DataFrame({"g": ["x"] * 2 + ["y"] * 200, "y": [1, 1] + [0] * 200})
    enc = analysis._encode(tr, "g", prior=0.1)
    # two wins out of two should not be read as a 100% win rate
    assert enc["x"] == pytest.approx((2 + 20 * 0.1) / (2 + 20))
    assert enc["x"] < 0.2
    assert enc["y"] < 0.1


def test_hazards_use_only_wins_before_forecast_month():
    leads = pd.DataFrame({
        "cm": [pd.Period("2018-01", "M")] * 100,
        "wm": [pd.Period("2018-02", "M")] * 10 + [pd.Period("2018-05", "M")] * 5 + [pd.NaT] * 85,
    })
    leads["age"] = [(w - c).n if pd.notna(w) else float("nan") for w, c in zip(leads["wm"], leads["cm"])]
    haz = analysis._hazards(leads, pd.Period("2018-04", "M"))
    assert haz[1] == pytest.approx(0.10)
    # the wins in May are after the forecast month and must not leak in
    assert haz[2] == pytest.approx(0.0)
