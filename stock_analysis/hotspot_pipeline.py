"""End-to-end orchestration for hotspot discovery without new scoring logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from stock_analysis.hotspot_candidates import (
    DEFAULT_CANDIDATES_PER_BOARD,
    BoardCandidateResult,
    CandidatePoolResult,
    HotspotCandidate,
    build_hotspot_candidate_pool,
)
from stock_analysis.hotspot_scoring import (
    DEFAULT_TOP_HOTSPOTS_PER_TYPE,
    ScoredHotspot,
    score_hotspots,
    select_top_hotspots,
)
from stock_analysis.market_hotspots import (
    BOARD_TYPE_CONCEPT,
    BOARD_TYPE_INDUSTRY,
    HotspotBoard,
    HotspotResult,
    get_concept_hotspots,
    get_industry_hotspots,
)


@dataclass(frozen=True)
class MatchedHotspot:
    board_type: str
    board_name: str | None
    board_code: str | None
    hotspot_score: float
    hotspot_rank: int
    stale: bool


@dataclass(frozen=True)
class PipelineCandidate:
    candidate: HotspotCandidate
    matched_hotspots: tuple[MatchedHotspot, ...]


@dataclass(frozen=True)
class HotspotPipelineResult:
    available: bool
    degraded: bool
    hotspots: dict[str, tuple[ScoredHotspot, ...]]
    candidates: tuple[PipelineCandidate, ...]
    board_results: tuple[BoardCandidateResult, ...]
    provider_results: tuple[HotspotResult, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class HotspotStageResult:
    available: bool
    degraded: bool
    hotspots: dict[str, tuple[ScoredHotspot, ...]]
    provider_results: tuple[HotspotResult, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class CandidateStageResult:
    available: bool
    degraded: bool
    candidates: tuple[PipelineCandidate, ...]
    board_results: tuple[BoardCandidateResult, ...]
    errors: tuple[str, ...]


HotspotFetcher = Callable[[], HotspotResult]
HotspotScorer = Callable[[Iterable[HotspotResult]], tuple[ScoredHotspot, ...]]
TopSelector = Callable[..., dict[str, tuple[ScoredHotspot, ...]]]
CandidateBuilder = Callable[..., CandidatePoolResult]


def _error_result(board_type: str, exc: Exception) -> HotspotResult:
    return HotspotResult(
        available=False,
        stale=False,
        metrics_complete=False,
        source_provider=None,
        fetched_at=None,
        data=(),
        error=f"{board_type} hotspots unavailable ({type(exc).__name__}: {exc})",
    )


def _fetch_safely(board_type: str, fetcher: HotspotFetcher) -> HotspotResult:
    try:
        result = fetcher()
        if not isinstance(result, HotspotResult):
            raise TypeError("hotspot fetcher returned an invalid result")
        return result
    except Exception as exc:
        return _error_result(board_type, exc)


def _selected_result(item: ScoredHotspot) -> HotspotResult:
    """Adapt a selected score back to the candidate module's existing input type."""
    board = HotspotBoard(
        date=item.date,
        fetched_at=item.fetched_at,
        board_type=item.board_type,
        board_code=item.board_code,
        board_name=item.board_name,
        rank=item.hotspot_rank,
        latest_price=item.latest_price,
        change_amount=item.change_amount,
        change_percent=item.change_percent,
        market_cap=item.market_cap,
        turnover_rate=item.turnover_rate,
        up_count=item.up_count,
        down_count=item.down_count,
        leader_name=item.leader_name,
        leader_change_percent=item.leader_change_percent,
        source_provider=item.source_provider,
    )
    return HotspotResult(
        available=True,
        stale=item.stale,
        metrics_complete=item.metrics_complete,
        source_provider=item.source_provider,
        fetched_at=item.fetched_at,
        data=(board,),
    )


def _matched_hotspot(item: ScoredHotspot) -> MatchedHotspot | None:
    if item.hotspot_score is None or item.hotspot_rank is None:
        return None
    return MatchedHotspot(
        board_type=item.board_type,
        board_name=item.board_name,
        board_code=item.board_code,
        hotspot_score=item.hotspot_score,
        hotspot_rank=item.hotspot_rank,
        stale=item.stale,
    )


def _candidate_associations(
    candidates: Iterable[HotspotCandidate], selected: Iterable[ScoredHotspot]
) -> tuple[PipelineCandidate, ...]:
    lookup: dict[tuple[str, str | None, str | None], MatchedHotspot] = {}
    for item in selected:
        metadata = _matched_hotspot(item)
        if metadata is not None:
            lookup[(item.board_type, item.board_name, item.board_code)] = metadata

    associated: list[PipelineCandidate] = []
    for candidate in candidates:
        if not isinstance(candidate, HotspotCandidate):
            continue
        matches = tuple(
            lookup[key]
            for board in candidate.matched_boards
            if (key := (board.board_type, board.board_name, board.board_code)) in lookup
        )
        associated.append(PipelineCandidate(candidate=candidate, matched_hotspots=matches))
    return tuple(associated)


def _empty_hotspot_stage(
    provider_results: tuple[HotspotResult, ...], errors: Iterable[str]
) -> HotspotStageResult:
    return HotspotStageResult(
        available=False,
        degraded=True,
        hotspots={BOARD_TYPE_INDUSTRY: (), BOARD_TYPE_CONCEPT: ()},
        provider_results=provider_results,
        errors=tuple(dict.fromkeys(error for error in errors if error)),
    )


def run_hotspot_stage(
    *,
    industry_top_n: int = DEFAULT_TOP_HOTSPOTS_PER_TYPE,
    concept_top_n: int = DEFAULT_TOP_HOTSPOTS_PER_TYPE,
    industry_hotspot_fetcher: HotspotFetcher = get_industry_hotspots,
    concept_hotspot_fetcher: HotspotFetcher = get_concept_hotspots,
    scorer: HotspotScorer = score_hotspots,
    top_selector: TopSelector = select_top_hotspots,
) -> HotspotStageResult:
    """Fetch, score, and select hotspots without requesting constituents."""
    provider_results = (
        _fetch_safely(BOARD_TYPE_INDUSTRY, industry_hotspot_fetcher),
        _fetch_safely(BOARD_TYPE_CONCEPT, concept_hotspot_fetcher),
    )
    errors = [result.error for result in provider_results if result.error]

    try:
        scored = scorer(provider_results)
        if not isinstance(scored, tuple) or any(
            not isinstance(item, ScoredHotspot) for item in scored
        ):
            raise TypeError("scorer returned an invalid result")
        selected = top_selector(
            scored,
            industry_limit=max(0, industry_top_n),
            concept_limit=max(0, concept_top_n),
        )
        if not isinstance(selected, dict):
            raise TypeError("top selector returned an invalid result")
        hotspots = {
            BOARD_TYPE_INDUSTRY: tuple(selected.get(BOARD_TYPE_INDUSTRY, ())),
            BOARD_TYPE_CONCEPT: tuple(selected.get(BOARD_TYPE_CONCEPT, ())),
        }
        selected_items = hotspots[BOARD_TYPE_INDUSTRY] + hotspots[BOARD_TYPE_CONCEPT]
        if any(
            not isinstance(item, ScoredHotspot) or not item.score_available
            for item in selected_items
        ):
            raise TypeError("top selector returned an unavailable hotspot")
    except Exception as exc:
        errors.append(f"hotspot scoring unavailable ({type(exc).__name__}: {exc})")
        return _empty_hotspot_stage(provider_results, errors)

    if not selected_items:
        return _empty_hotspot_stage(provider_results, errors)

    degraded = bool(errors) or any(
        not result.available or result.error for result in provider_results
    )
    return HotspotStageResult(
        available=True,
        degraded=degraded,
        hotspots=hotspots,
        provider_results=provider_results,
        errors=tuple(dict.fromkeys(error for error in errors if error)),
    )


def run_candidate_stage(
    hotspot_result: HotspotStageResult,
    *,
    candidates_per_board: int = DEFAULT_CANDIDATES_PER_BOARD,
    candidate_builder: CandidateBuilder = build_hotspot_candidate_pool,
) -> CandidateStageResult:
    """Build candidates only from a previously selected hotspot-stage result."""
    if not isinstance(hotspot_result, HotspotStageResult) or not hotspot_result.available:
        return CandidateStageResult(False, True, (), (), ())
    selected_items = (
        tuple(hotspot_result.hotspots.get(BOARD_TYPE_INDUSTRY, ()))
        + tuple(hotspot_result.hotspots.get(BOARD_TYPE_CONCEPT, ()))
    )
    if not selected_items:
        return CandidateStageResult(False, True, (), (), ())

    candidate_inputs = tuple(_selected_result(item) for item in selected_items)
    errors: list[str] = []
    try:
        candidate_pool = candidate_builder(
            candidate_inputs,
            max_candidates_per_board=max(0, candidates_per_board),
        )
        if not isinstance(candidate_pool, CandidatePoolResult):
            raise TypeError("candidate builder returned an invalid result")
    except Exception as exc:
        errors.append(f"candidate stage unavailable ({type(exc).__name__}: {exc})")
        candidate_pool = CandidatePoolResult(False, False, (), (), ())

    errors.extend(candidate_pool.errors)
    candidates = _candidate_associations(candidate_pool.candidates, selected_items)
    board_results = tuple(
        item for item in candidate_pool.board_results if isinstance(item, BoardCandidateResult)
    )
    errors.extend(
        f"{item.board_name or item.board_code}: {item.error}"
        for item in board_results
        if item.error
    )
    candidate_stage_degraded = (
        not candidate_pool.available
        or not candidates
        or len(board_results) < len(selected_items)
        or any(not item.available or item.error for item in board_results)
    )
    return CandidateStageResult(
        available=bool(candidate_pool.available and candidates),
        degraded=candidate_stage_degraded or bool(errors),
        candidates=candidates,
        board_results=board_results,
        errors=tuple(dict.fromkeys(error for error in errors if error)),
    )


def run_hotspot_pipeline(
    *,
    industry_top_n: int = DEFAULT_TOP_HOTSPOTS_PER_TYPE,
    concept_top_n: int = DEFAULT_TOP_HOTSPOTS_PER_TYPE,
    candidates_per_board: int = DEFAULT_CANDIDATES_PER_BOARD,
    industry_hotspot_fetcher: HotspotFetcher = get_industry_hotspots,
    concept_hotspot_fetcher: HotspotFetcher = get_concept_hotspots,
    scorer: HotspotScorer = score_hotspots,
    top_selector: TopSelector = select_top_hotspots,
    candidate_builder: CandidateBuilder = build_hotspot_candidate_pool,
) -> HotspotPipelineResult:
    """Run both stages while preserving the original full-pipeline API."""
    hotspot_stage = run_hotspot_stage(
        industry_top_n=industry_top_n,
        concept_top_n=concept_top_n,
        industry_hotspot_fetcher=industry_hotspot_fetcher,
        concept_hotspot_fetcher=concept_hotspot_fetcher,
        scorer=scorer,
        top_selector=top_selector,
    )
    candidate_stage = run_candidate_stage(
        hotspot_stage,
        candidates_per_board=candidates_per_board,
        candidate_builder=candidate_builder,
    )
    errors = tuple(dict.fromkeys(hotspot_stage.errors + candidate_stage.errors))
    return HotspotPipelineResult(
        available=hotspot_stage.available,
        degraded=hotspot_stage.degraded or candidate_stage.degraded,
        hotspots=hotspot_stage.hotspots,
        candidates=candidate_stage.candidates,
        board_results=candidate_stage.board_results,
        provider_results=hotspot_stage.provider_results,
        errors=errors,
    )
