"""Deterministic tests for the non-blocking Sina full-market cache."""

from datetime import datetime, timedelta, timezone
from threading import Event
import time
import unittest
from unittest.mock import Mock

import pandas as pd

from stock_analysis.sina_realtime import (
    SINA_CACHE_TTL_SECONDS,
    SINA_FAILURE_COOLDOWN_SECONDS,
    _reset_sina_cache_for_tests,
    get_sina_a_share_snapshot,
    get_sina_cache_status,
)


BASE_TIME = datetime(2026, 8, 27, 6, 0, tzinfo=timezone.utc)


def market_frame(*, missing_optional_fields: bool = False, price: float = 1500.0) -> pd.DataFrame:
    rows = [
        ["sh600519", "贵州茅台", price, 1490.0, 1492.0, 1510.0, 1488.0, 1_000.0, 1_500_000.0, 0.67, "2026-08-27 14:00:00"],
        ["sz000001", "平安银行", 12.3, 12.1, 12.2, 12.4, 12.0, 2_000.0, 24_600.0, 1.65, "14:00:00"],
        ["sz300750", "宁德时代", 300.0, 295.0, 296.0, 302.0, 294.0, 3_000.0, 900_000.0, 1.69, None],
    ]
    columns = ["代码", "名称", "最新价", "昨收", "今开", "最高", "最低", "成交量", "成交额", "涨跌幅", "时间戳"]
    frame = pd.DataFrame(rows, columns=columns)
    return frame.drop(columns=["最高", "成交额"]) if missing_optional_fields else frame


def wait_until_idle(timeout: float = 2.0) -> None:
    deadline = time.perf_counter() + timeout
    while get_sina_cache_status(now=BASE_TIME)["loading"]:
        if time.perf_counter() >= deadline:
            raise AssertionError("background refresh did not finish")
        time.sleep(0.005)


def load_cache(frame: pd.DataFrame | None = None) -> Mock:
    fetcher = Mock(return_value=frame if frame is not None else market_frame())
    first = get_sina_a_share_snapshot("600519", fetcher=fetcher, now=BASE_TIME)
    if not first.loading:
        raise AssertionError("initial load was not started")
    wait_until_idle()
    return fetcher


class SinaRealtimeProviderTests(unittest.TestCase):
    def setUp(self):
        _reset_sina_cache_for_tests()

    def tearDown(self):
        _reset_sina_cache_for_tests()

    def test_empty_cache_returns_loading_immediately(self):
        release = Event()
        entered = Event()

        def slow_fetch() -> pd.DataFrame:
            entered.set()
            release.wait(1)
            return market_frame()

        started = time.perf_counter()
        snapshot = get_sina_a_share_snapshot("600519", fetcher=slow_fetch, now=BASE_TIME)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.2)
        self.assertFalse(snapshot.available)
        self.assertTrue(snapshot.loading)
        self.assertFalse(snapshot.stale)
        self.assertIsNone(snapshot.error)
        self.assertTrue(entered.wait(0.2))
        release.set()
        wait_until_idle()

    def test_three_symbols_start_only_one_background_fetch(self):
        release = Event()
        entered = Event()
        calls = 0

        def slow_fetch() -> pd.DataFrame:
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(1)
            return market_frame()

        results = [
            get_sina_a_share_snapshot(code, fetcher=slow_fetch, now=BASE_TIME)
            for code in ("600519", "000001", "300750")
        ]
        self.assertTrue(entered.wait(0.2))
        self.assertTrue(all(item.loading and not item.available for item in results))
        self.assertEqual(calls, 1)
        release.set()
        wait_until_idle()

    def test_completed_fetch_populates_cache_and_symbol_mapping(self):
        fetcher = load_cache()
        expected = {
            "600519": ("600519.SS", "贵州茅台"),
            "600519.SS": ("600519.SS", "贵州茅台"),
            "000001": ("000001.SZ", "平安银行"),
            "000001.SZ": ("000001.SZ", "平安银行"),
            "300750": ("300750.SZ", "宁德时代"),
        }
        for raw, (ticker, name) in expected.items():
            with self.subTest(raw=raw):
                snapshot = get_sina_a_share_snapshot(raw, fetcher=fetcher, now=BASE_TIME)
                self.assertTrue(snapshot.available)
                self.assertFalse(snapshot.loading)
                self.assertEqual(snapshot.ticker, ticker)
                self.assertEqual(snapshot.company_name, name)
        fetcher.assert_called_once_with()

    def test_fetch_failure_enters_cooldown(self):
        fetcher = Mock(side_effect=ConnectionError("blocked"))
        first = get_sina_a_share_snapshot("600519", fetcher=fetcher, now=BASE_TIME)
        self.assertTrue(first.loading)
        wait_until_idle()
        status = get_sina_cache_status(now=BASE_TIME)
        self.assertFalse(status["loading"])
        self.assertFalse(status["has_cache"])
        self.assertIn("ConnectionError", status["last_error"])
        self.assertEqual(
            status["cooldown_until"], BASE_TIME + timedelta(seconds=SINA_FAILURE_COOLDOWN_SECONDS)
        )

    def test_cooldown_does_not_repeat_request(self):
        failed_fetcher = Mock(side_effect=TimeoutError("slow"))
        get_sina_a_share_snapshot("600519", fetcher=failed_fetcher, now=BASE_TIME)
        wait_until_idle()
        forbidden = Mock(side_effect=AssertionError("cooldown must prevent a request"))
        snapshot = get_sina_a_share_snapshot(
            "000001", fetcher=forbidden, now=BASE_TIME + timedelta(seconds=10)
        )
        self.assertFalse(snapshot.available)
        self.assertFalse(snapshot.loading)
        self.assertIn("TimeoutError", snapshot.error)
        forbidden.assert_not_called()

    def test_stale_cache_returns_immediately_and_starts_one_refresh(self):
        load_cache()
        release = Event()
        entered = Event()
        refresh = Mock()

        def slow_refresh() -> pd.DataFrame:
            refresh()
            entered.set()
            release.wait(1)
            return market_frame(price=1600.0)

        refresh_time = BASE_TIME + timedelta(hours=2)
        first = get_sina_a_share_snapshot("600519", fetcher=slow_refresh, now=refresh_time)
        second = get_sina_a_share_snapshot("000001", fetcher=slow_refresh, now=refresh_time)
        self.assertTrue(entered.wait(0.2))
        self.assertTrue(first.available and first.stale and first.loading)
        self.assertEqual(first.current_price, 1500.0)
        self.assertTrue(second.available and second.stale and second.loading)
        refresh.assert_called_once_with()
        release.set()
        wait_until_idle()
        updated = get_sina_a_share_snapshot("600519", now=refresh_time)
        self.assertEqual(updated.current_price, 1600.0)
        self.assertFalse(updated.stale)

    def test_background_exception_never_propagates_to_page_call(self):
        def broken_fetch() -> pd.DataFrame:
            raise RuntimeError("background boom")

        snapshot = get_sina_a_share_snapshot("600519", fetcher=broken_fetch, now=BASE_TIME)
        self.assertFalse(snapshot.available)
        self.assertTrue(snapshot.loading)
        wait_until_idle()
        status = get_sina_cache_status(now=BASE_TIME)
        self.assertIn("RuntimeError", status["last_error"])

    def test_non_a_share_symbol_does_not_start_thread(self):
        fetcher = Mock(side_effect=AssertionError("must not be called"))
        snapshot = get_sina_a_share_snapshot("AAPL", fetcher=fetcher, now=BASE_TIME)
        self.assertFalse(snapshot.available)
        self.assertFalse(snapshot.loading)
        self.assertEqual(snapshot.source, "sina")
        self.assertFalse(get_sina_cache_status(now=BASE_TIME)["loading"])
        fetcher.assert_not_called()

    def test_cache_status_reports_loading_and_fresh_cache(self):
        release = Event()
        entered = Event()

        def slow_fetch() -> pd.DataFrame:
            entered.set()
            release.wait(1)
            return market_frame()

        get_sina_a_share_snapshot("600519", fetcher=slow_fetch, now=BASE_TIME)
        self.assertTrue(entered.wait(0.2))
        loading_status = get_sina_cache_status(now=BASE_TIME)
        self.assertTrue(loading_status["loading"])
        self.assertFalse(loading_status["has_cache"])
        release.set()
        wait_until_idle()
        ready_status = get_sina_cache_status(now=BASE_TIME)
        self.assertFalse(ready_status["loading"])
        self.assertTrue(ready_status["has_cache"])
        self.assertFalse(ready_status["stale"])
        self.assertEqual(ready_status["fetched_at"], BASE_TIME)
        self.assertEqual(ready_status["cache_size"], 3)

    def test_missing_fields_are_none_and_fetch_time_is_explicit(self):
        load_cache(market_frame(missing_optional_fields=True))
        snapshot = get_sina_a_share_snapshot("000001", now=BASE_TIME)
        self.assertIsNone(snapshot.high)
        self.assertIsNone(snapshot.turnover_amount)
        self.assertEqual(snapshot.timestamp, BASE_TIME)
        self.assertEqual(snapshot.fetched_at, BASE_TIME)
        self.assertTrue(snapshot.timestamp_is_fetch_time)

    def test_invalid_refresh_does_not_pollute_old_cache(self):
        load_cache()
        refresh_time = BASE_TIME + timedelta(seconds=SINA_CACHE_TTL_SECONDS + 1)
        stale = get_sina_a_share_snapshot(
            "600519", fetcher=lambda: pd.DataFrame({"unexpected": ["bad"]}), now=refresh_time
        )
        self.assertTrue(stale.available)
        self.assertTrue(stale.stale)
        wait_until_idle()
        cached = get_sina_a_share_snapshot(
            "300750", allow_background_refresh=False, now=refresh_time
        )
        self.assertTrue(cached.available)
        self.assertTrue(cached.stale)
        self.assertEqual(cached.company_name, "宁德时代")
        self.assertIn("ValueError", cached.error)

    def test_background_refresh_can_be_disabled(self):
        fetcher = Mock(side_effect=AssertionError("must not be called"))
        snapshot = get_sina_a_share_snapshot(
            "600519", allow_background_refresh=False, fetcher=fetcher, now=BASE_TIME
        )
        self.assertFalse(snapshot.available)
        self.assertFalse(snapshot.loading)
        fetcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
