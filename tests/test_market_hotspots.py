"""Deterministic tests for market-hotspot normalization, caching, and fallback."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

import pandas as pd

from stock_analysis.market_hotspots import (
    BOARD_LIST_CACHE_TTL_SECONDS,
    BOARD_TYPE_CONCEPT,
    BOARD_TYPE_INDUSTRY,
    FAILURE_COOLDOWN_SECONDS,
    SOURCE_EASTMONEY,
    SOURCE_THS,
    STALE_MAX_AGE_SECONDS,
    _reset_hotspot_cache_for_tests,
    get_concept_hotspots,
    get_hotspot_cache_status,
    get_industry_hotspots,
    normalize_eastmoney_boards,
    normalize_ths_concept_directory,
    normalize_ths_industry,
)


NOW = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


def ths_industry_frame(rows=None):
    return pd.DataFrame(
        rows
        or [
            [1, "种植业与林业", 3.31, 100.0, 10.0, 2.0, 23, 7, 7.51, "敦煌种业", 8.21, 10.05],
            [2, "房地产", None, 90.0, 9.0, 1.0, 68, 15, 4.13, "我爱我家", 2.64, 10.0],
        ],
        columns=(
            "序号",
            "板块",
            "涨跌幅",
            "总成交量",
            "总成交额",
            "净流入",
            "上涨家数",
            "下跌家数",
            "均价",
            "领涨股",
            "领涨股-最新价",
            "领涨股-涨跌幅",
        ),
    )


def eastmoney_frame(rows=None):
    return pd.DataFrame(
        rows
        or [
            [1, "农业种植", "BK0888", 1234.5, 10.1, 2.59, 5000.0, 2.8, 69, 16, "神农种业", 10.01],
            [2, "低空经济", "BK1234", 900.0, -3.0, None, 4000.0, 1.5, 12, 20, "示例股份", 5.0],
        ],
        columns=(
            "排名",
            "板块名称",
            "板块代码",
            "最新价",
            "涨跌额",
            "涨跌幅",
            "总市值",
            "换手率",
            "上涨家数",
            "下跌家数",
            "领涨股票",
            "领涨股票-涨跌幅",
        ),
    )


def ths_concept_frame(rows=None):
    return pd.DataFrame(
        rows or [["阿尔茨海默概念", "308614"], ["AI手机", "309120"]],
        columns=("name", "code"),
    )


class MarketHotspotTests(unittest.TestCase):
    def setUp(self):
        _reset_hotspot_cache_for_tests()

    def tearDown(self):
        _reset_hotspot_cache_for_tests()

    def test_industry_ths_success(self):
        em = Mock(side_effect=AssertionError("fallback must not run"))
        result = get_industry_hotspots(
            ths_fetcher=lambda: ths_industry_frame(), eastmoney_fetcher=em, now=NOW
        )
        self.assertTrue(result.available)
        self.assertFalse(result.stale)
        self.assertTrue(result.metrics_complete)
        self.assertEqual(result.source_provider, SOURCE_THS)
        em.assert_not_called()

    def test_industry_ths_failure_falls_back_to_eastmoney(self):
        result = get_industry_hotspots(
            ths_fetcher=Mock(side_effect=ConnectionError("blocked")),
            eastmoney_fetcher=lambda: eastmoney_frame(),
            now=NOW,
        )
        self.assertTrue(result.available)
        self.assertEqual(result.source_provider, SOURCE_EASTMONEY)
        self.assertIn("ConnectionError", result.error)

    def test_industry_ths_empty_falls_back_to_eastmoney(self):
        result = get_industry_hotspots(
            ths_fetcher=lambda: ths_industry_frame().iloc[0:0],
            eastmoney_fetcher=lambda: eastmoney_frame(),
            now=NOW,
        )
        self.assertTrue(result.available)
        self.assertEqual(result.source_provider, SOURCE_EASTMONEY)

    def test_industry_both_fail_without_cache_is_unavailable(self):
        result = get_industry_hotspots(
            ths_fetcher=Mock(side_effect=ConnectionError("ths")),
            eastmoney_fetcher=Mock(side_effect=TimeoutError("em")),
            now=NOW,
        )
        self.assertFalse(result.available)
        self.assertFalse(result.stale)
        self.assertEqual(result.data, ())
        self.assertIsNone(result.fetched_at)

    def test_industry_both_fail_returns_valid_stale(self):
        original = get_industry_hotspots(ths_fetcher=lambda: ths_industry_frame(), now=NOW)
        later = NOW + timedelta(seconds=BOARD_LIST_CACHE_TTL_SECONDS + 1)
        stale = get_industry_hotspots(
            ths_fetcher=Mock(side_effect=ConnectionError("ths")),
            eastmoney_fetcher=Mock(side_effect=ConnectionError("em")),
            now=later,
        )
        self.assertTrue(stale.available)
        self.assertTrue(stale.stale)
        self.assertEqual(stale.data, original.data)

    def test_stale_older_than_24_hours_is_unavailable(self):
        get_industry_hotspots(ths_fetcher=lambda: ths_industry_frame(), now=NOW)
        later = NOW + timedelta(seconds=STALE_MAX_AGE_SECONDS + 1)
        result = get_industry_hotspots(
            ths_fetcher=Mock(side_effect=ConnectionError("ths")),
            eastmoney_fetcher=Mock(side_effect=ConnectionError("em")),
            now=later,
        )
        self.assertFalse(result.available)
        self.assertFalse(result.stale)
        self.assertEqual(result.data, ())

    def test_ths_failure_cooldown_prevents_repeat_call(self):
        ths = Mock(side_effect=ConnectionError("ths"))
        em = Mock(side_effect=ConnectionError("em"))
        get_industry_hotspots(ths_fetcher=ths, eastmoney_fetcher=em, now=NOW)
        get_industry_hotspots(
            ths_fetcher=ths,
            eastmoney_fetcher=em,
            now=NOW + timedelta(seconds=FAILURE_COOLDOWN_SECONDS - 1),
        )
        ths.assert_called_once_with()
        em.assert_called_once_with()

    def test_fallback_remains_available_while_primary_is_in_cooldown(self):
        ths = Mock(side_effect=ConnectionError("ths"))
        em = Mock(return_value=eastmoney_frame())
        first = get_industry_hotspots(ths_fetcher=ths, eastmoney_fetcher=em, now=NOW)
        second = get_industry_hotspots(
            ths_fetcher=ths,
            eastmoney_fetcher=em,
            now=NOW + timedelta(seconds=30),
        )
        self.assertEqual(first.source_provider, SOURCE_EASTMONEY)
        self.assertEqual(second.source_provider, SOURCE_EASTMONEY)
        ths.assert_called_once_with()
        em.assert_called_once_with()

    def test_concept_eastmoney_success(self):
        ths = Mock(side_effect=AssertionError("fallback must not run"))
        result = get_concept_hotspots(
            eastmoney_fetcher=lambda: eastmoney_frame(), ths_fetcher=ths, now=NOW
        )
        self.assertTrue(result.available)
        self.assertTrue(result.metrics_complete)
        self.assertEqual(result.source_provider, SOURCE_EASTMONEY)
        ths.assert_not_called()

    def test_concept_eastmoney_failure_uses_ths_directory(self):
        result = get_concept_hotspots(
            eastmoney_fetcher=Mock(side_effect=ConnectionError("em")),
            ths_fetcher=lambda: ths_concept_frame(),
            now=NOW,
        )
        self.assertTrue(result.available)
        self.assertEqual(result.source_provider, SOURCE_THS)
        self.assertFalse(result.metrics_complete)

    def test_ths_concept_directory_has_no_fake_metrics_or_rank(self):
        board = normalize_ths_concept_directory(ths_concept_frame(), fetched_at=NOW)[0]
        self.assertIsNone(board.date)
        self.assertIsNone(board.rank)
        self.assertIsNone(board.latest_price)
        self.assertIsNone(board.change_amount)
        self.assertIsNone(board.change_percent)
        self.assertIsNone(board.market_cap)
        self.assertIsNone(board.turnover_rate)
        self.assertIsNone(board.up_count)
        self.assertIsNone(board.down_count)
        self.assertIsNone(board.leader_name)
        self.assertIsNone(board.leader_change_percent)

    def test_eastmoney_concept_metrics_are_complete(self):
        result = get_concept_hotspots(eastmoney_fetcher=lambda: eastmoney_frame(), now=NOW)
        self.assertTrue(result.metrics_complete)

    def test_missing_ths_industry_fields_are_none(self):
        frame = pd.DataFrame({"板块": ["测试行业"], "涨跌幅": [1.2]})
        board = normalize_ths_industry(frame, fetched_at=NOW)[0]
        self.assertIsNone(board.board_code)
        self.assertIsNone(board.latest_price)
        self.assertIsNone(board.turnover_rate)
        self.assertIsNone(board.leader_name)

    def test_unicode_board_name_is_preserved(self):
        board = normalize_ths_concept_directory(ths_concept_frame(), fetched_at=NOW)[0]
        self.assertEqual(board.board_name, "阿尔茨海默概念")
        self.assertEqual(board.board_name.encode("utf-8").decode("utf-8"), board.board_name)

    def test_unicode_leader_name_is_preserved(self):
        board = normalize_ths_industry(ths_industry_frame(), fetched_at=NOW)[0]
        self.assertEqual(board.leader_name, "敦煌种业")

    def test_cache_ttl_is_twenty_minutes(self):
        fetcher = Mock(return_value=ths_industry_frame())
        get_industry_hotspots(ths_fetcher=fetcher, now=NOW)
        get_industry_hotspots(
            ths_fetcher=fetcher,
            now=NOW + timedelta(seconds=BOARD_LIST_CACHE_TTL_SECONDS),
        )
        fetcher.assert_called_once_with()

    def test_cache_refreshes_after_ttl(self):
        fetcher = Mock(return_value=ths_industry_frame())
        get_industry_hotspots(ths_fetcher=fetcher, now=NOW)
        get_industry_hotspots(
            ths_fetcher=fetcher,
            now=NOW + timedelta(seconds=BOARD_LIST_CACHE_TTL_SECONDS + 1),
        )
        self.assertEqual(fetcher.call_count, 2)

    def test_industry_and_concept_caches_are_isolated(self):
        industry = Mock(return_value=ths_industry_frame())
        concept = Mock(return_value=eastmoney_frame())
        get_industry_hotspots(ths_fetcher=industry, now=NOW)
        get_concept_hotspots(eastmoney_fetcher=concept, now=NOW)
        industry.assert_called_once_with()
        concept.assert_called_once_with()

    def test_source_provider_is_set_on_every_record(self):
        result = get_industry_hotspots(ths_fetcher=lambda: ths_industry_frame(), now=NOW)
        self.assertTrue(all(board.source_provider == SOURCE_THS for board in result.data))

    def test_fetched_at_is_preserved_on_stale(self):
        initial = get_industry_hotspots(ths_fetcher=lambda: ths_industry_frame(), now=NOW)
        later = NOW + timedelta(hours=2)
        stale = get_industry_hotspots(
            ths_fetcher=Mock(side_effect=ConnectionError("ths")),
            eastmoney_fetcher=Mock(side_effect=ConnectionError("em")),
            now=later,
        )
        self.assertEqual(stale.fetched_at, initial.fetched_at)
        self.assertTrue(all(board.fetched_at == NOW for board in stale.data))

    def test_missing_fields_do_not_use_current_time_as_market_data(self):
        board = normalize_ths_concept_directory(ths_concept_frame(), fetched_at=NOW)[0]
        self.assertEqual(board.fetched_at, NOW)
        self.assertIsNone(board.date)
        self.assertIsNone(board.latest_price)

    def test_none_change_percent_sorts_safely_to_the_end(self):
        boards = normalize_ths_industry(ths_industry_frame(), fetched_at=NOW)
        self.assertEqual(boards[-1].board_name, "房地产")
        self.assertIsNone(boards[-1].change_percent)

    def test_empty_dataframe_is_unavailable_after_both_providers(self):
        result = get_concept_hotspots(
            eastmoney_fetcher=lambda: eastmoney_frame().iloc[0:0],
            ths_fetcher=lambda: ths_concept_frame().iloc[0:0],
            now=NOW,
        )
        self.assertFalse(result.available)
        self.assertIn("ValueError", result.error)

    def test_key_error_falls_back_safely(self):
        bad = pd.DataFrame({"wrong": ["value"]})
        result = get_industry_hotspots(
            ths_fetcher=lambda: bad,
            eastmoney_fetcher=lambda: eastmoney_frame(),
            now=NOW,
        )
        self.assertTrue(result.available)
        self.assertEqual(result.source_provider, SOURCE_EASTMONEY)
        self.assertIn("KeyError", result.error)

    def test_connection_error_is_isolated(self):
        result = get_concept_hotspots(
            eastmoney_fetcher=Mock(side_effect=ConnectionError("blocked")),
            ths_fetcher=lambda: ths_concept_frame(),
            now=NOW,
        )
        self.assertTrue(result.available)
        self.assertIn("ConnectionError", result.error)

    def test_parsing_error_is_isolated(self):
        result = get_concept_hotspots(
            eastmoney_fetcher=lambda: "not a DataFrame",
            ths_fetcher=lambda: ths_concept_frame(),
            now=NOW,
        )
        self.assertTrue(result.available)
        self.assertIn("ValueError", result.error)

    def test_previous_beijing_date_cache_is_stale_even_within_ttl(self):
        fetched = datetime(2026, 8, 27, 15, 59, tzinfo=timezone.utc)
        current = fetched + timedelta(minutes=2)
        get_industry_hotspots(ths_fetcher=lambda: ths_industry_frame(), now=fetched)
        result = get_industry_hotspots(
            ths_fetcher=Mock(side_effect=ConnectionError("ths")),
            eastmoney_fetcher=Mock(side_effect=ConnectionError("em")),
            now=current,
        )
        self.assertTrue(result.available)
        self.assertTrue(result.stale)
        self.assertEqual(result.fetched_at, fetched)

    def test_cache_status_keeps_provider_and_board_type_keys_separate(self):
        get_industry_hotspots(ths_fetcher=lambda: ths_industry_frame(), now=NOW)
        get_concept_hotspots(eastmoney_fetcher=lambda: eastmoney_frame(), now=NOW)
        status = get_hotspot_cache_status(now=NOW)
        self.assertIn(f"{BOARD_TYPE_INDUSTRY}:{SOURCE_THS}", status["cache"])
        self.assertIn(f"{BOARD_TYPE_CONCEPT}:{SOURCE_EASTMONEY}", status["cache"])


if __name__ == "__main__":
    unittest.main()
