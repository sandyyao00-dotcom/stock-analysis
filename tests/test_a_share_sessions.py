"""Boundary tests for A-share display priority in Asia/Shanghai."""

from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from stock_analysis.a_share_sessions import (
    PHASE_AFTERNOON,
    PHASE_CLOSED,
    PHASE_LUNCH_BREAK,
    PHASE_MORNING,
    PHASE_PRE_OPEN,
    PHASE_WEEKEND,
    PHASE_MESSAGES,
    get_a_share_market_phase,
    is_a_share_trading_session,
    to_beijing_time,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def beijing_datetime(hour: int, minute: int, *, day: int = 27) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=BEIJING)


class AShareSessionTests(unittest.TestCase):
    def test_weekday_time_boundaries(self):
        cases = (
            (9, 14, PHASE_CLOSED, False),
            (9, 15, PHASE_PRE_OPEN, True),
            (9, 20, PHASE_PRE_OPEN, True),
            (9, 29, PHASE_PRE_OPEN, True),
            (9, 30, PHASE_MORNING, True),
            (11, 29, PHASE_MORNING, True),
            (11, 30, PHASE_LUNCH_BREAK, False),
            (12, 30, PHASE_LUNCH_BREAK, False),
            (12, 59, PHASE_LUNCH_BREAK, False),
            (13, 0, PHASE_AFTERNOON, True),
            (14, 59, PHASE_AFTERNOON, True),
            (15, 0, PHASE_CLOSED, False),
            (16, 0, PHASE_CLOSED, False),
            (20, 0, PHASE_CLOSED, False),
        )
        for hour, minute, phase, sina_priority in cases:
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                now = beijing_datetime(hour, minute)
                self.assertEqual(get_a_share_market_phase(now), phase)
                self.assertEqual(is_a_share_trading_session(now), sina_priority)

    def test_weekend_never_uses_sina_priority(self):
        saturday = datetime(2026, 8, 29, 10, 0, tzinfo=BEIJING)
        sunday = datetime(2026, 8, 30, 14, 0, tzinfo=BEIJING)
        for now in (saturday, sunday):
            with self.subTest(now=now):
                self.assertEqual(get_a_share_market_phase(now), PHASE_WEEKEND)
                self.assertFalse(is_a_share_trading_session(now))

    def test_aware_input_is_converted_to_beijing(self):
        utc_time = datetime(2026, 8, 27, 1, 20, tzinfo=timezone.utc)
        converted = to_beijing_time(utc_time)
        self.assertEqual(converted.tzinfo, BEIJING)
        self.assertEqual((converted.hour, converted.minute), (9, 20))
        self.assertEqual(get_a_share_market_phase(utc_time), PHASE_PRE_OPEN)

    def test_naive_input_is_rejected(self):
        with self.assertRaises(ValueError):
            get_a_share_market_phase(datetime(2026, 8, 27, 9, 30))

    def test_trading_session_copy_is_correct(self):
        incorrect_copy = "交易" + "优势"
        self.assertNotIn(incorrect_copy, "".join(PHASE_MESSAGES.values()))
        self.assertEqual(PHASE_MESSAGES[PHASE_MORNING], "当前交易时段：优先新浪实时行情")
        self.assertEqual(PHASE_MESSAGES[PHASE_AFTERNOON], "当前交易时段：优先新浪实时行情")


if __name__ == "__main__":
    unittest.main()
