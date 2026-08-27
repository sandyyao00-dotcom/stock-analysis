"""Regression tests for normalized realtime preview values."""

import unittest

from stock_analysis.providers import (
    ScreenshotRealtimeProvider,
    parse_brokerage_text,
    parse_financial_number,
    snapshot_from_values,
    validate_snapshot,
)


class RealtimeNumberParsingTests(unittest.TestCase):
    def test_chinese_turnover_unit_is_normalized(self):
        parsed = parse_financial_number("38.2亿", "turnover_amount")
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed.value, 3_820_000_000, delta=0.01)

    def test_scientific_normalized_turnover_is_accepted(self):
        parsed = parse_financial_number("3.82e+09", "turnover_amount")
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed.value, 3_820_000_000)

    def test_integer_normalized_turnover_is_accepted(self):
        parsed = parse_financial_number("3820000000", "turnover_amount")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.value, 3_820_000_000)

    def test_additional_explicit_chinese_units_remain_supported(self):
        self.assertEqual(parse_financial_number("382000万", "turnover_amount").value, 3_820_000_000)
        self.assertEqual(parse_financial_number("3.82亿元", "turnover_amount").value, 382_000_000)

    def test_malformed_ambiguous_turnover_is_rejected(self):
        self.assertIsNone(parse_financial_number("38.2亿万元", "turnover_amount"))
        self.assertIsNone(parse_financial_number("约38.2亿", "turnover_amount"))

    def test_original_pasted_quote_can_be_confirmed(self):
        text = """600519 贵州茅台
现价 1293.50
涨幅 +1.25%
今开 1281.00
最高 1298.80
最低 1278.20
昨收 1277.50
成交额 38.2亿
换手率 0.31%
"""
        parsed = parse_brokerage_text(text, "600519", "A股", "招商证券")
        editable_values = {
            "ticker": parsed.ticker,
            "current_price": str(parsed.current_price),
            "change_percent": f"{parsed.change_percent}%",
            "open": str(parsed.open),
            "high": str(parsed.high),
            "low": str(parsed.low),
            "previous_close": str(parsed.previous_close),
            "turnover_amount": "3820000000",
            "turnover_rate": f"{parsed.turnover_rate}%",
        }
        preview = snapshot_from_values(
            "600519", "A股", "招商证券", "pasted_text", editable_values
        )
        errors, _ = validate_snapshot(preview)
        self.assertEqual(errors, ())
        self.assertAlmostEqual(preview.turnover_amount, 3_820_000_000)

    def test_shared_numeric_path_accepts_scientific_notation(self):
        for field in (
            "volume",
            "current_price",
            "change_amount",
            "open",
            "high",
            "low",
            "previous_close",
        ):
            with self.subTest(field=field):
                self.assertIsNotNone(parse_financial_number("1.25e+03", field))
        self.assertIsNotNone(parse_financial_number("1.25e+01%", "turnover_rate"))

    def test_screenshot_conflicting_labeled_values_remain_blank(self):
        snapshot = ScreenshotRealtimeProvider(
            "600519\n现价 1293.50\n现价 1393.50", "600519", "A股", "测试截图"
        ).get_snapshot()
        self.assertIsNone(snapshot.current_price)
        diagnostic = next(item for item in snapshot.parser_diagnostics if item.field == "current_price")
        self.assertEqual(diagnostic.status, "D")

    def test_screenshot_diagnostics_distinguish_missing_label_and_value(self):
        snapshot = ScreenshotRealtimeProvider(
            "600519\n现价 --\n今开 1281.00", "600519", "A股", "测试截图"
        ).get_snapshot()
        statuses = {item.field: item.status for item in snapshot.parser_diagnostics}
        self.assertEqual(statuses["current_price"], "B")
        self.assertEqual(statuses["high"], "A")


if __name__ == "__main__":
    unittest.main()
