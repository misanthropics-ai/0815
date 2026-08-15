# 手動測試指南（Backend）

兩個測試入口，任選：

```bash
API=http://34.227.93.223:8000     # 雲端（Elastic IP，固定不變，live LLM）
API=http://localhost:8000         # 本機：backend/run.sh 啟動（無 creds 時自動 mock 模式）
```

> 打開根路徑 `/` 會回一個 JSON 索引（不是 404 了）。看到 `{"detail":"Not Found"}` 代表你打到未定義的路徑。

---

## 1. 瀏覽器直接開（不用任何工具）

| URL | 應該看到 |
|---|---|
| `/` | 服務索引 JSON |
| `/health` | `bedrock.ready: true`（雲端/本機有 creds）；`false` = mock 模式 |
| **`/docs`** | **Swagger UI — 所有 endpoint 可互動測試（最推薦的手測入口）** |
| `/taxonomy` | 12 個 attributes + 8 個 clusters |
| `/products` | 4 個種子產品（CabinZero/Osprey/Decathlon/Cotopaxi） |
| `/products/cabinzero-classic-36l@v1` | 完整產品：comfort 的 `value` 是 `null`（這是重點診斷訊號） |
| `/runs` | 歷史 runs 清單 |

## 2. 用 /docs（Swagger）測 POST

1. 開 `{API}/docs` → 點開任一 POST → **Try it out** → 改 body → **Execute**。
2. 注意：**SSE 端點**（`POST /simulate`、`POST /debate/sessions/{id}/messages`、`GET /runs/{id}/events`）在 Swagger 會等整條 stream 結束才一次顯示純文字 —— 功能正常，只是看不到逐字效果。要看逐字請用下面的 `curl -N`。

## 3. curl 劇情線測試

### 劇情線 A — P4 模擬器動線

```bash
# A1. 單次決策（非串流，回完整 DecisionResult JSON）
curl -s -X POST $API/simulate -H 'content-type: application/json' -d '{
  "intent": {"text": "Most comfortable travel backpack for walking all day",
             "cluster_id": "comfort_carry", "attributes": ["comfort"]},
  "candidates": ["cabinzero-classic-36l@v1","osprey-farpoint-40@v1",
                 "decathlon-forclaz-travel500-40l@v1","cotopaxi-allpa-35l@v1"],
  "stream": false}' | python3 -m json.tool | head -40
# ✅ 預期：winner 幾乎都是 osprey-farpoint-40@v1；CabinZero verdict=rejected 且
#    reasons_against 有 comfort 類逐字理由。live 模式一次 8~20 秒。

# A2. 串流版（看逐字 token → 最後 done 事件帶完整 decision）
curl -s -N -X POST $API/simulate -H 'content-type: application/json' -d '{
  "intent": {"text": "Backpack that fits Ryanair cabin limits",
             "cluster_id": "airline_compliance", "attributes": ["airline_compliance"]},
  "candidates": ["cabinzero-classic-36l@v1","osprey-farpoint-40@v1"], "stream": true}'
# ✅ 預期：一連串 event: token，最後 event: done（data 裡有 decision.winner）

# A3. 重播測試（demo 保險機制）：同 body 加 "cached": true 跑兩次
# ✅ 預期：第二次 <1 秒回傳、內容完全相同
```

### 劇情線 B — P5 診斷 + 辯論閉環（demo 主線）

```bash
# B1. 診斷
curl -s $API/products/cabinzero-classic-36l@v1/diagnosis | python3 -m json.tool | head -50
# ✅ 預期：overall.recommendation_share、defects[]（headline 含真實數字、
#    sample_rejection_reasons 是逐字引述、gap 分 information_gap/product_gap）
#    若回 202 {"status":"running"} → 等 30~60 秒再打一次（第一次會自動觸發模擬）

# B2. 開辯論 session
SID=$(curl -s -X POST $API/debate/sessions -H 'content-type: application/json' \
  -d '{"product_ref":"cabinzero-classic-36l@v1"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['session_id'])")
echo $SID

# B3. 攻擊句（agent 必須用數字反駁、不退讓）
curl -s -N -X POST $API/debate/sessions/$SID/messages -H 'content-type: application/json' \
  -d '{"text":"我的背包明明很舒適，你們的數據是假的"}'
# ✅ 預期：回覆引用具體數字（n 次模擬、敗率%）+ 逐字 rejection quote，語言跟你相同

# B4. 給新資訊（觸發 v2 + 重跑）
curl -s -N -X POST $API/debate/sessions/$SID/messages -H 'content-type: application/json' \
  -d '{"text":"其實我們有 ventilated mesh back panel 和加厚記憶棉肩帶，只是官網沒寫"}'
# ✅ 預期：承認改變局面 → 出現 event: action，data 裡有 new_ref(@v2)、compare_url

# B5. 輪詢 before/after（batch 跑 1~3 分鐘，期間回 202 pending）
curl -s "$API/metrics/compare?a=cabinzero-classic-36l@v1&b=cabinzero-classic-36l@v2&cluster=comfort_carry" | python3 -m json.tool
# ✅ 預期（最終）：a/b 的 recommendation_share、delta_recommendation、
#    changes_applied = debate 加上去的那段英文文案

# B6. 驗證 v2 真的重抽了屬性
curl -s $API/products/cabinzero-classic-36l@v2 | python3 -c "
import json,sys; p=json.load(sys.stdin)
print([a for a in p['attributes'] if a['attribute_id']=='comfort'])"
# ✅ 預期：comfort 從 null 變成有 value + evidence
```

### 劇情線 C — 五階段 pipeline

```bash
# C1. 啟動（mock 秒級；live 60 intents 約 5~10 分鐘，先用 20 條測）
RUN=$(curl -s -X POST $API/runs -H 'content-type: application/json' \
  -d '{"brand":"CabinZero","n_intents":20}' | python3 -c "import json,sys;print(json.load(sys.stdin)['run_id'])")
echo $RUN

# C2. 看即時進度（SSE：intents→execute→funnel→attribution→report）
curl -s -N $API/runs/$RUN/events
# （或輪詢 curl -s $API/runs/$RUN | python3 -c "...status/stage/progress"）

# C3. 完成後看結果
curl -s $API/runs/$RUN/funnel | python3 -m json.tool | head -40   # 漏斗數字
curl -s $API/runs/$RUN/losses | python3 -m json.tool | head -30   # 逐字敗因
curl -s "$API/runs/$RUN/report?format=md"                         # 完整 markdown 報告
# ✅ 預期：CabinZero retrieved 高、recommended 低；defects 含 gap 分類與 content_patch
```

## 4. 一鍵自動化檢查（本機）

```bash
python -m backend.scripts.preflight                       # AWS creds + Bedrock 模型
python -m backend.scripts.smoke_e2e                       # mock 全 pipeline（不需 AWS，~10 秒）
python -m backend.scripts.smoke_e2e --live --n=12         # live 小跑（~3 分鐘）
python contracts/check_contract.py $API                   # 契約驗證（fixtures + live probe）
```

## 5. 狀況對照表

| 看到 | 意思 / 處理 |
|---|---|
| `{"detail":"Not Found"}` | 路徑打錯（根路徑現在有索引頁；正確路徑見 `/docs`） |
| `/health` 的 `bedrock.ready:false` + error 有 `Expired` | **session token 過期** → 更新 `backend/.env` 三個 AWS_* 值後重啟（雲端機器用 instance role，不受影響） |
| 202 `{"status":"running"/"pending"}` | 正常，背景在算 → 幾秒後再打一次 |
| SSE 很久沒動 | live LLM 單次 8~25 秒；被 AWS throttle 會自動退避重試，等 |
| run events 回 error 說 restart | 後端重啟過 → `curl -X POST $API/runs/{id}/resume`（已完成部分自動跳過） |
| 雲端網址 | `34.227.93.223` 是 **Elastic IP，重佈署也不變**；`python deploy/deploy_ec2.py --status` 可查 |
