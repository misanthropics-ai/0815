# AI Purchase Decision Intelligence — 前後端架構版 6 人分工 Spec (v2)

> 架構變更:從「離線 pipeline + static dashboard」改為「**live 前後端產品**」。
> 四個 workstream:
> **A. Shopper Simulator 前端**(demo 用:給定產品,看 LLM 會選誰)
> **B. Diagnosis + Debate 前端**(產品本體:輸入產品 → 指出缺陷 → 可以跟使用者辯論)
> **C. Backend**(所有引擎)
> **D. Demo**(真實產品 attributes 的 before/after 對照)
>
> 原則不變:**先凍結 API 契約,再各自開工。** 前端全程對 mock server 開發,最後換 base URL。

---

## 0. 系統架構與 API 契約(Contract v2)— 開工前 45 分鐘全員凍結

### 0.1 架構圖

```
┌─────────────────────┐      ┌──────────────────────────┐
│ Frontend A          │      │ Frontend B               │
│ Shopper Simulator   │      │ Diagnosis + Debate       │
│ (P4)                │      │ (P5)                     │
└────────┬────────────┘      └──────────┬───────────────┘
         │  REST / SSE                  │  REST / SSE
┌────────▼──────────────────────────────▼───────────────┐
│                  Backend (FastAPI)                     │
│  ┌──────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │ Product      │ │ Decision     │ │ Diagnosis &   │  │
│  │ Ingestion    │ │ Engine       │ │ Debate Agent  │  │
│  │ (P1)         │ │ (P2)         │ │ (P3)          │  │
│  └──────────────┘ └──────────────┘ └───────────────┘  │
│            storage: SQLite + /data JSON files          │
└────────────────────────────────────────────────────────┘
                         ▲
              ┌──────────┴──────────┐
              │ Demo data & B/A 實驗 │
              │ (P6)                │
              └─────────────────────┘
```

### 0.2 Repo 結構

```
/backend
  app.py                  ← FastAPI 入口(P1 建骨架)
  /ingestion              ← P1
  /decision               ← P2
  /debate                 ← P3
  /storage                ← P1(SQLite schema + JSON 存取)
  mock_fixtures/          ← P1(T+1h 前提供,前端開發用)
/frontend-simulator       ← P4(Vite + React)
/frontend-diagnosis       ← P5(Vite + React)
/demo
  real_products/          ← P6(真實爬取的 attributes)
  before_after/           ← P6
  script.md               ← P6
/contracts
  openapi.yaml            ← P1 維護(唯一真相來源)
  check_contract.py       ← P6(schema 驗證工具)
```

### 0.3 核心資料物件

**Product**(手動輸入的 prototype 與爬來的真實產品共用同一 schema)
```json
{
  "product_id": "cabinzero-classic-36l",
  "brand": "CabinZero",
  "display_name": "CabinZero Classic 36L",
  "source": "url | manual_prototype",
  "source_url": "https://... (manual 時為 null)",
  "raw_text": "(頁面全文或使用者輸入的產品描述)",
  "attributes": [
    { "attribute_id": "weight", "value": "760g", "evidence": "quoted from page", "confidence": 0.95 },
    { "attribute_id": "back_support", "value": null, "evidence": null, "confidence": 0.0 }
  ],
  "version": 1
}
```
> attributes 一律對映到 `taxonomy.json`(沿用 v1 的 7±2 個 attribute);`value: null` 代表「頁面上找不到」——這正是缺陷分析的原料。
> `version` 支援 before/after:同一 product_id 可有 v1(原始)v2(修改後)。

**DecisionResult**(Decision Engine 的輸出,Simulator 與 batch 共用)
```json
{
  "decision_id": "dec_00042",
  "intent": { "text": "...", "cluster_id": "comfort_walking", "attributes": ["comfort", "airline_compliance"] },
  "candidates": ["cabinzero-classic-36l@v1", "osprey-farpoint-40@v1"],
  "winner": "osprey-farpoint-40@v1",
  "per_product": [
    {
      "product_ref": "cabinzero-classic-36l@v1",
      "considered": true,
      "verdict": "rejected",
      "reasons_for": [ { "text": "fits Ryanair limits", "attribute": "airline_compliance" } ],
      "reasons_against": [ { "text": "no back-support information available", "attribute": "back_support" } ]
    }
  ],
  "narrative": "(LLM 完整比較敘述,Simulator 前端逐字顯示用)"
}
```

**Diagnosis**(Frontend B 開頁時拿的分析結果)
```json
{
  "product_ref": "cabinzero-classic-36l@v1",
  "overall": { "recommendation_share": 0.34, "n_simulations": 120, "vs": { "osprey-farpoint-40@v1": 0.63 } },
  "defects": [
    {
      "defect_id": "def_001",
      "type": "missing_attribute | weak_evidence | losing_cluster | positioning",
      "attribute_id": "back_support",
      "severity": "high",
      "headline": "No back-support specs — losing 71% of comfort-driven comparisons",
      "evidence": {
        "losing_share_in_cluster": 0.88,
        "cluster_id": "comfort_walking",
        "sample_rejection_reasons": ["no information on back panel or hip belt"],
        "competitor_contrast": "Osprey page specifies ventilated AirScape back panel + hip belt"
      },
      "suggested_fix": "Add back-panel structure, torso fit range, hip-belt spec to product page"
    }
  ]
}
```

**Debate**(SSE streaming)
```
POST /debate/sessions                    → { "session_id": "...", "product_ref": "..." }
POST /debate/sessions/{id}/messages      body: { "text": "我的背包明明很舒適" }
                                         → SSE stream of assistant tokens
GET  /debate/sessions/{id}               → 完整歷史(重整頁面用)
```

### 0.4 API Endpoints(P1 在 openapi.yaml 維護,以下為凍結的最小集)

| Method | Path | 用途 | 主要使用者 |
|---|---|---|---|
| POST | `/products` | 輸入 URL 或手打 prototype → 抽取 attributes | Frontend B |
| GET | `/products/{ref}` | 取產品(含 version) | 兩個前端 |
| POST | `/products/{id}/versions` | 建立修改版(before/after 用) | P6 / Frontend B |
| POST | `/simulate` | 單次:intent + candidates → DecisionResult | Frontend A(live) |
| POST | `/simulate/batch` | intent cluster × N runs → 批次結果 + shares | P6 / Diagnosis |
| GET | `/products/{ref}/diagnosis` | 取 Diagnosis(無快取時觸發 batch) | Frontend B |
| POST | `/debate/sessions` 等 | 辯論(見 0.3) | Frontend B |
| GET | `/metrics/compare?a=...&b=...` | 兩個 product version 的 share 對照 | P6 demo |

**規範**:所有 endpoint 回 JSON;錯誤統一 `{ "error": { "code": "...", "message": "..." } }`;LLM 生成類 endpoint 一律支援 SSE(`Accept: text/event-stream`)。

---

## P1 — Backend Lead:骨架 / Product Ingestion / 契約守門人

**目標**:讓其他 5 人第一天早上就有東西可以接。

**產出物**
1. **T+1h**:FastAPI 骨架 + 所有 endpoint 的 **mock 版本**(回 `mock_fixtures/` 裡的假資料,含一個假 SSE stream)→ 前端從此刻起可開發
2. `contracts/openapi.yaml`(與 0.3/0.4 一致,改動需全員同意)
3. **Product Ingestion**:
   - URL 模式:fetch 頁面 → 清 HTML → LLM 抽取 attributes(對映 taxonomy,附 evidence 引文與 confidence;找不到填 null)
   - Manual prototype 模式:使用者貼產品描述文字 → 同一條抽取路徑
4. Storage:SQLite(products / decisions / diagnoses / debate_sessions 四張表)+ 簡單 DAO,給 P2/P3 直接用
5. Product versioning(`@v1` / `@v2` 的解析與存取)

**驗收**:mock server 起得來且前端能跑完整流程;真實 URL(CabinZero 官網頁)抽出 ≥ 5 個非 null attributes;null attribute 有正確標示。

**Deadline**:T+1h mock 全開;T+4h ingestion 真實可用;之後支援其他人接 storage。

---

## P2 — Backend:Decision Engine(Simulator 核心 + batch)

**目標**:實作「AI 購物決策」本身——單次即時版給 Frontend A,批次版給 Diagnosis 與 demo。

**產出物**
1. `POST /simulate`:
   - 輸入 intent + candidate product_refs
   - 組 comparison prompt:把各 product 的 attributes + raw_text 摘要塞進 context,要求模型以「幫使用者選購」的立場輸出結構化 DecisionResult(JSON mode)+ narrative
   - **SSE**:narrative 逐 token 流給前端,結束時附完整 JSON
2. `POST /simulate/batch`:cluster 內所有 intents × N runs(預設 3),並行 + rate limit + cache by `(intent_id, candidates_hash, run)`;回 per-product shares(recommendation / consideration)與所有 DecisionResults
3. Intent 生成沿用 v1 spec:內建 150 條 intents + clusters(直接把 v1 P1 的產出搬進來,開賽 2 小時內先手寫 30 條頂著)
4. 決策 prompt 版本化(`decision/prompt_v*.md`)

**關鍵設計**:模型只能依據**提供的 context** 決策,prompt 明確要求「頁面沒寫的資訊視為未知,不得腦補」——這條規則是整個產品邏輯成立的前提(缺陷 = AI 看不到,而不是產品不好),demo 被問到要答得出來。

**驗收**:同一 intent 跑 5 次,winner 的眾數穩定;reasons 的 attribute 對映 ≥ 90% 落在 taxonomy 內;batch 40 次 < 3 分鐘。

**Deadline**:T+4h 單次 simulate 可用;T+7h batch 可用。

---

## P3 — Backend:Diagnosis & Debate Agent

**目標**:產品的靈魂。兩個功能:(1) 從 batch 結果產生 Diagnosis;(2) 一個**會據理力爭的辯論 agent**。

**產出物**
1. **Diagnosis 生成**(`GET /products/{ref}/diagnosis`):
   - 從 batch DecisionResults 聚合:各 cluster 的敗率、rejection reasons 的 attribute 頻率、null attributes 清單
   - 每個 defect 依 0.3 schema 產出,severity 按「該 cluster 敗率 × intent 數量」排序
   - defect type 判定:attribute 為 null → `missing_attribute`;有值但 rejection reasons 仍集中在該 attribute → `weak_evidence`;整 cluster 全敗 → `losing_cluster`
2. **Debate Agent**(SSE chat):
   - System prompt 注入:該產品完整 Diagnosis + 抽樣 20 筆 DecisionResults 的 rejection reasons + 競品的 attribute 對照
   - **辯論行為規範**(prompt 明寫,版本化在 `debate/prompt_v*.md`):
     a. 使用者說「我的產品明明很舒適」→ **不附和、不道歉**,用數據反駁:「可能是,但在 120 次模擬中你在 comfort intents 只被推薦 12%。問題不是產品舒不舒適,是你的頁面上沒有任何 AI 引用得到的舒適性證據——沒有背板結構、沒有 hip belt 規格、沒有長時間背負的 review。AI 無法推薦它看不見的優點。」
     b. 每次反駁**必須引用具體數字或 sample rejection reason**,不打空泛太極
     c. 使用者提出**新的具體資訊**(「其實我們有 ventilated back panel,只是沒寫」)→ 承認這改變了局面,並轉為行動:「那這正是問題所在——把它寫上頁面。要不要我現在把這個 claim 加進 v2,重跑模擬給你看?」→ 呼叫內部 function 建立 product v2 + 觸發 batch(接 P2)
     d. 使用者純情緒堅持 → 立場不動,換一個 evidence 角度再解釋一次
   - 這個「辯論 → 承認新資訊 → 生成 v2 → 重模擬」的迴圈就是產品閉環,也是 demo 第二高潮
3. Debate session 存取(接 P1 storage)

**驗收**:準備 6 條攻擊性測試句(「你們數據是假的」「舒適是主觀的你憑什麼」「競品是付錢給你了吧」),agent 全部不失守、不空泛、每回合含具體數據;新資訊路徑能真的觸發 v2 + rerun。

**Deadline**:T+6h Diagnosis 可用;T+9h Debate 完整含 v2 迴圈。

---

## P4 — Frontend A:Shopper Simulator(demo 開場用)

**目標**:一個「看 AI 怎麼選」的劇場。介面要讓評審 3 秒看懂。

**產出物**(Vite + React,接 mock 開發)
1. 版面:左側 intent 輸入(含 6 顆預設 intent 快捷鍵,對映 clusters)+ 候選產品卡片(可勾選 2–4 個,顯示 P1 抽取的 attribute chips,null attribute 顯示灰色「?」——**這個灰色問號是視覺伏筆**,demo 講缺陷時回扣)
2. 按下 Simulate → narrative SSE 逐字顯示(打字機效果)→ 結束時 winner 卡片高亮、敗者卡片顯示 reasons_against 標籤
3. 「Run ×5」模式:同 intent 連跑 5 次,右側累計小計分板(誰贏幾次)——現場回應 stochasticity 質疑用
4. 錯誤與逾時的 graceful UI(spinner + retry),demo 不准白屏

**驗收**:P6 拿 demo script 操作全程不需要口頭解釋 UI;SSE 中斷能 retry;投影可讀(大字、高對比)。

**Deadline**:T+3h 接 mock 跑通;T+6h 接真 API;T+10h 視覺完稿。

---

## P5 — Frontend B:Diagnosis + Debate(產品本體)

**目標**:advertiser 視角的主介面,demo 的主場。

**產出物**(Vite + React,接 mock 開發)
1. **輸入頁**:貼 URL 或貼產品描述文字(prototype 模式)→ 顯示抽取進度 → attribute 表(有值/null 分色)
2. **Diagnosis 頁**:
   - 頂部:recommendation share 大數字 + vs 競品對比條
   - Defect 卡片列表(severity 排序):headline、cluster 敗率、sample rejection reason 引述、competitor contrast、suggested fix
   - 每張卡片一顆「Discuss this」按鈕 → 帶著該 defect context 開辯論
3. **Debate 頁**:chat UI(SSE streaming);當 agent 觸發「生成 v2 + 重模擬」時,聊天流中插入一張**進度卡片** → 完成後顯示 before/after share 對比小圖(inline)——這是 demo 高潮鏡頭,視覺要下功夫
4. Session 可重整不掉歷史(接 GET session)

**驗收**:從貼 URL 到辯論到 v2 對比,全程點擊 ≤ 8 次;辯論輸入框在 streaming 時鎖定避免 race;P6 排練認可動線。

**Deadline**:T+3h 接 mock 跑通;T+7h 接真 API;T+11h v2 對比卡片完成。

---

## P6 — Demo Lead:真實產品資料 / Before-After 實驗 / 整合與腳本

**目標**:用**真實產品的真實 attributes** 做出可信的 before/after,並把兩個前端縫成一條 demo 敘事。

**產出物**
1. **真實資料集**(`demo/real_products/`,T+3h 前):
   - 爬取或手動整理 4 個真實產品頁:CabinZero Classic 36L、Osprey Farpoint 40、Decathlon Forclaz 40L、Cotopaxi Allpa 35L
   - 每個過 P1 ingestion,人工檢查 attribute 抽取正確性(錯的修 raw_text 重抽,不准手改結果——demo 被問「資料哪來的」要答得出「全部從真實頁面抽取」)
   - 附 sources.md 記錄每頁 URL 與抓取時間
2. **Before/After 實驗**(`demo/before_after/`):
   - Before = CabinZero 真實頁面原文(v1)
   - After = 依 Diagnosis 的 suggested fixes 改寫的頁面文案(v2):補 back-support 規格、carry-comfort FAQ、兩則長時間實測 review 摘要——**改寫幅度要記錄成 diff**,demo 秀出「我們只加了這 3 段」
   - 對 comfort_walking cluster 跑 `/simulate/batch`(40 × 2 版本)→ 目標 delta ≥ 20 個百分點;不足就檢討 v2 文案或 P2 prompt,循環到顯著
   - 誠實話術準備好:「controlled simulation:同一決策引擎、同一 intents,唯一變因是頁面內容」
3. **契約驗證工具** `contracts/check_contract.py`(T+1h):打每個 endpoint 驗 response schema,CI 式每小時跑
4. **Demo script**(`demo/script.md`)——建議動線:
   - (60s) Frontend A:選 comfort intent + 4 個真實產品 → AI 現場選了 Osprey,理由唸出來 →「如果你是 CabinZero,你只看到你輸了」
   - (90s) Frontend B:貼 CabinZero 真實 URL → Diagnosis 秀出 missing back_support(回扣 A 畫面裡的灰色問號)
   - (60s) Debate:演一段「我的背包明明很舒適」→ agent 反駁 → 演示者「順勢」提供新資訊 → 觸發 v2 重模擬
   - (45s) Before/After 數字揭曉(12% → 5x%)→ 回 Frontend A 用 v2 再跑一次同一 intent,**這次 AI 選了 CabinZero** → 收尾 pitch
   - 每步含備援:全程錄影 + `/simulate` 結果快取開關(後端加 `?cached=true`,請 P2 支援)
5. Pitch deck 5 頁 + 排練 ≥ 3 次計時

**驗收**:乾淨環境一鍵起前後端(`docker compose up` 或 `make dev`,可請 P1 協作);demo 全程走完 < 規定時間 90%;live 環節每一步都有 cached fallback。

---

## 時間軸(24h 計)

| 時間 | 里程碑 |
|---|---|
| T+0.75h | 凍結 Contract v2(0.x 節)全員簽字 |
| T+1h | P1 mock server 全 endpoint 上線;P6 check_contract 上線 |
| T+2h | P2 手寫 30 條 intents 頂著(150 條隨後補) |
| T+3h | P4/P5 接 mock 跑通;P6 交 4 個真實產品資料 |
| T+4h | P1 ingestion 真實可用;P2 單次 simulate 可用 |
| T+6h | P3 Diagnosis 可用;P4 接真 API |
| T+7h | P2 batch 可用;P5 接真 API |
| T+9h | P3 Debate 完整(含 v2 迴圈) |
| T+11h | P6 before/after 數字定案;P5 對比卡片完成 |
| T+12h | 第一次全隊排練 |
| 剩餘 | 修 bug、美化、排練 ×3、錄備援影片 |

## 三條紅線(v2)

1. **openapi.yaml 是唯一真相**,改契約需全員同意並同步 mock fixtures。
2. **前端只准依賴契約**,不准讀後端內部檔案或 DB。
3. **Demo 每個 live 步驟都要有 cached fallback**,且 before/after 的資料來源必須全程可追溯到真實頁面。

