# 0815 — AI Recommendation Diagnostics

> **Are you losing to a better product — or just better information?**

診斷「AI（ChatGPT／Perplexity／Gemini 類助手）為什麼推薦競品」，把敗因拆成
Information Gap 與 Product Gap，並輸出可執行的修正建議及 before/after 驗證閉環。

## Repo 結構

```text
backend/          # FastAPI：pipeline、ingestion、simulate、diagnosis、debate
contracts/        # 正式 API 契約、Python/TypeScript 型別與驗證器
deploy/           # GitHub OIDC、ECR、SSM 與 EC2 部署
demo/             # P6 真實資料、before/after 實驗與簡報素材
frontend-simulator/ # P4 Shopper Simulator
frontend-diagnosis/ # P5 Diagnosis + Debate
contract-samples/ # 舊版契約樣本，僅供參考
scripts/          # 本機環境與 Bedrock 驗證工具
tests/            # 自動測試
```

## 快速開始

需求：Python 3.10+ 與 GNU Make。

```bash
make bootstrap
source .venv/bin/activate
make check
backend/run.sh
```

API 啟動於 `http://localhost:8000`。不設定 AWS credentials 時可使用 mock 模式；Bedrock
設定方式請參考 `backend/.env.example`，但不要提交 `.env` 或任何臨時憑證。

## 常用指令

```bash
make check      # lint、format、contract 與測試
make format     # 自動整理 Python 格式
make contract   # 驗證正式 contract 與 fixtures
make demo       # 驗證 P6 資料與實驗設定
make frontend   # 安裝並建置兩個前端
make bedrock    # 使用目前 AWS credentials 測試 Bedrock
```

## 文件

- [`FRONTEND.md`](FRONTEND.md)：P4/P5 前端串接指南、SSE 與 cookbook
- [`contracts/types.ts`](contracts/types.ts)：前端可直接 import 的 TypeScript 型別
- [`backend/README.md`](backend/README.md)：後端架構、endpoints 與 demo 流程
- [`deploy/README.md`](deploy/README.md)：GitHub Actions 與 AWS demo 部署
- [`demo/README.md`](demo/README.md)：P6 資料、before/after 實驗、講稿與 pitch deck
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：團隊開發與 PR 流程

## CI/CD

GitHub Actions 會在每個 pull request 與 `main` push 執行 lint、格式、contract、測試、
前端建置及容器 smoke test。`main` 的 CI 全部成功後，CD 會用 GitHub OIDC 取得短期 AWS
權限，推送不可變映像到 ECR，再透過 SSM 更新 demo EC2；不需要在 GitHub 保存 AWS access
key。初次建立 AWS 資源與維運方式請見 `deploy/README.md`。
