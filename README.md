# 0815 — AI Recommendation Diagnostics

> **Are you losing to a better product — or just better information?**
> 診斷「AI（ChatGPT/Perplexity/Gemini 類助手）為什麼推薦競品」，把敗因拆成 Information Gap vs Product Gap，並輸出可執行的修正建議 + before/after 驗證閉環。

## Repo 結構

```
backend/          # 整個後端（P1+P2+P3）：五階段 pipeline + ingestion + simulate + diagnosis + debate
contracts/        # API 契約（openapi.yaml + schemas.py + check_contract.py）— 唯一真相來源
deploy/           # AWS 部署（App Runner 一鍵腳本 / EC2 fallback）
docker-compose.yml
spec.md                             # v2 六人分工 spec
ai_recommendation_product_core.md   # 產品核心設計
contract-samples/                   # 舊 v2 契約樣本（參考用，已被 contracts/ 取代）
```

## 快速開始

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # 填 AWS creds；不填也能跑（mock 模式）
backend/run.sh                         # API 在 :8000
```

詳細文件：
- **`FRONTEND.md`** — P4/P5 前端串接指南（English：endpoints、SSE 解析、cookbook、TS types）
- `contracts/types.ts` — 前端可直接 import 的 TypeScript 型別
- `backend/README.md` — 後端架構 / endpoints / demo 流程
- `deploy/README.md` — 部署（EC2 一鍵 / App Runner / docker）
