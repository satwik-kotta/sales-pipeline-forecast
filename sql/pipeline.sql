-- Builds the analysis tables from the raw Olist CSVs (loaded by src/spf/load.py as
-- raw_mql, raw_deals, raw_orders, raw_items).

-- One row per marketing qualified lead, with its outcome if it was won.
CREATE OR REPLACE TABLE leads AS
SELECT
    m.mql_id,
    CAST(m.first_contact_date AS DATE)                          AS contact_date,
    DATE_TRUNC('month', CAST(m.first_contact_date AS DATE))     AS contact_month,
    m.landing_page_id,
    COALESCE(m.origin, 'missing')                               AS origin,
    d.seller_id,
    d.sr_id,
    d.sdr_id,
    CAST(d.won_date AS TIMESTAMP)                               AS won_at,
    DATE_TRUNC('month', CAST(d.won_date AS TIMESTAMP))          AS won_month,
    d.business_segment,
    COALESCE(d.lead_type, 'unknown')                            AS lead_type,
    d.declared_monthly_revenue,
    d.mql_id IS NOT NULL                                        AS won,
    DATE_DIFF('day', CAST(m.first_contact_date AS DATE), CAST(d.won_date AS DATE)) AS days_to_win
FROM raw_mql m
LEFT JOIN raw_deals d USING (mql_id);

-- Seller sales: every order item that was not canceled or unavailable.
CREATE OR REPLACE TABLE seller_sales AS
SELECT
    i.seller_id,
    CAST(o.order_purchase_timestamp AS TIMESTAMP) AS purchased_at,
    i.price
FROM raw_items i
JOIN raw_orders o USING (order_id)
WHERE o.order_status NOT IN ('canceled', 'unavailable');

-- First 90 days of sales for every won seller whose 90 day window ends before the
-- order data does (orders are complete through August 2018).
CREATE OR REPLACE TABLE deal_revenue AS
SELECT
    l.mql_id,
    l.seller_id,
    l.won_month,
    l.lead_type,
    l.business_segment,
    l.origin,
    COALESCE(SUM(s.price), 0)        AS gmv_90d,
    COUNT(s.price)                   AS items_90d
FROM leads l
LEFT JOIN seller_sales s
       ON s.seller_id = l.seller_id
      AND s.purchased_at >= l.won_at
      AND s.purchased_at <  l.won_at + INTERVAL 90 DAY
WHERE l.won
  AND l.won_at + INTERVAL 90 DAY <= TIMESTAMP '2018-09-01'
GROUP BY ALL;
