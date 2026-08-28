"""Pure-data tests for deterministic hotspot scoring and ranking."""

from datetime import datetime, timezone
import math
import unittest

from stock_analysis.hotspot_scoring import (
    BREADTH_WEIGHT,
    CHANGE_PERCENT_WEIGHT,
    LEADER_STRENGTH_WEIGHT,
    MINIMUM_SCORE_COVERAGE,
    TURNOVER_RATE_WEIGHT,
    calculate_breadth_ratio,
    score_hotspots,
    select_top_hotspots,
)
from stock_analysis.market_hotspots import (
    BOARD_TYPE_CONCEPT,
    BOARD_TYPE_INDUSTRY,
    SOURCE_EASTMONEY,
    HotspotBoard,
    HotspotResult,
)


NOW = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


def board(
    name,
    *,
    board_type=BOARD_TYPE_INDUSTRY,
    code=None,
    change=1.0,
    turnover=1.0,
    up=5,
    down=5,
    leader=1.0,
    source=SOURCE_EASTMONEY,
):
    return HotspotBoard(
        date=NOW.date(),
        fetched_at=NOW,
        board_type=board_type,
        board_code=code or f"BK-{name}",
        board_name=name,
        rank=None,
        latest_price=100.0,
        change_amount=2.0,
        change_percent=change,
        market_cap=1000.0,
        turnover_rate=turnover,
        up_count=up,
        down_count=down,
        leader_name="领涨股",
        leader_change_percent=leader,
        source_provider=source,
    )


def result(*boards, complete=True, stale=False, available=True):
    return HotspotResult(
        available=available,
        stale=stale,
        metrics_complete=complete,
        source_provider=boards[0].source_provider if boards else None,
        fetched_at=NOW if boards else None,
        data=tuple(boards),
    )


def three_boards(board_type=BOARD_TYPE_INDUSTRY):
    return (
        board("强势", board_type=board_type, change=3, turnover=3, up=8, down=2, leader=10),
        board("中间", board_type=board_type, change=2, turnover=2, up=5, down=5, leader=5),
        board("弱势", board_type=board_type, change=1, turnover=1, up=2, down=8, leader=0),
    )


class HotspotScoringTests(unittest.TestCase):
    def test_valid_complete_boards_are_scored(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertTrue(all(item.score_available for item in scored))
        self.assertEqual([item.hotspot_rank for item in scored], [1, 2, 3])

    def test_change_percentile(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertEqual([item.component_scores["change_percent"] for item in scored], [100, 50, 0])

    def test_breadth_calculation(self):
        self.assertAlmostEqual(calculate_breadth_ratio(20, 2), 20 / 22)
        self.assertLess(calculate_breadth_ratio(2, 20), calculate_breadth_ratio(20, 2))

    def test_breadth_percentile(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertEqual([item.component_scores["breadth"] for item in scored], [100, 50, 0])

    def test_turnover_percentile(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertEqual([item.component_scores["turnover_rate"] for item in scored], [100, 50, 0])

    def test_leader_percentile(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertEqual([item.component_scores["leader_strength"] for item in scored], [100, 50, 0])

    def test_weighted_score(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertEqual([item.hotspot_score for item in scored], [100, 50, 0])

    def test_score_is_bounded_zero_to_one_hundred(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertTrue(all(0 <= item.hotspot_score <= 100 for item in scored))

    def test_full_coverage(self):
        scored = score_hotspots((result(*three_boards()),))[0]
        self.assertEqual(scored.score_coverage, 1.0)

    def test_missing_one_metric_renormalizes(self):
        boards = three_boards()
        missing = board("缺换手", change=4, turnover=None, up=9, down=1, leader=11)
        scored = score_hotspots((result(*boards, missing),))[-1]
        self.assertEqual(scored.score_coverage, 0.8)
        expected = (
            scored.component_scores["change_percent"] * CHANGE_PERCENT_WEIGHT
            + scored.component_scores["breadth"] * BREADTH_WEIGHT
            + scored.component_scores["leader_strength"] * LEADER_STRENGTH_WEIGHT
        ) / 0.8
        self.assertAlmostEqual(scored.hotspot_score, round(expected, 2))

    def test_missing_metrics_reduce_coverage(self):
        item = score_hotspots(
            (result(board("部分", change=2, turnover=None, up=8, down=2, leader=None)),)
        )[0]
        self.assertEqual(item.score_coverage, 0.6)

    def test_coverage_below_threshold_is_unavailable(self):
        item = score_hotspots(
            (result(board("不足", change=2, turnover=None, up=None, down=None, leader=None)),)
        )[0]
        self.assertLess(item.score_coverage, MINIMUM_SCORE_COVERAGE)
        self.assertFalse(item.score_available)
        self.assertIsNone(item.hotspot_score)

    def test_metrics_incomplete_is_unavailable(self):
        item = score_hotspots((result(board("目录"), complete=False),))[0]
        self.assertFalse(item.score_available)
        self.assertEqual(item.skip_reason, "incomplete_board_metrics")
        self.assertTrue(all(value is None for value in item.component_scores.values()))

    def test_incomplete_board_does_not_change_percentiles(self):
        complete = three_boards()
        baseline = score_hotspots((result(*complete),))
        with_directory = score_hotspots(
            (result(*complete), result(board("目录极值", change=999), complete=False))
        )
        self.assertEqual(
            [item.hotspot_score for item in baseline],
            [item.hotspot_score for item in with_directory[:3]],
        )

    def test_industry_and_concept_are_ranked_separately(self):
        industry = board("行业", board_type=BOARD_TYPE_INDUSTRY, change=-5)
        concept = board("概念", board_type=BOARD_TYPE_CONCEPT, change=10)
        scored = score_hotspots((result(industry), result(concept)))
        self.assertEqual([item.hotspot_rank for item in scored], [1, 1])

    def test_deterministic_ranking(self):
        data = result(*three_boards())
        first = score_hotspots((data,))
        second = score_hotspots((data,))
        self.assertEqual(first, second)

    def test_score_tie_breaker_prefers_higher_change(self):
        first = board("A", change=3, turnover=1, up=1, down=9, leader=5)
        second = board("B", change=1, turnover=2, up=9, down=1, leader=5)
        third = board("C", change=2, turnover=3, up=5, down=5, leader=5)
        scored = score_hotspots((result(first, second, third),))
        a = next(item for item in scored if item.board_name == "A")
        b = next(item for item in scored if item.board_name == "B")
        self.assertEqual(a.hotspot_score, b.hotspot_score)
        self.assertLess(a.hotspot_rank, b.hotspot_rank)

    def test_final_tie_breaker_uses_board_name(self):
        scored = score_hotspots((result(board("乙"), board("甲")),))
        ranks = {item.board_name: item.hotspot_rank for item in scored}
        self.assertEqual(ranks["乙"], 1)
        self.assertEqual(ranks["甲"], 2)

    def test_none_is_safe(self):
        item = score_hotspots(
            (result(board("空值", change=None, turnover=None, up=None, down=None, leader=None)),)
        )[0]
        self.assertFalse(item.score_available)

    def test_nan_is_safe(self):
        item = score_hotspots((result(board("NaN", change=float("nan"))),))[0]
        self.assertIsNone(item.change_percent)

    def test_positive_inf_is_safe(self):
        item = score_hotspots((result(board("Inf", turnover=float("inf"))),))[0]
        self.assertIsNone(item.turnover_rate)

    def test_negative_inf_is_safe(self):
        item = score_hotspots((result(board("-Inf", leader=float("-inf"))),))[0]
        self.assertIsNone(item.leader_change_percent)

    def test_percentage_strings_are_parsed_without_rescaling(self):
        item = score_hotspots((result(board("字符串", change="3.25%", turnover="2.5%")),))[0]
        self.assertEqual(item.change_percent, 3.25)
        self.assertEqual(item.turnover_rate, 2.5)

    def test_empty_string_is_missing(self):
        item = score_hotspots((result(board("空字符串", change="")),))[0]
        self.assertIsNone(item.change_percent)

    def test_zero_total_breadth_is_missing(self):
        self.assertIsNone(calculate_breadth_ratio(0, 0))

    def test_missing_breadth_is_none(self):
        self.assertIsNone(calculate_breadth_ratio(None, 3))
        self.assertIsNone(calculate_breadth_ratio(3, None))

    def test_stale_is_preserved(self):
        item = score_hotspots((result(board("缓存"), stale=True),))[0]
        self.assertTrue(item.stale)

    def test_source_provider_is_preserved(self):
        item = score_hotspots((result(board("来源")),))[0]
        self.assertEqual(item.source_provider, SOURCE_EASTMONEY)

    def test_fetched_at_is_preserved(self):
        item = score_hotspots((result(board("时间")),))[0]
        self.assertEqual(item.fetched_at, NOW)

    def test_component_scores_include_all_components(self):
        item = score_hotspots((result(board("分项")),))[0]
        self.assertEqual(
            set(item.component_scores),
            {"change_percent", "breadth", "turnover_rate", "leader_strength"},
        )

    def test_scoring_reasons_only_follow_high_real_components(self):
        scored = score_hotspots((result(*three_boards()),))
        self.assertIn("板块涨幅处于同类板块相对前列", scored[0].scoring_reasons)
        self.assertEqual(scored[-1].scoring_reasons, ())

    def test_unavailable_score_has_no_rank(self):
        item = score_hotspots(
            (result(board("不足", change=None, turnover=None, up=None, down=None, leader=None)),)
        )[0]
        self.assertIsNone(item.hotspot_rank)

    def test_top_n_per_board_type(self):
        scored = score_hotspots(
            (result(*three_boards()), result(*three_boards(BOARD_TYPE_CONCEPT)))
        )
        top = select_top_hotspots(scored, industry_limit=2, concept_limit=1)
        self.assertEqual(len(top[BOARD_TYPE_INDUSTRY]), 2)
        self.assertEqual(len(top[BOARD_TYPE_CONCEPT]), 1)

    def test_fewer_than_n_returns_available_count(self):
        scored = score_hotspots((result(board("唯一")),))
        top = select_top_hotspots(scored)
        self.assertEqual(len(top[BOARD_TYPE_INDUSTRY]), 1)

    def test_unicode_board_name(self):
        item = score_hotspots((result(board("种植业与林业")),))[0]
        self.assertEqual(item.board_name, "种植业与林业")

    def test_no_buy_or_sell_language(self):
        scored = score_hotspots((result(*three_boards()),))
        text = " ".join(reason for item in scored for reason in item.scoring_reasons)
        for forbidden in ("买入", "卖出", "目标价", "上涨概率", "投资评级"):
            self.assertNotIn(forbidden, text)

    def test_invalid_values_do_not_create_fake_component_scores(self):
        item = score_hotspots(
            (result(board("非法", change="bad", turnover="", up="x", down=1, leader=None)),)
        )[0]
        self.assertTrue(all(value is None for value in item.component_scores.values()))
        self.assertEqual(item.score_coverage, 0)
        self.assertIsNone(item.hotspot_score)


if __name__ == "__main__":
    unittest.main()
