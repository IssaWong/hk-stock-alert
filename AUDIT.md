# Stock Market Volatility Alert 深度審計

Audit date: 2026-07-28
Scope: 本地工作樹、Git 歷史、公開 GitHub 倉庫、最近 Actions 運行、依賴及只讀行情實測
Baseline: `0076777`

## 結論

舊版未達可放心維護的生產標準。最嚴重問題是本地多處保存明文憑證；其次是行情／
通知錯誤被吞掉後 workflow 仍顯示成功。接管版本已在本地修正可由代碼解決的部分，
但外部憑證輪換及雲端部署尚未執行。

2026-07-28 15:59 HKT，GitHub 另因倉庫連續 60 天沒有提交活動而自動停用 workflow。
因此當前狀態是「本地新版已驗證、雲端告警已停止」。

## Findings

### P0 — 明文憑證暴露

**證據**

- 舊 `CLAUDE.md` 保存 Telegram Token、PushPlus Token、cron-job.org API key 及
  GitHub PAT。
- 舊 `stock_alert.sh` 硬編碼 Telegram Token／Chat ID。
- `.claude/settings.local.json` 保存含 API key 的完整命令。
- 舊對話存檔重複記錄多項秘密。
- 以上文件未被 Git 追蹤，且已確認 Git 提交歷史沒有命中常見 Token 格式；但本機、
  對話或同步備份中的秘密仍應視為已暴露。

**整改**

本地文件已去秘密化並加入忽略規則。外部四類憑證尚待輪換。

**驗收**

新憑證只存在於 GitHub Secrets／cron-job.org；舊值全部撤銷；追蹤文件、Git 歷史及
本地交接掃描零命中。

### P1 — 行情失敗不會令 workflow 失敗

**證據**

舊版對單股 `fetch error`／`no price data` 只 `continue`，最後仍輸出 `Done.` 並返回
0。隔離實測中 `2577.HK` 首次曾返回無資料，重試後才成功；這證明綠燈可能掩蓋漏監控。

**整改**

每隻交易中股票最多重試兩次；仍失敗則累計錯誤並以退出碼 1 標紅 workflow。

**驗收**

模擬單股連續失敗時 job 必須 failure，日誌列出股票代碼；正常乾跑 10／10 成功。

### P1 — 通知失敗仍被視為已告警

**證據**

舊版不執行 `raise_for_status()`，Telegram 直接打印 sent；PushPlus 只讀 `msg`；
`levels` 在通知前已更新。接口失敗後同一層級不會重試。

**整改**

校驗 HTTP／API 結果，通知全部成功後才更新層級；失敗令 workflow 標紅。

**驗收**

模擬 HTTP 500、API 錯誤及缺 Secret 時均不更新成功層級，job 返回 failure。

### P1 — 不同單位告警被直接比較

**證據**

`price_above` 與 `rise_pct` 同歸 `up`，再按 `value` 大小選擇。對 `2577.HK`，
目標價 85 永遠大於 +5／+10 百分比，會遮蔽漲幅層級。

**整改**

改為 `change_up`、`change_down`、`price_above`、`price_below` 四組，並遷移舊方向狀態。

**驗收**

價格目標與漲幅同時成立時可各自形成一次告警；同一漲幅組仍只報最極端層級。

### P1 — 雙排程造成重疊運行

**證據**

cron-job.org 每 2 分鐘 dispatch；workflow 同時保留 GitHub schedule。公開 API 顯示
10,084 次 dispatch 及 166 次 schedule，15:58 與 16:00 亦連續運行。

**整改**

本地 workflow 移除 `schedule`，只保留外部 dispatch，並加入 concurrency 串行控制。

**驗收**

部署後新運行只出現 `workflow_dispatch`，且沒有兩個 production job 同時執行。

### P1 — GitHub workflow 因倉庫不活躍而停用

**證據**

GitHub Actions 頁面及 2026-07-28 15:59 HKT 的官方通知均顯示：scheduled workflow
因倉庫 60 天沒有活動而自動停用。最後成功運行為 `#10296`；cron-job.org job
`7693116` 隨後每 2 分鐘收到 `422 Unprocessable Entity`。

**整改**

登入 GitHub、推送接管提交並重新啟用 workflow；之後保留正常維護提交或建立不依賴
GitHub 60 天 scheduled-workflow 規則的執行架構。

**驗收**

workflow 頁面顯示 Active；外部 dispatch 成功產生新 run；首輪日誌、行情、通知及
狀態快取全部通過。

### P2 — 依賴及 Actions 版本過時

**證據**

`yfinance 0.2.61` 發佈於 2025-05-12；2026-07-23 已有 1.5.2。最近運行亦明示
checkout v4、cache v4、setup-python v5 依賴 Node 20 並已被 runner 強制用 Node 24。

**整改**

本地升級至 yfinance 1.5.2、requests 2.32.5，以及 checkout v6、cache v5、
setup-python v6；1.5.2 已實測 10／10 股票可取行情。

**驗收**

乾淨 Python 3.11 安裝、測試及行情乾跑通過；Actions 日誌不再有 Node 20 淘汰警告。

### P2 — 假期與陳舊行情

**證據**

舊版只按星期與時段判斷，不校驗分鐘行情日期；假期可能取到上一交易日資料。

**整改**

新增 XHKG／XSHG 正式交易日曆及最新 bar 必須為 HKT 當日的門禁。

**驗收**

週末及非交易時段不抓價；舊日期 bar 被拒絕並明確標紅。

### P2 — 文檔與實況失真

**證據**

舊交接寫每 15 分鐘及 `once_per_day: false` 會每次重發，但實際外部排程為每 2 分鐘，
代碼則只在層級變化時通知。

**整改**

新增正式 README／CONTEXT／HANDOFF，統一記錄每 2 分鐘、層級變化及上線邊界。

**驗收**

新會話只讀三份正式文檔即可準確描述架構、頻率、狀態及未決風險。

## 剩餘風險與建議

1. 優先完成憑證輪換，否則 P0 仍未關閉。
2. 部署接管版本後，在完整交易時段觀察至少一日。
3. 已改為批量行情抓取＋逐股回退；若股票數量繼續增加，評估有 SLA 的正式數據源。
4. 中期把去重狀態移出 Actions Cache，使用有一致性與保留保證的輕量儲存。
