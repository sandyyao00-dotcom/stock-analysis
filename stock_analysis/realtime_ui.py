"""Streamlit UI for reviewed, session-only A-share realtime snapshots."""

from dataclasses import dataclass, replace
from datetime import datetime
from math import isclose
from zoneinfo import ZoneInfo

import streamlit as st

from stock_analysis.a_share_sessions import (
    BEIJING_TIMEZONE,
    PHASE_MESSAGES,
    SINA_PRIORITY_PHASES,
    get_a_share_market_phase,
)
from stock_analysis.markets import MARKET_A_SHARE, MarketSymbol, format_money
from stock_analysis.ocr import extract_screenshot_text, ocr_status
from stock_analysis.providers import (
    REALTIME_PRICE_DIFFERENCE_WARNING_PCT,
    ManualRealtimeProvider,
    PastedTextRealtimeProvider,
    RealtimeSnapshot,
    ScreenshotRealtimeProvider,
    confirm_snapshot,
    realtime_comparisons,
    snapshot_age_status,
    snapshot_from_values,
    validate_snapshot,
)
from stock_analysis.sina_realtime import get_sina_a_share_snapshot


SOURCE_OPTIONS = ("招商证券", "同花顺", "通达信", "东方财富", "其他")
METHOD_LABELS = {"manual": "手动输入", "pasted_text": "粘贴文字", "screenshot": "截图识别"}
VANCOUVER_TIMEZONE = ZoneInfo("America/Vancouver")


@dataclass(frozen=True)
class SinaDisplayState:
    """Human-readable UI state without exposing provider implementation flags."""

    state: str
    message: str
    cache_label: str | None = None
    fetched_at_label: str | None = None


def sina_enabled_for_market(market: str) -> bool:
    return market == MARKET_A_SHARE


def _friendly_sina_error(error: str | None) -> str:
    if not error:
        return "新浪自动行情暂时不可用。"
    lowered = error.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "新浪自动行情请求超时，请稍后再检查。"
    if "connection" in lowered or "network" in lowered:
        return "新浪自动行情网络连接失败，请稍后再检查。"
    if "does not contain" in lowered or "stock code" in lowered:
        return "新浪行情中暂未找到这只股票。"
    return "新浪自动行情暂时不可用，请稍后再检查。"


def format_sina_fetch_time(fetched_at: datetime | None) -> str | None:
    """Format one aware instant in Vancouver and Beijing without guessing naive time."""
    if fetched_at is None or fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        return None
    vancouver = fetched_at.astimezone(VANCOUVER_TIMEZONE)
    beijing = fetched_at.astimezone(BEIJING_TIMEZONE)
    date_delta = (beijing.date() - vancouver.date()).days
    if date_delta == 1:
        date_suffix = "（次日）"
    elif date_delta == -1:
        date_suffix = "（前日）"
    elif date_delta:
        date_suffix = f"（相差{date_delta:+d}日）"
    else:
        date_suffix = ""
    return (
        f"温哥华 {vancouver.strftime('%H:%M:%S')}｜"
        f"北京 {beijing.strftime('%H:%M:%S')}{date_suffix}"
    )


def build_sina_display_state(
    snapshot: RealtimeSnapshot, *, prefers_sina: bool = True
) -> SinaDisplayState:
    """Convert provider state to short Chinese UI copy."""
    fetched_at_label = format_sina_fetch_time(snapshot.fetched_at)
    if not prefers_sina:
        if snapshot.available:
            return SinaDisplayState(
                "cached",
                "最近新浪缓存，仅供辅助参考；当前优先使用 Yahoo 行情。",
                "辅助缓存",
                fetched_at_label,
            )
        return SinaDisplayState("yahoo", "当前使用 Yahoo / yfinance 历史参考行情。")
    if snapshot.available:
        if snapshot.stale:
            message = "缓存数据，可能不是最新行情"
            if snapshot.loading:
                message += "；后台正在更新"
            return SinaDisplayState("stale", message, "缓存已过期", fetched_at_label)
        return SinaDisplayState("available", "自动行情已加载。", "缓存有效", fetched_at_label)
    if snapshot.loading:
        return SinaDisplayState(
            "loading",
            "新浪行情正在后台加载，当前先显示 Yahoo 参考行情。你也可以使用下方手动实时行情。",
        )
    return SinaDisplayState(
        "unavailable",
        f"新浪实时行情暂时不可用，当前使用 Yahoo 行情。{_friendly_sina_error(snapshot.error)} "
        "你仍可以使用下方手动行情。",
    )


def _format_quantity(value: float | None, *, missing: str = "—") -> str:
    if value is None:
        return missing
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:,.2f} 亿"
    if abs(value) >= 10_000:
        return f"{value / 10_000:,.2f} 万"
    return f"{value:,.0f}"


def sina_display_fields(snapshot: RealtimeSnapshot, currency: str) -> tuple[tuple[str, str], ...]:
    """Format automatic quote fields without feeding them into analysis."""
    return (
        ("股票名称", snapshot.company_name or snapshot.ticker),
        ("最新价", format_money(snapshot.current_price, currency)),
        ("涨跌幅", f"{snapshot.change_percent:+.2f}%" if snapshot.change_percent is not None else "N/A"),
        ("今开", format_money(snapshot.open, currency)),
        ("最高", format_money(snapshot.high, currency)),
        ("最低", format_money(snapshot.low, currency)),
        ("昨收", format_money(snapshot.previous_close, currency)),
        ("成交量", _format_quantity(snapshot.volume)),
        ("成交额", _format_quantity(snapshot.turnover_amount)),
    )


def _load_sina_snapshot(
    market_symbol: MarketSymbol, *, allow_background_refresh: bool
) -> RealtimeSnapshot | None:
    if not sina_enabled_for_market(market_symbol.market):
        return None
    return get_sina_a_share_snapshot(
        market_symbol.yahoo_symbol,
        allow_background_refresh=allow_background_refresh,
    )


def _render_sina_automatic_quote(
    market_symbol: MarketSymbol, currency: str, *, now: datetime | None = None
) -> None:
    if not sina_enabled_for_market(market_symbol.market):
        return
    phase = get_a_share_market_phase(now)
    prefers_sina = phase in SINA_PRIORITY_PHASES
    snapshot = _load_sina_snapshot(
        market_symbol,
        allow_background_refresh=prefers_sina,
    )
    if snapshot is None:
        return
    state = build_sina_display_state(snapshot, prefers_sina=prefers_sina)
    with st.container(border=True):
        st.write("**新浪实时行情**" if prefers_sina else "**自动实时行情**")
        st.caption(PHASE_MESSAGES[phase])
        if state.state == "loading":
            st.info(state.message)
        elif state.state == "unavailable":
            st.warning(state.message)
        elif state.state == "yahoo":
            st.caption(state.message)
        else:
            if not prefers_sina:
                st.write("**最近新浪缓存**")
            fields = sina_display_fields(snapshot, currency)
            columns = st.columns(3)
            for column, (label, value) in zip(columns, fields[:3]):
                column.metric(label, value)
            with st.container(key="sina-secondary-quote"):
                for start in range(3, len(fields), 3):
                    columns = st.columns(3)
                    for column, (label, value) in zip(columns, fields[start : start + 3]):
                        column.metric(label, value)
            if state.state in {"stale", "cached"}:
                st.warning(state.message)
            else:
                st.caption(state.message)
            fetched_text = state.fetched_at_label or "未提供"
            st.caption(f"数据来源：新浪财经 ｜ 抓取时间：{fetched_text} ｜ {state.cache_label}")
        st.button("检查自动行情", key=f"check-sina-{market_symbol.yahoo_symbol}")


def _source_selector(key_prefix: str) -> str:
    selected = st.selectbox("数据来源", SOURCE_OPTIONS, key=f"{key_prefix}-source")
    if selected == "其他":
        return st.text_input("自定义来源", key=f"{key_prefix}-custom-source").strip() or "其他"
    return selected


def _store_candidate(yahoo_symbol: str, snapshot: RealtimeSnapshot) -> None:
    st.session_state["realtime_candidate"] = (yahoo_symbol, snapshot)
    st.session_state["realtime_candidate_nonce"] = st.session_state.get("realtime_candidate_nonce", 0) + 1


def _candidate_for(yahoo_symbol: str) -> RealtimeSnapshot | None:
    stored = st.session_state.get("realtime_candidate")
    if isinstance(stored, tuple) and len(stored) == 2 and stored[0] == yahoo_symbol:
        return stored[1]
    return None


def _confirmed_for(yahoo_symbol: str) -> RealtimeSnapshot | None:
    snapshots = st.session_state.get("confirmed_realtime_snapshots", {})
    return _confirmed_snapshot_from_store(snapshots, yahoo_symbol)


def _confirmed_snapshot_from_store(snapshots: object, yahoo_symbol: str) -> RealtimeSnapshot | None:
    snapshot = snapshots.get(yahoo_symbol) if isinstance(snapshots, dict) else None
    return snapshot if isinstance(snapshot, RealtimeSnapshot) and snapshot.confirmed else None


def _format_edit_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, float):
        # Hide harmless floating-point noise from unit conversion in editable fields.
        nearest_integer = round(value)
        if isclose(value, nearest_integer, rel_tol=1e-12, abs_tol=1e-9):
            return str(nearest_integer)
        return format(value, ".15g")
    return str(value)


def _render_manual_tab(market_symbol: MarketSymbol) -> None:
    with st.form("manual-realtime-form"):
        source = _source_selector("manual")
        columns = st.columns(3)
        values = {
            "current_price": columns[0].text_input("当前价（必填）"),
            "change_amount": columns[1].text_input("涨跌额（可选）"),
            "change_percent": columns[2].text_input("涨跌幅 %（可选，需包含 %）"),
            "open": columns[0].text_input("今开（可选）"),
            "high": columns[1].text_input("最高（可选）"),
            "low": columns[2].text_input("最低（可选）"),
            "previous_close": columns[0].text_input("昨收（可选）"),
            "volume": columns[1].text_input("成交量（可选，可含 股/手/万手）"),
            "turnover_amount": columns[2].text_input("成交额（可选，可含 万元/亿元）"),
            "turnover_rate": columns[0].text_input("换手率 %（可选，需包含 %）"),
            "timestamp": columns[1].text_input("数据时间（可选）", placeholder="2026-08-27 14:36:18"),
        }
        submitted = st.form_submit_button("生成手动输入预览")
    if submitted:
        snapshot = snapshot_from_values(
            market_symbol.user_symbol,
            market_symbol.market,
            source,
            "manual",
            {"ticker": market_symbol.user_symbol, **values},
        )
        _store_candidate(market_symbol.yahoo_symbol, ManualRealtimeProvider(snapshot).get_snapshot())


def _render_paste_tab(market_symbol: MarketSymbol) -> None:
    source = _source_selector("paste")
    text = st.text_area("粘贴行情文字", height=220, key="pasted-realtime-text")
    if st.button("解析粘贴文字", key="parse-pasted-text"):
        if not text.strip():
            st.warning("请先粘贴行情文字。")
        else:
            provider = PastedTextRealtimeProvider(text, market_symbol.user_symbol, market_symbol.market, source)
            _store_candidate(market_symbol.yahoo_symbol, provider.get_snapshot())


def _render_screenshot_tab(market_symbol: MarketSymbol) -> None:
    source = _source_selector("screenshot")
    available, unavailable_message = ocr_status()
    if not available:
        st.info(unavailable_message)
    uploaded = st.file_uploader("上传行情截图", type=("png", "jpg", "jpeg"), key="realtime-screenshot")
    st.caption(
        "为了提高识别率，建议截图尽量只包含股票行情区域，保证股票代码、现价、涨跌幅、今开、最高、最低等文字清晰可见。"
    )
    st.caption("截图只在本机处理，不会上传到外部 OCR 或 AI 服务。识别结果必须人工确认。")
    if st.button("识别截图并生成预览", key="extract-screenshot", disabled=not available):
        if uploaded is None:
            st.warning("请先上传 PNG、JPG 或 JPEG 截图。")
        else:
            result = extract_screenshot_text(uploaded.getvalue())
            if result.error:
                st.warning(result.error)
            else:
                provider = ScreenshotRealtimeProvider(
                    result.text, market_symbol.user_symbol, market_symbol.market, source
                )
                snapshot = provider.get_snapshot()
                snapshot = replace(
                    snapshot,
                    confidence=result.confidence,
                    warnings=(*snapshot.warnings, *result.warnings, "截图识别结果必须逐项人工核对。"),
                    raw_input_reference=result.text or None,
                )
                _store_candidate(market_symbol.yahoo_symbol, snapshot)


def _render_candidate_preview(market_symbol: MarketSymbol) -> None:
    candidate = _candidate_for(market_symbol.yahoo_symbol)
    if candidate is None:
        return
    st.subheader("待确认的实时行情预览")
    st.warning("此数据尚未确认，不会用于可信实时展示或任何下游分析。")
    if candidate.confidence is not None:
        confidence_label = "较高" if candidate.confidence >= 85 else "中等" if candidate.confidence >= 70 else "较低"
        st.caption(f"OCR 平均识别置信度：{candidate.confidence:.1f}%（{confidence_label}）；置信度不等于金融数据真实性。")
    if candidate.input_method == "screenshot":
        with st.expander("查看 OCR 原始识别结果"):
            st.caption("以下内容仅用于诊断，是多轮 OCR 的合并原文，不是已确认的金融数据。")
            st.code(candidate.raw_input_reference or "未识别到文本", language=None)
        if candidate.parser_diagnostics:
            st.caption("识别诊断：" + "；".join(item.message for item in candidate.parser_diagnostics))

    nonce = st.session_state.get("realtime_candidate_nonce", 0)
    prefix = f"preview-{market_symbol.yahoo_symbol}-{nonce}"
    columns = st.columns(3)
    values = {
        "ticker": columns[0].text_input("股票代码", _format_edit_value(candidate.ticker), key=f"{prefix}-ticker"),
        "company_name": columns[1].text_input("股票名称", _format_edit_value(candidate.company_name), key=f"{prefix}-name"),
        "current_price": columns[2].text_input("当前价", _format_edit_value(candidate.current_price), key=f"{prefix}-price"),
        "change_amount": columns[0].text_input("涨跌额", _format_edit_value(candidate.change_amount), key=f"{prefix}-change"),
        "change_percent": columns[1].text_input("涨跌幅（含 %）", f"{candidate.change_percent:g}%" if candidate.change_percent is not None else "", key=f"{prefix}-change-pct"),
        "open": columns[2].text_input("今开", _format_edit_value(candidate.open), key=f"{prefix}-open"),
        "high": columns[0].text_input("最高", _format_edit_value(candidate.high), key=f"{prefix}-high"),
        "low": columns[1].text_input("最低", _format_edit_value(candidate.low), key=f"{prefix}-low"),
        "previous_close": columns[2].text_input("昨收", _format_edit_value(candidate.previous_close), key=f"{prefix}-previous"),
        "volume": columns[0].text_input("成交量（股）", _format_edit_value(candidate.volume), key=f"{prefix}-volume"),
        "turnover_amount": columns[1].text_input("成交额（元）", _format_edit_value(candidate.turnover_amount), key=f"{prefix}-amount"),
        "turnover_rate": columns[2].text_input("换手率（含 %）", f"{candidate.turnover_rate:g}%" if candidate.turnover_rate is not None else "", key=f"{prefix}-turnover"),
        "timestamp": columns[0].text_input("数据时间", _format_edit_value(candidate.timestamp), key=f"{prefix}-time"),
    }
    source = columns[1].text_input("来源", candidate.source, key=f"{prefix}-source")
    columns[2].text_input("识别方式", METHOD_LABELS.get(candidate.input_method, candidate.input_method), disabled=True, key=f"{prefix}-method")

    edited = snapshot_from_values(
        market_symbol.user_symbol,
        market_symbol.market,
        source.strip() or "其他",
        candidate.input_method,
        values,
        confidence=candidate.confidence,
        raw_input_reference=candidate.raw_input_reference,
        warnings=candidate.warnings,
        parser_diagnostics=candidate.parser_diagnostics,
    )
    errors, warnings = validate_snapshot(edited)
    expected_code = market_symbol.yahoo_symbol.split(".", 1)[0]
    edited_code = edited.ticker.split(".", 1)[0]
    if edited_code != expected_code:
        errors = (*errors, f"预览股票代码 {edited.ticker} 与当前页面 {expected_code} 不一致，请核对。")
    for message in errors:
        st.error(message)
    for message in warnings:
        st.warning(message)

    confirm_column, discard_column = st.columns(2)
    if confirm_column.button("确认使用这组实时数据", disabled=bool(errors), key=f"{prefix}-confirm"):
        confirmed = confirm_snapshot(edited)
        snapshots = dict(st.session_state.get("confirmed_realtime_snapshots", {}))
        snapshots[market_symbol.yahoo_symbol] = confirmed
        st.session_state["confirmed_realtime_snapshots"] = snapshots
        st.session_state.pop("realtime_candidate", None)
        st.success("实时数据已确认，仅在当前页面会话中保存。")
        st.rerun()
    if discard_column.button("放弃本次预览", key=f"{prefix}-discard"):
        st.session_state.pop("realtime_candidate", None)
        st.rerun()


def _render_confirmed_snapshot(
    snapshot: RealtimeSnapshot, market_symbol: MarketSymbol, currency: str, automatic_price: float, levels: dict[str, float | None]
) -> None:
    st.subheader("已确认的用户实时行情")
    columns = st.columns(5)
    columns[0].metric("实时价", format_money(snapshot.current_price, currency))
    columns[1].metric("涨跌幅", f"{snapshot.change_percent:+.2f}%" if snapshot.change_percent is not None else "N/A")
    columns[2].metric("来源", f"{snapshot.source} · 用户提供")
    columns[3].metric("输入方式", METHOD_LABELS.get(snapshot.input_method, snapshot.input_method))
    columns[4].metric("状态", f"✓ 已确认 · {snapshot_age_status(snapshot.timestamp)}")
    st.caption(
        f"股票代码：{snapshot.ticker} ｜ 数据时间："
        f"{snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S') if snapshot.timestamp else '未提供'}"
    )

    if snapshot.current_price is not None:
        difference = snapshot.current_price - automatic_price
        difference_percent = difference / automatic_price * 100 if automatic_price else 0.0
        comparison_columns = st.columns(4)
        comparison_columns[0].metric("用户实时价", format_money(snapshot.current_price, currency))
        comparison_columns[1].metric("自动行情价（可能延迟）", format_money(automatic_price, currency))
        comparison_columns[2].metric("差值", format_money(difference, currency))
        comparison_columns[3].metric("差异百分比", f"{difference_percent:+.2f}%")
        if abs(difference_percent) > REALTIME_PRICE_DIFFERENCE_WARNING_PCT:
            st.warning("⚠ 用户实时数据与自动行情差异较大，请核对股票代码、数据时间及输入/识别结果。")

        comparisons = realtime_comparisons(snapshot, levels)
        position_columns = st.columns(5)
        labels = {"MA20": "距 MA20", "MA50": "距 MA50", "MA200": "距 MA200", "support": "距支撑位", "resistance": "距阻力位"}
        for column, key in zip(position_columns, labels):
            value = comparisons[key]
            column.metric(labels[key], f"{value:+.2f}%" if value is not None else "N/A")

    st.info("用户确认的实时价格仅用于实时行情展示和位置比较，不会修改基于历史行情计算的技术评分。")
    if st.button("清除实时数据", key=f"clear-{market_symbol.yahoo_symbol}"):
        snapshots = dict(st.session_state.get("confirmed_realtime_snapshots", {}))
        snapshots.pop(market_symbol.yahoo_symbol, None)
        st.session_state["confirmed_realtime_snapshots"] = snapshots
        st.rerun()


def render_ashare_realtime_panel(market_symbol: MarketSymbol, currency: str, summary: object) -> RealtimeSnapshot | None:
    """Render all user input methods and return only a confirmed snapshot."""
    with st.expander("实时行情补充"):
        _render_sina_automatic_quote(market_symbol, currency)
        st.write(
            "免费自动行情可能存在延迟。您可以从招商证券、同花顺、通达信、东方财富等行情软件"
            "手动输入、复制行情文字，或上传行情截图。所有识别结果都需要您确认后才会使用。"
        )
        st.caption("请勿提供券商用户名、密码、账号、持仓、交易记录或浏览器认证信息。")
        manual_tab, paste_tab, screenshot_tab = st.tabs(("手动输入", "粘贴行情文字", "上传行情截图"))
        with manual_tab:
            _render_manual_tab(market_symbol)
        with paste_tab:
            _render_paste_tab(market_symbol)
        with screenshot_tab:
            _render_screenshot_tab(market_symbol)
        _render_candidate_preview(market_symbol)

        confirmed = _confirmed_for(market_symbol.yahoo_symbol)
        if confirmed:
            levels = {
                "MA20": getattr(summary, "ma20", None),
                "MA50": getattr(summary, "ma50", None),
                "MA200": getattr(summary, "ma200", None),
                "support": getattr(summary, "support", None),
                "resistance": getattr(summary, "resistance", None),
            }
            _render_confirmed_snapshot(
                confirmed, market_symbol, currency, float(getattr(summary, "current_price")), levels
            )

        with st.container(border=True):
            st.subheader("数据来源")
            st.write("**历史行情：** Yahoo Finance / yfinance（自动行情可能延迟）")
            if confirmed:
                st.write(f"**实时行情：** {confirmed.source} · 用户{METHOD_LABELS.get(confirmed.input_method, confirmed.input_method)} · 已确认")
            else:
                st.write("**实时行情：** 尚无用户确认数据")
            st.write("**基本面：** Yahoo Finance / yfinance")
            st.write("**新闻：** Yahoo Finance / yfinance")
    return _confirmed_for(market_symbol.yahoo_symbol)
