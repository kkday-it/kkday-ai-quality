# deploy/ — EKS 部署草稿（供 cloud team review）

> **本目錄全部是草稿**：對齊公司規範（EKS＋Kustomize＋ArgoCD＋Woodpecker→ECR＋Jenkins，
> 見 KG/2065203221 六步 SOP）預先起草，**正式版落 `kkday-it/kkday-k8s-apps` repo**（目錄結構/
> labels/ArgoCD 慣例以該 repo 既有專案為準對齊）。任何 REPLACE_ME 佔位欄位未經 DevOps/SRE
> 確認前**不得 apply 到真實叢集**。

## 結構

```
base/                      # Deployment×2（backend Recreate·frontend RollingUpdate）+ Service×2 + Ingress + ConfigMap/Secret 範例
overlays/prod/             # namespace/host 環境差異（SIT/STAGE 屆時比照增設）
../.woodpecker.yml         # CI 草稿（tag → build → ECR；與 GH Actions 分工待確認）
```

本地驗證：`kubectl kustomize deploy/base`（語法）＋ `kubectl apply --dry-run=client -f -`（schema）。

## 設計要點（與 repo 內三處數值互鎖）

- **backend `Recreate` + `replicas: 1`**：4 套 job registry 為 in-mem（backend/Dockerfile CMD 註解），多 replica 會分裂 job 狀態。
- **timeout 鏈遞增**：uvicorn `--timeout-graceful-shutdown 30`（Dockerfile）< compose `stop_grace_period 35s` < k8s `terminationGracePeriodSeconds 40`（backend-deployment.yaml）——改任一須同步三處。
- **probe**：startupProbe `/api/status` 5 分鐘預算（涵蓋 entrypoint alembic upgrade）→ readiness HTTP → liveness TCP（公司慣例）。
- **secret**：`aiq-backend-config` / `-secret` / `-cloud-secret` 三物件（config-manager 平台維護實值＋Reloader 自動滾動重啟）；repo 內僅 key 名。

## ⚠️ 需與 SRE 對齊的風險：Recreate 空窗 vs「滾動更新無 5xx」DoD

backend 單 replica + Recreate 在部署更新時有「舊 pod 終止→新 pod 就緒」空窗（含 image pull
＋ entrypoint migration 時間），期間 `/api` 502/503——與 EKS 上線 DoD「滾動更新期間無 5xx」
存在張力，**非 app 層可單獨解**。可選方向：①接受短空窗（內部工具低峰部署）②縮短空窗
（預拉 image、migration 拆 PreSync Job）③P1 將 job 狀態遷 Redis/PG 後改多 replica + RollingUpdate。
frontend 已用 replicas:2 + RollingUpdate（無狀態），SPA shell 層無空窗。

## 待確認清單（cloud team / SRE / DevOps 逐項過）

| # | 項目 | 窗口 |
|---|---|---|
| 1 | ECR repo 命名（aiq-backend / aiq-frontend？）＋ SIT/STAGE(ap-southeast-1)/PROD(ap-northeast-1) | DevOps Step 1 |
| 2 | image tag 策略（Woodpecker 寫 tag vs ArgoCD Image Updater） | DevOps |
| 3 | Ingress class / annotations（scheme/group/certificate ARN）＋ host（SIT `<svc>.sit.kkday.com` 慣例） | SRE |
| 4 | namespace 歸屬 | SRE |
| 5 | config-manager 內 aiq-backend-config/-secret key 清單 ↔ `backend/app/core/config.py` Settings 欄位逐一對齊 | RD＋DevOps |
| 6 | resources requests/limits（現為未壓測初估）上線後依 Prometheus 實測修正 | RD |
| 7 | RDS endpoint（開通後回填 cloud-secret DATABASE_URL） | DevOps |
| 8 | Woodpecker vs GitHub Actions 分工（PR gate / build 職責切分） | DevOps |
| 9 | 非 B2CBE 團隊的 cloud team 對接 Slack channel／窗口 | Paul |
| 10 | Kibana log index template（log_type=aiq-backend）＋ Grafana dashboard＋alert route 初始化 | DevOps checklist |

## RDS 相容性檢查清單（自家庫 kkdb_ai_quality 由容器 PG 遷 RDS 前逐項驗）

1. **SSL**：`DATABASE_URL` 顯式帶 `?sslmode=require`（或 `verify-full`＋CA bundle）；engine 未顯式設 sslmode，若 RDS `rds.force_ssl=1` 需事前實測。
2. **PG 版本**：本地 `postgres:17-alpine`；確認公司 RDS 是否支援 17（僅到 16 需排查 migration 有無 17-only 語法）。
3. **alembic 執行身份**：RDS 使用者需 schema CREATE/ALTER/DROP；若 migration/runtime 帳號分離需另設計（本輪未做）。
4. **migration 模式**：entrypoint-in-every-pod 於 replicas:1 安全；多 replica 前需改 ArgoCD PreSync Job 防 DDL 競態。
5. **連線池**：pool 10＋overflow 20＝30/replica；對照 RDS `db.t4g.small` 的 `max_connections` 留餘裕。
6. **空庫初始化**：首次走 `init_db`（create_all＋stamp head）；正式 cutover 前對 scratch RDS 乾跑一輪 `upgrade head` 驗證。
7. **VPC/SG**：EKS pod → RDS 連通性屬 SRE；確認 pod 內 DNS 可解析 RDS endpoint。
8. **認證方式**：現為靜態密碼 URL；若要求 RDS Proxy/IAM 短效 token 需另設計刷新（本輪未做）。
9. **Locale**：alpine（musl C.UTF-8）vs RDS（glibc en_US.UTF-8）排序行為差異——依賴中文 ORDER BY 的功能上線前抽樣比對。
10. **Extension**：schema 掃描無 CREATE EXTENSION；仍以乾跑驗證兜底。
11. **備份腳本**：`scripts/ops/backup-db.sh` 走 `docker compose exec`，RDS 不適用——需改 bastion/pod 直連 `pg_dump -h <endpoint>` 變體；RDS 自動備份（每日 7 天＋AWS Backup 每 6 小時＋每月留 4 個月，KKDEVOPS/2890440）為主、腳本為輔的關係需定案。
12. **保留合規**：RDS 7 天 retention 是否滿足資料保留要求，與 `backups/db/` 手動備份互補關係。
