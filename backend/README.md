# Backend — AI Recommendation Diagnostics（P1+P2+P3 全部）

> **Are you losing to a better product — or just better information?**
> 五階段 pipeline 診斷「AI 為什麼推薦競品」，並把敗因拆成 **Information Gap vs Product Gap**。
> 所有 LLM 呼叫走 **AWS Bedrock**（Claude sonnet-4-6 / haiku-4-5，啟動時自動探索可用模型）。
> 沒有 AWS creds 時整套 API 自動降級 **mock 模式**，前端照常開發、demo 有保底。

## 快速啟動

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env      # 填入 AWS creds（hackathon 發的 session creds）
backend/run.sh                            # = uvicorn backend.app:app --port 8000
```

驗證：
```bash
python -m backend.scripts.preflight       # AWS creds + Bedrock 模型探索
python -m backend.scripts.smoke_e2e       # mock 全 pipeline（不需 AWS）
python -m backend.scripts.smoke_e2e --live --n=12   # live 小跑
python contracts/check_contract.py http://localhost:8000
```

## 五階段 Pipeline（POST /runs）

| Stage | 模組 | 內容 |
|---|---|---|
| 1. Intent Generation | `pipeline/intents.py` | Claude 依 persona+category 每個 cluster 生成 realistic buyer intents（10–300 條，attribute tags）；離線 fallback：內建 163 條 library |
| 2. Query Execution | `pipeline/engines/` | 對每條 intent 跑每個 engine，記錄完整 response + citations + search trace |
| 3. Funnel Parsing | `pipeline/funnel.py` | **LLM-as-judge**：Retrieved（citations 有品牌證據，另有 deterministic 比對）→ Considered（進入比較敘述）→ Recommended（最終推薦）；Considered 未 Recommended → 抽逐字 **stated loss reasons** |
| 4. Attribution | `pipeline/attribution.py` | loss reasons → attribute taxonomy（keyword + LLM 映射）；**evidence audit**：各品牌可檢索內容的 per-attribute 證據密度 → information_gap / product_gap / mixed 分類 |
| 5. Recommendation | `pipeline/recommend.py` | defects（missing_attribute / weak_evidence / losing_cluster / positioning + severity + gap）→ 可執行的 content/FAQ/schema.org 修正建議（含可直接貼上的 content_patch）+ exec summary + markdown 報告 |

### Engines（`pipeline/engines/`）

團隊決策：**LLM 全部走 AWS**，因此 Stage 2 用 Bedrock 模擬引擎：

- `sim-sonnet` / `sim-haiku` — 「controlled simulation」：對 corpus（產品頁 + 第三方評測文）做 lexical top-k 檢索 → 檢索結果變成 citations → Claude 只依 sources 回答（**頁面沒寫 = unknown，不准腦補**）。改頁面（v2）→ 檢索與證據改變 → before/after 機制成立。
- `mock` — 零網路決定論引擎（`mock_fixtures/mock_world.json` 控制勝率/理由），demo 保底。
- 未來要接真 ChatGPT/Perplexity/Gemini：實作 `engines/base.py` 的 `Engine` interface 丟進 `engines/__init__.py` 即可，funnel/aggregation 全部 engine-agnostic。

## 主要 Endpoints（完整見 `contracts/openapi.yaml`）

```
GET  /health                              # bedrock 狀態、模型、引擎可用性
POST /runs                                # 啟動五階段（body 見 request.post_runs.json）
GET  /runs/{id} /runs/{id}/events(SSE) /funnel /losses /evidence /report?format=md
POST /products                            # URL 或手貼文字 → LLM 抽 attributes（null=頁面沒寫）
POST /products/{id}/versions              # v2（重抽 attributes）
GET  /products/{ref}/diagnosis            # v2 契約形狀診斷（run 優先，無則觸發 batch 回 202）
POST /simulate                            # 單次決策模擬，SSE token→done{decision}
POST /simulate/batch                      # cluster×runs 批次 + shares + CI（cache by intent×candidates×run）
POST /debate/sessions + /messages(SSE)    # 辯論 agent：數據反駁；新資訊→<action>→自動 v2+重跑
GET  /metrics/compare?a&b&cluster         # before/after share 對照
```

SSE 事件只有：`token / action / progress / error / done`（simulate 的最終 JSON 在 `done` 的 payload）。
錯誤統一 `{"error":{"code","message"}}`。

## Demo 閉環（已 live 驗證）

1. `GET /products/cabinzero-classic-36l@v1/diagnosis` → 缺陷卡（comfort 無證據、losing clusters、gap 分類）
2. Debate：「我的背包明明很舒適」→ agent 用具體數字+逐字 rejection quote 反駁，不退讓
3. 使用者給新資訊（中文也行）→ agent 產生英文頁面文案 → 自動建 `@v2` + 重抽 attributes + 背景跑 before/after batches
4. `GET /metrics/compare?a=...@v1&b=...@v2&cluster=...` → delta 揭曉（實測 +25pt）

> **注意**：run 預設自動選每個產品的**最新版本**。debate 產生 v2 之後，要跑「v1 baseline」對照 run 時請在 POST /runs 明確給 `product_refs`（例如 `["cabinzero-classic-36l@v1", ...]`）。

## 設定（backend/.env）

| 變數 | 說明 |
|---|---|
| `AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN` | hackathon creds（session token 會過期，過期就更新這裡） |
| `BEDROCK_MODEL` / `BEDROCK_FAST_MODEL` | 留空 = 啟動自動探索（目前選到 sonnet-4-6 / haiku-4-5） |
| `DEFAULT_ENGINES` | 預設 `sim-sonnet,sim-haiku` |
| `MODE` | `auto`（有 creds 用 live，否則 mock）/ `mock` / `live` |
| `BEDROCK_MAX_CONCURRENCY` | 預設 4，被 throttle 就調低 |

## 目錄

```
backend/
  app.py                # FastAPI 全 endpoints + SSE
  config.py             # env 載入
  llm/bedrock.py        # Converse wrapper：模型探索、retry/throttle、JSON 強制、streaming
  storage/db.py         # SQLite DAO（WAL）
  taxonomy/taxonomy.json# attribute taxonomy + clusters（keywords 供 evidence density）
  pipeline/             # 五階段（intents/engines/funnel/attribution/recommend/runner/corpus）
  ingestion/            # P1 URL/manual 抽取 + versioning
  decision/             # P2 simulate 單次+batch（prompts 版本化）
  diagnosis/            # P3 診斷組裝（v2 契約形狀）
  debate/               # P3 辯論 agent（prompts 版本化）
  prompts/              # intent_v1 / funnel_v1 / attribution_v1 / recommend_v1 / extract_v1
  seeds/                # 4 個示範產品 + 6 篇第三方來源（P6 之後換成真爬取資料）
  mock_fixtures/        # 契約 fixtures（gen_fixtures.py 從真實回應生成）+ mock_world.json
  scripts/              # preflight / smoke_e2e / gen_fixtures / gen_intent_library
```

部署見 `deploy/README.md`（App Runner 一鍵 / EC2 / docker compose）。

## 注意事項

- **成本/限速**：live run 預設 n_intents=60 × 2 engines ≈ 120 sim + 120 judge 呼叫；hackathon 帳號 quota 低，被 throttle 時 client 會自動退避重試。demo 現場建議先跑好 run，靠 cache（同 intent/corpus 重跑幾乎免費）。
- **可信話術**：模擬引擎是「同一決策引擎、同一 intents、唯一變因是頁面內容」的 controlled simulation；不宣稱 = 真實 ChatGPT 流量。
- server 重啟後 running 中的 run 會停：`POST /runs/{id}/resume` 續跑（已完成 stage 自動跳過）。
