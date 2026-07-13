import time

from apps.api.rate_limit import RateLimiter


class TestRateLimiter:
    def test_allows_under_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is True

    def test_blocks_at_limit(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        assert limiter.check("ip1") is True
        assert limiter.check("ip1") is True
        assert limiter.check("ip1") is False

    def test_different_keys_tracked_independently(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.check("ip1") is True
        assert limiter.check("ip2") is True
        assert limiter.check("ip1") is False
        assert limiter.check("ip2") is False

    def test_window_expiry_allows_again(self):
        limiter = RateLimiter(max_requests=1, window_seconds=0.1)
        assert limiter.check("ip1") is True
        assert limiter.check("ip1") is False
        time.sleep(0.15)
        assert limiter.check("ip1") is True

    def test_cleanup_removes_stale_keys(self):
        limiter = RateLimiter(max_requests=5, window_seconds=0.1)
        limiter.check("stale-ip")
        time.sleep(0.15)
        limiter.cleanup()
        assert "stale-ip" not in limiter._hits

    def test_cleanup_keeps_recent_keys(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60)
        limiter.check("recent-ip")
        limiter.cleanup()
        assert "recent-ip" in limiter._hits

    def test_zero_max_requests_blocks_all(self):
        limiter = RateLimiter(max_requests=0, window_seconds=60)
        assert limiter.check("ip1") is False
