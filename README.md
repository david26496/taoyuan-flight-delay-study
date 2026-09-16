# 桃園國際機場入境航班來源地理區域與抵達延誤之探索性分析

此 repository 只負責保存可追溯的原始航班快照。由 **GitHub Actions 的雲端 Ubuntu runner** 執行，排程不依賴個人電腦開機。Python 3.12，僅使用標準函式庫；`requirements.txt` 無第三方執行依賴。

## 研究日期與排程

所有程式日期判斷、檔名及 `collected_at` 均使用 `Asia/Taipei`（UTC+08:00）。研究航班日期為 **2026-09-17 00:00:00 至 2026-09-21 23:59:59**。快照可能包含其他日期、出境航班或共掛班號，**完整保留**，不在此階段清洗、篩選或 codeshare 去重。

| 來源 | 台北預定執行時間 | GitHub cron（明確使用 UTC） | 蒐集範圍 |
|---|---|---|---|
| 桃園機場 | 每天 00:07、06:07、12:07、18:07 | `7 4,10,16,22 * * *` | 9/17–9/21，另 9/22 00:07 補抓一次 |
| TDX | 每小時第 17 分鐘 | `17 * * * *` | 9/17 00:00 至 9/22 02:00（含） |

- 桃園例行 20 次，加補抓 1 次。9/22 允許補抓的時間窗為 00:00–02:00；若已存在該日成功的原始快照，手動重跑會跳過，失敗則可在窗內重試。
- TDX 預定首筆 9/17 00:17，末筆 9/22 01:17，共 122 次執行。02:17 超出範圍，所以不下載。這是「每小時第 17 分鐘」與「02:00 截止」同時成立的安排。
- 手動執行也受相同日期限制，**沒有強制忽略日期的開關**。窗外 exit 0，輸出 `SKIPPED`，不讀 Secrets、不呼叫 HTTP、不建立 raw 或 log。每次 retry、分頁請求和寫入前也重新檢查日期。
- cron 不含年份；程式硬性限制 2026 年。研究結束後可在 Actions 停用兩個 workflow，避免持續啟動空跑工作。
- GitHub 排程可能延遲或遭丟棄，非精準計時服務；因此以**實際蒐集時間**命名，不偽裝成原定時刻。延遲超過 02:00 就不再蒐集。[GitHub 排程限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## A. 桃園機場來源（2026-09-16 核對）

採用指定的 [a_flight_v4.txt](https://www.taoyuan-airport.com/uploads/flightx/a_flight_v4.txt)：

```text
https://www.taoyuan-airport.com/uploads/flightx/a_flight_v4.txt
```

已實測 HTTP 200、`text/plain`，是逗號分隔的無表頭資料，含入境 A／出境 D、航廈、航空公司、航班號、表訂日期時間、預計日期時間、往來地點、航班狀態、機型等。驗證只檢查 ASCII 分隔符與記錄結構，不將內容轉為 UTF-8。

[機場官方欄位文件](https://www.taoyuan-airport.com/api/imagecrop/fileid/82F74AE3-6C9F-EC11-BBB7-005056BD475F) 的 ASCII 說明不能當作中文實際編碼保證；實測含非 ASCII 中文 bytes。程式不宣告或轉換原始 encoding，連 CRLF、空白都保留。

[政府資料集](https://data.gov.tw/dataset/26194) 目前另連到 `https://odp.taoyuan-airport.com/dataset/2023081816?format=csv`，其格式不同，實測內容出現 2023 年資料，因此未靜默替換指定的即時 v4 來源。若 v4 日後失效，程式報錯，不把網站首頁或另一資料集當作研究資料。

儲存：`raw/taoyuan/a_flight_YYYYMMDD_HHMMSS.txt`。

## B. TDX 官方 API 與真實 schema（2026-09-16 核對）

採用 `GET`：

```text
https://tdx.transportdata.tw/api/basic/v2/Air/FIDS/Airport/Arrival/TPE
```

來源：[官方 Swagger UI](https://tdx.transportdata.tw/api-service/swagger/basic/eb87998f-2f9c-4592-8d75-c62e5b724962)、[官方 OpenAPI JSON](https://tdx.transportdata.tw/webapi/File/Swagger/V3/eb87998f-2f9c-4592-8d75-c62e5b724962)。與本研究相關的 endpoint、schema、認證定義及完整規格檔 SHA256 已保存在 [`docs/tdx-arrival-schema.json`](docs/tdx-arrival-schema.json)。

回傳為 `FIDSArrival` 物件陣列。直接保存 API 真正提供的欄位，沒有自行發明 `ScheduledArrival`、`OriginAirport` 或 `FlightStatus` 欄位：

| 研究概念 | 官方欄位 |
|---|---|
| 航班日期 | `FlightDate` |
| 表訂抵達 | `ScheduleArrivalTime` |
| 預估抵達 | `EstimatedArrivalTime` |
| 實際抵達 | `ActualArrivalTime` |
| 班號、航空公司 | `FlightNumber`、`AirlineID` |
| 出發／抵達機場 IATA 代碼 | `DepartureAirportID`、`ArrivalAirportID` |
| 狀態 | `ArrivalRemark`、`ArrivalRemarkEn` |
| 其他提供欄位 | `AirRouteType`、`Terminal`、`Gate`、`Apron`、`CodeShare`、`IsCargo`、`AcType`、`BaggageClaim`、`CheckCounter`、`UpdateTime` |

時間欄位可為 null 或未提供，保留原樣。schema 有欄位不代表每一筆實際資料都會填入；本次尚未以研究帳號 Secrets 完成 TDX 實際認證下載。schema 說明中的抵達時間格式為 `yyyy-MM-ddTHH:mm`，`UpdateTime` 含時區，後續分析才處理時間解讀。`FlightNumber` 本身不含 `AirlineID`；來源地區需另行對照機場代碼，這一步不做。

認證採官方 [OIDC／OAuth 2.0 Client Credentials](https://motc-ptx.gitbook.io/tdx-xin-shou-zhi-yin/api-shi-yong-shuo-ming/api-shou-quan-yan-zheng-yu-shi-yong-fang-shi)：

1. `POST https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token`。
2. 表單 `application/x-www-form-urlencoded`，包含 `grant_type=client_credentials`、`client_id`、`client_secret`。
3. 航班 API 使用 `Authorization: Bearer <access_token>`。

每次 workflow 只取得一個 token，存在記憶體供所有分頁與 retry 共用；不將 token、認證 request body 或 token response body 寫進 Git、log、artifact 或 cache。認證錯誤只記錄安全的狀態，不列印憑證內容。

### 分頁與完整性

官方列有 `$top` 預設 30，故請求明確帶 `$format=JSON`、`$top=1000`、`$skip=0`，後續按實際筆數遞增 `$skip`，直到收到空陣列。即使回傳少於 1000 筆，也繼續查下一頁，避免伺服器上限造成截斷。最多 100 頁，重複的非空頁會報錯。沒有 `$select`、`IsCargo` 或日期篩選。

**每一頁都各自保存完整原始 HTTP body**，檔名均為 `raw/tdx/arrival_TPE_YYYYMMDD_HHMMSS.json`。包含表示分頁結束的合法 `[]`（不是失敗時寫出的零 bytes 空檔）。同次執行的各頁由 log 的 `run_id`、`page`、`skip`、`row_count` 及 `collection_complete.snapshots` 關聯。多頁可能在相鄰秒命名，不拼接或重新序列化 JSON。API 在翻頁過程可能更新，後續研究應檢查跨頁一致性；此 API 並未保證交易式快照。

## 原始檔與稽核紀錄

```text
scripts/collect_taoyuan.py
scripts/collect_tdx.py
scripts/collection_common.py
scripts/persist_snapshots.sh
.github/workflows/collect_taoyuan.yml
.github/workflows/collect_tdx.yml
raw/taoyuan/  # 首次有效下載後建立
raw/tdx/
logs/taoyuan/
logs/tdx/
```

- HTTP 使用 30 秒 socket timeout；連線中斷、不完整內容、408／416／423／429／500／502／503／504 最多嘗試 4 次，等待 2、4、8 秒；數字 `Retry-After` 可延長等待，但上限 60 秒。其他 HTTP 失敗回傳非 0。
- 明確要求 `Accept-Encoding: identity`，不自動解壓縮或轉碼。若伺服器仍壓縮就報錯。保存 HTTP response **body bytes**（HTTP 傳輸分塊框架與 headers 不算原始資料檔內容）。拒絕空 body、HTML、非預期結構；不使用 304 快取，也不跟隨 redirect。
- 先完整接收與驗證，再用同目錄暫存檔與 atomic no-clobber link 發布。磁碟既有檔永不取代；同秒檔名衝突最多等 4 次，使用真實下一秒時間，不能成功則報錯。
- `.gitattributes` 對 raw 停用換行正規化，確保 Git 取回後 bytes 不變。
- `logs/<source>/` 每個事件一個不可覆寫的 JSON。HTTP 嘗試記錄 `collected_at`、`source_url`、`http_status`、`response_size`、`sha256`、嘗試次數，以及安全的 response metadata；無 HTTP response 時狀態、大小、hash 為 null。token response 的 hash 固定為 null。
- `snapshot_saved` 表示已驗證並保存；`collection_complete` 表示本次全部頁完成；`collection_failed`／`window_closed` 要查明原因。部分成功的真實頁可保留，但不得把它當成完整一次蒐集。
- 兩個 workflow 共用 concurrency group，不會主動中斷已開始的工作。Git 僅加入新的 raw/log，無變更不 commit；push 衝突先 fetch/rebase，最多 4 次推送，不 force-push。
- 下載失敗仍提交稽核 log，workflow 保持失敗；push 或下載失敗時另保存 14 天 recovery artifact（raw 與安全 log）。若 checkout、runner 或 GitHub 本身失效，artifact 也可能無法生成。

## 啟用 GitHub Actions

1. 將程式和兩份 workflow 推送至 repository 的 **預設分支 `main`**。如果預設分支不是 main，先在 GitHub 設定正確分支；GitHub 排程只使用預設分支。手動選到其他分支時，job 會跳過。
2. Repository → **Settings → Secrets and variables → Actions → New repository secret**，分別建立：
   - `TDX_CLIENT_ID`
   - `TDX_CLIENT_SECRET`
3. 值從 TDX 會員中心的 API 金鑰頁取得，確認帳號可使用航空基礎服務。**不要將值貼到程式、README、issue 或 commit**。不需另加 PAT；workflow 用 GitHub 自動提供的 `GITHUB_TOKEN` 推送。
4. Settings → Actions → General：允許使用 GitHub Actions 及本 workflow 使用的官方 actions。workflow 已宣告 job 的 `contents: write`；組織政策、ruleset 或 main 的 branch protection 仍可能禁止 bot 直接 push，須由管理者安排允許的自動資料提交方式，不能假設 YAML 可以繞過政策。
5. 確認 private repository 的 Actions 可用額度／支出上限足夠，且 Actions 未停用。

以上設定完成並且 workflow 位於 main 後，排程由 GitHub 雲端執行，關掉個人電腦也不影響。

## 手動執行與確認排程

Repository → **Actions** → 選擇 **Collect Taoyuan flight snapshots** 或 **Collect TDX TPE arrivals** → **Run workflow** → 分支選 `main` → Run workflow。

研究期間前手動執行應顯示 `SKIPPED`，這可驗證 workflow 啟動，但**無法驗證 TDX Secrets 或資料 endpoint**；需在蒐集窗內確認第一次有效下載。

每次檢查：

1. Actions run 清單是否有 `schedule` 觸發的新紀錄（手動執行的 `workflow_dispatch` 不等於排程已成功）。比較 run 實際開始時間與上表預定台北時間。
2. Collect 步驟有 `snapshot_saved` 和 `collection_complete`，而非僅綠色的 `SKIPPED`。
3. Commit 步驟成功，`main` 最新 commit 包含對應 raw 檔及 log；`collected_at` 為預期日期和 `+08:00`。
4. TDX 頁面直到空陣列且整組有 `collection_complete`；確認時間欄位的實際可用程度。
5. 若紅色失敗：檢查 HTTP status（401 常見為認證／權限、429 為限流），及推送權限；下載 recovery artifact 保存證據，再於時間窗內重跑。缺少排程 run 也要列為研究資料缺口，不能用稍晚資料偽裝補成當時快照。

本機語法與離線測試指令（原始檔完整性則需另以 SHA256 對照 log）：

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
bash -n scripts/persist_snapshots.sh
actionlint .github/workflows/collect_taoyuan.yml .github/workflows/collect_tdx.yml
```

測試使用假資料、暫存目錄與模擬時鐘，不需 Secrets，也不建立研究 raw。測試覆蓋日期邊界、HTML／空回應、retry、檔案不覆寫、原始 bytes／SHA256、分頁與憑證不洩漏。正式執行入口：`python scripts/collect_taoyuan.py` 與 `python scripts/collect_tdx.py`。

## 研究解讀限制

這些是即時來源的觀測快照，不保證完整歷史或最終狀態。9/22 補抓有助觀察 9/21 深夜航班，但不保證每個航班已落地或仍被來源保留；不得直接把 Estimated Arrival 當 Actual Arrival。來源機場地理區域對照、延誤計算、缺失值處理、航班日期篩選及 codeshare 去重，均留待後續分析。
