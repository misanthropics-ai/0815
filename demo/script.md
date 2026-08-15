# Demo script — AI Purchase Decision Intelligence

目標 4:15，上限 4:45。核心句：**我們顯示 AI 為什麼看不見產品優點，再量化補上證據後的差異。**

## 0:00–1:00｜Shopper Simulator

Frontend A 選「Walking 8+ hours in Europe」、勾四個產品、按 Simulate。

> AI 選了 Osprey。CabinZero 不是沒被看到；它是被比較後淘汰。頁面有重量、尺寸和航空相容性，卻沒有 AI 能引用的背部支撐證據。

8 秒無首 token 就切 `cached=true`。

## 1:00–2:15｜Diagnosis

Frontend B 載入 CabinZero v1 diagnosis。

> 現行 CabinZero 頁已有 shoulder strap airflow，所以 comfort 並非空白；真正缺口是 comfort 裡的 harness/back-support 證據。競品寫 frame、backpanel、hipbelt，CabinZero 的 harness 是「-」。

點 `Discuss this` 開啟 `comfort` defect。

## 2:15–3:15｜Debate

輸入：`我的背包明明很舒適，你們的分析有問題。`

Agent 必須引用推薦率、loss 數與 rejection reason。再輸入：`我們其實有 ventilated back panel、12mm 肩帶和 sternum strap，只是頁面沒寫。`

> 新資訊會改變局面，但不會偷偷變成事實。系統標記 owner-supplied、待驗證，建立 v2，再用同一批 intents 重跑。

## 3:15–4:00｜Before/After

展示三段 diff 和 compare。

> 同一引擎、intents、競品；唯一變因是三段頁面內容。12%→55% 是 cached contract fixture，live 結果由 runner 取代。我們不把 fallback 冒充量測；驗收線是至少 +20pp。

## 4:00–4:15｜收尾

> 商品沒有在四分鐘內被改造；AI 能看見的證據被改造了。品牌因此得到可辯論、可修改、可重跑的決策閉環。

## 故障備援

| 步驟 | 觸發 | 備援 |
|---|---|---|
| Simulate SSE | 8 秒無 token | `cached=true`，再失敗切錄影 |
| Diagnosis | 3 秒未載入 | cached diagnosis |
| Debate | 5 秒無 token | 預錄 session |
| v2 rerun | 10 秒未完成 | cached compare，明說 fixture |
| 前端白屏 | retry 仍失敗 | 完整錄影＋pitch deck |

上台前執行 `python contracts/check_contract.py --base-url http://127.0.0.1:8000`，並核對 `latest.compare.json` 的日期、n、delta 和 UI。
