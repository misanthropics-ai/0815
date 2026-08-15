# Real product sources

擷取時間：2026-08-15 02:47 UTC。所有 `evidence` 都是同檔 `raw_text` 的逐字子字串，並由 `demo/validate_demo_data.py` 自動檢查。價格與產品頁會變動，正式上台前需重抓一次。

| product_ref | 官方來源 | 人工 QA | 重要備註 |
|---|---|---|---|
| `cabinzero-classic-36l@v1` | [CabinZero Classic 36L](https://www.cabinzero.com/products/classic-cabin-backpack-36l) | PASS | 現行頁為 700g、45×31×20cm；已寫 shoulder strap airflow，但沒有結構化 harness/back-support claim。 |
| `osprey-farpoint-40@v1` | [Osprey Farpoint 40](https://www.osprey.com/gb/farpoint-40-s26) | PASS | AirScape、LightWire frame、framesheet、hipbelt 均有明確原文；擷取內容沒有價格，故 `price=null`。 |
| `decathlon-forclaz-travel500-40l@v1` | [Decathlon Forclaz Travel 500 Organizer 40L](https://www.decathlon.com/products/forclaz-travel-500-organizer-40-l-backpack-338564) | PASS | 官方頁明列 padded straps、lumbar belt、fixed back 與多數航空 cabin 相容但要求旅客自行核對。 |
| `cotopaxi-allpa-35l@v1` | [Cotopaxi Allpa 35L](https://www.cotopaxi.com/products/allpa-35l-travel-pack-4) | PASS | 美國官方頁沒有明示 cabin compliance，故 `airline_compliance=null`。 |

## 可追溯性與限制

- JSON 是依官方頁可見文字整理成 Contract v3 fixture，並通過 literal-evidence QA。整合時請依 `ingest_manifest.json` 逐筆呼叫 `POST /products` 重新抽取；若輸出不同，不可直接手改 attribute，應保留原始回應並調整 `raw_text` 再重抽。
- CabinZero 的原 sample（760g、44×30×20cm、comfort=null）已過時，P6 資料不沿用。
- `source_url` 只指官方商品頁；第三方內容僅用在 v2 prototype 的明示來源，不混入 v1。
