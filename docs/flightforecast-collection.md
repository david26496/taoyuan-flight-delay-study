# 航班運量整點人數預報表收集

2026-10-28 起，研究者改用桃園機場官方預報表，TDX Arrival / Departure 後續收集停用。歷史 TDX raw/log 保留。

## 官方來源與內容

- 官方頁面：<https://www.taoyuanairport.com.tw/flightforecast>
- 該網站 frontend 的 `thirdparty/fos` 清單，在相同官方網站可讀取：<https://www.taoyuanairport.com.tw/api/api/thirdparty/fos>
- 頁面明列「每日初版」與「每小時更新版」，各有 XLS、ODS。檔案內文也寫明「本檔案每小時更新」。例如 `2026_10_28_update.xls` / `.ods`。
- 這是按時間區間、航廈及旅客流向彙整的**人次預報**，不是每個航班的實際旅客數。原始值與欄位完整保存，不補值、不生成 Passenger Count 或 labels。
- Collector 驗證官方清單中的檔案 URL、日期及初版/更新版語意；ODS URL 依官方頁面展示的同 stem 連結取得，不猜其他日期檔名。

## 自動流程

`Collect Taoyuan hourly passenger forecasts` → 原始清單 → XLS/ODS 驗證 → immutable raw + JSON audit → scoped commit/push。

- 手動：GitHub Actions → 選擇 workflow → Run workflow。
- 定時：cron-job.org 一個 `Taoyuan flightforecast hourly [david26496/taoyuan-flight-delay-study]` job，每小時 **:12，Asia/Taipei**。
- 沿用既有研究截止：**2026-11-06 02:00 +08:00**，最後正常 hourly trigger 為 **11/6 01:12**。本次不延長既有 study window。
- 桃園 `a_flight_v4.txt` 原有 A+D collector 與 :07 排程保留。
- `Configure active collection sources` 的 `apply`：停用同 repository 的所有 TDX cron jobs（包含十一月），建立或更新唯一 forecast job。`status` 僅讀回設定與最近狀態。
- 使用既有 `CRON_JOB_API_KEY` / `SCHEDULER_GITHUB_TOKEN`，無新 Secret，TDX Secrets 不再被新流程使用。
- TDX workflows 另以 disabled 狀態與 job-level false guard 停止；舊 provision 已移除 TDX specs，舊 TDX repair entrypoint 也拒絕執行，避免誤重新啟用。

## 保存位置與版本

- `raw/taoyuan_forecast/index/<sha256>.json`：原始官方清單。
- `raw/taoyuan_forecast/YYYY-MM-DD/initial/`：每日初版 XLS/ODS。
- `raw/taoyuan_forecast/YYYY-MM-DD/hourly/`：可見的更新版 XLS/ODS。
- `logs/taoyuan_forecast/<collection_timestamp>_<run_uuid>.json`：每次收集 audit。
- 每個檔名含來源原始 stem 與完整 SHA256；相同內容只保存一次。相同 URL 若官方內容改變，存為新 SHA 檔，不覆寫。
- metadata 保留 Asia/Taipei / ISO8601、官方 URL、報表日期/版本/格式、產出時間、HTTP status/content type、Last-Modified、size/SHA256、run UUID/GitHub run、saved/unchanged/historical_cached 與錯誤。
- XLS 由固定版本 `xlrd==2.0.2` 解析；ODS 驗證 ZIP、XML、sheet 日期與報表標題。官方 ODS 缺少 `mimetype` ZIP entry 是實際格式特徵，故驗證實際 spreadsheet XML，而非誤判為壞檔。
- 首次執行保存目前官方清單所有可用檔案（頁面約 30 日）。這些只是當下官方可下載的版本，**不代表回補了過去每小時的歷史快照**。
- 後續持續檢查今天、明天及昨天，舊日期已保存檔案不重複下載；如要重查官方仍列出的所有歷史版本，可手動加 `--refresh-history`。
- 空回應、HTML、錯誤格式/日期/標題不進正式 raw；錯誤記錄在 audit。partial failures 使 workflow failure，已通過驗證的檔案與 audit 仍會安全保存。

## 本機驗證

所有本機輔助檔放在 `/Users/david/Documents/University/碩一/巨量資料管理/1007/flightforecast_integration/`。

```sh
python -m pip install -r requirements-flightforecast.txt
python -B scripts/collect_flightforecast.py --current-only \
  --output-root '/Users/david/Documents/University/碩一/巨量資料管理/1007/flightforecast_integration/tmp/live'
```

`--current-only` 僅是 staging smoke test 選項；正式 workflow 保存全部尚未建檔的官方清單資料。

## Git 安全

獨立 forecast concurrency group，cancel-in-progress=false；只 add `raw/taoyuan_forecast/`、`logs/taoyuan_forecast/` 新檔。Push 前 fetch/rebase，最多四次安全重試，無 force push；失敗時 recovery artifact 保留原始證據。與既有桃園 raw collector 的資料路徑互不覆蓋。

## 2026-10-28 首次部署驗收

- [首次正式收集 run 40652033812](https://github.com/david26496/taoyuan-flight-delay-study/actions/runs/40652033812)：collect、commit/push 成功，122 個報表（61 XLS + 61 ODS），涵蓋 2026-09-29 至 2026-10-29。31 日初版、30 日更新版，各兩種格式。
- Data commit：`7511bcedb3a18c1712151ce6356ba3bbedcfcd1b`。已逐檔核對 size/SHA256 與 workbook parse，全數通過；commit 僅新增 forecast raw/index/log。
- [來源切換 run 40652274093](https://github.com/david26496/taoyuan-flight-delay-study/actions/runs/40652274093)：成功停用 TDX jobs `8459883`、`8589253`、`8589254`、`8604010`；沒有刪除 jobs 或歷史 raw。
- 新預報表 job `8739783`：enabled=true、Asia/Taipei、minute=12、expiresAt=20261106020000，均已直接讀回。下一次定時為 2026-10-28 16:12 +08:00；本次驗收是 manual run，尚未把未來定時執行當成已成功。
- 本機 staging 6 檔成功後，重跑全部 unchanged；6 項 forecast/scheduler 範圍檢查及 4 項既有桃園 scheduler regression checks 通過。
- 本機證據：`1007/flightforecast_integration/qa/github_raw_acceptance.json`、`cloud_acceptance_events.json`。

## 2026-10-31：單一外部排程

保留 `Taoyuan flightforecast hourly` 每小時 :12 的 cron-job.org job，GitHub workflow 每次收集預報表，並在台北時間 00、06、12、18 時加收桃園航班 raw；最後補抓為 11/6 00:12，截止仍為 02:00。手動驗證可選 `force_taoyuan=true`。排程是依 workflow 實際啟動時的小時判斷；若延遲跨越整點可能漏掉該次航班快照，應查看 logs。排程整合前應先驗證一次手動執行，再停用原 October、November、final jobs；不要刪除歷史 raw。
