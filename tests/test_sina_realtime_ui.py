"""UI-boundary tests for Sina display without network or Streamlit startup."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import inspect
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from stock_analysis.markets import MARKET_A_SHARE, MARKET_HK, MARKET_US, MarketSymbol
from stock_analysis.providers import RealtimeSnapshot
from stock_analysis import realtime_ui


FETCHED_AT = datetime(2026, 8, 27, 6, 30, tzinfo=timezone.utc)
BEIJING = ZoneInfo("Asia/Shanghai")
VANCOUVER = ZoneInfo("America/Vancouver")
MORNING_TIME = datetime(2026, 8, 27, 10, 0, tzinfo=BEIJING)


def snapshot(**changes: object) -> RealtimeSnapshot:
    base = RealtimeSnapshot(
        ticker="600519.SS",
        market=MARKET_A_SHARE,
        company_name="贵州茅台",
        current_price=1500.0,
        change_percent=1.25,
        open=1490.0,
        high=1510.0,
        low=1480.0,
        previous_close=1481.48,
        volume=6_500_000.0,
        turnover_amount=3_820_000_000.0,
        source="sina",
        source_type="automatic",
        input_method="sina_market_cache",
        available=True,
        fetched_at=FETCHED_AT,
    )
    return replace(base, **changes)


class MinimalStreamlit:
    def __init__(self, button_value: bool = False):
        self.button_value = button_value
        self.messages: list[tuple[str, str]] = []

    def container(self, **_: object):
        return nullcontext()

    def write(self, value: str):
        self.messages.append(("write", value))

    def info(self, value: str):
        self.messages.append(("info", value))

    def warning(self, value: str):
        self.messages.append(("warning", value))

    def caption(self, value: str):
        self.messages.append(("caption", value))

    def button(self, label: str, **_: object) -> bool:
        self.messages.append(("button", label))
        return self.button_value


class SinaRealtimeUITests(unittest.TestCase):
    def test_us_market_does_not_load_or_show_sina(self):
        symbol = MarketSymbol(MARKET_US, "AAPL", "AAPL", "USD")
        fake_st = MinimalStreamlit()
        with patch.object(realtime_ui, "st", fake_st), patch.object(
            realtime_ui, "get_sina_a_share_snapshot"
        ) as provider:
            self.assertIsNone(realtime_ui._load_sina_snapshot(symbol, allow_background_refresh=True))
            realtime_ui._render_sina_automatic_quote(symbol, "USD", now=MORNING_TIME)
            provider.assert_not_called()
        self.assertEqual(fake_st.messages, [])

    def test_hk_market_does_not_load_or_show_sina(self):
        symbol = MarketSymbol(MARKET_HK, "700", "0700.HK", "HKD")
        fake_st = MinimalStreamlit()
        with patch.object(realtime_ui, "st", fake_st), patch.object(
            realtime_ui, "get_sina_a_share_snapshot"
        ) as provider:
            self.assertIsNone(realtime_ui._load_sina_snapshot(symbol, allow_background_refresh=True))
            realtime_ui._render_sina_automatic_quote(symbol, "HKD", now=MORNING_TIME)
            provider.assert_not_called()
        self.assertEqual(fake_st.messages, [])

    def test_a_share_renders_sina_region(self):
        symbol = MarketSymbol(MARKET_A_SHARE, "600519", "600519.SS", "CNY")
        fake_st = MinimalStreamlit()
        loading = snapshot(available=False, loading=True, fetched_at=None)
        with (
            patch.object(realtime_ui, "st", fake_st),
            patch.object(realtime_ui, "get_sina_a_share_snapshot", return_value=loading) as provider,
        ):
            realtime_ui._render_sina_automatic_quote(symbol, "CNY", now=MORNING_TIME)
        provider.assert_called_once_with("600519.SS", allow_background_refresh=True)
        self.assertIn(("write", "**新浪实时行情**"), fake_st.messages)

    def test_loading_state_has_clear_chinese_message(self):
        state = realtime_ui.build_sina_display_state(
            snapshot(available=False, loading=True, fetched_at=None)
        )
        self.assertEqual(state.state, "loading")
        self.assertIn("新浪行情正在后台加载", state.message)
        self.assertIn("当前先显示 Yahoo 参考行情", state.message)
        self.assertIn("手动实时行情", state.message)

    def test_available_state_and_fields_are_formatted(self):
        quote = snapshot()
        state = realtime_ui.build_sina_display_state(quote)
        fields = dict(realtime_ui.sina_display_fields(quote, "CNY"))
        self.assertEqual(state.state, "available")
        self.assertEqual(state.cache_label, "缓存有效")
        self.assertIn("温哥华", state.fetched_at_label)
        self.assertIn("北京", state.fetched_at_label)
        self.assertEqual(fields["股票名称"], "贵州茅台")
        self.assertIn("1,500.00", fields["最新价"])
        self.assertEqual(fields["涨跌幅"], "+1.25%")
        self.assertEqual(fields["成交量"], "650.00 万")
        self.assertEqual(fields["成交额"], "38.20 亿")

    def test_sina_turnover_amount_uses_scale_without_currency_suffix(self):
        cases = (
            (3_204_000_000.0, "32.04 亿"),
            (65_000_000.0, "6,500.00 万"),
            (8_500.0, "8,500"),
            (None, "—"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                quote = snapshot(turnover_amount=value)
                fields = dict(realtime_ui.sina_display_fields(quote, "CNY"))
                self.assertEqual(fields["成交额"], expected)

    def test_stale_state_is_explicit(self):
        state = realtime_ui.build_sina_display_state(snapshot(stale=True, loading=True))
        self.assertEqual(state.state, "stale")
        self.assertIn("缓存数据，可能不是最新行情", state.message)
        self.assertIn("后台正在更新", state.message)
        self.assertIn("温哥华", state.fetched_at_label)
        self.assertIn("北京", state.fetched_at_label)

    def test_fetch_time_crossing_calendar_date_is_marked_next_day(self):
        vancouver = datetime(2026, 8, 27, 19, 12, 8, tzinfo=VANCOUVER)
        label = realtime_ui.format_sina_fetch_time(vancouver.astimezone(timezone.utc))
        self.assertIn("温哥华 19:12:08", label)
        self.assertIn("北京 10:12:08", label)
        self.assertIn("次日", label)

    def test_fetch_time_same_calendar_date_has_no_next_day_label(self):
        vancouver = datetime(2026, 8, 27, 7, 0, 0, tzinfo=VANCOUVER)
        label = realtime_ui.format_sina_fetch_time(vancouver.astimezone(timezone.utc))
        self.assertIn("温哥华 07:00:00", label)
        self.assertIn("北京 22:00:00", label)
        self.assertNotIn("次日", label)

    def test_missing_or_naive_fetch_time_is_not_guessed(self):
        self.assertIsNone(realtime_ui.format_sina_fetch_time(None))
        self.assertIsNone(realtime_ui.format_sina_fetch_time(datetime(2026, 8, 27, 19, 12, 8)))
        loading = realtime_ui.build_sina_display_state(
            snapshot(available=False, loading=True, fetched_at=None)
        )
        unavailable = realtime_ui.build_sina_display_state(
            snapshot(available=False, loading=False, fetched_at=None)
        )
        self.assertIsNone(loading.fetched_at_label)
        self.assertIsNone(unavailable.fetched_at_label)

    def test_unavailable_state_hides_raw_exception(self):
        quote = snapshot(
            available=False,
            loading=False,
            fetched_at=None,
            error="ConnectionError: HTTPSConnectionPool(host='example'): long internal traceback",
        )
        state = realtime_ui.build_sina_display_state(quote)
        self.assertEqual(state.state, "unavailable")
        self.assertIn("网络连接失败", state.message)
        self.assertIn("手动行情", state.message)
        self.assertNotIn("HTTPSConnectionPool", state.message)

    def test_confirmed_manual_snapshot_is_not_replaced(self):
        manual = snapshot(
            source="招商证券",
            source_type="user_provided",
            input_method="manual",
            confirmed=True,
        )
        store = {"600519.SS": manual}
        realtime_ui.build_sina_display_state(snapshot())
        self.assertIs(realtime_ui._confirmed_snapshot_from_store(store, "600519.SS"), manual)
        self.assertEqual(store, {"600519.SS": manual})

    def test_sina_display_has_no_technical_summary_input(self):
        parameters = inspect.signature(realtime_ui._render_sina_automatic_quote).parameters
        self.assertEqual(tuple(parameters), ("market_symbol", "currency", "now"))
        quote = snapshot()
        technical_score = 73
        realtime_ui.build_sina_display_state(quote)
        realtime_ui.sina_display_fields(quote, "CNY")
        self.assertEqual(technical_score, 73)

    def test_sina_display_does_not_modify_history_dataframe(self):
        history = pd.DataFrame({"Close": [100.0, 101.0], "RSI": [48.0, 51.0]})
        original = history.copy(deep=True)
        realtime_ui.sina_display_fields(snapshot(), "CNY")
        pd.testing.assert_frame_equal(history, original)

    def test_check_button_reads_once_and_does_not_force_another_call(self):
        symbol = MarketSymbol(MARKET_A_SHARE, "600519", "600519.SS", "CNY")
        fake_st = MinimalStreamlit(button_value=True)
        loading = snapshot(available=False, loading=True, fetched_at=None)
        provider = Mock(return_value=loading)
        with patch.object(realtime_ui, "st", fake_st), patch.object(
            realtime_ui, "get_sina_a_share_snapshot", provider
        ):
            realtime_ui._render_sina_automatic_quote(symbol, "CNY", now=MORNING_TIME)
        provider.assert_called_once_with("600519.SS", allow_background_refresh=True)

    def test_lunch_break_reads_cache_without_starting_refresh(self):
        symbol = MarketSymbol(MARKET_A_SHARE, "600519", "600519.SS", "CNY")
        fake_st = MinimalStreamlit(button_value=True)
        unavailable = snapshot(available=False, loading=False, fetched_at=None)
        lunch = datetime(2026, 8, 27, 12, 0, tzinfo=BEIJING)
        with (
            patch.object(realtime_ui, "st", fake_st),
            patch.object(realtime_ui, "get_sina_a_share_snapshot", return_value=unavailable) as provider,
        ):
            realtime_ui._render_sina_automatic_quote(symbol, "CNY", now=lunch)
        provider.assert_called_once_with("600519.SS", allow_background_refresh=False)
        self.assertIn(("caption", "午间休市：当前优先 Yahoo 行情"), fake_st.messages)

    def test_closed_and_weekend_never_allow_background_refresh(self):
        symbol = MarketSymbol(MARKET_A_SHARE, "600519", "600519.SS", "CNY")
        times = (
            datetime(2026, 8, 27, 16, 0, tzinfo=BEIJING),
            datetime(2026, 8, 29, 10, 0, tzinfo=BEIJING),
        )
        for now in times:
            with self.subTest(now=now):
                fake_st = MinimalStreamlit(button_value=True)
                provider = Mock(return_value=snapshot(available=False, loading=False, fetched_at=None))
                with patch.object(realtime_ui, "st", fake_st), patch.object(
                    realtime_ui, "get_sina_a_share_snapshot", provider
                ):
                    realtime_ui._render_sina_automatic_quote(symbol, "CNY", now=now)
                provider.assert_called_once_with("600519.SS", allow_background_refresh=False)

    def test_pre_open_and_afternoon_allow_background_refresh(self):
        symbol = MarketSymbol(MARKET_A_SHARE, "600519", "600519.SS", "CNY")
        times = (
            datetime(2026, 8, 27, 9, 20, tzinfo=BEIJING),
            datetime(2026, 8, 27, 14, 0, tzinfo=BEIJING),
        )
        for now in times:
            with self.subTest(now=now):
                fake_st = MinimalStreamlit()
                provider = Mock(return_value=snapshot(available=False, loading=True, fetched_at=None))
                with patch.object(realtime_ui, "st", fake_st), patch.object(
                    realtime_ui, "get_sina_a_share_snapshot", provider
                ):
                    realtime_ui._render_sina_automatic_quote(symbol, "CNY", now=now)
                provider.assert_called_once_with("600519.SS", allow_background_refresh=True)

    def test_non_trading_cache_is_labeled_as_recent_cache(self):
        state = realtime_ui.build_sina_display_state(snapshot(), prefers_sina=False)
        self.assertEqual(state.state, "cached")
        self.assertIn("最近新浪缓存", state.message)
        self.assertIn("当前优先使用 Yahoo", state.message)


if __name__ == "__main__":
    unittest.main()
