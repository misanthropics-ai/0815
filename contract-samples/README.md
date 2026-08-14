# Contract v2 — Sample Files 使用說明

每個檔案就是「你要生產 / 消費的物件長什麼樣」。開發時不要憑記憶, 直接 diff 這些檔案。

## 誰擁有什麼 (owner = 有權改, 改前需全員同意)

| 檔案 | Owner | 消費者 |
|---|---|---|
| contracts/openapi.yaml | P1 | 全員 |
| contracts/schemas.py | P1 | P1/P2/P3 直接 import; P6 驗證用; P4/P5 對照欄位 |
| mock_fixtures/taxonomy.json | P1 | 全員 |
| mock_fixtures/intents.sample.json | P2 | P2, P3, P6 |
| mock_fixtures/product.*.json | P1 | P2, P3, P4, P5 |
| mock_fixtures/request.post_products.manual.json / response.post_products.manual.json | P1 | P5 (輸入頁) |
| mock_fixtures/request.post_simulate.json / response.decision_result.json | P2 | P4 (Simulator) |
| mock_fixtures/sse.simulate.stream.txt | P2 | P4 |
| mock_fixtures/request.post_simulate_batch.json / response.simulate_batch.json | P2 | P3, P6 |
| mock_fixtures/response.diagnosis.json | P3 | P5 (Diagnosis 頁) |
| mock_fixtures/request.post_debate_session.json / response.post_debate_session.json / response.get_debate_session.json | P3 | P5 (Debate 頁) |
| mock_fixtures/sse.debate.stream.txt | P3 | P5 (含 action 進度卡片) |
| mock_fixtures/response.metrics_compare.json | P6 (數字) / P2 (endpoint) | P5, demo |
| mock_fixtures/product.cabinzero-classic-36l.v2.json | P6 | P2, P5 |
| mock_fixtures/response.error.sample.json | P1 | 全員 |
| demo/before_after/cabinzero.v1_v2.diff.md | P6 | demo |
| demo/real_products/sources.sample.md | P6 | demo |

## 三個關鍵約定 (每個人都要知道)

1. **ProductRef 格式** `product_id@vN`。所有跨模組引用產品一律用 ref, 不准只傳 product_id。
2. **`value: null` 的語義** = 「頁面上找不到這個 attribute」。這不是 bug, 是缺陷分析的原料; 前端渲染成灰色「?」。
3. **SSE 事件只有四種**: `token` / `action` / `error` / `done`。前端 switch 這四種即可, 後端不准發明新事件型別 (要加先改 contract)。

## Mock server 起法 (P1 T+1h 交付)

FastAPI 讀本目錄的 fixtures 原樣回傳; SSE 端點把 sse.*.txt 的 token 事件以 50ms 間隔重播。
前端開發全程指向 mock, 換真後端只改 base URL。

## 驗證

任何人改了 fixture 或後端 response, 先跑:
    python contracts/check_contract.py   # P6 T+1h 交付: 用 schemas.py 驗所有 fixtures + 打真實 endpoints
