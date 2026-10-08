# Case2 TDX Departure：唯一新增的 cron-job.org job

## Existing jobs：全部 KEEP ACTIVE

- Case 1 Taoyuan（A + D 混合 raw）：KEEP ACTIVE，包含既有 final supplementary job。
- Case 1 TDX Arrival：KEEP ACTIVE。
- 不改它們的 ID、URL、token、schedule、workflow 或啟用狀態。本次沒有直接操作 cron-job.org。

## New job

Name：**Case2 TDX Departure**。不新增 Taoyuan Departure，也不新增 TDX Arrival。

先將 `.github/workflows/case2_tdx_departure.yml` 與 scripts 部署到 repository 預設分支，手動 workflow_dispatch 通過 live QA，再建立並啟用新 job。

Method：`POST`

URL：

```text
https://api.github.com/repos/david26496/taoyuan-flight-delay-study/actions/workflows/case2_tdx_departure.yml/dispatches
```

通用 pattern：`https://api.github.com/repos/<OWNER>/<REPO>/actions/workflows/<WORKFLOW_FILE>/dispatches`

Headers：

```text
Accept: application/vnd.github+json
Authorization: Bearer <GITHUB_TOKEN>
X-GitHub-Api-Version: 2026-03-10
Content-Type: application/json
```

Body（目前 repo 文件使用 main；若預設分支不同，改成實際預設分支）：

```json
{"ref":"main","inputs":{"trigger_source":"cron-job.org"}}
```

使用具目標 repository Actions write 權限的有效 GitHub token。只在 cron-job.org 的安全設定填值，不放入 repo/報告。TDX credentials 不傳给 cron-job.org，新 workflow 重用既有 GitHub Secrets `TDX_CLIENT_ID`、`TDX_CLIENT_SECRET`，不需新 Secret。

## 建議排程

Timezone：`Asia/Taipei`。每小時 **:22**（cron 表示 `22 * * * *`），與 repo 的 Case 1 TDX Arrival :17 錯開 5 分鐘；現有桃園 00:07／06:07／12:07／18:07 完全不變。

依據：現有 `.github/workflows/collect_tdx.yml` 與 README 2026-10-06 重啟說明；本次未登入讀回外部 scheduler，人工確認它仍為 :17 後才套用。如果實際不同，只调整新 Departure 時間，使其錯開數分鐘。

既有 `docs/external-scheduler.md` 的 9 月驗證顯示 TDX 約 :17:20–24 完成，但這不是 10 月最新 duration 證據；延遲或重試可能超過 5 分鐘。新流程有獨立 concurrency 與安全 fetch/rebase/push retry 保護，沒有更改 Arrival。

收集 window 繼承現有 helper：截至 **2026-11-06 02:00 +08:00**，最後正常 hourly Departure 觸發為 **11/6 01:22**。從實際啟用後才開始，不補歷史觸發。新 job 可設定結束日期；若服務無此功能，屆時僅停用新 Departure job。Case 1 jobs 保持原設定。

## HTTP 與 Action success 分開判斷

[GitHub 官方 workflow dispatch 文件](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) 目前對 2026-03-10 版本列出 **200**，回傳 workflow run ID 與 URLs。這只代表 dispatch 已接受，不能單憑 HTTP 200 判斷資料收集成功；應使用回傳的 run URL 繼續核驗。401/403 檢查 token 與權限；404 檢查 repo 可見性、workflow 是否已部署及檔名。

每次驗證都要：

1. 在 Actions 找到 `Case2 TDX Departure`，確認來源 ref 與 trigger_source。
2. 確認 collect 與 commit/push 步驟成功；不能只看 cron HTTP 回應。
3. 在 `logs/case2/tdx/` 找到相同 run_id 的 `collection_complete`：success=true、pages、total_rows、pagination_termination=empty_page。
4. 核對 manifest 全部 snapshots 存在，大小與 SHA256 一致；含最後原始 `[]` 終止頁。
5. 首次只抽 5–20 rows，確認 DepartureAirportID=TPE、destination 通常為其他機場、Departure 時間／狀態欄位與語意合理、AcType/IsCargo 原樣保留。缺值可保留，異常方向需停止新來源並調查；不要動 Case 1。
6. 記錄首次成功 collection 的實際 timestamp 到 coverage 文件；首次手動通過後，觀察至少下一次定時成功。
7. 若失敗，查看 logs 與 recovery artifact。不要覆寫 raw 或 force push。

本次沒有 commit/push/deploy 或 cron 操作；外部持續運轉狀態仍待研究者驗證。
