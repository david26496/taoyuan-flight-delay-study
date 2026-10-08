# Case 2 collection：TDX Departure

## 目標與目前狀態

建立 Arrival + Departure 的真實 flight features 基礎。本次僅新增 TDX Departure；已部署並通過第一次 authenticated live collection 驗收；cron 尚未建立，持續排程收集仍待啟用。

| Source | Arrival | Departure | Source path |
|---|---|---|---|
| Taoyuan Airport | Existing | Existing | `raw/taoyuan/a_flight_*.txt`，同檔 A + D |
| TDX | Existing, active per researcher's confirmation | New, first live run validated; cron pending | Arrival: `raw/tdx/`; Departure: `raw/case2/tdx/departure/` |

桃園現有 a_flight raw 已含 A + D，不新增桃園 collector、workflow 或 cron job。不複製任何既有 raw。Case 1 live collectors/workflows 與 frozen Phase 1/2/3、Master、figures、reports 均不修改。

## 官方 endpoint 與語意

`GET https://tdx.transportdata.tw/api/basic/v2/Air/FIDS/Airport/Departure/TPE`

依 repository 原有官方 Swagger 來源，於 2026-10-08 確認：

- [TDX 官方 OpenAPI](https://tdx.transportdata.tw/webapi/File/Swagger/V3/eb87998f-2f9c-4592-8d75-c62e5b724962)：server `https://tdx.transportdata.tw/api/basic`、path `/v2/Air/FIDS/Airport/Departure/{IATA}`、operation `AirApi_Departure_2012_1`、response `FIDSDeparture`。
- `IATA=TPE` 是出發機場；每列驗證 `DepartureAirportID == TPE`。
- `$format=JSON&$top=1000&$skip=0`，後續 skip 加上實際回傳列數。即使短頁也續抓，直到取得原始 `[]` 終止頁。100 頁上限含終止頁；超限視為失敗。
- 官方欄位包含 `ScheduleDepartureTime`、`ActualDepartureTime`、`EstimatedDepartureTime`、`DepartureRemark`、`DepartureRemarkEn`。它們不是 required，允許 missing/null；但非空頁若完全沒有任何 Departure 時間／狀態欄位，整次收集失敗以防抓錯 schema。
- required identity fields：AirlineID、ArrivalAirportID、DepartureAirportID、FlightDate、FlightNumber、UpdateTime。

## 新增檔案與執行

- `scripts/case2/collect_tdx_departure.py`
- `scripts/case2/persist_snapshots.sh`
- `.github/workflows/case2_tdx_departure.yml`
- 本 README 與 `cron_job_setup.md`

Runtime 沿用 Python 3.12 standard library，不新增 dependencies。唯讀使用 `collection_common.py` 的 HTTP/timeout/retry/atomic-new，以及 `collect_tdx.access_token` 的 authentication；不呼叫 Arrival collect()。

GitHub 使用既有 `TDX_CLIENT_ID`、`TDX_CLIENT_SECRET`，不需要新增 Secret。無 token/body 寫入 logs。原有 User-Agent、identity encoding、30 秒 timeout、最多 4 次 HTTP attempts、退避與禁止 redirect 的安全行為保持一致。

本機執行必須指定 staging root（先以安全方式提供 credentials，不寫 plaintext 檔）：

```sh
python3 -B scripts/case2/collect_tdx_departure.py \
  --output-root '/Users/david/Documents/University/碩一/巨量資料管理/1007/case2/tdx_departure/tmp/live'
```

預設 output root 是 repository，供 GitHub workflow 使用。本機 QA 不使用預設值。raw/log 目錄在執行時建立，不放假 raw 或 placeholder observations。

## Immutable full raw

逐頁保存原始 response bytes，包含原始空白、所有欄位、cargo、cancelled、codeshare、missing AcType/Actual、極端延誤等。沒有 `$select`、cargo/date/airline/destination filter，也不重編 JSON、不補資料。

先在記憶體驗證完整 pagination（總 response 64 MiB 上限），成功取得終止頁後才發布正式 raw。HTTP 失敗、HTML、無內容、非法 JSON、非陣列、錯誤 schema、重複頁、第一頁空陣列、超限等皆只寫 logs，不發布本次 raw。空終止頁 `[]` 是合法分頁證據，並非空 HTTP body。

原始檔名：`tdx_departure_YYYYMMDD_HHMMSS_microseconds_<run_uuid>_pNNN.json`，Asia/Taipei。時間取自該頁成功 request 後；不同 run UUID 區分相同時刻或重複 dispatch。atomic hard-link 禁止覆寫既有檔；寫入後重新核對 SHA256。

metadata 放在 `logs/case2/tdx/`，含 ISO 8601 +08:00、timezone、source=TDX、direction=DEPARTURE、airport=TPE、endpoint、HTTP status、content type、raw path/size/SHA256、rows/pages、終止條件、run UUID、GitHub run/attempt/SHA、成功失敗與錯誤。欄位存在數也記錄在 snapshot metadata，missing 不刪列。

每次完整收集必須有同 run_id 的 `collection_complete`，其 `snapshots` 列出所有原始頁。若發布階段遇到磁碟錯誤或 runner 中斷，可能僅有部分已驗證頁，禁止當作完整 collection；保留以供稽核，不覆寫或就地合併。

## Workflow、排程與 Git

只有 `workflow_dispatch`，沒有新增原生 schedule。建議 cron-job.org 每小時 :22（Asia/Taipei），與 repo 記載的 TDX Arrival :17 錯開 5 分鐘。GitHub 實際外部 dispatch 紀錄確認 2026-10-08 15:17、16:17 均成功；未直接登入 cron-job.org 讀回設定，且不修改任何既有 cron job。详見 `cron_job_setup.md`。

獨立 concurrency group `case2-tdx-departure`、`cancel-in-progress: false`：同一 Departure 不重疊，GitHub 預設最多保留一個 pending，新 trigger 可能取代較舊 pending；不取消正在跑的工作。不加入 Case 1 的 group，避免擠掉 pending Arrival。

跨 Case 的執行仍可能重疊，因此僅 stage `raw/case2/tdx/departure/` 與 `logs/case2/tdx/` 的新檔，拒絕既有 raw/log 變更及預先 staged 變更。每次 push 前 fetch/rebase，push 競爭最多 4 次 attempts，絕不 force push；rebase conflict/fetch failure 明確失敗，workflow recovery artifact 保留證據。時間錯開只減少競爭，不是互斥鎖。

**日期限制：**為重用既有已驗證 HTTP helper，本次繼承其 `2026-10-06T00:00:00+08:00` 至 `2026-11-06T02:00:00+08:00` window。Case 2 超窗會記錄失敗並不發 request。既有 Case 1 也有其原有截止限制，本次未改。Departure 排程只應設定至 11/6 01:22；如需之後繼續，另行規劃 Case 2 的日期策略，不能順手修改 Arrival helper。

## Coverage 與 gap

Historical Case 1、collection gap、restart、TDX Departure 首次成功時間必須分開。README 記載 2026-10-06 16:07:05 TDX、16:08:03 桃園重新啟用驗證成功；這是文件證據，不是本次雲端重新核驗。歷史實際邊界與連續性仍待確認。TDX Departure first valid timestamp 為 **2026-10-08T17:10:54.551714+08:00**（第一個成功 raw page）。本次 run `37754838546` 在 17:10:57.393899 完成；4 頁、2,082 rows，最後為原始 `[]`。Raw/log commit：`4f3fb74e6f47b06d05a291d97f2c793f01806c39`。

`common_start = max(current valid Arrival coverage start, TDX Departure coverage start)`。

目前 common_start candidate 為 **2026-10-08T17:10:54.551714+08:00**（既有 Arrival 重啟早於此刻）；共同起點僅是候選區間，還須核對各來源實際成功快照與中間缺口。較早 Arrival 可留作參考，但不能假裝當時已有 TDX Departure。桃園既有 Departure 不代表 TDX 也有同期間 coverage。不裁切 raw、不補 gap。

詳細本機報告：`/Users/david/Documents/University/碩一/巨量資料管理/1007/case2/tdx_departure/reports/collection_windows.md`。

## 驗證與啟用門檻

離線測試在 `1007/case2/tdx_departure/scripts/test_departure.py`；mock fixtures 僅為測試，不是研究資料。已測 1000+2+空頁、短頁續抓、內容與 SHA、方向、失敗拒收、重試、不覆寫、Git 實際 push race 與 Case 1 不變。

[首次正式 GitHub run 37754838546](https://github.com/david26496/taoyuan-flight-delay-study/actions/runs/37754838546) 的 collect、commit/push 與整體均成功，透過既有 GitHub Secrets 完成認證。本機未取得或保存 TDX credential values。

Live QA：4 頁（1000 + 1000 + 82 + []），2,082 rows，全部 DepartureAirportID=TPE；每页大小/SHA256 與 metadata 一致。20 列抽樣皆為其他 destination airport，ScheduleDepartureTime/EstimatedDepartureTime/DepartureRemark 都存在；ActualDepartureTime 11/20、AcType 16/20、IsCargo 20/20，missing 保留。完整此快照含 142 cargo、5 cancelled、930 missing AcType、911 missing Actual rows，未刪除。數字只描述此快照，不是 unique flights。

Live data commit 僅新增 4 raw + 10 logs；沒有改写既有 raw。push 記錄確認無 race rejection/conflict，未 force push。Case 1 collection 程式及 workflows 與部署前一致，GitHub state 都是 active；近期外部 runs 成功。原生 schedule skipped 是原有外部 scheduler 設計，不是本次停用。

本機驗收證據：`1007/case2/tdx_departure/qa/first_live_validation.json`、`first_live_sample_20_rows.json`、`first_live_git_push.json` 與 `reports/live_acceptance.md`。

**TDX Departure live collection deployment validated.** 只有首次 manual collection 通過，尚未建立 cron；下一步只新增 `Case2 TDX Departure`，每小時 :22（Asia/Taipei），再確認首次定時成功。全部 Case 1 jobs KEEP ACTIVE。收集截止仍為 2026-11-06 02:00 +08:00。

## Future work（本次不執行）

unique flight consolidation、codeshare handling、seat-capacity enrichment、passenger-count generation、Heavy / Light classification 與 modeling 留待另行設計。未生成 Passenger Count、Seat Capacity、Load Factor 或 labels。`snapshot rows ≠ unique flights ≠ modeling observations`；不能拿重複快照累積量宣称已達 10,000 samples。
