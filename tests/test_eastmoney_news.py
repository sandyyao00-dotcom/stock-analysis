"""Deterministic EastMoney provider and market-routing tests."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

import pandas as pd

from stock_analysis.eastmoney_news import (
    DEFAULT_NEWS_LOOKBACK_DAYS,
    EASTMONEY_NEWS_CACHE_TTL_SECONDS,
    EASTMONEY_NEWS_FAILURE_COOLDOWN_SECONDS,
    EastMoneyNewsResult,
    StandardNewsItem,
    _reset_eastmoney_news_cache_for_tests,
    get_eastmoney_news,
    normalize_eastmoney_frame,
)
from stock_analysis.markets import MARKET_A_SHARE, MARKET_HK, MARKET_US, MarketSymbol
from stock_analysis.news import NewsResult, fetch_market_news, normalize_eastmoney_items


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def frame(rows: list[list[object]] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        rows
        or [["600519", "贵州茅台发布业绩报告", "中文正文，标点正常。", "2026-08-28 10:00:00", "东方财富", "https://example.cn/新闻/1"]],
        columns=("关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"),
    )


def source_item(**changes: object) -> StandardNewsItem:
    values = {
        "title": "贵州茅台发布业绩报告",
        "published_at": NOW - timedelta(hours=1),
        "source_name": "东方财富",
        "source_provider": "eastmoney",
        "url": "https://example.cn/1",
        "summary_or_content": "中文摘要",
        "symbol": "600519",
    }
    values.update(changes)
    return StandardNewsItem(**values)


class EastMoneyProviderTests(unittest.TestCase):
    def setUp(self):
        _reset_eastmoney_news_cache_for_tests()

    def tearDown(self):
        _reset_eastmoney_news_cache_for_tests()

    def test_successful_normalization_and_source_provider(self):
        result = get_eastmoney_news("600519", fetcher=lambda _: frame(), now=NOW)
        self.assertTrue(result.available)
        self.assertEqual(result.raw_item_count, 1)
        item = result.news[0]
        self.assertEqual(item.title, "贵州茅台发布业绩报告")
        self.assertEqual(item.source_name, "东方财富")
        self.assertEqual(item.source_provider, "eastmoney")
        self.assertEqual(item.summary_or_content, "中文正文，标点正常。")
        self.assertIsNotNone(item.published_at.utcoffset())

    def test_thirty_day_window_and_exact_boundary(self):
        cutoff_shanghai = (NOW - timedelta(days=DEFAULT_NEWS_LOOKBACK_DAYS)).astimezone(
            timezone(timedelta(hours=8))
        )
        just_old = cutoff_shanghai - timedelta(seconds=1)
        data = frame(
            [
                ["600519", "边界新闻", "保留", cutoff_shanghai.strftime("%Y-%m-%d %H:%M:%S"), "来源", "https://x/1"],
                ["600519", "过旧新闻", "丢弃", just_old.strftime("%Y-%m-%d %H:%M:%S"), "来源", "https://x/2"],
            ]
        )
        items = normalize_eastmoney_frame(data, "600519", now=NOW)
        self.assertEqual([item.title for item in items], ["边界新闻"])

    def test_unparseable_time_is_dropped_not_replaced(self):
        data = frame([["600519", "时间未知", "正文", "not-a-time", "来源", "https://x/1"]])
        self.assertEqual(normalize_eastmoney_frame(data, "600519", now=NOW), ())

    def test_duplicate_url_is_removed(self):
        data = frame(
            [
                ["600519", "标题一", "正文一", "2026-08-28 10:00:00", "来源", "https://x/same"],
                ["600519", "标题二", "正文二", "2026-08-28 11:00:00", "来源", "HTTPS://X/SAME"],
            ]
        )
        self.assertEqual(len(normalize_eastmoney_frame(data, "600519", now=NOW)), 1)

    def test_missing_url_uses_title_and_time_deduplication(self):
        data = frame(
            [
                ["600519", "同一 标题！", "正文一", "2026-08-28 10:00:00", "来源", None],
                ["600519", "同一标题", "正文二", "2026-08-28 10:00:00", "来源", None],
                ["600519", "同一标题", "正文三", "2026-08-28 11:00:00", "来源", None],
            ]
        )
        self.assertEqual(len(normalize_eastmoney_frame(data, "600519", now=NOW)), 2)

    def test_unicode_title_content_source_and_url_survive(self):
        item = normalize_eastmoney_frame(frame(), "600519", now=NOW)[0]
        self.assertEqual(item.title, "贵州茅台发布业绩报告")
        self.assertEqual(item.summary_or_content, "中文正文，标点正常。")
        self.assertEqual(item.source_name, "东方财富")
        self.assertEqual(item.url, "https://example.cn/新闻/1")

    def test_missing_optional_fields_are_none(self):
        data = pd.DataFrame({"新闻标题": ["有效标题"], "发布时间": ["2026-08-28 10:00:00"]})
        item = normalize_eastmoney_frame(data, "600519", now=NOW)[0]
        self.assertIsNone(item.source_name)
        self.assertIsNone(item.url)
        self.assertIsNone(item.summary_or_content)

    def test_missing_time_column_is_parse_failure(self):
        result = get_eastmoney_news(
            "600519", fetcher=lambda _: pd.DataFrame({"新闻标题": ["标题"]}), now=NOW
        )
        self.assertFalse(result.available)
        self.assertIn("ValueError", result.error)

    def test_key_error_is_isolated(self):
        result = get_eastmoney_news(
            "600519", fetcher=Mock(side_effect=KeyError("code")), now=NOW
        )
        self.assertFalse(result.available)
        self.assertEqual(result.news, ())
        self.assertIn("KeyError", result.error)

    def test_connection_error_is_isolated(self):
        result = get_eastmoney_news(
            "600519", fetcher=Mock(side_effect=ConnectionError("blocked")), now=NOW
        )
        self.assertFalse(result.available)
        self.assertIn("ConnectionError", result.error)

    def test_timeout_error_is_isolated(self):
        result = get_eastmoney_news(
            "600519", fetcher=Mock(side_effect=TimeoutError("slow")), now=NOW
        )
        self.assertFalse(result.available)
        self.assertIn("TimeoutError", result.error)

    def test_invalid_symbol_does_not_call_fetcher(self):
        fetcher = Mock(side_effect=AssertionError("must not be called"))
        result = get_eastmoney_news("INVALID", fetcher=fetcher, now=NOW)
        self.assertFalse(result.available)
        fetcher.assert_not_called()

    def test_empty_frame_is_unavailable(self):
        result = get_eastmoney_news("600519", fetcher=lambda _: frame([]).iloc[0:0], now=NOW)
        self.assertFalse(result.available)
        self.assertEqual(result.news, ())

    def test_cache_is_per_symbol_and_honors_ttl(self):
        fetcher = Mock(return_value=frame())
        get_eastmoney_news("600519", fetcher=fetcher, now=NOW)
        get_eastmoney_news(
            "600519", fetcher=fetcher, now=NOW + timedelta(seconds=EASTMONEY_NEWS_CACHE_TTL_SECONDS)
        )
        get_eastmoney_news("000001", fetcher=fetcher, now=NOW)
        self.assertEqual(fetcher.call_count, 2)
        self.assertEqual(fetcher.call_args_list[0].args, ("600519",))
        self.assertEqual(fetcher.call_args_list[1].args, ("000001",))

    def test_failure_cooldown_prevents_retry(self):
        fetcher = Mock(side_effect=TimeoutError("slow"))
        first = get_eastmoney_news("600519", fetcher=fetcher, now=NOW)
        second = get_eastmoney_news(
            "600519",
            fetcher=fetcher,
            now=NOW + timedelta(seconds=EASTMONEY_NEWS_FAILURE_COOLDOWN_SECONDS - 1),
        )
        self.assertFalse(first.available)
        self.assertEqual(second.error, first.error)
        fetcher.assert_called_once_with("600519")


class NewsMarketRoutingTests(unittest.TestCase):
    def setUp(self):
        self.a_share = MarketSymbol(MARKET_A_SHARE, "600519", "600519.SS", "CNY")
        self.us = MarketSymbol(MARKET_US, "AAPL", "AAPL", "USD")
        self.hk = MarketSymbol(MARKET_HK, "700", "0700.HK", "HKD")

    def test_a_share_prefers_eastmoney_and_does_not_call_yahoo(self):
        getter = Mock(return_value=EastMoneyNewsResult(True, (source_item(),), raw_item_count=1))
        yahoo = Mock(side_effect=AssertionError("Yahoo must not be called"))
        result = fetch_market_news(
            self.a_share, eastmoney_getter=getter, yahoo_fetcher=yahoo, now=NOW
        )
        self.assertEqual(result.source_provider, "eastmoney")
        self.assertEqual(result.articles[0].source_provider, "eastmoney")
        getter.assert_called_once_with("600519", now=NOW)
        yahoo.assert_not_called()

    def test_eastmoney_failure_falls_back_to_yahoo(self):
        expected = NewsResult(1, (), source_provider="yahoo")
        yahoo = Mock(return_value=expected)
        result = fetch_market_news(
            self.a_share,
            eastmoney_getter=Mock(return_value=EastMoneyNewsResult(False, (), "failed")),
            yahoo_fetcher=yahoo,
            now=NOW,
        )
        self.assertIs(result, expected)
        yahoo.assert_called_once_with("600519.SS", count=15)

    def test_unexpected_route_exception_falls_back_to_yahoo(self):
        expected = NewsResult(0, (), source_provider="yahoo")
        yahoo = Mock(return_value=expected)
        result = fetch_market_news(
            self.a_share,
            eastmoney_getter=Mock(side_effect=RuntimeError("unexpected")),
            yahoo_fetcher=yahoo,
            now=NOW,
        )
        self.assertIs(result, expected)
        yahoo.assert_called_once_with("600519.SS", count=15)

    def test_eastmoney_empty_news_falls_back_to_yahoo(self):
        yahoo = Mock(return_value=NewsResult(0, (), source_provider="yahoo"))
        fetch_market_news(
            self.a_share,
            eastmoney_getter=Mock(return_value=EastMoneyNewsResult(True, ())),
            yahoo_fetcher=yahoo,
            now=NOW,
        )
        yahoo.assert_called_once_with("600519.SS", count=15)

    def test_us_and_hk_never_call_eastmoney(self):
        for market_symbol in (self.us, self.hk):
            with self.subTest(market=market_symbol.market):
                getter = Mock(side_effect=AssertionError("EastMoney must not be called"))
                expected = NewsResult(0, (), source_provider="yahoo")
                yahoo = Mock(return_value=expected)
                result = fetch_market_news(
                    market_symbol, eastmoney_getter=getter, yahoo_fetcher=yahoo, now=NOW
                )
                self.assertIs(result, expected)
                getter.assert_not_called()
                yahoo.assert_called_once_with(market_symbol.yahoo_symbol, count=15)

    def test_unicode_article_reaches_existing_classifier_and_display_fields(self):
        article = normalize_eastmoney_items((source_item(),), now=NOW)[0]
        self.assertEqual(article.title, "贵州茅台发布业绩报告")
        self.assertEqual(article.summary_or_content, "中文摘要")
        self.assertEqual(article.publisher, "东方财富")
        self.assertEqual(article.source_provider, "eastmoney")
        self.assertTrue(article.category)
        self.assertTrue(article.event_label)


if __name__ == "__main__":
    unittest.main()
