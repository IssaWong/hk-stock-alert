# HANDOFF | Stock Market Volatility Alert

Last updated: 2026-07-30

## 接管決定

用戶已於 2026-07-28 明確確認：此舊 Claude Code 項目今後由 Codex 接管。
後續會話不得依賴舊 Claude 對話內的狀態或憑證；應以本文件、`CONTEXT.md`、
當前工作樹及遠端實際運行證據為準。

## 當前狀態

`DEPLOYED_SCHEDULER_ACTIVE_CLOUD_RUNNER_QUEUED_CREDENTIAL_ROTATION_PARTIAL`

本地接管版本已經部署至遠端 `main`（`80c57fd`），GitHub workflow 已恢復 active，
cron-job.org job `7693116` 已啟用並改為 `*/2 9-11,13-15 * * 1-5`。新的
fine-grained PAT 只限本倉庫及 Actions 讀寫，2026-10-28 到期；舊的無到期日
classic PAT 已撤銷。新 PAT 讀取 workflow 返回 HTTP 200，乾跑 dispatch 返回
HTTP 204。

本地 22 項測試、配置、依賴、編譯及 10／10 當日行情驗證通過；但 2026-07-30
發出的兩個雲端乾跑仍停留 queued 且沒有 job，尚未取得新版完整雲端執行日誌。
因此可稱「已部署、排程已接通」，不可稱「新版雲端執行已完全驗收」。

第二輪優化已新增全天候 `smoke_test`、20 分鐘陳舊行情門禁、部分通知續傳、
validation／production 併發隔離、SHA 固定 Actions、Python 3.11／3.12 CI 及
Dependabot。本地 26／26 測試與 10／10 真實 smoke test 通過；雲端結果以合併後
最新 Actions run 為準。

## 接手順序

1. 讀 `CONTEXT.md`、`README.md`、`AUDIT.md`。
2. 查看 `git status`，保留未提交對話存檔及用戶資料。
3. 用 Python 3.11+ 安裝 `requirements.txt`。
4. 執行 `--check-config`、單元測試；交易時段用 `--dry-run`，其他時段用
   `--smoke-test`。
5. 查看最新 GitHub Actions run；若 queued 已解除，檢查完整日誌及乾跑結果。
6. 在下一個交易窗口觀察 job `7693116` 的首次正式 dispatch。
7. 輪換 PushPlus、Telegram，以及確認不影響其他 job 後再處理 cron-job.org
   全局 API key。
8. 於 2026-10-28 前續期或替換 `hk-stock-alert-cron-job` fine-grained PAT。

## 上線驗收

- 配置驗證通過：10 stocks / 41 alerts。
- 單元測試全通過。
- 交易時段乾跑 10／10 代碼成功，且不發通知、不寫狀態。
- workflow 只由 `workflow_dispatch` 觸發，沒有重複 `schedule`。
- Actions 使用 checkout v6、cache v5、setup-python v6，沒有 Node 20 淘汰警告。
- 任一交易中股票連續取價失敗時，workflow 結果為 failure。
- 交易日／時段按 XHKG、XSHG 正式日曆判斷。
- 批量行情缺失時逐股回退，10 隻股票全部得到有效當日 snapshot。
- 交易時段行情延遲不超過 `max_quote_delay_minutes`。
- 其中一個通知渠道失敗時，下一輪只重試未成功渠道。
- validation 不阻塞 production，亦不讀寫 production state cache。
- PR 由 Python 3.11／3.12 CI 驗證，第三方 Actions 固定至完整 SHA。
- PushPlus 使用 HTTPS；HTTP／API 失敗不寫入成功去重狀態。
- 系統錯誤通知按簽名及 30 分鐘冷卻去重；健康摘要每日最多一次。
- cron 觸發 PAT 已更新且舊 PAT 已撤銷；通知 Token 及全局 API key 仍待處理。
- Git 追蹤文件與歷史掃描沒有明文秘密。

## 未決風險

- Yahoo Finance／yfinance 是非交易級第三方資料來源，仍可能延遲、限流或中斷。
- GitHub Actions Cache 不是專用持久狀態庫；快取遺失可造成當日重複通知。
- 每 2 分鐘頻率仍會放大 Yahoo Finance 限流風險；目前已改用批量請求及逐股回退，
  若再擴大股票數量應評估正式市場數據供應商。
- `exchange-calendars 4.13.2` 在 NumPy 新版本會輸出上游棄用警告，目前不影響結果。
- GitHub Actions runner 不保證兩分鐘準時啟動；若 queued 問題持續，應把執行器遷移
  至小型常駐主機，而不是繼續增加 dispatch 重試。
- 2026-07-30 的兩個 `dry_run=true` dispatch 均獲 API 以 HTTP 204 接受，但長時間
  queued 且沒有 job；此為 GitHub runner／queue 層未完成，不能當成程式成功或失敗。
- 舊告警 workflow 的 queued run 無法普通或強制取消，GitHub 均返回 500；正式入口
  已換成 `.github/workflows/stock-alert-v2.yml`，舊 workflow ID 不再使用。
- cron-job.org 下一個正式執行窗口為 2026-07-31 09:00 HKT；首輪正式通知與狀態
  持久化仍待交易時段實證。
- PushPlus、Telegram 舊憑證仍視為已暴露；cron-job.org 全局 API key 可能被其他
  job 共用，未確認影響範圍前不可直接撤銷。
