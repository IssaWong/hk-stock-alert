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
- 外部現行 cron：`*/2 9-16 * * 1-5`；待部署時收窄為
  `*/2 9-11,13-15 * * 1-5`
- 同一告警組只在層級變化時通知
- 價格目標與漲跌幅分開計算，不混用數值大小
- 以港交所／上交所交易日曆判斷交易時段及正式假期
- 批量抓取 10 隻股票，缺失代碼自動回退至單股重試
- 0.5% 遲滯區間，降低價格在門檻附近反覆進出造成的噪音
- 行情連續兩次失敗或通知失敗時，運行明確標紅
- 系統異常即時通知並按 30 分鐘冷卻去重；每個交易日收市前發健康摘要

## 文件

| 文件 | 用途 |
|---|---|
| `config.json` | 股票、通知路由及告警門檻 |
| `check_alerts.py` | 行情、告警、去重及通知邏輯 |
| `.github/workflows/stock-alert.yml` | GitHub Actions 執行流程 |
| `tests/test_check_alerts.py` | 核心回歸測試 |
| `CONTEXT.md` | 業務背景與架構邊界 |
| `HANDOFF.md` | 運維交接與下一步 |
| `AUDIT.md` | 2026-07-28 深度審計 |

## 本機驗證

需要 Python 3.11 或以上。

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python check_alerts.py --check-config
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python check_alerts.py --dry-run
```

`--dry-run` 會抓取及評估行情，但不發通知、不更新狀態。

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

## 發佈邊界

本地修改不等於已部署。正式上線前需要：

1. 通過測試及乾跑；
2. 輪換所有曾在舊交接／對話出現過的憑證；
3. 更新 GitHub Actions Secrets 與 cron-job.org；
4. 推送至 `main`；
5. 查看首個 workflow 日誌，確認 10 隻股票及狀態恢復正常。

## 目前雲端狀態

2026-07-28 15:59 HKT，GitHub 因倉庫連續 60 天沒有提交活動，自動停用了
`港股告警監控` workflow。這不是程式錯誤；在重新啟用並推送接管版本前，正式告警
處於停止狀態。
