# Stock Market Volatility Alert 深度優化報告

Date: 2026-07-30
Baseline: `3e451ca`
Scope: 程式邏輯、通知一致性、GitHub Actions、依賴、測試、雲端運維

## 結論

本輪優先處理會造成漏驗、誤判或重複通知的問題，沒有引入新付費服務或重建整套架構。
完成後，本機可以在任何時段真實驗證 10 隻股票；正式運行會拒絕超過 20 分鐘的陳舊
行情；PushPlus／Telegram 部分成功時，只重試失敗渠道；雲端驗證與正式運行亦不再
共用同一併發組或狀態快取。

## 基準證據

- 配置：10 stocks / 41 alerts。
- 優化前：22 項測試通過。
- 優化前直接批量行情：10／10 成功，約 3.07 秒。
- GitHub Actions cache：699 個、合計約 45.9 MB；主要是每兩分鐘產生的狀態 cache。
- 兩個 2026-07-30 手動乾跑獲 API 接受，但持續 queued 且沒有 job。
- 本機默認 Python 3.9.6 不符合項目 Python 3.11+ 要求，需明確使用新版 Python。

## 已執行優化

### P1 — 全天候真實 smoke test

舊 `--dry-run` 在休市後會跳過全部股票，綠燈不代表行情抓取成功。新增
`--smoke-test`，按交易日曆找最近交易日，在任何時段抓取並驗證全部股票，同時不發
通知、不寫狀態。

驗收：2026-07-30 20:16 HKT，10／10 股票均由 `yfinance-batch` 成功取得，
0 errors；最終回歸約 2.78 秒，且狀態文件雜湊保持不變。

### P1 — 陳舊行情門禁

舊邏輯只檢查 bar 是否屬於當日；若行情上午中斷，下午仍可能使用數小時前價格。
新增 `max_quote_delay_minutes`，正式交易時段超過 20 分鐘即標記失敗並觸發系統
異常流程。smoke test 在休市後只驗證最近交易日，不套用盤中延遲門禁。

### P1 — 部分通知續傳

舊邏輯在 PushPlus 成功、Telegram 失敗時不更新告警層級；下一輪會再次向兩邊發送，
造成已成功渠道重複通知。新增 `pending_notifications` 狀態：保存已成功渠道，下次只
重試失敗渠道；全部成功後才更新告警層級。

### P1 — 驗證與正式任務隔離

乾跑、smoke test 與 production 改用不同的 job-level concurrency group；每個驗證
run 使用獨立 group，不會被舊 workflow-level 鎖阻塞，production 則以新的 `v2`
group 串行。驗證任務亦不再 restore／save production state cache。cache 保存條件
由 `always()` 改為 `!cancelled()`，避免取消中的 job 繼續保存狀態。

GitHub 官方說明：同一 concurrency group 默認只保留一個 pending run，而 cache
內容不可原地更新，只能以新 key 建立新 cache。

### P2 — 自動回歸與供應鏈維護

- 新增 Python 3.11／3.12 CI：配置、編譯及單元測試。
- GitHub Actions 由浮動 major tag 改為完整 commit SHA。
- Dependabot 首輪結果經官方 release notes 核對後，升至 checkout 7.0.1、
  setup-python 7.0.0、cache 6.1.0。
- 新增 Dependabot，每週檢查 pip 與 GitHub Actions。
- `requests` 由 2.32.5 升至 2.34.2。

官方依據：

- [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [GitHub cache semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Secure use of GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use)
- [Dependabot for GitHub Actions](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/auto-update-actions)

## 優化後驗收

- 配置：10 stocks / 41 alerts。
- 單元測試：26／26 通過。
- Python 編譯及依賴一致性檢查通過。
- 三份 GitHub YAML 均可解析。
- 真實 smoke test：10／10、0 errors、約 2.78 秒，狀態文件未變。
- Git 差異格式及常見秘密模式掃描需在提交前保持 0 問題。

## 暫不執行的架構改造

### GitHub Actions 不提供兩分鐘即時性保證

目前 queued 問題發生在 runner 分配前，代碼無法消除。若未來要求準時兩分鐘級監控，
最穩定方案是遷移至香港或鄰近地區的小型常駐主機，由 systemd timer／cron 直接運行，
並使用專用持久化儲存；這會新增主機成本、監控及備份責任，未在本輪擅自引入。

### Actions Cache 仍不是正式狀態庫

現有 699 個 cache 合計只有約 45.9 MB，短期容量風險低；但 cache 不可變且會被淘汰，
仍可能造成當日去重狀態遺失。遷移前應先確定運行平台；若改用常駐主機，可直接使用
SQLite 並做每日備份，避免在 GitHub 上另造一套狀態 API。

### 行情來源仍無 SLA

Yahoo Finance／yfinance 適合個人提示，不適合交易級風控。若告警開始直接影響交易，
應改用有授權、延遲定義及 SLA 的港股／A 股行情供應商，再做雙源比對。
