"""Provider interfaces and conservative user-supplied realtime quote parsing."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import re
from typing import Callable


REALTIME_PRICE_DIFFERENCE_WARNING_PCT = 2.0


@dataclass(frozen=True)
class RealtimeSnapshot:
    """Normalized realtime quote shared by every present and future provider."""

    ticker: str
    market: str
    company_name: str | None = None
    current_price: float | None = None
    change_amount: float | None = None
    change_percent: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    turnover_amount: float | None = None
    turnover_rate: float | None = None
    timestamp: datetime | None = None
    source: str = "其他"
    source_type: str = "user_provided"
    input_method: str = "manual"
    confirmed: bool = False
    confidence: float | None = None
    raw_input_reference: str | None = None
    warnings: tuple[str, ...] = ()
    input_errors: tuple[str, ...] = ()
    parser_diagnostics: tuple["ParserDiagnostic", ...] = ()


@dataclass(frozen=True)
class ParsedNumber:
    """A parsed numeric value plus its explicit source unit."""

    value: float
    unit: str | None = None


@dataclass(frozen=True)
class ParserDiagnostic:
    """Why an expected OCR field could not be populated."""

    field: str
    status: str
    message: str


class MarketDataProvider(ABC):
    """Common interface for normalized realtime providers."""

    source_type = "user_provided"
    input_method = "unknown"

    @abstractmethod
    def get_snapshot(self) -> RealtimeSnapshot:
        """Return an unconfirmed normalized snapshot."""


class AutomaticHistoricalProvider(MarketDataProvider):
    """Adapter for the latest value already present in historical data."""

    source_type = "automatic"
    input_method = "historical_latest"

    def __init__(self, ticker: str, market: str, current_price: float):
        self.ticker = ticker
        self.market = market
        self.current_price = current_price

    def get_snapshot(self) -> RealtimeSnapshot:
        return RealtimeSnapshot(
            ticker=self.ticker,
            market=self.market,
            current_price=self.current_price,
            source="Yahoo Finance / yfinance",
            source_type=self.source_type,
            input_method=self.input_method,
        )


class ManualRealtimeProvider(MarketDataProvider):
    input_method = "manual"

    def __init__(self, snapshot: RealtimeSnapshot):
        self.snapshot = snapshot

    def get_snapshot(self) -> RealtimeSnapshot:
        return replace(self.snapshot, input_method=self.input_method, confirmed=False)


class PastedTextRealtimeProvider(MarketDataProvider):
    input_method = "pasted_text"

    def __init__(self, text: str, ticker: str, market: str, source: str):
        self.text = text
        self.ticker = ticker
        self.market = market
        self.source = source

    def get_snapshot(self) -> RealtimeSnapshot:
        return parse_brokerage_text(self.text, self.ticker, self.market, self.source, self.input_method)


class ScreenshotRealtimeProvider(PastedTextRealtimeProvider):
    """OCR text uses exactly the same conservative parser as pasted text."""

    input_method = "screenshot"

    def get_snapshot(self) -> RealtimeSnapshot:
        snapshot = super().get_snapshot()
        return replace(snapshot, parser_diagnostics=diagnose_brokerage_text(self.text, snapshot))


class FutureLicensedRealtimeProvider(MarketDataProvider):
    """Placeholder base class; licensed network calls are intentionally absent."""

    def get_snapshot(self) -> RealtimeSnapshot:
        raise NotImplementedError("V5.2 尚未实现授权实时行情接口。")


class IFindRealtimeProvider(FutureLicensedRealtimeProvider):
    pass


class TushareRealtimeProvider(FutureLicensedRealtimeProvider):
    pass


class TongdaXinRealtimeProvider(FutureLicensedRealtimeProvider):
    pass


class BrokerageAPIProvider(FutureLicensedRealtimeProvider):
    pass


NUMBER_PATTERN = r"[+-]?(?:\d+(?:,\d{3})*|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?"
UNIT_PATTERN = r"(?:万亿港元|万亿元人民币|亿元人民币|亿港元|万手|亿股|万股|亿元|万元|亿|万|手|股|元)?"


def parse_financial_number(raw: object, field: str) -> ParsedNumber | None:
    """Parse only explicit numbers/units appropriate for the requested field."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return ParsedNumber(value) if math.isfinite(value) else None
    text = str(raw).strip().replace("，", ",").replace("％", "%")
    match = re.fullmatch(rf"\s*({NUMBER_PATTERN})\s*({UNIT_PATTERN})\s*(%)?\s*", text)
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    unit = match.group(2) or None
    percent_sign = match.group(3)

    if field in {"change_percent", "turnover_rate"}:
        if not percent_sign:
            return None
        return ParsedNumber(number, "%")
    if percent_sign:
        return None

    if field == "volume":
        multipliers = {None: 1, "股": 1, "万股": 10_000, "亿股": 100_000_000, "手": 100, "万手": 1_000_000}
        if unit not in multipliers:
            return None
        return ParsedNumber(number * multipliers[unit], unit)
    if field == "turnover_amount":
        multipliers = {
            None: 1,
            "元": 1,
            "万": 10_000,
            "万元": 10_000,
            "亿": 100_000_000,
            "亿元": 100_000_000,
            "亿元人民币": 100_000_000,
            "亿港元": 100_000_000,
            "万亿元人民币": 1_000_000_000_000,
            "万亿港元": 1_000_000_000_000,
        }
        if unit not in multipliers:
            return None
        return ParsedNumber(number * multipliers[unit], unit)
    if unit:
        return None
    return ParsedNumber(number)


FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "current_price": ("现价", "最新价", "最新", "当前价"),
    "change_amount": ("涨跌额", "涨跌"),
    "change_percent": ("涨跌幅", "涨幅"),
    "open": ("今开", "开盘"),
    "high": ("最高",),
    "low": ("最低",),
    "previous_close": ("昨收",),
    "volume": ("成交量",),
    "turnover_amount": ("成交额",),
    "turnover_rate": ("换手率", "换手"),
}

FIELD_DISPLAY_NAMES = {
    "current_price": "现价",
    "change_amount": "涨跌额",
    "change_percent": "涨跌幅",
    "open": "今开",
    "high": "最高",
    "low": "最低",
    "previous_close": "昨收",
    "volume": "成交量",
    "turnover_amount": "成交额",
    "turnover_rate": "换手率",
}


def _extract_field(text: str, field: str, labels: tuple[str, ...]) -> tuple[ParsedNumber | None, tuple[str, ...]]:
    candidates: list[ParsedNumber] = []
    for label in sorted(labels, key=len, reverse=True):
        pattern = rf"(?m)(?:^|\s){re.escape(label)}\s*[:：]?\s*({NUMBER_PATTERN}\s*{UNIT_PATTERN}\s*%?)"
        for raw_value in re.findall(pattern, text):
            parsed = parse_financial_number(raw_value, field)
            if parsed is not None:
                candidates.append(parsed)
    unique = {(item.value, item.unit) for item in candidates}
    if len(unique) > 1:
        return None, (f"{labels[0]}识别到多个不同候选值，请人工核对。",)
    if candidates and field in {"volume", "turnover_amount"} and candidates[0].unit is None:
        return candidates[0], (f"{labels[0]}单位未明确，请人工确认其单位后再使用。",)
    return (candidates[0] if candidates else None), ()


def diagnose_brokerage_text(text: str, snapshot: RealtimeSnapshot) -> tuple[ParserDiagnostic, ...]:
    """Classify OCR/parser failures without guessing a missing financial value."""
    diagnostics: list[ParserDiagnostic] = []
    for field, labels in FIELD_LABELS.items():
        if getattr(snapshot, field) is not None:
            continue
        display_name = FIELD_DISPLAY_NAMES[field]
        ambiguity = any(labels[0] in warning and "多个不同候选值" in warning for warning in snapshot.warnings)
        if ambiguity:
            diagnostics.append(ParserDiagnostic(field, "D", f"{display_name}：识别到多个候选，请人工填写"))
            continue
        recognized_labels = [label for label in labels if re.search(re.escape(label), text, re.IGNORECASE)]
        if not recognized_labels:
            diagnostics.append(ParserDiagnostic(field, "A", f"{display_name}：未识别到字段标签"))
            continue
        label_pattern = "|".join(re.escape(label) for label in recognized_labels)
        nearby = re.search(rf"(?:{label_pattern})([^\n]{{0,50}})", text, re.IGNORECASE)
        nearby_text = nearby.group(1) if nearby else ""
        if not re.search(NUMBER_PATTERN, nearby_text):
            diagnostics.append(ParserDiagnostic(field, "B", f"{display_name}：已识别标签，但未识别到数值"))
        else:
            diagnostics.append(ParserDiagnostic(field, "C", f"{display_name}：存在原始数值，但格式无法解析"))
    return tuple(diagnostics)


def parse_timestamp(raw: object) -> datetime | None:
    """Parse common quote timestamps without inventing a missing date."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt.startswith("%H"):
                today = datetime.now().astimezone()
                parsed = parsed.replace(year=today.year, month=today.month, day=today.day)
            return parsed.astimezone()
        except ValueError:
            continue
    return None


def parse_brokerage_text(
    text: str, fallback_ticker: str, market: str, source: str, input_method: str = "pasted_text"
) -> RealtimeSnapshot:
    """Extract quote fields conservatively from copied text or OCR output."""
    normalized_text = text.strip()
    warnings: list[str] = []
    ticker_match = re.search(r"(?<!\d)(\d{6})(?!\d)", normalized_text)
    ticker = ticker_match.group(1) if ticker_match else fallback_ticker
    company_name = None
    if ticker_match:
        line_end = normalized_text.find("\n", ticker_match.end())
        suffix = normalized_text[ticker_match.end() : line_end if line_end >= 0 else len(normalized_text)].strip(" ：:-")
        if suffix and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9 ()（）·.-]{2,40}", suffix):
            company_name = suffix

    values: dict[str, float | None] = {}
    for field, labels in FIELD_LABELS.items():
        parsed, field_warnings = _extract_field(normalized_text, field, labels)
        values[field] = parsed.value if parsed else None
        warnings.extend(field_warnings)

    timestamp = None
    time_match = re.search(r"(?:(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s+)?(\d{1,2}:\d{2}(?::\d{2})?)", normalized_text)
    if time_match:
        timestamp = parse_timestamp(" ".join(part for part in time_match.groups() if part))

    return RealtimeSnapshot(
        ticker=ticker,
        market=market,
        company_name=company_name,
        timestamp=timestamp,
        source=source,
        source_type="user_provided",
        input_method=input_method,
        confirmed=False,
        raw_input_reference=normalized_text[:5000] or None,
        warnings=tuple(warnings),
        **values,
    )


def snapshot_from_values(
    ticker: str,
    market: str,
    source: str,
    input_method: str,
    values: dict[str, object],
    company_name: str | None = None,
    confidence: float | None = None,
    raw_input_reference: str | None = None,
    warnings: tuple[str, ...] = (),
    parser_diagnostics: tuple[ParserDiagnostic, ...] = (),
) -> RealtimeSnapshot:
    """Normalize editable UI values without silently correcting malformed input."""
    parsed: dict[str, float | None] = {}
    input_errors: list[str] = []
    field_labels = {
        "current_price": "当前价", "change_amount": "涨跌额", "change_percent": "涨跌幅",
        "open": "今开", "high": "最高", "low": "最低", "previous_close": "昨收",
        "volume": "成交量", "turnover_amount": "成交额", "turnover_rate": "换手率",
    }
    for field in (
        "current_price", "change_amount", "change_percent", "open", "high", "low",
        "previous_close", "volume", "turnover_amount", "turnover_rate",
    ):
        result = parse_financial_number(values.get(field), field)
        parsed[field] = result.value if result else None
        raw_value = values.get(field)
        if raw_value is not None and str(raw_value).strip() and result is None:
            input_errors.append(f"{field_labels[field]}格式无效，请输入明确数字和单位。")
    raw_timestamp = values.get("timestamp")
    parsed_timestamp = parse_timestamp(raw_timestamp)
    if raw_timestamp is not None and str(raw_timestamp).strip() and parsed_timestamp is None:
        input_errors.append("数据时间格式无效，请使用 YYYY-MM-DD HH:MM:SS 或 HH:MM:SS。")
    return RealtimeSnapshot(
        ticker=str(values.get("ticker") or ticker).strip().upper(),
        market=market,
        company_name=str(values.get("company_name") or company_name or "").strip() or None,
        timestamp=parsed_timestamp,
        source=source,
        source_type="user_provided",
        input_method=input_method,
        confirmed=False,
        confidence=confidence,
        raw_input_reference=raw_input_reference,
        warnings=warnings,
        input_errors=tuple(input_errors),
        parser_diagnostics=parser_diagnostics,
        **parsed,
    )


def validate_snapshot(snapshot: RealtimeSnapshot) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return blocking errors and non-blocking consistency warnings."""
    errors: list[str] = list(snapshot.input_errors)
    warnings = list(snapshot.warnings)
    if snapshot.current_price is None or snapshot.current_price <= 0:
        errors.append("当前价必须是大于 0 的有效数字。")
    for field, label in (
        ("open", "今开"), ("high", "最高"), ("low", "最低"), ("previous_close", "昨收"),
    ):
        value = getattr(snapshot, field)
        if value is not None and value <= 0:
            errors.append(f"{label}必须大于 0。")
    for field, label in (("volume", "成交量"), ("turnover_amount", "成交额"), ("turnover_rate", "换手率")):
        value = getattr(snapshot, field)
        if value is not None and value < 0:
            errors.append(f"{label}不能小于 0。")
    if snapshot.high is not None and snapshot.low is not None:
        if snapshot.high < snapshot.low:
            errors.append("最高价不能低于最低价。")
        if snapshot.open is not None and not snapshot.low <= snapshot.open <= snapshot.high:
            errors.append("今开必须位于最低价与最高价之间。")
        if snapshot.current_price is not None and not snapshot.low <= snapshot.current_price <= snapshot.high:
            warnings.append("当前价位于输入的最高价/最低价区间之外，请核对。")
    if snapshot.previous_close is not None and snapshot.current_price is not None:
        if snapshot.change_amount is not None:
            expected = snapshot.previous_close + snapshot.change_amount
            tolerance = max(0.02, snapshot.current_price * 0.001)
            if abs(expected - snapshot.current_price) > tolerance:
                warnings.append("当前价与昨收+涨跌额不一致，请核对。")
        if snapshot.change_percent is not None and snapshot.previous_close > 0:
            expected_percent = (snapshot.current_price / snapshot.previous_close - 1) * 100
            if abs(expected_percent - snapshot.change_percent) > 0.15:
                warnings.append("当前价、昨收与涨跌幅不一致，请核对。")
    return tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings))


def confirm_snapshot(snapshot: RealtimeSnapshot) -> RealtimeSnapshot:
    """Mark valid user-reviewed data as trusted for this session only."""
    errors, warnings = validate_snapshot(snapshot)
    if errors:
        raise ValueError("；".join(errors))
    return replace(snapshot, confirmed=True, warnings=warnings)


def snapshot_age_status(timestamp: datetime | None, now: datetime | None = None) -> str:
    if timestamp is None:
        return "时间未提供"
    current = now or datetime.now().astimezone()
    local_timestamp = timestamp.astimezone(current.tzinfo) if timestamp.tzinfo else timestamp.replace(tzinfo=current.tzinfo)
    if local_timestamp.date() != current.date():
        return "非当前交易日数据"
    minutes = max(0, (current - local_timestamp).total_seconds() / 60)
    if minutes <= 5:
        return "最新"
    if minutes <= 30:
        return "近期"
    if minutes <= 60:
        return "可能过期"
    return "已过期"


def realtime_comparisons(snapshot: RealtimeSnapshot, levels: dict[str, float | None]) -> dict[str, float | None]:
    """Calculate simple position differences without changing technical indicators."""
    if snapshot.current_price is None:
        return {key: None for key in levels}
    return {
        key: ((snapshot.current_price / value) - 1) * 100 if value not in (None, 0) else None
        for key, value in levels.items()
    }
