# mock_fixtures — 契約實例（前端開發用）

每個檔案就是「你要生產/消費的物件長什麼樣」。開發時不要憑記憶，直接 diff 這些檔案。
**全部由真實後端回應生成**（`python -m backend.scripts.gen_fixtures`），改了後端 response 形狀請重新生成並跑 `python contracts/check_contract.py` 驗證。

| 檔案 | 對應 endpoint |
|---|---|
| `taxonomy.json` | GET /taxonomy |
| `intents.sample.json` | 內建 164 條 intent library（Stage1 離線 fallback） |
| `mock_world.json` | mock 引擎行為設定（勝率/理由/引用池） |
| `product.*.json` | GET /products/{ref}（含 v2 版本範例） |
| `request/response.post_products.manual.json` | POST /products |
| `request.post_runs.json` / `response.run_status.json` | POST /runs、GET /runs/{id} |
| `response.funnel.json` | GET /runs/{id}/funnel |
| `response.report.json` | GET /runs/{id}/report |
| `sse.run_events.txt` | GET /runs/{id}/events |
| `request.post_simulate.json` / `response.decision_result.json` / `sse.simulate.stream.txt` | POST /simulate |
| `request/response.simulate_batch.json` | POST /simulate/batch |
| `response.diagnosis.json` | GET /products/{ref}/diagnosis |
| `request/response.post_debate_session.json` / `response.get_debate_session.json` / `sse.debate.stream.txt` | debate 三支 |
| `response.metrics_compare.json` | GET /metrics/compare |
| `response.error.sample.json` | 統一錯誤形狀 |

SSE 事件只有 `token / action / progress / error / done` 五種；前端 switch 這五種即可。
