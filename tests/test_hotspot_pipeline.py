"""Pure orchestration tests for the hotspot discovery pipeline."""

from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from stock_analysis.hotspot_candidates import (
    BoardCandidateResult,
    CandidatePoolResult,
    HotspotCandidate,
    MatchedBoard,
)
from stock_analysis.hotspot_pipeline import run_hotspot_pipeline
from stock_analysis.market_hotspots import (
    BOARD_TYPE_CONCEPT,
    BOARD_TYPE_INDUSTRY,
    SOURCE_EASTMONEY,
    HotspotBoard,
    HotspotResult,
)


NOW = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)


def board(name, board_type, change, *, stale=False, complete=True):
    item = HotspotBoard(
        date=NOW.date(), fetched_at=NOW, board_type=board_type,
        board_code=f"BK-{name}", board_name=name, rank=None,
        latest_price=100.0, change_amount=change, change_percent=change,
        market_cap=1000.0, turnover_rate=change, up_count=8, down_count=2,
        leader_name="龙头", leader_change_percent=change,
        source_provider=SOURCE_EASTMONEY,
    )
    return HotspotResult(True, stale, complete, SOURCE_EASTMONEY, NOW, (item,))


def unavailable(board_type, error):
    return HotspotResult(False, False, False, None, None, (), error)


def candidate_for(item, symbol="600519.SS"):
    matched = MatchedBoard(
        item.board_type, item.board_name, item.board_code, item.rank
    )
    return HotspotCandidate(
        symbol=symbol, name="贵州茅台", latest_price=1300.0,
        change_percent=3.0, change_amount=1.0, volume=100.0, amount=200.0,
        amplitude=2.0, turnover_rate=1.0, pe=20.0, pb=8.0,
        market_cap=1000.0, float_market_cap=900.0,
        source_provider=SOURCE_EASTMONEY, fetched_at=NOW, relative_rank=1,
        selection_reasons=("板块内涨幅排名靠前",),
        matched_boards=(matched,), matched_board_count=1,
    )


def successful_builder(inputs, *, max_candidates_per_board):
    inputs = tuple(inputs)
    board_results = []
    candidates = []
    for index, result in enumerate(inputs):
        item = result.data[0]
        candidate = candidate_for(item, symbol=f"600{index:03d}.SS")
        candidates.append(candidate)
        board_results.append(BoardCandidateResult(
            item.board_type, item.board_name, item.board_code, True,
            result.stale, SOURCE_EASTMONEY, NOW, 1, (candidate,)
        ))
    return CandidatePoolResult(True, any(r.stale for r in board_results),
                               tuple(candidates), tuple(board_results), ())


class HotspotPipelineTests(unittest.TestCase):
    def test_normal_industry_and_concept_flow(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: board("机器人", BOARD_TYPE_CONCEPT, 4),
            candidate_builder=successful_builder,
        )
        self.assertTrue(result.available)
        self.assertFalse(result.degraded)
        self.assertEqual(len(result.hotspots[BOARD_TYPE_INDUSTRY]), 1)
        self.assertEqual(len(result.hotspots[BOARD_TYPE_CONCEPT]), 1)
        self.assertEqual(len(result.candidates), 2)

    def test_call_order(self):
        calls = []
        industry = board("银行", BOARD_TYPE_INDUSTRY, 3)
        concept = board("机器人", BOARD_TYPE_CONCEPT, 4)

        def fetch_i(): calls.append("industry"); return industry
        def fetch_c(): calls.append("concept"); return concept
        def scorer(results):
            from stock_analysis.hotspot_scoring import score_hotspots
            calls.append("score"); return score_hotspots(results)
        def selector(scored, **limits):
            from stock_analysis.hotspot_scoring import select_top_hotspots
            calls.append("select"); return select_top_hotspots(scored, **limits)
        def builder(inputs, **kwargs):
            calls.append("candidates"); return successful_builder(inputs, **kwargs)

        run_hotspot_pipeline(
            industry_hotspot_fetcher=fetch_i, concept_hotspot_fetcher=fetch_c,
            scorer=scorer, top_selector=selector, candidate_builder=builder,
        )
        self.assertEqual(calls, ["industry", "concept", "score", "select", "candidates"])

    def test_configurable_top_n_per_type(self):
        industries = tuple(board(str(i), BOARD_TYPE_INDUSTRY, i).data[0] for i in range(1, 8))
        concepts = tuple(board(str(i), BOARD_TYPE_CONCEPT, i).data[0] for i in range(1, 6))
        captured = []
        def builder(inputs, **kwargs):
            captured.extend(inputs); return successful_builder(inputs, **kwargs)
        result = run_hotspot_pipeline(
            industry_top_n=2, concept_top_n=3,
            industry_hotspot_fetcher=lambda: HotspotResult(True, False, True, SOURCE_EASTMONEY, NOW, industries),
            concept_hotspot_fetcher=lambda: HotspotResult(True, False, True, SOURCE_EASTMONEY, NOW, concepts),
            candidate_builder=builder,
        )
        self.assertEqual(len(result.hotspots[BOARD_TYPE_INDUSTRY]), 2)
        self.assertEqual(len(result.hotspots[BOARD_TYPE_CONCEPT]), 3)
        self.assertEqual(len(captured), 5)

    def test_candidates_per_board_is_forwarded(self):
        builder = Mock(return_value=CandidatePoolResult(False, False, (), (), ()))
        run_hotspot_pipeline(
            candidates_per_board=3,
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "none"),
            candidate_builder=builder,
        )
        self.assertEqual(builder.call_args.kwargs["max_candidates_per_board"], 3)

    def test_incomplete_and_score_unavailable_never_reach_candidates(self):
        incomplete = board("目录", BOARD_TYPE_INDUSTRY, 99, complete=False)
        low_coverage_board = board("缺失", BOARD_TYPE_INDUSTRY, 1).data[0]
        low_coverage_board = HotspotBoard(**{
            **low_coverage_board.__dict__, "turnover_rate": None,
            "up_count": None, "down_count": None, "leader_change_percent": None,
        })
        builder = Mock(return_value=CandidatePoolResult(False, False, (), (), ()))
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: HotspotResult(True, False, True, SOURCE_EASTMONEY, NOW, (low_coverage_board,)),
            concept_hotspot_fetcher=lambda: incomplete,
            candidate_builder=builder,
        )
        self.assertFalse(result.available)
        builder.assert_not_called()

    def test_stale_score_rank_and_matched_metadata_are_preserved(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3, stale=True),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "none"),
            candidate_builder=successful_builder,
        )
        hotspot = result.hotspots[BOARD_TYPE_INDUSTRY][0]
        match = result.candidates[0].matched_hotspots[0]
        self.assertTrue(hotspot.stale)
        self.assertTrue(match.stale)
        self.assertEqual(match.hotspot_score, hotspot.hotspot_score)
        self.assertEqual(match.hotspot_rank, hotspot.hotspot_rank)
        self.assertEqual(result.candidates[0].candidate.matched_board_count, 1)
        self.assertEqual(len(result.candidates[0].candidate.matched_boards), 1)

    def test_existing_candidate_dedup_result_is_not_recreated(self):
        def deduplicated_builder(inputs, **kwargs):
            items = tuple(inputs)
            one = candidate_for(items[0].data[0])
            return CandidatePoolResult(True, False, (one,), (), ())
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "none"),
            candidate_builder=deduplicated_builder,
        )
        self.assertEqual(len(result.candidates), 1)

    def test_default_top_five_per_type(self):
        industries = tuple(board(str(i), BOARD_TYPE_INDUSTRY, i).data[0] for i in range(1, 8))
        concepts = tuple(board(str(i), BOARD_TYPE_CONCEPT, i).data[0] for i in range(1, 8))
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: HotspotResult(True, False, True, SOURCE_EASTMONEY, NOW, industries),
            concept_hotspot_fetcher=lambda: HotspotResult(True, False, True, SOURCE_EASTMONEY, NOW, concepts),
            candidate_builder=successful_builder,
        )
        self.assertEqual(len(result.hotspots[BOARD_TYPE_INDUSTRY]), 5)
        self.assertEqual(len(result.hotspots[BOARD_TYPE_CONCEPT]), 5)

    def test_industry_failure_does_not_block_concept(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=Mock(side_effect=ConnectionError("blocked")),
            concept_hotspot_fetcher=lambda: board("机器人", BOARD_TYPE_CONCEPT, 4),
            candidate_builder=successful_builder,
        )
        self.assertTrue(result.available)
        self.assertTrue(result.degraded)
        self.assertTrue(result.errors)

    def test_concept_failure_does_not_block_industry(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=Mock(side_effect=ConnectionError("blocked")),
            candidate_builder=successful_builder,
        )
        self.assertTrue(result.available)
        self.assertTrue(result.degraded)

    def test_one_board_failure_preserves_other_board(self):
        def partial_builder(inputs, **kwargs):
            items = tuple(inputs)
            good = items[0].data[0]
            candidate = candidate_for(good)
            return CandidatePoolResult(True, False, (candidate,), (
                BoardCandidateResult(good.board_type, good.board_name, good.board_code, True, False, SOURCE_EASTMONEY, NOW, 1, (candidate,)),
                BoardCandidateResult(items[1].data[0].board_type, items[1].data[0].board_name, items[1].data[0].board_code, False, False, None, None, 0, error="failed"),
            ), ("failed",))
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: board("机器人", BOARD_TYPE_CONCEPT, 4),
            candidate_builder=partial_builder,
        )
        self.assertTrue(result.available)
        self.assertTrue(result.degraded)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(result.board_results), 2)
        self.assertTrue(any("failed" in error for error in result.errors))

    def test_all_constituents_failure_keeps_hotspots(self):
        failed = CandidatePoolResult(False, False, (), (), ("all failed",))
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "none"),
            candidate_builder=lambda *args, **kwargs: failed,
        )
        self.assertTrue(result.available)
        self.assertTrue(result.degraded)
        self.assertEqual(result.candidates, ())
        self.assertTrue(result.hotspots[BOARD_TYPE_INDUSTRY])

    def test_provider_errors_and_board_results_are_preserved(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "concept failed"),
            candidate_builder=successful_builder,
        )
        self.assertIn("concept failed", result.errors)
        self.assertEqual(len(result.provider_results), 2)
        self.assertEqual(len(result.board_results), 1)

    def test_malformed_downstream_results_are_safe(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: object(),
            concept_hotspot_fetcher=lambda: board("机器人", BOARD_TYPE_CONCEPT, 4),
            scorer=lambda _: object(),
            candidate_builder=Mock(),
        )
        self.assertFalse(result.available)
        self.assertTrue(result.degraded)
        self.assertTrue(result.errors)

    def test_candidate_builder_exception_is_isolated(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "none"),
            candidate_builder=Mock(side_effect=KeyError("bad")),
        )
        self.assertTrue(result.available)
        self.assertTrue(result.degraded)
        self.assertEqual(result.candidates, ())
        self.assertTrue(result.errors)

    def test_unicode_none_safety_and_no_fake_values(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("种植业与林业", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "none"),
            candidate_builder=successful_builder,
        )
        hotspot = result.hotspots[BOARD_TYPE_INDUSTRY][0]
        self.assertEqual(hotspot.board_name, "种植业与林业")
        self.assertIsNone(hotspot.skip_reason)

    def test_deterministic_output(self):
        kwargs = dict(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: board("机器人", BOARD_TYPE_CONCEPT, 4),
            candidate_builder=successful_builder,
        )
        self.assertEqual(run_hotspot_pipeline(**kwargs), run_hotspot_pipeline(**kwargs))

    def test_no_stock_score_or_recommendation_language(self):
        result = run_hotspot_pipeline(
            industry_hotspot_fetcher=lambda: board("银行", BOARD_TYPE_INDUSTRY, 3),
            concept_hotspot_fetcher=lambda: unavailable(BOARD_TYPE_CONCEPT, "none"),
            candidate_builder=successful_builder,
        )
        self.assertFalse(hasattr(result.candidates[0], "candidate_score"))
        text = repr(result)
        for forbidden in ("建议买入", "建议卖出", "上涨概率", "预计收益", "目标价", "强烈推荐"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
