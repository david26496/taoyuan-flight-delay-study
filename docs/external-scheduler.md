# 獨立雲端排程切換

GitHub 原生 schedule 實測出現數小時延遲。改用 cron-job.org 發送 GitHub `workflow_dispatch`，蒐集、TDX 認證與 Git 推送仍在 GitHub 雲端。外部觸發仍可能遇到網路或 runner 延遲，不能保證分秒準時。

## 一次性準備

由帳號擁有者建立下列兩個 repository Secrets，不要將值放在對話、程式或文件：

| GitHub Secret | 來源與用途 |
|---|---|
| `CRON_JOB_API_KEY` | cron-job.org Settings 的 API key，供一次性建立排程和查詢執行紀錄 |
| `SCHEDULER_GITHUB_TOKEN` | GitHub fine-grained PAT，只選本 repository，授予 `Actions: Read and write`，建議 2026-09-23 到期 |

專用 GitHub token 會寫入 cron-job.org 工作的 Authorization header，讓該服務呼叫 GitHub；此權限包含 Actions 管理能力，不是僅限單一 workflow 的執行權。TDX Client ID、Client Secret 不傳給 cron-job.org。不要把原有 GitHub CLI 的廣泛授權 token 拿來替代。

## 建立及查驗

Actions → **Configure external cloud scheduler** → Run workflow：

1. `mode=plan`：只顯示不含金鑰的設定。
2. `mode=provision`：建立三項工作，或更新相同名稱及 URL 的既有研究工作；不碰其他工作。讀回設定比對成功後才回報 `verified: true`。連線失敗時不盲目重送 create，重跑前會重新讀取現有工作。
3. `mode=status`：列出工作 ID、啟用狀態、下次執行時間及最近 HTTP 狀態，不列印 headers 或 response body。

三項工作時區都是 Asia/Taipei，到期值 `20260922020000` 表示 2026-09-22 02:00 後不再排程（此欄位不是 Unix timestamp）。

| 工作 | 月／日 | 小時 | 分鐘 |
|---|---|---|---|
| TDX hourly | 9 月 17–22 日 | 每小時 | 17 |
| Taoyuan daily | 9 月 17–21 日 | 0、6、12、18 | 7 |
| Taoyuan final | 9 月 22 日 | 0 | 7 |

外部請求使用 POST，URL 是本 repository 的 `/actions/workflows/collect_tdx.yml/dispatches` 或 `/actions/workflows/collect_taoyuan.yml/dispatches`，body 為 `{"ref":"main","inputs":{"trigger_source":"cron-job.org"}}`。原有 Python 日期檢查仍生效。

## 驗證後切換，失敗可回復

先確認 cron-job.org 的 Test run 或實際排程收到 GitHub 成功回應，再確認 GitHub run 成功且 raw/log 已推送；HTTP 成功只表示 GitHub 接受觸發，不代表航班下載成功。新增的日誌欄位 `trigger_source=cron-job.org` 可用來辨識外部觸發。

確認外部觸發能完成資料提交後，將 repository **Variable**（不是 Secret）`EXTERNAL_SCHEDULER_ACTIVE` 設為 `true`。這會讓原生 `schedule` 跳過蒐集，避免與外部服務重複抓取；`workflow_dispatch` 仍正常。變數未設定前，原生排程繼續運作，遷移不會先中斷蒐集。

若需回復原生排程，將此變數設為 `false`，並在 cron-job.org 停用三項研究工作。研究結束後可撤銷專用 GitHub token 和 cron-job.org API key。

獨立排程只能改善往後的觸發，不能還原過去漏掉的即時快照。仍須以 `collection_complete`、原始檔 SHA256 與實際蒐集時間判斷每小時覆蓋率。

官方依據：[cron-job.org API](https://docs.cron-job.org/rest-api.html)、[GitHub workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)。

## 2026-09-17 運作查驗與切換紀錄

截至 Asia/Taipei 21:20，TDX 外部定時觸發從 10:17 至 21:17 連續 12 次全部成功，完成時間為每小時 17 分 20–24 秒（末次為 23.429 秒）。桃園來源在 12:07、18:07 均成功，完成時間約為 07 分 17 秒。這些是實際定時紀錄，不僅是手動測試。已核對全部 76 個 TDX 與 9 個桃園原始檔的大小及 SHA256，均與 snapshot_saved 日誌一致。TDX 早期 3 次認證失敗已保留稽核紀錄，不算成功快照。

- [最新 TDX 成功紀錄](https://github.com/david26496/taoyuan-flight-delay-study/actions/runs/35226109101)
- [最新桃園成功紀錄](https://github.com/david26496/taoyuan-flight-delay-study/actions/runs/35208787227)
- cron-job.org 工作 ID：TDX 8459883、桃園每日 8459884、桃園最終補抓 8459885。

本次查驗後設定並讀回確認 repository Variable `EXTERNAL_SCHEDULER_ACTIVE=true`。先前原生 schedule 仍有額外快照，全部保留；後續原生 schedule 跳過蒐集，外部與手動 workflow_dispatch 照常執行。歷史漏抓無法由此切換補回，後續仍以實際蒐集時間分析。
