# AWS 部署

## 路徑 0（最快，不需要 docker）：EC2 一鍵腳本

```bash
python deploy/deploy_ec2.py            # zip 程式碼→S3→開 EC2(t3.small)→systemd 起服務→印出 URL
python deploy/deploy_ec2.py --status   # 查狀態 / health URL
python deploy/deploy_ec2.py --terminate
```
- 會嘗試建 instance role（`EC2Bedrock0815`，bedrock:Invoke* + SSM）→ 服務器**不依賴會過期的 session creds**；IAM 被鎖就退回把 creds 寫進 `.env`（過期後重跑 deploy 即可）。
- 使用 **Elastic IP**（tag: `app=ai-rec-diagnostics`）：重佈署 URL 不變。目前為 `http://34.227.93.223:8000`。
- 開機 + pip install 約 1–2 分鐘後 `/health` 就緒。

## 路徑 A（需要本機 docker）：App Runner 一鍵部署

前置：本機有 docker、`backend/.env` 有有效 AWS creds。

```bash
python deploy/deploy_aws.py            # build → ECR push → App Runner create/update → 印出 URL
python deploy/deploy_aws.py --status   # 查狀態 / URL
```

- 腳本會嘗試建立 **instance role**（`AppRunnerBedrock0815`，帶 bedrock:Invoke* 權限）——成功的話部署後的服務**不依賴會過期的 session credentials**。
- 若 workshop 帳號不給建 IAM role，會退回把 `.env` 的 session creds 塞進服務環境變數。creds 過期後跑：
  ```bash
  # 先更新 backend/.env 的三個 AWS_* 值，然後：
  python deploy/deploy_aws.py --update-env
  ```

## 路徑 B：EC2 手動（IAM 完全鎖死時）

1. Console 開一台 **Amazon Linux 2023 t3.small**，Security Group 開 8000/tcp。
2. SSH 進去：
   ```bash
   sudo dnf install -y git python3.11 python3.11-pip
   git clone https://github.com/misanthropics-ai/0815.git && cd 0815
   python3.11 -m pip install -r backend/requirements.txt
   # 把本機的 backend/.env 內容貼到伺服器的 backend/.env
   nohup python3.11 -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 &
   ```
3. `http://<EC2_IP>:8000/health` 驗證。creds 過期就更新 `.env` 重啟。

## 路徑 C：本機 docker（隊友 demo 用）

```bash
docker compose up --build       # 需要 backend/.env
# 或不用 docker：
backend/run.sh
```

## 部署後驗證

```bash
curl https://<URL>/health                        # bedrock.ready 應為 true
python contracts/check_contract.py https://<URL> # 契約驗證
```
