"""Pure-data and session tests for the read-only hotspot UI layer."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from stock_analysis.hotspot_candidates import HotspotCandidate, MatchedBoard
from stock_analysis.hotspot_pipeline import (
    HotspotPipelineResult,
    MatchedHotspot,
    PipelineCandidate,
)
from stock_analysis.hotspot_ui import (
    HOTSPOT_REFRESH_ERROR_KEY,
    HOTSPOT_SESSION_KEY,
    prepare_hotspot_display,
    sanitize_hotspot_error,
    update_hotspot_session,
)
from stock_analysis.market_hotspots import BOARD_TYPE_CONCEPT, BOARD_TYPE_INDUSTRY


NOW = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


def scored(name="机器人", board_type=BOARD_TYPE_CONCEPT, stale=False):
    return SimpleNamespace(
        hotspot_rank=1,
        board_name=name,
        hotspot_score=88.6,
        scoring_reasons=("板块涨幅处于同类板块相对前列", "上涨家数占比较高"),
        stale=stale,
        board_type=board_type,
        board_code="BK1001",
    )


def candidate(name="股票A"):
    matched_board = MatchedBoard(BOARD_TYPE_CONCEPT, "机器人", "BK1001", 1)
    raw = HotspotCandidate(
        symbol="600001.SS", name=name, latest_price=10.0, change_percent=3.0,
        change_amount=0.3, volume=100.0, amount=200.0, amplitude=2.0,
        turnover_rate=1.0, pe=20.0, pb=2.0, market_cap=1000.0,
        float_market_cap=900.0, source_provider="eastmoney", fetched_at=NOW,
        relative_rank=1,
        selection_reasons=("板块内涨幅排名靠前", "成交额在板块内相对活跃"),
        matched_boards=(matched_board,), matched_board_count=1,
    )
    matched = MatchedHotspot(BOARD_TYPE_CONCEPT, "机器人", "BK1001", 88.6, 1, False)
    return PipelineCandidate(raw, (matched,))


def pipeline_result(*, available=True, degraded=False, candidates=None, errors=()):
    return HotspotPipelineResult(
        available=available,
        degraded=degraded,
        hotspots={
            BOARD_TYPE_INDUSTRY: (scored("电子", BOARD_TYPE_INDUSTRY),),
            BOARD_TYPE_CONCEPT: (scored(),),
        },
        candidates=(candidate(),) if candidates is None else candidates,
        board_results=(), provider_results=(), errors=errors,
    )


class HotspotUITests(unittest.TestCase):
    def test_industry_and_concept_groups(self):
        view = prepare_hotspot_display(pipeline_result())
        self.assertEqual(view.industry[0].name, "电子")
        self.assertEqual(view.concept[0].name, "机器人")

    def test_rank_score_and_reasons_are_preserved(self):
        item = prepare_hotspot_display(pipeline_result()).concept[0]
        self.assertEqual(item.rank, "1")
        self.assertEqual(item.score, "88.60")
        self.assertEqual(item.reasons, scored().scoring_reasons)

    def test_stale_is_explicit(self):
        result = pipeline_result()
        result.hotspots[BOARD_TYPE_CONCEPT] = (scored(stale=True),)
        self.assertTrue(prepare_hotspot_display(result).concept[0].stale)

    def test_degraded_and_available_flags(self):
        view = prepare_hotspot_display(pipeline_result(degraded=True))
        self.assertTrue(view.available)
        self.assertTrue(view.degraded)

    def test_unavailable_is_preserved(self):
        self.assertFalse(prepare_hotspot_display(pipeline_result(available=False)).available)

    def test_hotspots_with_empty_candidates(self):
        view = prepare_hotspot_display(pipeline_result(candidates=()))
        self.assertTrue(view.available)
        self.assertEqual(view.candidates, ())

    def test_candidate_fields_and_selection_reasons(self):
        item = prepare_hotspot_display(pipeline_result()).candidates[0]
        self.assertEqual((item.symbol, item.name), ("600001.SS", "股票A"))
        self.assertEqual(item.selection_reasons, candidate().candidate.selection_reasons)

    def test_matched_boards_and_count(self):
        item = prepare_hotspot_display(pipeline_result()).candidates[0]
        self.assertEqual(item.matched_boards, ("机器人",))
        self.assertEqual(item.matched_board_count, 1)

    def test_matched_hotspots_are_real_metadata(self):
        item = prepare_hotspot_display(pipeline_result()).candidates[0].matched_hotspots[0]
        self.assertEqual((item.name, item.rank, item.score), ("机器人", "1", "88.60"))

    def test_missing_fields_are_safe(self):
        malformed = SimpleNamespace(available=True, degraded=False, hotspots=None, candidates=(SimpleNamespace(candidate=SimpleNamespace()),), errors=())
        view = prepare_hotspot_display(malformed)
        self.assertEqual(view.industry, ())
        self.assertEqual(view.candidates[0].symbol, "—")
        self.assertIsNone(view.candidates[0].matched_board_count)

    def test_non_finite_score_is_not_displayed(self):
        result = pipeline_result()
        result.hotspots[BOARD_TYPE_CONCEPT] = (
            SimpleNamespace(
                hotspot_rank=1, board_name="机器人", hotspot_score=float("nan"),
                scoring_reasons=(), stale=False,
            ),
        )
        self.assertEqual(prepare_hotspot_display(result).concept[0].score, "—")

    def test_unicode_is_preserved(self):
        self.assertEqual(prepare_hotspot_display(pipeline_result()).candidates[0].name, "股票A")

    def test_error_summary_removes_traceback_and_paths(self):
        error = "failed C:\\Users\\Lenovo\\secret.py\nTraceback (most recent call last): ..."
        cleaned = sanitize_hotspot_error(error)
        self.assertNotIn("Lenovo", cleaned)
        self.assertNotIn("Traceback", cleaned)

    def test_errors_are_sanitized(self):
        view = prepare_hotspot_display(pipeline_result(errors=("failed /home/user/app.py",)))
        self.assertNotIn("/home/user", view.errors[0])

    def test_no_stock_score_or_buy_sell_language(self):
        view = prepare_hotspot_display(pipeline_result())
        self.assertFalse(hasattr(view.candidates[0], "stock_score"))
        text = repr(view)
        for forbidden in ("建议买入", "建议卖出", "强烈推荐", "上涨概率", "预计收益"):
            self.assertNotIn(forbidden, text)

    def test_session_is_reused_without_request(self):
        existing = pipeline_result()
        state = {HOTSPOT_SESSION_KEY: existing}
        runner = Mock()
        self.assertIs(update_hotspot_session(state, requested=False, pipeline_runner=runner), existing)
        runner.assert_not_called()

    def test_explicit_request_stores_result(self):
        current = pipeline_result()
        state = {}
        runner = Mock(return_value=current)
        self.assertIs(update_hotspot_session(state, requested=True, pipeline_runner=runner), current)
        self.assertIs(state[HOTSPOT_SESSION_KEY], current)
        runner.assert_called_once_with()

    def test_refresh_exception_preserves_previous_result(self):
        previous = pipeline_result()
        state = {HOTSPOT_SESSION_KEY: previous}
        returned = update_hotspot_session(
            state, requested=True, pipeline_runner=Mock(side_effect=ConnectionError("blocked"))
        )
        self.assertIs(returned, previous)
        self.assertIn(HOTSPOT_REFRESH_ERROR_KEY, state)
        self.assertEqual(state[HOTSPOT_REFRESH_ERROR_KEY], "刷新失败，当前显示上一次结果。")

    def test_unavailable_refresh_preserves_previous_result(self):
        previous = pipeline_result()
        state = {HOTSPOT_SESSION_KEY: previous}
        returned = update_hotspot_session(
            state, requested=True, pipeline_runner=lambda: pipeline_result(available=False)
        )
        self.assertIs(returned, previous)
        self.assertEqual(state[HOTSPOT_REFRESH_ERROR_KEY], "刷新失败，当前显示上一次结果。")


if __name__ == "__main__":
    unittest.main()
