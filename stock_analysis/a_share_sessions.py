"""A-share display-session rules evaluated exclusively in Beijing time."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")

PHASE_PRE_OPEN = "pre_open"
PHASE_MORNING = "morning"
PHASE_LUNCH_BREAK = "lunch_break"
PHASE_AFTERNOON = "afternoon"
PHASE_CLOSED = "closed"
PHASE_WEEKEND = "weekend"

SINA_PRIORITY_PHASES = frozenset((PHASE_PRE_OPEN, PHASE_MORNING, PHASE_AFTERNOON))

PHASE_MESSAGES = {
    PHASE_PRE_OPEN: "集合竞价 / 开盘前时段：优先新浪行情",
    PHASE_MORNING: "当前交易时段：优先新浪实时行情",
    PHASE_LUNCH_BREAK: "午间休市：当前优先 Yahoo 行情",
    PHASE_AFTERNOON: "当前交易时段：优先新浪实时行情",
    PHASE_CLOSED: "当前非交易时段：优先 Yahoo 行情",
    PHASE_WEEKEND: "当前非交易日：优先 Yahoo 行情",
}


def to_beijing_time(now: datetime | None = None) -> datetime:
    """Return an aware Asia/Shanghai datetime; reject ambiguous naive input."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(BEIJING_TIMEZONE)


def is_a_share_weekday(beijing_now: datetime) -> bool:
    """Central hook for a future official SSE/SZSE trading calendar."""
    return beijing_now.weekday() < 5


def get_a_share_market_phase(now: datetime | None = None) -> str:
    beijing_now = to_beijing_time(now)
    if not is_a_share_weekday(beijing_now):
        return PHASE_WEEKEND

    local_time = beijing_now.time().replace(tzinfo=None)
    if time(9, 15) <= local_time < time(9, 30):
        return PHASE_PRE_OPEN
    if time(9, 30) <= local_time < time(11, 30):
        return PHASE_MORNING
    if time(11, 30) <= local_time < time(13, 0):
        return PHASE_LUNCH_BREAK
    if time(13, 0) <= local_time < time(15, 0):
        return PHASE_AFTERNOON
    return PHASE_CLOSED


def is_a_share_trading_session(now: datetime | None = None) -> bool:
    return get_a_share_market_phase(now) in SINA_PRIORITY_PHASES


def a_share_phase_message(now: datetime | None = None) -> str:
    return PHASE_MESSAGES[get_a_share_market_phase(now)]
