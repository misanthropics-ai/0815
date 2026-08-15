# AWS CI/CD

建議的 demo／staging 流程是：

```text
pull request -> CI -> merge main -> GitHub OIDC -> ECR/SSM/EC2 + S3 frontends
```

- PR 只執行 lint、contract、測試、前端 build 與 Docker smoke test。
- `main` 的 CI 成功後，`Deploy AWS demo` 才會部署該次通過測試的 commit SHA。
- GitHub OIDC 換取短期 AWS credentials，不保存 access key 或 session token。
- ECR tag 是不可變的 commit SHA；部署失敗時遠端腳本會復原上一個映像。
- SQLite 放在 Docker named volume `ai-rec-data`，換版不會刪除資料。
- P4/P5 會在 build 時注入 API 與彼此的 URL，再同步到獨立 S3 website buckets。

## 一次性建立 AWS 環境

需要具備 IAM、CloudFormation、EC2、ECR 與 SSM 管理權限的 AWS 身分，以及已登入的
GitHub CLI。臨時 AWS credentials 只放在目前 shell，不要寫進 repo 或 GitHub Secrets。

```bash
aws sts get-caller-identity
gh auth status
source .venv/bin/activate
python deploy/bootstrap_cicd.py --configure-github
```

腳本會建立或更新：

- account-level GitHub OIDC provider；
- immutable ECR repository（保留最近 30 個映像）；
- CloudFormation stack 與 GitHub deploy role；
- P4 S3 website bucket（保留策略；刪除 stack 不會刪內容）；
- GitHub repository variables：`AWS_ACCOUNT_ID`、`AWS_REGION`、
  `AWS_DEPLOY_ROLE_ARN`、`ECR_REPOSITORY`、`EC2_INSTANCE_ID`、`AWS_API_URL`、
  `P4_S3_BUCKET`、`P4_SITE_URL`、`P5_S3_BUCKET`、`P5_SITE_URL`。

預設沿用 `ai-rec-diagnostics-p5-<account-id>` 作為既有 P5 bucket，並建立
`ai-rec-diagnostics-p4-<account-id>`。若名稱不同，可傳入 `--p4-bucket`／`--p5-bucket`。

bootstrap 會從 GitHub API 讀取實際 OIDC subject prefix；新 repo 使用包含 owner ID 與 repo
ID 的 immutable subject，IAM trust 會鎖定該固定 ID 與 `main` branch，不依賴可能改名的
repo 名稱。

若帳號中剛好有一台 running 且 tag 為 `app=ai-rec-diagnostics` 的 EC2，bootstrap 會沿用
該 instance 與 Elastic IP、補上 ECR／SSM runtime 權限、強制 IMDSv2 並安裝 Docker，**不會
建立第二台 EC2**。也可用 `--existing-instance-id i-...` 明確指定；只有傳入
`--new-instance` 時，CloudFormation 才會建立新的 EC2、security group 與 instance role。

建立新 instance 且帳號沒有 default VPC 時，請加上 `--vpc-id` 和 `--subnet-id`。新環境預設
公開 `http://<EC2>:8000`，只適合 demo；可用 `--allowed-cidr 203.0.113.10/32` 限制來源
IP。若要固定模型，可另外建立非機密 repo variable `BEDROCK_MODEL`。

## 第一次與日常部署

workflow 合併進 `main` 後，到 GitHub Actions 手動執行一次 `Deploy AWS demo`；之後每次
合併只要 `CI` 成功便會自動部署。也可以在 Actions 頁面對 `main` 手動重新部署目前版本。
第一次部署到舊版 systemd EC2 時，遠端腳本會先停止 `backend.service`，將
`/opt/app/backend/data` 搬到 named volume，再啟動 Docker。若容器 health check 失敗，會
自動重新啟動原本的 systemd 服務。

部署完成後：

```bash
curl "$(gh variable get AWS_API_URL)/health"
python contracts/check_contract.py "$(gh variable get AWS_API_URL)"
curl "$(gh variable get P4_SITE_URL)"
curl "$(gh variable get P5_SITE_URL)"
```

如果映像啟動或 health check 失敗，workflow 會失敗且 EC2 會嘗試重啟上一個映像。若需要
指定舊 commit，從該 commit 建立修復 commit 並合併，或在確認程式碼狀態後重新執行對應
workflow；不要覆寫既有 ECR tag。

## 安全與正式環境界線

CloudFormation 把 OIDC trust 限制在 `misanthropics-ai/0815` 的 `main` branch，EC2 強制
IMDSv2，部署不使用 SSH。EC2 透過 instance role 存取 ECR 與 Bedrock，credentials 不會
進入映像或環境檔。

目前架構是單台 EC2、公開 HTTP 與本機 SQLite，適合 workshop、demo、staging，不適合直接
承載正式客戶流量。正式版至少應補上 authentication、HTTPS／load balancer、私有 subnet、
WAF、集中式 logs／alarms，以及將 SQLite 遷移到有備份與高可用性的 managed database。

## 舊版手動工具

`deploy/deploy_ec2.py` 與 `deploy/deploy_aws.py` 保留供除錯或既有環境使用；前者保留
Elastic IP 與遷移前的 `--update` SSM 更新能力。兩者都要求 runtime IAM role，絕不會把
本機 AWS credentials 寫入 EC2 user-data 或服務環境變數。開始由 Docker CI/CD 管理後，
團隊正常部署只使用 GitHub Actions，不再執行 legacy `--update`。
