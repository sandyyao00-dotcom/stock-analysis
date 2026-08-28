"""Deterministic, cross-sectional hotspot scoring for normalized board data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import math
from typing import Iterable

from stock_analysis.market_hotspots import (
    BOARD_TYPE_CONCEPT,
    BOARD_TYPE_INDUSTRY,
    HotspotBoard,
    HotspotResult,
)


CHANGE_PERCENT_WEIGHT = 0.35
BREADTH_WEIGHT = 0.25
TURNOVER_RATE_WEIGHT = 0.20
LEADER_STRENGTH_WEIGHT = 0.20
MINIMUM_SCORE_COVERAGE = 0.60
DEFAULT_TOP_HOTSPOTS_PER_TYPE = 5

_COMPONENT_WEIGHTS = {
    "change_percent": CHANGE_PERCENT_WEIGHT,
    "breadth": BREADTH_WEIGHT,
    "turnover_rate": TURNOVER_RATE_WEIGHT,
    "leader_strength": LEADER_STRENGTH_WEIGHT,
}


@dataclass(frozen=True)
class ScoredHotspot:
    date: date | None
    board_type: str
    board_code: str | None
    board_name: str | None
    rank: int | None
    latest_price: float | None
    change_amount: float | None
    change_percent: float | None
    market_cap: float | None
    turnover_rate: float | None
    up_count: int | None
    down_count: int | None
    leader_name: str | None
    leader_change_percent: float | None
    breadth_ratio: float | None
    hotspot_score: float | None
    score_coverage: float
    score_available: bool
    hotspot_rank: int | None
    scoring_reasons: tuple[str, ...]
    component_scores: dict[str, float | None]
    component_values: dict[str, float | None]
    source_provider: str
    fetched_at: datetime
    stale: bool
    metrics_complete: bool
    skip_reason: str | None = None


def _safe_number(value: object, *, percentage: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if percentage and text.endswith("%"):
            text = text[:-1].strip()
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_count(value: object) -> int | None:
    number = _safe_number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def calculate_breadth_ratio(up_count: object, down_count: object) -> float | None:
    """Return breadth from real non-negative counts, otherwise None."""
    up = _safe_count(up_count)
    down = _safe_count(down_count)
    if up is None or down is None or up + down <= 0:
        return None
    return up / (up + down)


def _percentile_scores(values: dict[int, float]) -> dict[int, float]:
    """Return tie-aware 0-100 percentile ranks for one comparable cross-section."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    if len(ordered) == 1:
        return {ordered[0][0]: 100.0}
    scores: dict[int, float] = {}
    position = 0
    while position < len(ordered):
        end = position
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[position][1]:
            end += 1
        average_position = (position + end) / 2
        percentile = average_position / (len(ordered) - 1) * 100
        for index in range(position, end + 1):
            scores[ordered[index][0]] = percentile
        position = end + 1
    return scores


def _component_values(board: HotspotBoard) -> dict[str, float | None]:
    return {
        "change_percent": _safe_number(board.change_percent, percentage=True),
        "breadth": calculate_breadth_ratio(board.up_count, board.down_count),
        "turnover_rate": _safe_number(board.turnover_rate, percentage=True),
        "leader_strength": _safe_number(board.leader_change_percent, percentage=True),
    }


def _base_scored_board(
    board: HotspotBoard, *, stale: bool, metrics_complete: bool
) -> ScoredHotspot:
    values = _component_values(board)
    coverage = sum(
        weight for component, weight in _COMPONENT_WEIGHTS.items() if values[component] is not None
    )
    skip_reason = "incomplete_board_metrics" if not metrics_complete else None
    return ScoredHotspot(
        date=board.date,
        board_type=board.board_type,
        board_code=board.board_code,
        board_name=board.board_name,
        rank=board.rank,
        latest_price=_safe_number(board.latest_price),
        change_amount=_safe_number(board.change_amount, percentage=True),
        change_percent=values["change_percent"],
        market_cap=_safe_number(board.market_cap),
        turnover_rate=values["turnover_rate"],
        up_count=_safe_count(board.up_count),
        down_count=_safe_count(board.down_count),
        leader_name=board.leader_name,
        leader_change_percent=values["leader_strength"],
        breadth_ratio=values["breadth"],
        hotspot_score=None,
        score_coverage=round(coverage, 2),
        score_available=False,
        hotspot_rank=None,
        scoring_reasons=(),
        component_scores={component: None for component in _COMPONENT_WEIGHTS},
        component_values=values,
        source_provider=board.source_provider,
        fetched_at=board.fetched_at,
        stale=stale,
        metrics_complete=metrics_complete,
        skip_reason=skip_reason,
    )


def _reason_for(component: str) -> str:
    return {
        "change_percent": "板块涨幅处于同类板块相对前列",
        "breadth": "上涨家数占比处于同类板块相对前列",
        "turnover_rate": "换手活跃度处于同类板块相对前列",
        "leader_strength": "领涨股表现处于同类板块相对前列",
    }[component]


def _score_group(group: list[ScoredHotspot]) -> list[ScoredHotspot]:
    percentiles: dict[str, dict[int, float]] = {}
    for component in _COMPONENT_WEIGHTS:
        percentiles[component] = _percentile_scores(
            {
                index: value
                for index, item in enumerate(group)
                if item.metrics_complete
                and (value := item.component_values[component]) is not None
            }
        )

    scored: list[ScoredHotspot] = []
    for index, item in enumerate(group):
        component_scores = (
            {
                component: percentiles[component].get(index)
                for component in _COMPONENT_WEIGHTS
            }
            if item.metrics_complete
            else {component: None for component in _COMPONENT_WEIGHTS}
        )
        score_available = (
            item.metrics_complete and item.score_coverage >= MINIMUM_SCORE_COVERAGE
        )
        if score_available:
            weighted_sum = sum(
                component_scores[component] * weight
                for component, weight in _COMPONENT_WEIGHTS.items()
                if component_scores[component] is not None
            )
            hotspot_score = round(weighted_sum / item.score_coverage, 2)
            reasons = tuple(
                _reason_for(component)
                for component in _COMPONENT_WEIGHTS
                if component_scores[component] is not None
                and component_scores[component] >= 60
            )
            skip_reason = None
        else:
            hotspot_score = None
            reasons = ()
            skip_reason = item.skip_reason or "insufficient_score_coverage"
        scored.append(
            replace(
                item,
                hotspot_score=hotspot_score,
                score_available=score_available,
                scoring_reasons=reasons,
                component_scores=component_scores,
                skip_reason=skip_reason,
            )
        )

    available = [item for item in scored if item.score_available]
    available.sort(
        key=lambda item: (
            -(item.hotspot_score if item.hotspot_score is not None else float("-inf")),
            -(item.change_percent if item.change_percent is not None else float("-inf")),
            item.board_name or "",
            item.board_code or "",
        )
    )
    ranks = {id(item): rank for rank, item in enumerate(available, start=1)}
    return [replace(item, hotspot_rank=ranks.get(id(item))) for item in scored]


def score_hotspots(results: Iterable[HotspotResult]) -> tuple[ScoredHotspot, ...]:
    """Score available board records separately within industry and concept groups."""
    groups = {BOARD_TYPE_INDUSTRY: [], BOARD_TYPE_CONCEPT: []}
    passthrough: list[ScoredHotspot] = []
    for result in results:
        if not result.available:
            continue
        for board in result.data:
            item = _base_scored_board(
                board, stale=result.stale, metrics_complete=result.metrics_complete
            )
            if board.board_type in groups:
                groups[board.board_type].append(item)
            else:
                passthrough.append(replace(item, skip_reason="unsupported_board_type"))
    scored = _score_group(groups[BOARD_TYPE_INDUSTRY]) + _score_group(
        groups[BOARD_TYPE_CONCEPT]
    )
    return tuple(scored + passthrough)


def select_top_hotspots(
    scored: Iterable[ScoredHotspot],
    *,
    industry_limit: int = DEFAULT_TOP_HOTSPOTS_PER_TYPE,
    concept_limit: int = DEFAULT_TOP_HOTSPOTS_PER_TYPE,
) -> dict[str, tuple[ScoredHotspot, ...]]:
    """Select already-ranked, score-available boards without fetching any data."""
    limits = {
        BOARD_TYPE_INDUSTRY: max(0, industry_limit),
        BOARD_TYPE_CONCEPT: max(0, concept_limit),
    }
    selected: dict[str, tuple[ScoredHotspot, ...]] = {}
    items = tuple(scored)
    for board_type, limit in limits.items():
        eligible = [
            item
            for item in items
            if item.board_type == board_type and item.score_available and item.hotspot_rank is not None
        ]
        eligible.sort(key=lambda item: item.hotspot_rank or math.inf)
        selected[board_type] = tuple(eligible[:limit])
    return selected
