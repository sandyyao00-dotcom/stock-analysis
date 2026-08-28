"""Read-only Streamlit presentation for the hotspot discovery pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Callable, MutableMapping

import streamlit as st

from stock_analysis.hotspot_pipeline import (
    CandidateStageResult,
    HotspotStageResult,
    run_candidate_stage,
    run_hotspot_stage,
)
from stock_analysis.market_hotspots import BOARD_TYPE_CONCEPT, BOARD_TYPE_INDUSTRY


HOTSPOT_SESSION_KEY = "market_hotspot_stage_result"
CANDIDATE_SESSION_KEY = "market_hotspot_candidate_result"
HOTSPOT_REFRESH_ERROR_KEY = "market_hotspot_refresh_error"
CANDIDATE_REFRESH_ERROR_KEY = "market_hotspot_candidate_refresh_error"


@dataclass(frozen=True)
class HotspotDisplayItem:
    rank: str
    name: str
    score: str
    reasons: tuple[str, ...]
    stale: bool


@dataclass(frozen=True)
class MatchedHotspotDisplay:
    name: str
    rank: str
    score: str
    stale: bool


@dataclass(frozen=True)
class CandidateDisplayItem:
    symbol: str
    name: str
    matched_boards: tuple[str, ...]
    matched_board_count: int | None
    selection_reasons: tuple[str, ...]
    matched_hotspots: tuple[MatchedHotspotDisplay, ...]


@dataclass(frozen=True)
class HotspotDisplayState:
    available: bool
    degraded: bool
    industry: tuple[HotspotDisplayItem, ...]
    concept: tuple[HotspotDisplayItem, ...]
    candidates: tuple[CandidateDisplayItem, ...]
    errors: tuple[str, ...]


def _text(value: object, fallback: str = "—") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _number(value: object, *, decimals: int = 2) -> str:
    if value is None or isinstance(value, bool):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "—"
    return f"{number:.{decimals}f}" if math.isfinite(number) else "—"


def sanitize_hotspot_error(value: object) -> str | None:
    """Return a short user-safe status summary without paths or tracebacks."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.split("Traceback", 1)[0]
    text = re.sub(r"[A-Za-z]:\\[^\s]+", "[本机路径]", text)
    text = re.sub(r"/(?:[^\s/]+/)+[^\s]+", "[内部路径]", text)
    text = " ".join(text.split())
    return text[:157] + "..." if len(text) > 160 else text


def prepare_hotspot_display(
    hotspot_result: object, candidate_result: object | None = None
) -> HotspotDisplayState:
    """Convert a pipeline result into safe, presentation-only values."""
    raw_hotspots = getattr(hotspot_result, "hotspots", {})
    if not isinstance(raw_hotspots, dict):
        raw_hotspots = {}

    def prepare_boards(board_type: str) -> tuple[HotspotDisplayItem, ...]:
        boards = raw_hotspots.get(board_type, ()) or ()
        prepared = []
        for board in boards:
            reasons = tuple(
                str(reason) for reason in (getattr(board, "scoring_reasons", ()) or ()) if reason
            )
            prepared.append(
                HotspotDisplayItem(
                    rank=_text(getattr(board, "hotspot_rank", None)),
                    name=_text(getattr(board, "board_name", None)),
                    score=_number(getattr(board, "hotspot_score", None)),
                    reasons=reasons,
                    stale=bool(getattr(board, "stale", False)),
                )
            )
        return tuple(prepared)

    prepared_candidates = []
    candidate_source = candidate_result if candidate_result is not None else hotspot_result
    for wrapped in (getattr(candidate_source, "candidates", ()) or ()):
        candidate = getattr(wrapped, "candidate", None)
        if candidate is None:
            continue
        matched_boards = tuple(
            _text(getattr(board, "board_name", None), _text(getattr(board, "board_code", None)))
            for board in (getattr(candidate, "matched_boards", ()) or ())
        )
        matched_hotspots = tuple(
            MatchedHotspotDisplay(
                name=_text(getattr(hotspot, "board_name", None)),
                rank=_text(getattr(hotspot, "hotspot_rank", None)),
                score=_number(getattr(hotspot, "hotspot_score", None)),
                stale=bool(getattr(hotspot, "stale", False)),
            )
            for hotspot in (getattr(wrapped, "matched_hotspots", ()) or ())
        )
        count = getattr(candidate, "matched_board_count", None)
        prepared_candidates.append(
            CandidateDisplayItem(
                symbol=_text(getattr(candidate, "symbol", None)),
                name=_text(getattr(candidate, "name", None)),
                matched_boards=matched_boards,
                matched_board_count=(
                    count
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0
                    else None
                ),
                selection_reasons=tuple(
                    str(reason)
                    for reason in (getattr(candidate, "selection_reasons", ()) or ())
                    if reason
                ),
                matched_hotspots=matched_hotspots,
            )
        )

    raw_errors = tuple(getattr(hotspot_result, "errors", ()) or ()) + tuple(
        getattr(candidate_result, "errors", ()) or ()
    )
    errors = tuple(
        cleaned
        for error in raw_errors
        if (cleaned := sanitize_hotspot_error(error))
    )
    return HotspotDisplayState(
        available=bool(getattr(hotspot_result, "available", False)),
        degraded=(
            bool(getattr(hotspot_result, "degraded", False))
            or bool(getattr(candidate_result, "degraded", False))
        ),
        industry=prepare_boards(BOARD_TYPE_INDUSTRY),
        concept=prepare_boards(BOARD_TYPE_CONCEPT),
        candidates=tuple(prepared_candidates),
        errors=errors,
    )


def update_hotspot_session(
    state: MutableMapping[str, object],
    *,
    requested: bool,
    hotspot_runner: Callable[[], HotspotStageResult] = run_hotspot_stage,
) -> object | None:
    """Refresh only hotspots, invalidating candidates only after success."""
    previous = state.get(HOTSPOT_SESSION_KEY)
    if not requested:
        return previous
    try:
        current = hotspot_runner()
        if not isinstance(current, HotspotStageResult):
            raise TypeError("hotspot stage returned an invalid result")
    except Exception as exc:
        state[HOTSPOT_REFRESH_ERROR_KEY] = (
            "刷新失败，当前显示上一次结果。"
            if bool(getattr(previous, "available", False))
            else sanitize_hotspot_error(exc) or "市场热点暂时不可用"
        )
        return previous
    if bool(getattr(previous, "available", False)) and not current.available:
        state[HOTSPOT_REFRESH_ERROR_KEY] = "刷新失败，当前显示上一次结果。"
        return previous
    state[HOTSPOT_SESSION_KEY] = current
    state.pop(CANDIDATE_SESSION_KEY, None)
    state.pop(CANDIDATE_REFRESH_ERROR_KEY, None)
    state.pop(HOTSPOT_REFRESH_ERROR_KEY, None)
    return current


def update_candidate_session(
    state: MutableMapping[str, object],
    *,
    hotspot_result: object,
    requested: bool,
    candidate_runner: Callable[[HotspotStageResult], CandidateStageResult] = run_candidate_stage,
) -> object | None:
    """Refresh candidates only on request and never mutate the hotspot result."""
    previous = state.get(CANDIDATE_SESSION_KEY)
    if not requested:
        return previous
    if not isinstance(hotspot_result, HotspotStageResult) or not hotspot_result.available:
        return previous
    try:
        current = candidate_runner(hotspot_result)
        if not isinstance(current, CandidateStageResult):
            raise TypeError("candidate stage returned an invalid result")
    except Exception as exc:
        state[CANDIDATE_REFRESH_ERROR_KEY] = (
            "候选刷新失败，当前显示上一次结果。"
            if bool(getattr(previous, "available", False))
            else sanitize_hotspot_error(exc) or "热点候选股票暂时不可用"
        )
        return previous
    if bool(getattr(previous, "available", False)) and not current.available:
        state[CANDIDATE_REFRESH_ERROR_KEY] = "候选刷新失败，当前显示上一次结果。"
        return previous
    state[CANDIDATE_SESSION_KEY] = current
    state.pop(CANDIDATE_REFRESH_ERROR_KEY, None)
    return current


def _render_board_tab(items: tuple[HotspotDisplayItem, ...]) -> None:
    if not items:
        st.caption("暂无可展示的板块。")
        return
    for item in items:
        with st.container(border=True):
            cache_label = " · 缓存数据" if item.stale else ""
            st.markdown(f"**{item.rank}. {item.name}**　热度分 {item.score}{cache_label}")
            for reason in item.reasons:
                st.write(f"- {reason}")


def _render_candidates(items: tuple[CandidateDisplayItem, ...]) -> None:
    st.subheader("热点候选股票")
    for item in items:
        with st.container(border=True):
            st.markdown(f"**{item.symbol}　{item.name}**")
            if item.matched_boards:
                count = (
                    f"（命中 {item.matched_board_count} 个热点板块）"
                    if item.matched_board_count is not None
                    else ""
                )
                st.write(f"命中板块：{' · '.join(item.matched_boards)}{count}")
            if item.matched_hotspots:
                details = []
                for hotspot in item.matched_hotspots:
                    stale = "，缓存数据" if hotspot.stale else ""
                    details.append(
                        f"{hotspot.name}（排名 {hotspot.rank}，热度分 {hotspot.score}{stale}）"
                    )
                st.caption("热点关联：" + "；".join(details))
            for reason in item.selection_reasons:
                st.write(f"- {reason}")


def render_market_hotspots_panel(
    *,
    hotspot_runner: Callable[[], HotspotStageResult] = run_hotspot_stage,
    candidate_runner: Callable[[HotspotStageResult], CandidateStageResult] = run_candidate_stage,
) -> None:
    """Render a user-triggered, session-cached read-only hotspot panel."""
    st.header("市场热点")
    st.caption("热度分反映同类板块当前相对活跃程度，不代表未来涨跌。")
    has_result = HOTSPOT_SESSION_KEY in st.session_state
    requested = st.button("刷新市场热点" if has_result else "获取市场热点")
    if requested:
        with st.spinner("正在获取市场热点…"):
            result = update_hotspot_session(
                st.session_state, requested=True, hotspot_runner=hotspot_runner
            )
    else:
        result = update_hotspot_session(st.session_state, requested=False)

    refresh_error = st.session_state.get(HOTSPOT_REFRESH_ERROR_KEY)
    if refresh_error:
        if refresh_error == "刷新失败，当前显示上一次结果。":
            st.warning("刷新失败，当前显示上一次结果。")
        else:
            st.warning("市场热点暂时无法获取，请稍后重试。")
    if result is None:
        st.caption("点击按钮后获取行业和概念热点。")
        return

    candidate_result = update_candidate_session(
        st.session_state, hotspot_result=result, requested=False
    )
    view = prepare_hotspot_display(result, candidate_result)
    if not view.available:
        st.info("暂未取得可用热点数据，请稍后再试。")
        return
    if view.degraded:
        st.warning("部分数据源暂时不可用，当前结果可能不完整。")

    industry_tab, concept_tab = st.tabs(("行业热点", "概念热点"))
    with industry_tab:
        _render_board_tab(view.industry)
    with concept_tab:
        _render_board_tab(view.concept)

    has_candidates = CANDIDATE_SESSION_KEY in st.session_state
    candidate_requested = st.button(
        "刷新热点候选股票" if has_candidates else "获取热点候选股票"
    )
    if candidate_requested:
        with st.spinner("正在获取热点候选股票…"):
            candidate_result = update_candidate_session(
                st.session_state,
                hotspot_result=result,
                requested=True,
                candidate_runner=candidate_runner,
            )
        view = prepare_hotspot_display(result, candidate_result)

    candidate_refresh_error = st.session_state.get(CANDIDATE_REFRESH_ERROR_KEY)
    if candidate_refresh_error:
        if candidate_refresh_error == "候选刷新失败，当前显示上一次结果。":
            st.warning(candidate_refresh_error)
        else:
            st.warning("热点候选股票暂时无法获取，请稍后重试。")

    if view.candidates:
        _render_candidates(view.candidates)
    elif candidate_result is not None:
        st.info("热点板块已取得，但候选成分股暂时不可用。")
    if view.errors:
        with st.expander("查看数据状态"):
            st.write("部分板块数据获取失败。")
            for error in view.errors:
                st.caption(error)
