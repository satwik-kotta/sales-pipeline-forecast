#!/usr/bin/env bash
# Downloads the public Olist marketing funnel and e-commerce datasets (CC BY-NC-SA 4.0,
# originally published by Olist on Kaggle) from a GitHub mirror into data/raw/.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw
if [ ! -d data/mirror ]; then
  git clone --depth 1 https://github.com/panda-nut/Marketing-Funnel-by-Olist.git data/mirror
fi
for f in olist_marketing_qualified_leads_dataset olist_closed_deals_dataset \
         olist_orders_dataset olist_order_items_dataset olist_sellers_dataset; do
  cp "data/mirror/Kaggle_statistcs/$f.csv" data/raw/
done
ls -la data/raw
