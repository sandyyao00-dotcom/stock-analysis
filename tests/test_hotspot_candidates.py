"""Deterministic constituent-cache and hotspot-candidate tests."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

import pandas as pd

from stock_analysis.hotspot_candidates import (
    CONSTITUENTS_CACHE_TTL_SECONDS,
    DEFAULT_CANDIDATES_PER_BOARD,
    FAILURE_COOLDOWN_SECONDS,
    STALE_MAX_AGE_SECONDS,
    _reset_candidate_cache_for_tests,
    build_hotspot_candidate_pool,
    get_board_constituents,
    normalize_eastmoney_constituents,
)
from stock_analysis.market_hotspots import (
    BOARD_TYPE_CONCEPT,
    BOARD_TYPE_INDUSTRY,
    SOURCE_EASTMONEY,
    SOURCE_THS,
    HotspotBoard,
    HotspotResult,
)


NOW = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


def board(
    *,
    board_type=BOARD_TYPE_CONCEPT,
    board_code="BK1001",
    board_name="人工智能",
    source_provider=SOURCE_EASTMONEY,
    rank=1,
):
    return HotspotBoard(
        date=NOW.date(),
        fetched_at=NOW,
        board_type=board_type,
        board_code=board_code,
        board_name=board_name,
        rank=rank,
        latest_price=100.0,
        change_amount=2.0,
        change_percent=3.0,
        market_cap=1000.0,
        turnover_rate=2.5,
        up_count=20,
        down_count=5,
        leader_name="龙头股份",
        leader_change_percent=10.0,
        source_provider=source_provider,
    )


def hotspot_result(*boards, metrics_complete=True, available=True, error=None):
    return HotspotResult(
        available=available,
        stale=False,
        metrics_complete=metrics_complete,
        source_provider=boards[0].source_provider if boards else None,
        fetched_at=NOW if boards else None,
        data=tuple(boards),
        error=error,
    )


def constituent_frame(rows=None):
    return pd.DataFrame(
        rows
        or [
            [1, "600519", "贵州茅台", 1300.0, 5.0, 60.0, 1000.0, 100.0, 4.0, 3.0, 20.0, 8.0, 2000.0, 1800.0],
            [2, "000001", "平安银行", 12.0, 5.0, 0.5, 2000.0, 200.0, 3.0, 4.0, 6.0, 0.8, 1000.0, 900.0],
            [3, "300750", "宁德时代", 250.0, 3.0, 7.0, 1500.0, None, 2.0, None, 25.0, 5.0, 1500.0, 1200.0],
            [4, "688001", "华兴源创", 30.0, 2.0, 0.6, 800.0, 50.0, 5.0, 2.0, 30.0, 3.0, 500.0, 400.0],
            [5, "000002", "万科A", 8.0, 1.0, 0.1, 600.0, 10.0, 1.0, 1.0, 10.0, 0.7, 600.0, 500.0],
            [6, "600000", "浦发银行", 9.0, -1.0, -0.1, 500.0, 20.0, 2.0, 1.5, 5.0, 0.5, 700.0, 650.0],
        ],
        columns=(
            "序号",
            "代码",
            "名称",
            "最新价",
            "涨跌幅",
            "涨跌额",
            "成交量",
            "成交额",
            "振幅",
            "换手率",
            "市盈率-动态",
            "市净率",
            "总市值",
            "流通市值",
        ),
    )


class HotspotCandidateTests(unittest.TestCase):
    def setUp(self):
        _reset_candidate_cache_for_tests()

    def tearDown(self):
        _reset_candidate_cache_for_tests()

    def test_concept_constituents_success(self):
        result = get_board_constituents(
            board(), fetcher=lambda identifier: constituent_frame(), now=NOW
        )
        self.assertTrue(result.available)
        self.assertFalse(result.stale)
        self.assertEqual(result.source_provider, SOURCE_EASTMONEY)
        self.assertEqual(result.candidate_count, DEFAULT_CANDIDATES_PER_BOARD)

    def test_concept_exception_is_unavailable(self):
        result = get_board_constituents(
            board(), fetcher=Mock(side_effect=ConnectionError("blocked")), now=NOW
        )
        self.assertFalse(result.available)
        self.assertIn("ConnectionError", result.error)

    def test_industry_constituents_success_with_mock(self):
        industry = board(board_type=BOARD_TYPE_INDUSTRY, board_code="BK2001", board_name="银行")
        fetcher = Mock(return_value=constituent_frame())
        result = get_board_constituents(industry, fetcher=fetcher, now=NOW)
        self.assertTrue(result.available)
        fetcher.assert_called_once_with("BK2001")

    def test_industry_exception_does_not_kill_concept_pool(self):
        concept = board()
        industry = board(board_type=BOARD_TYPE_INDUSTRY, board_code="BK2001", board_name="银行")
        pool = build_hotspot_candidate_pool(
            (hotspot_result(concept), hotspot_result(industry)),
            concept_fetcher=lambda _: constituent_frame(),
            industry_fetcher=Mock(side_effect=ConnectionError("industry unavailable")),
            now=NOW,
        )
        self.assertTrue(pool.available)
        self.assertTrue(pool.candidates)
        self.assertEqual(len(pool.board_results), 2)
        self.assertFalse(pool.board_results[1].available)

    def test_empty_dataframe_is_unavailable(self):
        result = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame().iloc[0:0], now=NOW
        )
        self.assertFalse(result.available)
        self.assertIn("ValueError", result.error)

    def test_missing_symbol_is_filtered_from_candidates(self):
        rows = [[1, None, "无代码", 10, 3, 1, 10, 20, 1, 1, 2, 1, None, None]]
        result = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(rows), now=NOW
        )
        self.assertTrue(result.available)
        self.assertEqual(result.candidates, ())

    def test_missing_name_is_filtered_from_candidates(self):
        rows = [[1, "600519", None, 10, 3, 1, 10, 20, 1, 1, 2, 1, None, None]]
        result = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(rows), now=NOW
        )
        self.assertEqual(result.candidates, ())

    def test_missing_change_percent_is_filtered_from_candidates(self):
        rows = [[1, "600519", "贵州茅台", 10, None, 1, 10, 20, 1, 1, 2, 1, None, None]]
        result = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(rows), now=NOW
        )
        self.assertEqual(result.candidates, ())

    def test_missing_amount_is_safe_and_has_no_activity_reason(self):
        rows = [[1, "300750", "宁德时代", 10, 3, 1, 10, None, 1, 1, 2, 1, None, None]]
        candidate = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(rows), now=NOW
        ).candidates[0]
        self.assertIsNone(candidate.amount)
        self.assertNotIn("成交额在板块内相对活跃", candidate.selection_reasons)

    def test_missing_turnover_is_safe(self):
        rows = [[1, "300750", "宁德时代", 10, 3, 1, 10, 20, 1, None, 2, 1, None, None]]
        candidate = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(rows), now=NOW
        ).candidates[0]
        self.assertIsNone(candidate.turnover_rate)

    def test_unicode_stock_name(self):
        stocks = normalize_eastmoney_constituents(constituent_frame(), board=board(), fetched_at=NOW)
        self.assertEqual(stocks[0].name, "贵州茅台")
        self.assertEqual(stocks[0].name.encode("utf-8").decode("utf-8"), stocks[0].name)

    def test_code_normalization_reuses_existing_rules(self):
        stocks = normalize_eastmoney_constituents(constituent_frame(), board=board(), fetched_at=NOW)
        self.assertEqual(stocks[0].symbol, "600519.SS")
        self.assertEqual(stocks[1].symbol, "000001.SZ")
        self.assertEqual(stocks[3].symbol, "688001.SS")

    def test_cache_hit_does_not_repeat_request(self):
        fetcher = Mock(return_value=constituent_frame())
        get_board_constituents(board(), fetcher=fetcher, now=NOW)
        get_board_constituents(board(), fetcher=fetcher, now=NOW + timedelta(minutes=1))
        fetcher.assert_called_once_with("BK1001")

    def test_cache_ttl_is_45_minutes(self):
        fetcher = Mock(return_value=constituent_frame())
        get_board_constituents(board(), fetcher=fetcher, now=NOW)
        get_board_constituents(
            board(), fetcher=fetcher, now=NOW + timedelta(seconds=CONSTITUENTS_CACHE_TTL_SECONDS)
        )
        fetcher.assert_called_once_with("BK1001")

    def test_cache_refreshes_after_ttl(self):
        fetcher = Mock(return_value=constituent_frame())
        get_board_constituents(board(), fetcher=fetcher, now=NOW)
        get_board_constituents(
            board(),
            fetcher=fetcher,
            now=NOW + timedelta(seconds=CONSTITUENTS_CACHE_TTL_SECONDS + 1),
        )
        self.assertEqual(fetcher.call_count, 2)

    def test_cache_isolated_per_board(self):
        fetcher = Mock(return_value=constituent_frame())
        get_board_constituents(board(board_code="BK1001"), fetcher=fetcher, now=NOW)
        get_board_constituents(board(board_code="BK1002"), fetcher=fetcher, now=NOW)
        self.assertEqual(fetcher.call_count, 2)

    def test_concept_and_industry_cache_isolation(self):
        fetcher = Mock(return_value=constituent_frame())
        get_board_constituents(board(board_code="BK1001"), fetcher=fetcher, now=NOW)
        get_board_constituents(
            board(board_type=BOARD_TYPE_INDUSTRY, board_code="BK1001"),
            fetcher=fetcher,
            now=NOW,
        )
        self.assertEqual(fetcher.call_count, 2)

    def test_failure_cooldown_is_per_board(self):
        fetcher = Mock(side_effect=ConnectionError("blocked"))
        get_board_constituents(board(board_code="BK1001"), fetcher=fetcher, now=NOW)
        get_board_constituents(
            board(board_code="BK1001"),
            fetcher=fetcher,
            now=NOW + timedelta(seconds=FAILURE_COOLDOWN_SECONDS - 1),
        )
        get_board_constituents(
            board(board_code="BK1002"),
            fetcher=fetcher,
            now=NOW + timedelta(seconds=30),
        )
        self.assertEqual(fetcher.call_count, 2)

    def test_valid_stale_is_returned_after_failure(self):
        initial = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(), now=NOW
        )
        stale = get_board_constituents(
            board(),
            fetcher=Mock(side_effect=ConnectionError("blocked")),
            now=NOW + timedelta(seconds=CONSTITUENTS_CACHE_TTL_SECONDS + 1),
        )
        self.assertTrue(stale.available)
        self.assertTrue(stale.stale)
        self.assertEqual(stale.fetched_at, initial.fetched_at)

    def test_stale_over_24_hours_is_unavailable(self):
        get_board_constituents(board(), fetcher=lambda _: constituent_frame(), now=NOW)
        result = get_board_constituents(
            board(),
            fetcher=Mock(side_effect=ConnectionError("blocked")),
            now=NOW + timedelta(seconds=STALE_MAX_AGE_SECONDS + 1),
        )
        self.assertFalse(result.available)
        self.assertFalse(result.stale)

    def test_selection_top_n(self):
        result = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(), max_candidates=3, now=NOW
        )
        self.assertEqual(len(result.candidates), 3)

    def test_fewer_than_n_returns_only_available_stocks(self):
        result = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame().head(2), now=NOW
        )
        self.assertEqual(len(result.candidates), 2)

    def test_deterministic_ordering(self):
        first = get_board_constituents(board(), fetcher=lambda _: constituent_frame(), now=NOW)
        _reset_candidate_cache_for_tests()
        second = get_board_constituents(board(), fetcher=lambda _: constituent_frame(), now=NOW)
        self.assertEqual(
            [candidate.symbol for candidate in first.candidates],
            [candidate.symbol for candidate in second.candidates],
        )

    def test_change_percent_is_primary_sort(self):
        result = get_board_constituents(board(), fetcher=lambda _: constituent_frame(), now=NOW)
        self.assertEqual(result.candidates[0].change_percent, 5.0)
        self.assertEqual(result.candidates[2].change_percent, 3.0)

    def test_amount_is_secondary_sort(self):
        result = get_board_constituents(board(), fetcher=lambda _: constituent_frame(), now=NOW)
        self.assertEqual(result.candidates[0].symbol, "000001.SZ")
        self.assertEqual(result.candidates[1].symbol, "600519.SS")

    def test_duplicate_symbol_across_boards_is_unique(self):
        pool = build_hotspot_candidate_pool(
            (hotspot_result(board(board_code="BK1")), hotspot_result(board(board_code="BK2"))),
            concept_fetcher=lambda _: constituent_frame().head(1),
            now=NOW,
        )
        self.assertEqual(len(pool.candidates), 1)

    def test_matched_boards_are_preserved(self):
        pool = build_hotspot_candidate_pool(
            (
                hotspot_result(board(board_code="BK1", board_name="概念一")),
                hotspot_result(board(board_code="BK2", board_name="概念二")),
            ),
            concept_fetcher=lambda _: constituent_frame().head(1),
            now=NOW,
        )
        self.assertEqual(
            [matched.board_name for matched in pool.candidates[0].matched_boards],
            ["概念一", "概念二"],
        )

    def test_matched_board_count(self):
        pool = build_hotspot_candidate_pool(
            (hotspot_result(board(board_code="BK1")), hotspot_result(board(board_code="BK2"))),
            concept_fetcher=lambda _: constituent_frame().head(1),
            now=NOW,
        )
        self.assertEqual(pool.candidates[0].matched_board_count, 2)

    def test_selection_reasons_are_truthful(self):
        candidates = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(), now=NOW
        ).candidates
        active = next(candidate for candidate in candidates if candidate.symbol == "000001.SZ")
        missing = next(candidate for candidate in candidates if candidate.symbol == "300750.SZ")
        self.assertIn("成交额在板块内相对活跃", active.selection_reasons)
        self.assertNotIn("成交额在板块内相对活跃", missing.selection_reasons)

    def test_incomplete_board_metrics_are_skipped(self):
        fetcher = Mock(side_effect=AssertionError("must not fetch"))
        pool = build_hotspot_candidate_pool(
            (hotspot_result(board(source_provider=SOURCE_THS), metrics_complete=False),),
            concept_fetcher=fetcher,
            now=NOW,
        )
        self.assertFalse(pool.available)
        self.assertEqual(pool.board_results[0].skip_reason, "incomplete_board_metrics")
        fetcher.assert_not_called()

    def test_one_board_failure_does_not_kill_other_board(self):
        fetcher = Mock(side_effect=[ConnectionError("first"), constituent_frame()])
        pool = build_hotspot_candidate_pool(
            (hotspot_result(board(board_code="BK1"), board(board_code="BK2")),),
            concept_fetcher=fetcher,
            now=NOW,
        )
        self.assertTrue(pool.available)
        self.assertEqual([result.available for result in pool.board_results], [False, True])

    def test_no_fake_optional_values(self):
        rows = [[1, "600519", "贵州茅台", None, 3, None, None, None, None, None, None, None, None, None]]
        candidate = get_board_constituents(
            board(), fetcher=lambda _: constituent_frame(rows), now=NOW
        ).candidates[0]
        self.assertIsNone(candidate.latest_price)
        self.assertIsNone(candidate.change_amount)
        self.assertIsNone(candidate.volume)
        self.assertIsNone(candidate.amount)
        self.assertIsNone(candidate.amplitude)
        self.assertIsNone(candidate.turnover_rate)
        self.assertIsNone(candidate.market_cap)

    def test_source_provider_preserved(self):
        result = get_board_constituents(board(), fetcher=lambda _: constituent_frame(), now=NOW)
        self.assertTrue(
            all(candidate.source_provider == SOURCE_EASTMONEY for candidate in result.candidates)
        )

    def test_fetched_at_preserved_on_stale_candidates(self):
        get_board_constituents(board(), fetcher=lambda _: constituent_frame(), now=NOW)
        stale = get_board_constituents(
            board(),
            fetcher=Mock(side_effect=ConnectionError("blocked")),
            now=NOW + timedelta(hours=2),
        )
        self.assertTrue(all(candidate.fetched_at == NOW for candidate in stale.candidates))

    def test_key_error_handling(self):
        result = get_board_constituents(
            board(), fetcher=lambda _: pd.DataFrame({"名称": ["无代码"]}), now=NOW
        )
        self.assertFalse(result.available)
        self.assertIn("KeyError", result.error)

    def test_parsing_provider_exception(self):
        result = get_board_constituents(
            board(), fetcher=lambda _: "not a DataFrame", now=NOW
        )
        self.assertFalse(result.available)
        self.assertIn("ValueError", result.error)

    def test_ths_board_code_is_not_passed_to_eastmoney(self):
        ths_board = board(
            board_code="309120", board_name="AI手机", source_provider=SOURCE_THS
        )
        fetcher = Mock(return_value=constituent_frame())
        get_board_constituents(ths_board, fetcher=fetcher, now=NOW)
        fetcher.assert_called_once_with("AI手机")

    def test_missing_board_identifier_is_skipped(self):
        missing = board(board_code=None, board_name=None, source_provider=SOURCE_THS)
        fetcher = Mock(side_effect=AssertionError("must not fetch"))
        result = get_board_constituents(missing, fetcher=fetcher, now=NOW)
        self.assertEqual(result.skip_reason, "missing_board_identifier")
        fetcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
