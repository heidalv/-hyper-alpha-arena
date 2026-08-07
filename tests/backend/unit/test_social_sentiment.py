"""
SocialSentimentCollector 单元测试

覆盖:
- collect_all 返回结构正确
- 无 API key 时的优雅降级
- 缓存机制
- collect_flat 输出格式
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.unit
class TestSocialSentimentCollector:
    def test_instantiation(self):
        from backend.services.social_sentiment_collector import SocialSentimentCollector
        collector = SocialSentimentCollector()
        assert collector is not None

    def test_collect_all_returns_dict(self):
        from backend.services.social_sentiment_collector import SocialSentimentCollector
        collector = SocialSentimentCollector()
        # Without real API, all sources fail gracefully → neutral defaults
        result = collector.collect_all(["BTC"])
        assert "BTC" in result
        assert hasattr(result["BTC"], "social_score")

    def test_neutral_defaults_on_failure(self):
        from backend.services.social_sentiment_collector import SocialSentimentCollector, SentimentData
        collector = SocialSentimentCollector()
        result = collector.collect_all(["UNKNOWN_COIN_XYZ"])
        data = result["UNKNOWN_COIN_XYZ"]
        # Should be neutral defaults when all sources fail
        assert data.social_score == 50.0 or data.sources_available == 0

    def test_collect_flat_format(self):
        from backend.services.social_sentiment_collector import SocialSentimentCollector
        collector = SocialSentimentCollector()
        flat = collector.collect_flat(["BTC"])
        assert "BTC" in flat
        assert "social_score" in flat["BTC"]
        assert "news_sentiment" in flat["BTC"]
        assert "discussion_volume" in flat["BTC"]
        assert "sentiment_change_24h" in flat["BTC"]

    def test_cache_returns_same_result(self):
        from backend.services.social_sentiment_collector import SocialSentimentCollector
        collector = SocialSentimentCollector()
        # First call
        r1 = collector.collect_all(["BTC"])
        # Second call should use cache
        r2 = collector.collect_all(["BTC"])
        assert r1["BTC"].social_score == r2["BTC"].social_score
