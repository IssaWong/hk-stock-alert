# CONTEXT | Stock Market Volatility Alert

Last updated: 2026-07-30

## 維護權責

- 2026-07-28 起，本項目由 Codex 正式接管及持續維護。
- 舊 Claude Code 對話與 `CLAUDE.md` 只作歷史參考，不再是權威狀態來源。
- 正式續接順序為 `CONTEXT.md` → `HANDOFF.md` → `README.md` → `AUDIT.md`。
- 每次重要改動必須同步更新 `CHANGELOG.md` 及 `HANDOFF.md`，並以實際測試、
  GitHub Actions 日誌及通知結果作為完成證據。

## 目標

以低維護成本監控指定港股／A 股的日內價格及漲跌幅，在告警層級變化時透過微信
PushPlus 及 Telegram 通知。系統只作個人提示，不承擔下單、止損或正式市場數據職責。

## 正式架構

```text
cron-job.org（工作日交易窗口，每 2 分鐘）
  -> GitHub workflow_dispatch
  -> GitHub Actions / Python 3.11
  -> Yahoo Finance（yfinance）
  -> PushPlus + Telegram
```

GitHub 內建 `schedule` 已從本地接管版本移除，避免與外部排程重疊。
運行狀態暫以 GitHub Actions Cache 在同一交易日內銜接。
外部 job `7693116` 現行 cron 是 `*/2 9-11,13-15 * * 1-5`；精確開市、午休、
收市及假期仍由交易日曆判斷。

## 不變邊界

- 不把憑證寫入代碼、文檔、Git 歷史或對話存檔。
- 不以 Actions 成功狀態代替逐股票行情成功驗證。
- 休市後的雲端驗證必須使用 `smoke_test`；普通 `dry_run` 跳過全部股票不算行情驗收。
- 不把 Yahoo Finance 延遲行情描述為即時或交易級行情。
- 不在未驗證代碼、門檻、漲跌停規則及交易日曆前擴大監控範圍。
- 外部排程、Secrets、PAT、通知 Token 的修改需要明確授權。

## 2026-07-28 接管快照

- 遠端：`IssaWong/hk-stock-alert`，Public，`main`。
- 本地與遠端基線 commit：`0076777`。
- 遠端累計 10,296 次運行：10,084 次 `workflow_dispatch`、166 次 `schedule`。
- 每 2 分鐘 dispatch 是用戶在 2026-05-29 明確設定，並非未授權異常。
- 2026-07-28 15:58 HKT 運行成功，10 隻配置中港股正常檢查，A 股因收市跳過。
- 2026-07-28 15:59 HKT，GitHub 因倉庫 60 天沒有提交而自動停用 workflow；遠端
  現時不再執行告警。cron-job.org job `7693116` 仍觸發，但其後返回 HTTP 422。
- 舊版鎖定 `yfinance 0.2.61`；本地已用 `1.5.2` 驗證 10／10 代碼可取行情。
- 本地改為批量行情＋逐股回退、正式交易日曆、門檻遲滯、每日健康摘要及系統異常
  冷卻告警。
- 本地 22 項單元測試、配置驗證、依賴檢查、YAML 解析及秘密掃描全部通過。
- 本地接管改造尚未推送，遠端仍停留在 `0076777`。

## 2026-07-30 雲端接管快照

- PR #1（可靠性、安全及運維接管）與 PR #2（雲端 `dry_run`）已 squash merge。
- 遠端 `main` 為 `80c57fd`，workflow 已恢復 active。
- cron-job.org job `7693116` 已啟用並收窄交易窗口；下一次為
  2026-07-31 09:00 HKT。
- cron PAT 已改為只限 `IssaWong/hk-stock-alert`、Actions read/write、90 日有效的
  fine-grained PAT；舊 `cron-job-trigger` classic PAT 已撤銷。
- 新 PAT 讀取 workflow 返回 HTTP 200，帶 `dry_run=true` 的 dispatch 返回
  HTTP 204。
- 兩個新版乾跑均卡在 GitHub queued、沒有分配 job；API 接通已驗證，但新版雲端
  完整執行仍未驗收。
- 2026-07-30 19:30 HKT 再驗證：22／22 測試通過，10／10 股票取得同日批量行情。

## 2026-07-30 第二輪優化

- 新增任何時段可用的最近交易日 `smoke_test`，避免休市後假綠燈。
- 正式交易時段加入 20 分鐘行情延遲上限。
- 新增多渠道通知續傳，只重試未成功渠道。
- 驗證與 production 使用不同 concurrency group，且驗證不接觸 production cache。
- Actions 以完整 SHA 固定，新建 Python 3.11／3.12 CI 及每週 Dependabot。
- 優化後 26／26 測試通過；20:16 HKT 真實 smoke test 為 10／10、0 errors。

## 最高優先事項

曾出現在舊 `CLAUDE.md`、舊 shell、舊本機權限配置及對話存檔內的所有憑證均視為
已暴露。cron 觸發 GitHub PAT 已完成輪換並撤銷舊 PAT；Telegram、PushPlus 及可能
影響其他 job 的 cron-job.org 全局 API key 尚待輪換。在全部完成前不得稱安全整改
完成。
