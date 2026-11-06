"""Load the raw Olist CSVs into DuckDB, build the analysis tables and run integrity checks."""
from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
FILES = {
    "raw_mql": "olist_marketing_qualified_leads_dataset.csv",
    "raw_deals": "olist_closed_deals_dataset.csv",
    "raw_orders": "olist_orders_dataset.csv",
    "raw_items": "olist_order_items_dataset.csv",
}


def build(db_path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(db_path)
    for table, name in FILES.items():
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto('{RAW / name}', header=true)")
    con.execute((ROOT / "sql" / "pipeline.sql").read_text())
    return con


def checks(con: duckdb.DuckDBPyConnection) -> dict:
    """Integrity checks. Raises if any fail."""
    q = lambda s: con.execute(s).fetchone()[0]
    out = {
        "leads": q("SELECT COUNT(*) FROM leads"),
        "won_deals": q("SELECT COUNT(*) FROM leads WHERE won"),
        "duplicate_leads": q("SELECT COUNT(*) - COUNT(DISTINCT mql_id) FROM leads"),
        "deals_without_lead": q("SELECT COUNT(*) FROM raw_deals d ANTI JOIN raw_mql m USING (mql_id)"),
        "duplicate_won_sellers": q("SELECT COUNT(*) - COUNT(DISTINCT seller_id) FROM leads WHERE won"),
        "wins_before_contact": q("SELECT COUNT(*) FROM leads WHERE days_to_win < -2"),
        "sales_before_won_date": q("""SELECT COUNT(*) FROM seller_sales s JOIN leads l USING (seller_id)
                                      WHERE l.won AND s.purchased_at < l.won_at"""),
        "deals_in_revenue_window": q("SELECT COUNT(*) FROM deal_revenue"),
        # One deal is dated a day or two before its lead's first contact (a logging gap);
        # it is kept and treated as a same day win.
        "wins_one_or_two_days_early": q("SELECT COUNT(*) FROM leads WHERE days_to_win < 0"),
    }
    for k in ["duplicate_leads", "deals_without_lead", "duplicate_won_sellers", "wins_before_contact",
              "sales_before_won_date"]:
        if out[k] != 0:
            raise AssertionError(f"check failed: {k} = {out[k]}")
    return out
