# HANDOFF | Stock Market Volatility Alert

Last updated: 2026-07-28

## 接管決定

用戶已於 2026-07-28 明確確認：此舊 Claude Code 項目今後由 Codex 接管。
後續會話不得依賴舊 Claude 對話內的狀態或憑證；應以本文件、`CONTEXT.md`、
當前工作樹及遠端實際運行證據為準。

## 當前狀態

`LOCAL_VALIDATED_REMOTE_DISABLED_AWAITING_GITHUB_LOGIN_AND_DEPLOYMENT`

本地已完成核心可靠性、安全及文檔接管改造，並通過配置、22 項單元測試、
依賴一致性、YAML、秘密掃描及 10／10 股票行情驗證。遠端仍是舊基線
`0076777`，且 GitHub 已於 2026-07-28 15:59 HKT 因 60 天沒有倉庫提交而自動停用
workflow；cron-job.org 隨後每 2 分鐘收到 `422 Unprocessable Entity`。正式告警
現時停止。不要把本地完成誤報為雲端已上線。

## 接手順序

1. 讀 `CONTEXT.md`、`README.md`、`AUDIT.md`。
2. 查看 `git status`，保留未提交對話存檔及用戶資料。
3. 用 Python 3.11+ 安裝 `requirements.txt`。
4. 執行 `--check-config`、單元測試、`--dry-run`。
5. 取得用戶明確確認後，輪換四類外部憑證。
6. 安裝並登入 GitHub CLI，或在已登入 GitHub 的瀏覽器重新授權。
7. 推送本地改造至 `main`，重新啟用 workflow。
8. 觀察首個外部 dispatch 的完整日誌及通知結果。

## 上線驗收

- 配置驗證通過：10 stocks / 41 alerts。
- 單元測試全通過。
- 交易時段乾跑 10／10 代碼成功，且不發通知、不寫狀態。
- workflow 只由 `workflow_dispatch` 觸發，沒有重複 `schedule`。
- Actions 使用 checkout v6、cache v5、setup-python v6，沒有 Node 20 淘汰警告。
- 任一交易中股票連續取價失敗時，workflow 結果為 failure。
- 交易日／時段按 XHKG、XSHG 正式日曆判斷。
- 批量行情缺失時逐股回退，10 隻股票全部得到有效當日 snapshot。
- PushPlus 使用 HTTPS；HTTP／API 失敗不寫入成功去重狀態。
- 系統錯誤通知按簽名及 30 分鐘冷卻去重；健康摘要每日最多一次。
- 新 Token 已更新，舊 Token／PAT／API key 已撤銷。
- Git 追蹤文件與歷史掃描沒有明文秘密。

## 未決風險

- Yahoo Finance／yfinance 是非交易級第三方資料來源，仍可能延遲、限流或中斷。
- GitHub Actions Cache 不是專用持久狀態庫；快取遺失可造成當日重複通知。
- 每 2 分鐘頻率仍會放大 Yahoo Finance 限流風險；目前已改用批量請求及逐股回退，
  若再擴大股票數量應評估正式市場數據供應商。
- `exchange-calendars 4.13.2` 在 NumPy 新版本會輸出上游棄用警告，目前不影響結果。
- GitHub workflow 已停用；未完成登入、重新啟用、部署及首輪雲端驗證前不可稱恢復。
- cron-job.org 帳號可正常登入、job `7693116` 存在，但停用期間會持續留下 422
  失敗紀錄。
- 現行 cron 為 `*/2 9-16 * * 1-5`，包含午休及收市後無效觸發；建議部署時改為
  `*/2 9-11,13-15 * * 1-5`，程式交易日曆繼續負責 09:30 前及正式休市過濾。
