import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import exchange_calendars as exchange_calendars
import pandas as pd
import requests
import yfinance as yf


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state" / "state.json"
SUPPORTED_ALERT_TYPES = {"price_above", "price_below", "drop_pct", "rise_pct"}


@dataclass(frozen=True)
class PriceSnapshot:
    current: float
    previous_close: float
    day_open: float
    day_high: float
    day_low: float
    latest_bar_time: datetime
    delay_seconds: float
    source: str
    day_volume: Optional[float] = None
    average_volume_5d: Optional[float] = None


def load_config(path: Path = CONFIG_FILE) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        config = json.load(file)
    validate_config(config)
    return config


def validate_config(config: Dict[str, Any]) -> None:
    if not isinstance(config.get("stocks"), list) or not config["stocks"]:
        raise ValueError("config.json 必須包含非空 stocks 清單")

    try:
        ZoneInfo(config.get("timezone", "Asia/Hong_Kong"))
    except Exception as error:
        raise ValueError("config.json 的 timezone 無效") from error

    for section_name in ("health_summary", "system_alert"):
        section = config.get(section_name, {})
        if not section.get("enabled", False):
            continue
        channels = section.get("notify")
        if not isinstance(channels, list) or not channels:
            raise ValueError(f"{section_name}.notify 至少需要一個通知渠道")

    summary = config.get("health_summary", {})
    ranking_limit = summary.get("ranking_limit", 3)
    if (
        not isinstance(ranking_limit, int)
        or isinstance(ranking_limit, bool)
        or not 1 <= ranking_limit <= 5
    ):
        raise ValueError("health_summary.ranking_limit 必須是 1 至 5 的整數")

    cooldown = config.get("system_alert", {}).get("cooldown_minutes", 30)
    if (
        not isinstance(cooldown, (int, float))
        or not math.isfinite(cooldown)
        or cooldown <= 0
    ):
        raise ValueError("system_alert.cooldown_minutes 必須是正數")

    max_quote_delay = config.get("max_quote_delay_minutes", 20)
    if (
        not isinstance(max_quote_delay, (int, float))
        or not math.isfinite(max_quote_delay)
        or max_quote_delay <= 0
    ):
        raise ValueError("max_quote_delay_minutes 必須是正數")

    seen_codes = set()
    seen_alert_ids = set()
    for stock in config["stocks"]:
        code = stock.get("code")
        if not isinstance(code, str) or not code.endswith((".HK", ".SS", ".SZ")):
            raise ValueError(f"股票代碼無效：{code!r}")
        if code in seen_codes:
            raise ValueError(f"股票代碼重複：{code}")
        seen_codes.add(code)

        if not stock.get("name"):
            raise ValueError(f"{code} 缺少 name")
        channels = stock.get("notify", [])
        if not isinstance(channels, list) or not channels:
            raise ValueError(f"{code} 至少需要一個通知渠道")

        for alert in stock.get("alerts", []):
            alert_id = alert.get("id")
            alert_type = alert.get("type")
            value = alert.get("value")
            if not alert_id or alert_id in seen_alert_ids:
                raise ValueError(f"告警 id 缺失或重複：{alert_id!r}")
            if alert_type not in SUPPORTED_ALERT_TYPES:
                raise ValueError(f"{alert_id} 的告警類型無效：{alert_type!r}")
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{alert_id} 的 value 必須是有限數字")
            if alert_type == "drop_pct" and value >= 0:
                raise ValueError(f"{alert_id} 的跌幅門檻必須小於 0")
            if alert_type == "rise_pct" and value <= 0:
                raise ValueError(f"{alert_id} 的升幅門檻必須大於 0")
            hysteresis = alert.get(
                "hysteresis", config.get("default_hysteresis", 0.5)
            )
            if (
                not isinstance(hysteresis, (int, float))
                or not math.isfinite(hysteresis)
                or hysteresis < 0
            ):
                raise ValueError(f"{alert_id} 的 hysteresis 必須是非負有限數字")
            seen_alert_ids.add(alert_id)


def load_state(today: str, path: Path = STATE_FILE) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with path.open(encoding="utf-8") as file:
                state = json.load(file)
            if state.get("date") == today:
                state.setdefault("levels", {})
                state.setdefault("sent_today", {})
                state.setdefault("health", {})
                state.setdefault("pending_notifications", {})
                return state
        except (OSError, json.JSONDecodeError) as error:
            print(f"Warning: state 讀取失敗，重建當日狀態 — {error}")
    return {
        "date": today,
        "levels": {},
        "sent_today": {},
        "health": {},
        "pending_notifications": {},
    }


def save_state(state: Dict[str, Any], path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    temporary.replace(path)


def _valid_price(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0


def get_price(
    code: str,
    now: datetime,
    attempts: int = 2,
    retry_delay_seconds: float = 1.0,
) -> PriceSnapshot:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            ticker = yf.Ticker(code)
            history = ticker.history(
                period="1d",
                interval="1m",
                auto_adjust=False,
                prepost=False,
                raise_errors=True,
            )
            if history.empty:
                raise RuntimeError("Yahoo Finance 沒有返回分鐘行情")

            latest_index = history.index[-1]
            latest_bar_time = latest_index.to_pydatetime()
            if latest_bar_time.tzinfo is None:
                latest_bar_time = latest_bar_time.replace(tzinfo=now.tzinfo)
            latest_bar_time = latest_bar_time.astimezone(now.tzinfo)
            expected_date = _expected_session_date(code, now)
            if latest_bar_time.date() != expected_date:
                raise RuntimeError(
                    "分鐘行情不是最近交易日："
                    f"預期 {expected_date.isoformat()}，"
                    f"最新 bar 為 {latest_bar_time.isoformat()}"
                )

            snapshot = PriceSnapshot(
                current=float(history["Close"].iloc[-1]),
                previous_close=float(ticker.fast_info.previous_close),
                day_open=float(history["Open"].iloc[0]),
                day_high=float(history["High"].max()),
                day_low=float(history["Low"].min()),
                latest_bar_time=latest_bar_time,
                delay_seconds=max(0.0, (now - latest_bar_time).total_seconds()),
                source="yfinance-single-fallback",
                day_volume=float(history["Volume"].fillna(0).sum())
                if "Volume" in history
                else None,
            )
            values = (
                snapshot.current,
                snapshot.previous_close,
                snapshot.day_open,
                snapshot.day_high,
                snapshot.day_low,
            )
            if not all(_valid_price(value) for value in values):
                raise RuntimeError("行情包含無效或非正數價格")
            return snapshot
        except Exception as error:
            last_error = error
            if attempt < attempts:
                print(f"    {code}: 第 {attempt} 次行情請求失敗，稍後重試 — {error}")
                time.sleep(retry_delay_seconds)

    raise RuntimeError(f"{code} 行情請求連續失敗 {attempts} 次") from last_error


def _ticker_frame(data: pd.DataFrame, code: str) -> pd.DataFrame:
    if data.empty:
        raise RuntimeError("批量行情為空")
    if not isinstance(data.columns, pd.MultiIndex):
        return data
    level_zero = data.columns.get_level_values(0)
    level_one = data.columns.get_level_values(1)
    if code in level_zero:
        return data[code]
    if code in level_one:
        return data.xs(code, axis=1, level=1)
    raise RuntimeError(f"批量行情缺少 {code}")


def _snapshot_from_frames(
    code: str,
    intraday_data: pd.DataFrame,
    daily_data: pd.DataFrame,
    now: datetime,
) -> PriceSnapshot:
    intraday = _ticker_frame(intraday_data, code).dropna(
        subset=["Open", "High", "Low", "Close"], how="any"
    )
    if intraday.empty:
        raise RuntimeError("分鐘行情為空")

    index = intraday.index
    if index.tz is None:
        index = index.tz_localize(now.tzinfo)
    else:
        index = index.tz_convert(now.tzinfo)
    intraday = intraday.copy()
    intraday.index = index
    expected_date = _expected_session_date(code, now)
    today_rows = intraday[intraday.index.date == expected_date]
    if today_rows.empty:
        raise RuntimeError(f"沒有最近交易日 {expected_date.isoformat()} 的分鐘行情")

    daily = _ticker_frame(daily_data, code).dropna(subset=["Close"])
    previous_rows = daily[daily.index.date < expected_date]
    if previous_rows.empty:
        raise RuntimeError("沒有上一交易日收盤價")

    latest_bar_time = today_rows.index[-1].to_pydatetime()
    day_volume = (
        float(today_rows["Volume"].fillna(0).sum())
        if "Volume" in today_rows
        else None
    )
    previous_volumes = (
        previous_rows["Volume"].dropna()
        if "Volume" in previous_rows
        else pd.Series(dtype=float)
    )
    previous_volumes = previous_volumes[previous_volumes > 0].tail(5)
    average_volume_5d = (
        float(previous_volumes.mean()) if not previous_volumes.empty else None
    )
    snapshot = PriceSnapshot(
        current=float(today_rows["Close"].iloc[-1]),
        previous_close=float(previous_rows["Close"].iloc[-1]),
        day_open=float(today_rows["Open"].iloc[0]),
        day_high=float(today_rows["High"].max()),
        day_low=float(today_rows["Low"].min()),
        latest_bar_time=latest_bar_time,
        delay_seconds=max(0.0, (now - latest_bar_time).total_seconds()),
        source="yfinance-batch",
        day_volume=day_volume,
        average_volume_5d=average_volume_5d,
    )
    values = (
        snapshot.current,
        snapshot.previous_close,
        snapshot.day_open,
        snapshot.day_high,
        snapshot.day_low,
    )
    if not all(_valid_price(value) for value in values):
        raise RuntimeError("行情包含無效或非正數價格")
    return snapshot


def get_prices(
    codes: Iterable[str],
    now: datetime,
    attempts: int = 2,
    retry_delay_seconds: float = 1.0,
) -> Tuple[Dict[str, PriceSnapshot], Dict[str, str]]:
    code_list = list(dict.fromkeys(codes))
    if not code_list:
        return {}, {}

    intraday_data = pd.DataFrame()
    daily_data = pd.DataFrame()
    batch_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            intraday_data = yf.download(
                code_list,
                period="1d",
                interval="1m",
                auto_adjust=False,
                prepost=False,
                group_by="ticker",
                threads=True,
                progress=False,
                timeout=15,
            )
            daily_data = yf.download(
                code_list,
                period="10d",
                interval="1d",
                auto_adjust=False,
                prepost=False,
                group_by="ticker",
                threads=True,
                progress=False,
                timeout=15,
            )
            if intraday_data.empty or daily_data.empty:
                raise RuntimeError("Yahoo Finance 批量行情為空")
            break
        except Exception as error:
            batch_error = error
            if attempt < attempts:
                print(f"  批量行情第 {attempt} 次失敗，稍後重試 — {error}")
                time.sleep(retry_delay_seconds)

    snapshots: Dict[str, PriceSnapshot] = {}
    errors: Dict[str, str] = {}
    for code in code_list:
        try:
            if intraday_data.empty or daily_data.empty:
                raise RuntimeError(f"批量行情失敗：{batch_error}")
            snapshots[code] = _snapshot_from_frames(
                code, intraday_data, daily_data, now
            )
        except Exception as batch_extract_error:
            print(f"  {code}: 批量行情不可用，改用單股請求 — {batch_extract_error}")
            try:
                snapshots[code] = get_price(
                    code,
                    now,
                    attempts=attempts,
                    retry_delay_seconds=retry_delay_seconds,
                )
            except Exception as fallback_error:
                errors[code] = str(fallback_error)
    return snapshots, errors


@lru_cache(maxsize=2)
def _market_calendar(code: str):
    return exchange_calendars.get_calendar(
        "XHKG" if code.upper().endswith(".HK") else "XSHG"
    )


def _expected_session_date(code: str, now: datetime) -> date:
    session = _market_calendar(code).date_to_session(
        pd.Timestamp(now.date()),
        direction="previous",
    )
    return session.date()


def is_trading_hours(code: str, now: datetime) -> bool:
    minute = pd.Timestamp(now).tz_convert("UTC").floor("min")
    return bool(_market_calendar(code).is_open_on_minute(minute))


def is_market_session(code: str, now: datetime) -> bool:
    session = pd.Timestamp(now.date())
    return bool(_market_calendar(code).is_session(session))


def get_alert_group(alert_type: str) -> str:
    groups = {
        "rise_pct": "change_up",
        "drop_pct": "change_down",
        "price_above": "price_above",
        "price_below": "price_below",
    }
    return groups[alert_type]


def find_most_extreme(alerts: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = list(alerts)
    if not candidates:
        return None
    alert_type = candidates[0]["type"]
    if alert_type in ("rise_pct", "price_above"):
        return max(candidates, key=lambda alert: alert["value"])
    return min(candidates, key=lambda alert: alert["value"])


def check_condition(
    alert_type: str,
    value: float,
    current: float,
    previous_close: float,
    active: bool = False,
    hysteresis: float = 0.0,
) -> bool:
    if alert_type == "price_above":
        threshold = value * (1 - hysteresis / 100) if active else value
        return current >= threshold
    if alert_type == "price_below":
        threshold = value * (1 + hysteresis / 100) if active else value
        return current <= threshold
    change_pct = (current - previous_close) / previous_close * 100
    if alert_type == "drop_pct":
        threshold = value + hysteresis if active else value
        return change_pct <= threshold
    if alert_type == "rise_pct":
        threshold = value - hysteresis if active else value
        return change_pct >= threshold
    return False


def build_message(
    name: str,
    code: str,
    alert: Dict[str, Any],
    snapshot: PriceSnapshot,
    change_pct: float,
    now: datetime,
) -> str:
    currency = "HKD " if code.upper().endswith(".HK") else "CNY "
    alert_type = alert["type"]
    label = alert.get("label", alert["id"])
    if alert_type == "drop_pct":
        emoji = "🚨"
    elif alert_type == "price_above":
        emoji = "🎯"
    elif alert_type == "price_below":
        emoji = "🔻"
    else:
        emoji = "🚀"

    sign = "+" if change_pct >= 0 else ""
    gap_pct = (
        (snapshot.day_open - snapshot.previous_close)
        / snapshot.previous_close
        * 100
    )
    intraday_pct = (snapshot.current - snapshot.day_open) / snapshot.day_open * 100
    day_range = snapshot.day_high - snapshot.day_low
    range_position = (
        (snapshot.current - snapshot.day_low) / day_range * 100
        if day_range > 0
        else 50.0
    )
    lines = [
        f"{emoji} {name} ({code})",
        "",
        f"⚡ 觸發條件：{label}",
        f"💰 現　　價：{currency}{snapshot.current:.3f}（{sign}{change_pct:.2f}%）",
        (
            f"📊 今日行情：開 {currency}{snapshot.day_open:.3f} ｜ "
            f"高 {currency}{snapshot.day_high:.3f} ｜ "
            f"低 {currency}{snapshot.day_low:.3f}"
        ),
        f"📌 昨日收盤：{currency}{snapshot.previous_close:.3f}",
        (
            f"🧭 事實底稿：開盤缺口 {gap_pct:+.2f}% ｜ "
            f"日內 {intraday_pct:+.2f}% ｜ 當日區間位置 {range_position:.0f}%"
        ),
    ]
    if (
        snapshot.day_volume is not None
        and snapshot.average_volume_5d is not None
        and snapshot.average_volume_5d > 0
    ):
        volume_ratio = snapshot.day_volume / snapshot.average_volume_5d * 100
        lines.append(
            f"📦 截至目前成交量：{_format_volume(snapshot.day_volume)}"
            f"（近5日全日均量的 {volume_ratio:.0f}%）"
        )
    if alert_type in ("price_above", "price_below"):
        target = alert["value"]
        distance_pct = (snapshot.current - target) / target * 100
        sign2 = "+" if distance_pct >= 0 else ""
        lines.append(f"📍 距目標價：{sign2}{distance_pct:.2f}%")
    lines.extend(
        [
            f"🕒 最新行情：{snapshot.latest_bar_time.strftime('%Y-%m-%d %H:%M %Z')}",
            f"📡 行情延遲：約 {snapshot.delay_seconds / 60:.1f} 分鐘",
            f"⏰ 檢查時間：{now.strftime('%Y-%m-%d %H:%M %Z')}",
        ]
    )
    return "\n".join(lines)


def _format_volume(value: float) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}億"
    if value >= 10_000:
        return f"{value / 10_000:.1f}萬"
    return f"{value:.0f}"


def send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("    Telegram error: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 未設定")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram API 返回 ok=false")
        print("    Telegram sent")
        return True
    except Exception as error:
        print(f"    Telegram error: {error}")
        return False


def send_pushplus(token: str, title: str, message: str) -> bool:
    try:
        response = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": token,
                "title": title,
                "content": message,
                "template": "txt",
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in (0, 200):
            raise RuntimeError(f"PushPlus API error: {payload.get('msg', payload)}")
        print(f"    PushPlus sent: {payload.get('msg', 'success')}")
        return True
    except Exception as error:
        print(f"    PushPlus error: {error}")
        return False


def notify(channels: Iterable[str], title: str, message: str) -> bool:
    results = notify_with_results(channels, title, message)
    return bool(results) and all(results.values())


def notify_with_results(
    channels: Iterable[str],
    title: str,
    message: str,
) -> Dict[str, bool]:
    results: Dict[str, bool] = {}
    for channel in dict.fromkeys(channels):
        if channel == "TELEGRAM":
            results[channel] = send_telegram(message)
            continue
        token = os.environ.get(channel)
        if not token:
            print(f"    PushPlus error: secret {channel} 未設定")
            results[channel] = False
            continue
        results[channel] = send_pushplus(token, title, message)
    return results


def deliver_with_resume(
    state: Dict[str, Any],
    delivery_key: str,
    channels: Iterable[str],
    title: str,
    message: str,
) -> Tuple[bool, List[str]]:
    ordered_channels = list(dict.fromkeys(channels))
    pending = state.setdefault("pending_notifications", {})
    already_sent = set(pending.get(delivery_key, []))
    remaining = [
        channel for channel in ordered_channels if channel not in already_sent
    ]
    results = notify_with_results(remaining, title, message)
    successful = already_sent | {
        channel for channel, succeeded in results.items() if succeeded
    }
    failed = [channel for channel in ordered_channels if channel not in successful]
    if not failed:
        pending.pop(delivery_key, None)
        return True, []
    pending[delivery_key] = [
        channel for channel in ordered_channels if channel in successful
    ]
    return False, failed


def _delivery_key(code: str, group: str, alert_id: str) -> str:
    return f"{code}|{group}|{alert_id}"


def _clear_pending_group(
    state: Dict[str, Any],
    code: str,
    group: str,
    keep: Optional[str] = None,
) -> None:
    pending = state.setdefault("pending_notifications", {})
    prefix = f"{code}|{group}|"
    for key in list(pending):
        if key.startswith(prefix) and key != keep:
            pending.pop(key, None)


def _quote_is_fresh(snapshot: PriceSnapshot, max_delay_minutes: float) -> bool:
    return snapshot.delay_seconds <= max_delay_minutes * 60


def _migrate_legacy_levels(
    stock_levels: Dict[str, Optional[str]],
    alerts: Iterable[Dict[str, Any]],
) -> None:
    alerts_by_id = {alert["id"]: alert for alert in alerts}
    for legacy_key in ("up", "down"):
        legacy_id = stock_levels.get(legacy_key)
        if not legacy_id or legacy_id not in alerts_by_id:
            continue
        group = get_alert_group(alerts_by_id[legacy_id]["type"])
        stock_levels.setdefault(group, legacy_id)


def _health_state(state: Dict[str, Any]) -> Dict[str, Any]:
    health = state.setdefault("health", {})
    health.setdefault("runs", 0)
    health.setdefault("successful_codes", [])
    health.setdefault("fetch_failures", {})
    health.setdefault("notifications", 0)
    health.setdefault("max_delay_seconds", 0.0)
    health.setdefault("latest_snapshots", {})
    health.setdefault("summary_sent", False)
    health.setdefault("last_error_signature", None)
    health.setdefault("last_error_notified_at", None)
    return health


def _record_fetch_success(
    health: Dict[str, Any],
    code: str,
    snapshot: PriceSnapshot,
) -> None:
    if code not in health["successful_codes"]:
        health["successful_codes"].append(code)
    health["max_delay_seconds"] = max(
        float(health["max_delay_seconds"]),
        snapshot.delay_seconds,
    )
    change_pct = (
        (snapshot.current - snapshot.previous_close) / snapshot.previous_close * 100
    )
    day_range_pct = (
        (snapshot.day_high - snapshot.day_low) / snapshot.previous_close * 100
    )
    health["latest_snapshots"][code] = {
        "current": snapshot.current,
        "previous_close": snapshot.previous_close,
        "change_pct": change_pct,
        "day_range_pct": day_range_pct,
        "day_volume": snapshot.day_volume,
        "average_volume_5d": snapshot.average_volume_5d,
        "latest_bar_time": snapshot.latest_bar_time.isoformat(),
        "source": snapshot.source,
    }


def _record_fetch_failure(health: Dict[str, Any], code: str) -> None:
    failures = health["fetch_failures"]
    failures[code] = int(failures.get(code, 0)) + 1


def _health_summary_due(config: Dict[str, Any], state: Dict[str, Any], now: datetime) -> bool:
    summary = config.get("health_summary", {})
    if not summary.get("enabled", False):
        return False
    if state.get("health", {}).get("summary_sent"):
        return False
    if not any(is_market_session(code, now) for code in ("0005.HK", "600000.SS")):
        return False
    hour, minute = (int(part) for part in summary.get("after", "15:50").split(":"))
    return now.hour * 60 + now.minute >= hour * 60 + minute


def build_health_summary(
    config: Dict[str, Any],
    state: Dict[str, Any],
    now: datetime,
) -> str:
    health = _health_state(state)
    expected_codes = [stock["code"] for stock in config["stocks"]]
    successful = set(health["successful_codes"])
    missing = [code for code in expected_codes if code not in successful]
    failures = health["fetch_failures"]
    failure_total = sum(int(count) for count in failures.values())
    max_delay_minutes = float(health["max_delay_seconds"]) / 60
    status = "✅ 正常" if not missing and not failures else "⚠️ 需要注意"
    lines = [
        f"📋 股票告警尾盤復盤 — {status}",
        "",
        f"📅 日期：{now.strftime('%Y-%m-%d')}",
        f"🔄 執行次數：{health['runs']}",
        f"📈 成功覆蓋：{len(successful)}/{len(expected_codes)} 隻股票",
        f"📡 最大行情延遲：約 {max_delay_minutes:.1f} 分鐘",
        f"🔔 告警通知：{health['notifications']} 次",
        f"❌ 行情失敗：{failure_total} 次",
    ]
    if config.get("health_summary", {}).get("include_watchlist_review", True):
        lines.extend(_build_watchlist_review_lines(config, health))
    if missing:
        lines.append(f"⚠️ 今日未成功覆蓋：{', '.join(missing)}")
    if failures:
        details = ", ".join(
            f"{code}×{count}" for code, count in sorted(failures.items())
        )
        lines.append(f"🧾 失敗明細：{details}")
    lines.extend(
        [
            f"⏰ 摘要時間：{now.strftime('%H:%M %Z')}",
            "ℹ️ 客觀延遲行情快照，僅作個人監控，不構成投資建議。",
        ]
    )
    return "\n".join(lines)


def _build_watchlist_review_lines(
    config: Dict[str, Any],
    health: Dict[str, Any],
) -> List[str]:
    snapshots = health.get("latest_snapshots", {})
    names = {
        stock["code"]: stock.get("name", stock["code"])
        for stock in config["stocks"]
    }
    records = [
        (code, names.get(code, code), snapshot)
        for code, snapshot in snapshots.items()
        if code in names and isinstance(snapshot.get("change_pct"), (int, float))
    ]
    if not records:
        return ["", "📊 自選股復盤：尚無可用行情快照"]

    limit = int(config.get("health_summary", {}).get("ranking_limit", 3))
    gainers = sorted(records, key=lambda item: item[2]["change_pct"], reverse=True)[
        :limit
    ]
    laggards = sorted(records, key=lambda item: item[2]["change_pct"])[:limit]
    ranges = sorted(
        records,
        key=lambda item: item[2].get("day_range_pct", 0),
        reverse=True,
    )[:limit]

    def format_changes(items: List[Tuple[str, str, Dict[str, Any]]]) -> str:
        return " ｜ ".join(
            f"{name} {snapshot['change_pct']:+.2f}%"
            for _, name, snapshot in items
        )

    range_text = " ｜ ".join(
        f"{name} {snapshot.get('day_range_pct', 0):.2f}%"
        for _, name, snapshot in ranges
    )
    return [
        "",
        f"📊 自選股復盤（最近成功快照 {len(records)}/{len(names)}）",
        f"📈 漲幅前{len(gainers)}：{format_changes(gainers)}",
        f"📉 表現末{len(laggards)}：{format_changes(laggards)}",
        f"🌊 日內振幅前{len(ranges)}：{range_text}",
    ]


def _error_signature(errors: Iterable[str]) -> str:
    normalized = "\n".join(sorted(set(errors)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _system_alert_due(
    config: Dict[str, Any],
    state: Dict[str, Any],
    errors: List[str],
    now: datetime,
) -> bool:
    alert_config = config.get("system_alert", {})
    if not alert_config.get("enabled", False) or not errors:
        return False

    health = _health_state(state)
    signature = _error_signature(errors)
    if health.get("last_error_signature") != signature:
        return True

    notified_at = health.get("last_error_notified_at")
    if not notified_at:
        return True
    try:
        last_notification = datetime.fromisoformat(notified_at)
    except (TypeError, ValueError):
        return True

    cooldown_minutes = float(alert_config.get("cooldown_minutes", 30))
    return (now - last_notification).total_seconds() >= cooldown_minutes * 60


def build_system_alert(errors: List[str], now: datetime) -> str:
    lines = [
        "🚨 股票告警系統異常",
        "",
        f"📅 時間：{now.strftime('%Y-%m-%d %H:%M %Z')}",
        f"❌ 異常數量：{len(errors)}",
    ]
    lines.extend(f"• {error}" for error in errors[:8])
    if len(errors) > 8:
        lines.append(f"• 其餘 {len(errors) - 8} 項請查看 GitHub Actions 日誌")
    lines.append("")
    lines.append("本次運行已標記失敗；相同異常會按冷卻時間重發。")
    return "\n".join(lines)


def _mark_system_alert_sent(
    state: Dict[str, Any],
    errors: List[str],
    now: datetime,
) -> None:
    health = _health_state(state)
    health["last_error_signature"] = _error_signature(errors)
    health["last_error_notified_at"] = now.isoformat()


def _clear_system_alert(state: Dict[str, Any]) -> None:
    health = _health_state(state)
    health["last_error_signature"] = None
    health["last_error_notified_at"] = None


def run(dry_run: bool = False, smoke_test: bool = False) -> int:
    if smoke_test:
        dry_run = True
    config = load_config()
    timezone = ZoneInfo(config.get("timezone", "Asia/Hong_Kong"))
    now = datetime.now(timezone)
    today = now.strftime("%Y-%m-%d")
    print(
        f"[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] "
        f"Alert check started — {len(config['stocks'])} stocks "
        f"(mode={'smoke-test' if smoke_test else 'dry-run' if dry_run else 'production'})"
    )

    state = load_state(today)
    health = _health_state(state)
    health["runs"] += 1
    errors: List[str] = []
    fetched_count = 0
    notification_count = 0

    active_stocks = (
        config["stocks"]
        if smoke_test
        else [
            stock
            for stock in config["stocks"]
            if is_trading_hours(stock["code"], now)
        ]
    )
    snapshots, batch_errors = get_prices(
        (stock["code"] for stock in active_stocks),
        now,
    )

    for stock in config["stocks"]:
        code = stock["code"]
        name = stock["name"]
        alerts = stock.get("alerts", [])
        channels = stock.get("notify", ["PUSHPLUS_TOKEN", "TELEGRAM"])

        if not smoke_test and not is_trading_hours(code, now):
            print(f"  {name} ({code}): 休市，跳過")
            continue

        snapshot = snapshots.get(code)
        if snapshot is None:
            message = f"{code}: fetch error — {batch_errors.get(code, '未知行情錯誤')}"
            print(f"  {message}")
            errors.append(message)
            _record_fetch_failure(health, code)
            continue
        max_delay_minutes = float(config.get("max_quote_delay_minutes", 20))
        if not smoke_test and not _quote_is_fresh(snapshot, max_delay_minutes):
            message = (
                f"{code}: 行情過期 — 延遲 {snapshot.delay_seconds / 60:.1f} 分鐘，"
                f"上限 {max_delay_minutes:.1f} 分鐘"
            )
            print(f"  {message}")
            errors.append(message)
            _record_fetch_failure(health, code)
            continue
        fetched_count += 1
        _record_fetch_success(health, code, snapshot)

        change_pct = (
            (snapshot.current - snapshot.previous_close)
            / snapshot.previous_close
            * 100
        )
        print(
            f"  {name} ({code}): {snapshot.current:.3f}  {change_pct:+.2f}%  "
            f"delay={snapshot.delay_seconds / 60:.1f}m  source={snapshot.source}"
        )

        levels = state.setdefault("levels", {})
        stock_levels = levels.setdefault(code, {})
        _migrate_legacy_levels(stock_levels, alerts)
        sent_today = state.setdefault("sent_today", {}).setdefault(code, [])

        triggered_by_group: Dict[str, List[Dict[str, Any]]] = {}
        for alert in alerts:
            group = get_alert_group(alert["type"])
            active = stock_levels.get(group) == alert["id"]
            hysteresis = float(
                alert.get(
                    "hysteresis",
                    config.get("default_hysteresis", 0.5),
                )
            )
            if check_condition(
                alert["type"],
                alert["value"],
                snapshot.current,
                snapshot.previous_close,
                active=active,
                hysteresis=hysteresis,
            ):
                triggered_by_group.setdefault(group, []).append(alert)
                print(f'    [{alert["id"]}] condition met')
            else:
                print(f'    [{alert["id"]}] not triggered')

        all_groups = {
            "change_up",
            "change_down",
            "price_above",
            "price_below",
        }
        for group in all_groups:
            most_extreme = find_most_extreme(triggered_by_group.get(group, []))
            current_level_id = most_extreme["id"] if most_extreme else None
            last_level_id = stock_levels.get(group)

            if current_level_id == last_level_id:
                if current_level_id:
                    print(f"    [{group}] 層級不變（{current_level_id}），跳過")
                continue

            if current_level_id is None:
                if last_level_id:
                    print(f"    [{group}] 解除（{last_level_id} → 無告警）")
                stock_levels[group] = None
                _clear_pending_group(state, code, group)
                continue

            if most_extreme.get("once_per_day") and current_level_id in sent_today:
                print(f"    [{group}] 今日已通知（{current_level_id}），跳過")
                stock_levels[group] = current_level_id
                _clear_pending_group(state, code, group)
                continue

            print(
                f"    [{group}] 層級變化："
                f"{last_level_id} → {current_level_id}  TRIGGERED"
            )
            emoji = {
                "change_down": "🚨",
                "price_below": "🔻",
                "price_above": "🎯",
            }.get(group, "🚀")
            title = f"{emoji} {name}({code}) 告警"
            message = build_message(
                name,
                code,
                most_extreme,
                snapshot,
                change_pct,
                now,
            )

            if dry_run:
                print("    DRY RUN: 通知已略過，狀態不寫入")
                continue
            delivery_key = _delivery_key(code, group, current_level_id)
            _clear_pending_group(state, code, group, keep=delivery_key)
            delivered, failed_channels = deliver_with_resume(
                state,
                delivery_key,
                channels,
                title,
                message,
            )
            if delivered:
                stock_levels[group] = current_level_id
                notification_count += 1
                health["notifications"] += 1
                if most_extreme.get("once_per_day"):
                    sent_today.append(current_level_id)
            else:
                errors.append(
                    f"{code}/{current_level_id}: 通知渠道未全部成功 "
                    f"({', '.join(failed_channels)})"
                )

    if _health_summary_due(config, state, now):
        summary_config = config["health_summary"]
        title = f"📋 股票告警尾盤復盤 {today}"
        message = build_health_summary(config, state, now)
        if dry_run:
            print("DRY RUN health summary:")
            print(message)
        elif notify(summary_config["notify"], title, message):
            health["summary_sent"] = True
        else:
            errors.append("尾盤復盤通知失敗")

    if smoke_test:
        print("SMOKE TEST watchlist review:")
        print(build_health_summary(config, state, now))

    if errors and _system_alert_due(config, state, errors, now):
        alert_config = config["system_alert"]
        title = f"🚨 股票告警系統異常 {today}"
        message = build_system_alert(errors, now)
        if dry_run:
            print("DRY RUN system alert:")
            print(message)
        elif notify(alert_config["notify"], title, message):
            _mark_system_alert_sent(state, errors, now)
        else:
            print("ERROR: 系統異常通知未能完整送達")
    elif not errors:
        _clear_system_alert(state)

    if not dry_run:
        save_state(state)
    print(
        f"Done. fetched={fetched_count}, notifications={notification_count}, "
        f"errors={len(errors)}, dry_run={dry_run}, smoke_test={smoke_test}"
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="股票波動告警檢查")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="抓取及評估行情，但不發通知、不更新狀態",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="只驗證 config.json",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="任何時段驗證全部股票最近交易日行情，但不發通知、不更新狀態",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check_config:
        config = load_config()
        alert_count = sum(len(stock.get("alerts", [])) for stock in config["stocks"])
        print(f"Config OK: {len(config['stocks'])} stocks, {alert_count} alerts")
        return 0
    return run(dry_run=args.dry_run, smoke_test=args.smoke_test)


if __name__ == "__main__":
    raise SystemExit(main())
