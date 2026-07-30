# Stock Market Volatility Alert

港股及 A 股價格波動告警。正式運行於 GitHub Actions，主排程由
cron-job.org 在工作日粗略時段每 2 分鐘觸發，程式再按正式交易日曆過濾；
通知經 PushPlus 及 Telegram 發送。

> 這是個人監控工具，不是交易系統。Yahoo Finance 行情可能延遲、暫時缺失或受限，
> 不應作為唯一的交易決策或風險控制依據。

## 當前範圍

- 10 隻股票、41 條告警
- 港股：09:30–12:00、13:00–16:00 HKT
- A 股：09:30–11:30、13:00–15:00 HKT
- 外部現行 cron：`*/2 9-11,13-15 * * 1-5`
- 同一告警組只在層級變化時通知
- 價格目標與漲跌幅分開計算，不混用數值大小
- 以港交所／上交所交易日曆判斷交易時段及正式假期
- 批量抓取 10 隻股票，缺失代碼自動回退至單股重試
- 0.5% 遲滯區間，降低價格在門檻附近反覆進出造成的噪音
- 正式交易時段拒絕超過 20 分鐘的陳舊行情
- 多渠道部分成功時只重試失敗渠道，避免重複通知
- 行情連續兩次失敗或通知失敗時，運行明確標紅
- 系統異常即時通知並按 30 分鐘冷卻去重；每個交易日收市前發健康摘要

## 文件

| 文件 | 用途 |
|---|---|
| `config.json` | 股票、通知路由及告警門檻 |
| `check_alerts.py` | 行情、告警、去重及通知邏輯 |
| `.github/workflows/stock-alert-v2.yml` | GitHub Actions 執行流程 |
| `tests/test_check_alerts.py` | 核心回歸測試 |
| `CONTEXT.md` | 業務背景與架構邊界 |
| `HANDOFF.md` | 運維交接與下一步 |
| `AUDIT.md` | 2026-07-28 深度審計 |
| `OPTIMIZATION_2026-07-30.md` | 第二輪深度優化、基準及架構建議 |

## 本機驗證

需要 Python 3.11 或以上；macOS 內建 Python 3.9 不適用。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python check_alerts.py --check-config
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python check_alerts.py --dry-run
.venv/bin/python check_alerts.py --smoke-test
```

`--dry-run` 會抓取及評估行情，但不發通知、不更新狀態。
在休市時，`--dry-run` 會正常跳過全部股票；`--smoke-test` 則會抓取最近交易日的
10 隻股票，適合任何時段做真正的行情與依賴驗證。GitHub Actions 手動執行頁提供
`dry_run` 及 `smoke_test` 選項；cron-job.org 不傳任何選項，因此正式排程仍會正常
通知及保存狀態。

## 新增股票

在 `config.json` 的 `stocks` 加入一項：

```json
{
  "code": "0700.HK",
  "name": "騰訊控股",
  "notify": ["PUSHPLUS_TOKEN", "TELEGRAM"],
  "alerts": [
    {
      "id": "0700_drop_5",
      "type": "drop_pct",
      "value": -5,
      "label": "跌幅 -5%",
      "once_per_day": false
    }
  ]
}
```

支持 `rise_pct`、`drop_pct`、`price_above`、`price_below`。
`once_per_day: true` 代表同一條告警每日最多成功通知一次；`false` 則在條件解除後
再次進入或層級改變時可以重發。

全局 `default_hysteresis` 可抑制門檻附近反覆告警；個別告警可用自己的
`hysteresis` 覆蓋。`system_alert` 及 `health_summary` 用於監控這套告警系統本身，
不是股票價格告警。

提交前必須執行配置驗證及全部測試。股票代碼也要用實際行情抓取驗證。

## Secrets

GitHub Actions 需要：

- `PUSHPLUS_TOKEN`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

cron-job.org 另持有一個只用於觸發 workflow 的 GitHub 憑證。
任何實際值都不得寫入本倉庫；本機只使用未追蹤的環境變數。

## 發佈與驗收邊界

本地修改、API 接受 dispatch 及雲端 workflow 完成是三種不同證據。每次重要發佈需要：

1. 通過測試及乾跑；
2. 輪換所有曾在舊交接／對話出現過的憑證；
3. 更新 GitHub Actions Secrets 與 cron-job.org；
4. 推送至 `main`；
5. 查看首個 workflow 日誌，確認 10 隻股票及狀態恢復正常。

## 目前雲端狀態

截至 2026-07-30 20:00 HKT：

- 接管及深度優化已透過 PR #1、#2、#4、#9 及 #10 合併至 `main`。
- `股票波動告警監控 v2` 是正式入口；舊 workflow ID 因 GitHub 端無法取消的 queued
  狀態而退役。
- cron-job.org job `7693116` 已啟用，排程已收窄至
  `*/2 9-11,13-15 * * 1-5`，dispatch URL 已改為 `stock-alert-v2.yml`；
  下一個正式窗口為 2026-07-31 09:00 HKT。
- cron 觸發憑證已換成只限本倉庫、只限 Actions 讀寫、90 日有效的 fine-grained
  PAT；舊的無到期日 classic PAT 已撤銷。新 PAT 到期日為 2026-10-28。
- 新 PAT 的 workflow 讀取請求返回 HTTP 200，`dry_run=true` dispatch 返回
  HTTP 204，證明授權及觸發路徑可用。
- v2 雲端 smoke run `30542866973` 已在 25 秒內成功：10／10 股票、
  `yfinance-batch`、0 errors；validation 正確跳過 production cache。
- 舊 workflow ID 的四個 queued run 仍無法取消，GitHub 普通／強制取消均返回 500；
  它們只屬已退役入口，不再接收 cron dispatch。
- PushPlus、Telegram 及可能影響其他 job 的 cron-job.org 全局 API key 尚未輪換；
  在完成前不得稱全部憑證整改完成。
