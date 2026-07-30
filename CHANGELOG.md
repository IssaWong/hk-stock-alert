# CHANGELOG

## 2026-07-30 — 第二輪深度優化

### Reliability

- 新增全天候 `--smoke-test`，以最近交易日行情驗證全部 10 隻股票。
- 新增 20 分鐘陳舊行情門禁。
- 新增多渠道通知續傳，部分成功後只重試失敗渠道。
- concurrency 鎖移至 job 層；validation 使用逐 run 獨立 group，production 使用
  新的 `v2` 串行 group，避開舊 workflow-level 卡死狀態。validation 不再讀寫
  production state cache。
- cache 保存由 `always()` 改為排除已取消 run。

### Supply chain

- Actions 固定至完整 commit SHA，並升至 checkout 7.0.1、setup-python 7.0.0、
  cache 6.1.0。
- 新增 Python 3.11／3.12 CI 與每週 Dependabot。
- `requests` 由 2.32.5 升至 2.34.2。

### Validation

- 單元測試由 22 項增加至 26 項，全部通過。
- 2026-07-30 20:16 HKT 真實 smoke test：10／10、0 errors、約 2.78 秒，
  狀態文件未變。

## 2026-07-30 — 雲端接管與排程恢復

### Deployed

- PR #1 及 PR #2 已合併至 `main`；遠端接管版本為 `80c57fd`。
- GitHub workflow 已恢復 active，並加入安全的手動 `dry_run` 輸入。
- cron-job.org job `7693116` 已啟用，排程由 `*/2 9-16 * * 1-5` 收窄為
  `*/2 9-11,13-15 * * 1-5`。

### Security

- 新建只限 `IssaWong/hk-stock-alert`、Actions read/write、90 日有效的
  fine-grained PAT，並更新至 cron-job.org。
- 撤銷舊 `cron-job-trigger` 無到期日 classic PAT。
- 新 PAT 讀取 workflow 返回 HTTP 200，乾跑 dispatch 返回 HTTP 204。
- PushPlus、Telegram 及 cron-job.org 全局 API key 尚待輪換。

### Validation

- 2026-07-30 重新通過 22／22 單元測試、配置、依賴及 Python 編譯檢查。
- 10／10 股票取得 2026-07-30 同日批量行情。
- 兩個新版雲端乾跑獲 API 接受，但仍停留 queued 且沒有 job；完整雲端執行日誌
  尚待 GitHub runner 分配後驗收。

## 2026-07-28 — 深度接管（本地，未部署）

### Decision

- 用戶確認本項目自 2026-07-28 起由 Codex 長期接管。
- 舊 Claude Code 資料降為歷史參考，正式狀態改由項目接管文檔及實測證據維護。

### Added

- `README.md`、`CONTEXT.md`、`HANDOFF.md`、`AUDIT.md`。
- 配置驗證、乾跑模式、原子狀態寫入、行情日期校驗及重試。
- 22 項核心單元測試。
- `.env.example` 與明確秘密管理邊界。
- 批量行情抓取及缺失代碼逐股回退。
- XHKG／XSHG 正式交易日曆、告警遲滯、每日健康摘要及系統異常冷卻告警。

### Changed

- `yfinance` 由 0.2.61 升至 1.5.2，`requests` 升至 2.32.5，新增
  `exchange-calendars 4.13.2`。
- PushPlus 改用 HTTPS，通知接口開始校驗 HTTP 及 API 結果。
- 價格目標與漲跌幅改為獨立告警組，保留舊狀態遷移。
- 行情或通知失敗會令運行標紅；失敗通知不會提前寫入成功狀態。
- GitHub Actions 升至 checkout v6、cache v5、setup-python v6。
- workflow 加入最小權限、5 分鐘超時及串行控制。
- 移除重複的 GitHub `schedule`，保留 cron-job.org 每 2 分鐘主排程。
- 舊 shell 改為安全退役提示，不再包含憑證。

### Security

- 清除本地交接、舊 shell、工具權限配置及對話存檔內可識別的明文秘密。
- 將舊對話存檔及本機工具配置加入 `.gitignore`。
- 外部憑證尚未輪換，安全整改仍未完成。
- 2026-07-28 15:59 HKT，GitHub 因倉庫 60 天無提交自動停用 workflow；待登入、
  推送及重新啟用，雲端告警目前停止。

### Validation

- `config.json`：10 stocks / 41 alerts。
- 單元測試：15／15 通過。
- `yfinance 1.5.2`：10／10 股票取得 2026-07-28 當日行情。
- 依賴一致性、Python 編譯、workflow YAML、差異檢查全部通過。
- 工作樹及 Git 歷史常見秘密格式掃描：0 命中。
