import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import check_alerts
import pandas as pd


HKT = ZoneInfo("Asia/Hong_Kong")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "timezone": "Asia/Hong_Kong",
            "stocks": [
                {
                    "code": "2577.HK",
                    "name": "英諾賽科",
                    "notify": ["PUSHPLUS_TOKEN"],
                    "alerts": [
                        {
                            "id": "rise_5",
                            "type": "rise_pct",
                            "value": 5,
                            "label": "升幅 +5%",
                        }
                    ],
                }
            ],
        }

    def test_valid_config(self):
        check_alerts.validate_config(self.config)

    def test_duplicate_stock_is_rejected(self):
        config = copy.deepcopy(self.config)
        config["stocks"].append(copy.deepcopy(config["stocks"][0]))
        with self.assertRaisesRegex(ValueError, "股票代碼重複"):
            check_alerts.validate_config(config)

    def test_bad_percentage_sign_is_rejected(self):
        config = copy.deepcopy(self.config)
        config["stocks"][0]["alerts"][0]["value"] = -5
        with self.assertRaisesRegex(ValueError, "升幅門檻"):
            check_alerts.validate_config(config)

    def test_invalid_quote_delay_is_rejected(self):
        config = copy.deepcopy(self.config)
        config["max_quote_delay_minutes"] = 0
        with self.assertRaisesRegex(ValueError, "max_quote_delay_minutes"):
            check_alerts.validate_config(config)

    def test_invalid_summary_ranking_limit_is_rejected(self):
        config = copy.deepcopy(self.config)
        config["health_summary"] = {"ranking_limit": 0}
        with self.assertRaisesRegex(ValueError, "ranking_limit"):
            check_alerts.validate_config(config)


class TradingHoursTests(unittest.TestCase):
    def test_hk_market_sessions(self):
        self.assertTrue(
            check_alerts.is_trading_hours(
                "2577.HK", datetime(2026, 7, 28, 9, 30, tzinfo=HKT)
            )
        )
        self.assertFalse(
            check_alerts.is_trading_hours(
                "2577.HK", datetime(2026, 7, 28, 12, 30, tzinfo=HKT)
            )
        )
        self.assertTrue(
            check_alerts.is_trading_hours(
                "2577.HK", datetime(2026, 7, 28, 15, 59, tzinfo=HKT)
            )
        )

    def test_a_share_sessions(self):
        self.assertFalse(
            check_alerts.is_trading_hours(
                "600487.SS", datetime(2026, 7, 28, 11, 30, tzinfo=HKT)
            )
        )
        self.assertTrue(
            check_alerts.is_trading_hours(
                "600487.SS", datetime(2026, 7, 28, 14, 59, tzinfo=HKT)
            )
        )

    def test_weekend_is_closed(self):
        self.assertFalse(
            check_alerts.is_trading_hours(
                "2577.HK", datetime(2026, 8, 1, 10, 0, tzinfo=HKT)
            )
        )

    def test_exchange_holiday_is_closed(self):
        self.assertFalse(
            check_alerts.is_trading_hours(
                "600487.SS", datetime(2026, 10, 1, 10, 0, tzinfo=HKT)
            )
        )


class AlertLogicTests(unittest.TestCase):
    def test_price_and_percentage_use_separate_groups(self):
        self.assertEqual(check_alerts.get_alert_group("price_above"), "price_above")
        self.assertEqual(check_alerts.get_alert_group("rise_pct"), "change_up")

    def test_most_extreme_percentage(self):
        alerts = [
            {"id": "rise_5", "type": "rise_pct", "value": 5},
            {"id": "rise_10", "type": "rise_pct", "value": 10},
        ]
        self.assertEqual(check_alerts.find_most_extreme(alerts)["id"], "rise_10")

    def test_conditions(self):
        self.assertTrue(check_alerts.check_condition("rise_pct", 5, 106, 100))
        self.assertTrue(check_alerts.check_condition("drop_pct", -5, 94, 100))
        self.assertTrue(check_alerts.check_condition("price_above", 85, 85, 80))
        self.assertFalse(check_alerts.check_condition("price_below", 60, 61, 80))

    def test_hysteresis_prevents_threshold_flapping(self):
        self.assertFalse(
            check_alerts.check_condition("rise_pct", 5, 104.6, 100)
        )
        self.assertTrue(
            check_alerts.check_condition(
                "rise_pct", 5, 104.6, 100, active=True, hysteresis=0.5
            )
        )
        self.assertTrue(
            check_alerts.check_condition(
                "price_above", 85, 84.6, 80, active=True, hysteresis=0.5
            )
        )

    def test_legacy_state_is_migrated_without_duplicate_alert(self):
        levels = {"up": "rise_5", "down": None}
        alerts = [{"id": "rise_5", "type": "rise_pct", "value": 5}]
        check_alerts._migrate_legacy_levels(levels, alerts)
        self.assertEqual(levels["change_up"], "rise_5")


class StateTests(unittest.TestCase):
    def test_invalid_state_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            state = check_alerts.load_state("2026-07-28", path)
            self.assertEqual(
                state,
                {
                    "date": "2026-07-28",
                    "levels": {},
                    "sent_today": {},
                    "health": {},
                    "pending_notifications": {},
                },
            )

    def test_state_save_is_atomic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            expected = {
                "date": "2026-07-28",
                "levels": {"2577.HK": {"change_down": "drop_5"}},
                "sent_today": {},
            }
            check_alerts.save_state(expected, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)
            self.assertFalse(path.with_suffix(".tmp").exists())


class BatchPriceTests(unittest.TestCase):
    def test_snapshot_uses_batch_intraday_and_previous_daily_close(self):
        code = "2577.HK"
        intraday_index = pd.DatetimeIndex(
            ["2026-07-28 09:30:00+08:00", "2026-07-28 09:31:00+08:00"]
        )
        intraday_frame = pd.DataFrame(
            {
                "Open": [40.0, 40.5],
                "High": [40.7, 41.2],
                "Low": [39.9, 40.4],
                "Close": [40.5, 41.0],
                "Volume": [1000, 2000],
            },
            index=intraday_index,
        )
        daily_frame = pd.DataFrame(
            {"Close": [39.0, 41.0], "Volume": [2500, 3000]},
            index=pd.DatetimeIndex(["2026-07-27", "2026-07-28"]),
        )
        intraday = pd.concat({code: intraday_frame}, axis=1)
        daily = pd.concat({code: daily_frame}, axis=1)
        now = datetime(2026, 7, 28, 9, 33, tzinfo=HKT)

        snapshot = check_alerts._snapshot_from_frames(
            code, intraday, daily, now
        )

        self.assertEqual(snapshot.current, 41.0)
        self.assertEqual(snapshot.previous_close, 39.0)
        self.assertEqual(snapshot.day_high, 41.2)
        self.assertEqual(snapshot.delay_seconds, 120.0)
        self.assertEqual(snapshot.source, "yfinance-batch")
        self.assertEqual(snapshot.day_volume, 3000)
        self.assertEqual(snapshot.average_volume_5d, 2500)

    def test_snapshot_accepts_latest_session_outside_market_day(self):
        code = "2577.HK"
        intraday_frame = pd.DataFrame(
            {
                "Open": [40.0],
                "High": [41.0],
                "Low": [39.5],
                "Close": [40.5],
            },
            index=pd.DatetimeIndex(["2026-07-31 15:59:00+08:00"]),
        )
        daily_frame = pd.DataFrame(
            {"Close": [39.0, 40.5]},
            index=pd.DatetimeIndex(["2026-07-30", "2026-07-31"]),
        )
        snapshot = check_alerts._snapshot_from_frames(
            code,
            pd.concat({code: intraday_frame}, axis=1),
            pd.concat({code: daily_frame}, axis=1),
            datetime(2026, 8, 1, 10, 0, tzinfo=HKT),
        )
        self.assertEqual(snapshot.latest_bar_time.date().isoformat(), "2026-07-31")
        self.assertEqual(snapshot.previous_close, 39.0)

    def test_quote_freshness_guard(self):
        snapshot = check_alerts.PriceSnapshot(
            current=40,
            previous_close=39,
            day_open=39.5,
            day_high=40.5,
            day_low=39,
            latest_bar_time=datetime(2026, 7, 28, 10, 0, tzinfo=HKT),
            delay_seconds=20 * 60,
            source="test",
        )
        self.assertTrue(check_alerts._quote_is_fresh(snapshot, 20))
        self.assertFalse(check_alerts._quote_is_fresh(snapshot, 19.9))


class HealthSummaryTests(unittest.TestCase):
    def test_summary_reports_coverage_failures_and_delay(self):
        config = {
            "stocks": [{"code": "2577.HK"}, {"code": "600487.SS"}],
        }
        state = {
            "health": {
                "runs": 100,
                "successful_codes": ["2577.HK"],
                "fetch_failures": {"600487.SS": 2},
                "notifications": 3,
                "max_delay_seconds": 180,
                "summary_sent": False,
            }
        }
        message = check_alerts.build_health_summary(
            config,
            state,
            datetime(2026, 7, 28, 15, 50, tzinfo=HKT),
        )
        self.assertIn("成功覆蓋：1/2", message)
        self.assertIn("最大行情延遲：約 3.0 分鐘", message)
        self.assertIn("600487.SS×2", message)

    def test_summary_includes_objective_watchlist_review(self):
        config = {
            "health_summary": {"ranking_limit": 2},
            "stocks": [
                {"code": "2577.HK", "name": "英諾賽科"},
                {"code": "600487.SS", "name": "亨通光電"},
                {"code": "301396.SZ", "name": "宏景科技"},
            ],
        }
        state = {
            "health": {
                "successful_codes": ["2577.HK", "600487.SS", "301396.SZ"],
                "latest_snapshots": {
                    "2577.HK": {"change_pct": 5.7, "day_range_pct": 7.0},
                    "600487.SS": {"change_pct": -1.2, "day_range_pct": 2.0},
                    "301396.SZ": {"change_pct": 20.0, "day_range_pct": 20.5},
                },
            }
        }
        message = check_alerts.build_health_summary(
            config,
            state,
            datetime(2026, 7, 31, 15, 50, tzinfo=HKT),
        )
        self.assertIn("漲幅前2：宏景科技 +20.00% ｜ 英諾賽科 +5.70%", message)
        self.assertIn("表現末2：亨通光電 -1.20% ｜ 英諾賽科 +5.70%", message)
        self.assertIn("日內振幅前2：宏景科技 20.50%", message)
        self.assertIn("不構成投資建議", message)


class MessageTests(unittest.TestCase):
    def test_alert_message_contains_fact_base_and_volume_context(self):
        snapshot = check_alerts.PriceSnapshot(
            current=108,
            previous_close=100,
            day_open=102,
            day_high=110,
            day_low=99,
            latest_bar_time=datetime(2026, 7, 31, 15, 30, tzinfo=HKT),
            delay_seconds=60,
            source="test",
            day_volume=12_000_000,
            average_volume_5d=10_000_000,
        )
        message = check_alerts.build_message(
            "測試股",
            "2577.HK",
            {"id": "rise_5", "type": "rise_pct", "value": 5, "label": "升幅 +5%"},
            snapshot,
            8,
            datetime(2026, 7, 31, 15, 31, tzinfo=HKT),
        )
        self.assertIn("事實底稿：開盤缺口 +2.00%", message)
        self.assertIn("日內 +5.88%", message)
        self.assertIn("當日區間位置 82%", message)
        self.assertIn("近5日全日均量的 120%", message)


class SystemAlertTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "system_alert": {
                "enabled": True,
                "cooldown_minutes": 30,
                "notify": ["PUSHPLUS_TOKEN"],
            }
        }
        self.now = datetime(2026, 7, 28, 10, 0, tzinfo=HKT)

    def test_new_error_is_due_and_same_error_obeys_cooldown(self):
        state = {"health": {}}
        errors = ["2577.HK: fetch error"]
        self.assertTrue(
            check_alerts._system_alert_due(self.config, state, errors, self.now)
        )
        check_alerts._mark_system_alert_sent(state, errors, self.now)
        self.assertFalse(
            check_alerts._system_alert_due(
                self.config,
                state,
                errors,
                datetime(2026, 7, 28, 10, 29, tzinfo=HKT),
            )
        )
        self.assertTrue(
            check_alerts._system_alert_due(
                self.config,
                state,
                errors,
                datetime(2026, 7, 28, 10, 30, tzinfo=HKT),
            )
        )

    def test_changed_error_signature_alerts_immediately(self):
        state = {"health": {}}
        check_alerts._mark_system_alert_sent(
            state, ["2577.HK: fetch error"], self.now
        )
        self.assertTrue(
            check_alerts._system_alert_due(
                self.config,
                state,
                ["600487.SS: fetch error"],
                datetime(2026, 7, 28, 10, 1, tzinfo=HKT),
            )
        )

    def test_healthy_run_clears_error_dedup_state(self):
        state = {"health": {}}
        check_alerts._mark_system_alert_sent(
            state, ["2577.HK: fetch error"], self.now
        )
        check_alerts._clear_system_alert(state)
        self.assertIsNone(state["health"]["last_error_signature"])
        self.assertIsNone(state["health"]["last_error_notified_at"])


class NotificationTests(unittest.TestCase):
    @patch("check_alerts.requests.post")
    def test_pushplus_uses_https_and_checks_success(self, post):
        response = Mock()
        response.json.return_value = {"code": 200, "msg": "success"}
        post.return_value = response
        self.assertTrue(check_alerts.send_pushplus("token", "title", "message"))
        self.assertEqual(post.call_args.args[0], "https://www.pushplus.plus/send")
        response.raise_for_status.assert_called_once()

    @patch("check_alerts.requests.post")
    def test_pushplus_api_failure_is_reported(self, post):
        response = Mock()
        response.json.return_value = {"code": 500, "msg": "failed"}
        post.return_value = response
        self.assertFalse(check_alerts.send_pushplus("token", "title", "message"))

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_notification_secret_fails_closed(self):
        self.assertFalse(
            check_alerts.notify(["PUSHPLUS_TOKEN"], "title", "message")
        )

    @patch("check_alerts.notify_with_results")
    def test_partial_delivery_resumes_only_failed_channel(self, notify_results):
        state = {"pending_notifications": {}}
        key = check_alerts._delivery_key("2577.HK", "change_up", "rise_5")
        notify_results.return_value = {
            "PUSHPLUS_TOKEN": True,
            "TELEGRAM": False,
        }

        delivered, failed = check_alerts.deliver_with_resume(
            state,
            key,
            ["PUSHPLUS_TOKEN", "TELEGRAM"],
            "title",
            "message",
        )

        self.assertFalse(delivered)
        self.assertEqual(failed, ["TELEGRAM"])
        self.assertEqual(state["pending_notifications"][key], ["PUSHPLUS_TOKEN"])

        notify_results.return_value = {"TELEGRAM": True}
        delivered, failed = check_alerts.deliver_with_resume(
            state,
            key,
            ["PUSHPLUS_TOKEN", "TELEGRAM"],
            "title",
            "message",
        )

        self.assertTrue(delivered)
        self.assertEqual(failed, [])
        self.assertNotIn(key, state["pending_notifications"])
        self.assertEqual(
            notify_results.call_args.args[0],
            ["TELEGRAM"],
        )


if __name__ == "__main__":
    unittest.main()
