# Sales Pipeline & Revenue Forecast

**Question.** A marketplace's sales team signs new sellers from a pipeline of qualified leads. Finance needs to know how many sellers the team will sign next month, which leads to work first, and how much the new sellers will actually sell.

**Data.** Olist's public seller acquisition funnel: 8,000 marketing qualified leads from June 2017 to May 2018 and the 842 deals the sales team closed. These are joined by seller to Olist's public order data to get each new seller's real sales. Olist is a Brazilian marketplace, so sales are in Brazilian reais (R$).

[Dashboard](https://satwik-kotta.github.io/sales-pipeline-forecast/) · [One page memo](https://satwik-kotta.github.io/sales-pipeline-forecast/memo.html)

## What it found

- **Pipeline forecast.** Each open lead gets the win chance seen for leads of its age. This forecast missed the monthly deal count by **25%** on average from March to August 2018, against **28%** for repeating last month and **45%** for a three month average.
- **Lead score.** The score was trained on January and February leads and tested on March to May leads. It reached an AUC of 0.67. The top 30% of leads by score held **54% of wins**, and the top tenth converted at **20.4%** against an 11.0% average.
- **Deals are not revenue.** **57%** of new sellers sold nothing in their first 90 days. The top 10% made **81%** of new seller sales. Big online sellers were 15% of deals but 47% of sales.
- **Revenue forecast.** A plain average per deal overshot each month's new seller sales by **69%**, because a few very large sellers pulled it up. Capping each past deal at the 99th percentile cut the error to **17%**.

## How it works

1. **SQL tables** (`sql/pipeline.sql`, DuckDB) build three tables:
   - one row per lead with its outcome and days to win
   - seller sales, excluding canceled and unavailable orders
   - each signed seller's first 90 days of sales, kept only where that window ends before the order data does (August 2018)
2. **Integrity checks** (`src/spf/load.py`) fail the build on duplicate leads, deals without a lead, duplicate sellers, wins before first contact, or sales dated before a seller was signed.
3. **Lead score.** A logistic regression on traffic source and landing page. Each is encoded as a win rate shrunk toward the average with 20 pseudo counts, so a page with two leads and two wins is not read as 100%. A win means a deal within 90 days.
4. **Pipeline forecast.** At the start of each month it estimates the chance that a lead still open at age *a* (months since first contact) is won that month. It uses only cohorts contacted after the sales team started and only wins observed before that month. Leads from before the sales team started are forecast with their own three month average.
5. **Revenue forecast.** Each month's new sellers are forecast from sellers signed in earlier months, with a plain or capped average per deal, overall or by lead type.

## Honest limits

- **The forecast range is too narrow.** The 80% range from the pipeline model held the actual in only 2 of 6 months. Month to month swings are wider than a sum of independent leads implies. A rep capacity term is the obvious next step, since open leads more than doubled from March to June while monthly wins did not.
- **The cap was chosen after seeing the results.** I tried caps at the 95th, 98th and 99th percentile and the 99th did best, on only three test months. Treat 17% as optimistic.
- **Lead type is only known after a deal closes.** The sales rep records it at close, so it helps plan revenue for signed deals but cannot score new leads.
- **This is a narrow sample.** It covers one company and one year, and the leads are a sample of Olist's leads. The lead data ends in May 2018, so wins from June on come only from older leads.

## Run it

```bash
pip install -r requirements.txt
bash scripts/get_data.sh            # copies the public CSVs into data/raw
cd src && python -m spf.analysis    # writes docs/data/results.json and outputs/*.csv
cd .. && pytest -q tests
```

## Layout

```
sql/pipeline.sql      leads, seller sales and 90 day revenue tables (DuckDB)
src/spf/load.py       loads the CSVs, builds the tables, runs integrity checks
src/spf/analysis.py   funnel, lead score, monthly deal forecast, revenue per deal
docs/                 dashboard (ECharts) and memo, served with GitHub Pages
outputs/              lead scores, forecast backtest and revenue tables as CSV
tests/                planted data tests for the SQL, the encoding and the forecast's no look ahead rule
```

Data: Olist, Marketing Funnel and Brazilian E-Commerce public datasets, CC BY-NC-SA 4.0.
