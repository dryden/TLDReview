"""Zero-dependency unit tests for TL;DReview core engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import patch

from core.tldreview_tool import (
    PlaceInfo,
    Review,
    _redact_api_key,
    clip_reviews_for_prompt,
    curate_reviews,
    extract_google_maps_url,
    extract_maps_data_id,
    format_markdown_output,
    infer_language,
    parse_raw_reviews,
    run_pipeline,
)


class TestTLDReviewCore(unittest.TestCase):
    def test_extract_google_maps_url_and_instagram_wrapper(self) -> None:
        text = "這家超好吃 (https://maps.app.goo.gl/abc123XYZ)!"
        self.assertEqual(extract_google_maps_url(text), "https://maps.app.goo.gl/abc123XYZ")

        ig_wrapped = (
            "https://l.instagram.com/?u=https%3A%2F%2Fwww.google.com%2Fmaps%2Fplace"
            "%2FTest%2Fdata%3D!3m1!1s0x3442abb6da9c9e1f%3A0x123456789abcdef0&e=AT1"
        )
        extracted = extract_google_maps_url(ig_wrapped)
        self.assertIsNotNone(extracted)
        self.assertIn("0x3442abb6da9c9e1f:0x123456789abcdef0", extracted or "")

    def test_extract_maps_data_id_prioritizes_1s_over_5s(self) -> None:
        url = (
            "https://www.google.com/maps/place/Sample/data=!4m6!3m5"
            "!5s0x1111111111111111:0x2222222222222222"
            "!1s0x3442abb6da9c9e1f:0x9999999999999999"
        )
        self.assertEqual(
            extract_maps_data_id(url),
            "0x3442abb6da9c9e1f:0x9999999999999999",
        )

    def test_infer_language_and_word_boundary(self) -> None:
        self.assertEqual(infer_language("106台北市大安區新生南路一段, 台灣"), "zh-tw")
        self.assertEqual(infer_language("1-1 Chiyoda, Tokyo, Japan"), "ja")
        # "Fukuoka" contains "uk" substring, must not match UK ("en") when JP is present
        self.assertEqual(infer_language("Hakata Ward, Fukuoka"), "en")
        self.assertEqual(infer_language("10 Downing St, London, UK"), "en")

    def test_redact_api_key_prevents_secret_leak(self) -> None:
        secret = "secret_serp_key_987654"
        raw = f"Request failed for https://serpapi.com/search.json?api_key={secret}&hl=zh-tw (key={secret})"
        masked = _redact_api_key(raw, secret)
        self.assertNotIn(secret, masked)
        self.assertIn("api_key=***", masked)

    def test_curate_reviews_prioritizes_guides_and_critical_reviews(self) -> None:
        now = datetime.now(timezone.utc)
        recent_iso = (now - timedelta(days=30)).isoformat()
        old_iso = (now - timedelta(days=900)).isoformat()

        r_generic = Review(author="UserA", text="Good", rating=5.0, published_at=recent_iso, like_count=0)
        r_liked = Review(author="UserB", text="Great", rating=5.0, published_at=recent_iso, like_count=5)
        r_critical = Review(
            author="UserC",
            text="五星送小菜，實際上湯頭超鹹又冷",
            rating=2.0,
            published_at=recent_iso,
            like_count=0,
        )
        r_guide = Review(
            author="GuideD",
            text="Detailed local guide review",
            rating=4.0,
            published_at=recent_iso,
            is_local_guide=True,
        )
        r_old_guide = Review(
            author="OldGuide",
            text="Outdated 3 years ago",
            rating=5.0,
            published_at=old_iso,
            is_local_guide=True,
        )

        # Feed in reverse priority order with >=10 recent items so padding doesn't trigger
        extras = [
            Review(author=f"Extra{i}", text="Normal", rating=4.0, published_at=recent_iso)
            for i in range(8)
        ]
        curated = curate_reviews([r_generic, r_liked, r_critical, r_guide, r_old_guide] + extras)

        self.assertEqual(curated[0].author, "GuideD")
        self.assertEqual(curated[1].author, "UserC")  # Critical <=3★ prioritized before generic 5★
        self.assertEqual(curated[2].author, "UserB")  # High likes next
        self.assertNotIn(r_old_guide, curated)

    def test_dedup_key_uses_review_id_across_languages(self) -> None:
        payload_zh = {
            "reviews": [
                {"review_id": "rev_001", "user": {"name": "Alice"}, "snippet": "牛肉麵超讚", "rating": 5}
            ]
        }
        payload_en = {
            "reviews": [
                {"review_id": "rev_001", "user": {"name": "Alice"}, "snippet": "Beef noodle is great", "rating": 5}
            ]
        }
        r_zh = parse_raw_reviews(payload_zh)[0]
        r_en = parse_raw_reviews(payload_en)[0]
        self.assertEqual(r_zh.dedup_key(), r_en.dedup_key())

    def test_smart_truncate_in_clip_reviews(self) -> None:
        long_text = "這是一段非常詳細的評論。" * 110 + "最後一句話還沒說完就被切斷"
        clipped = clip_reviews_for_prompt([Review(author="Tester", text=long_text, rating=4.0)])
        self.assertEqual(len(clipped), 1)
        _, out_text = clipped[0]
        self.assertTrue(out_text.endswith("。…"))

    def test_cache_reused_across_preferred_languages_and_old_guides_trigger_en_refetch(self) -> None:
        old_iso = (datetime.now(timezone.utc) - timedelta(days=1000)).isoformat()
        recent_iso = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()

        # 6 old Local Guides (>2 years) + 1 recent review -> should trigger English refetch!
        zh_reviews = [
            Review(
                author=f"OldG{i}",
                text=f"舊評論{i}",
                rating=5.0,
                published_at=old_iso,
                is_local_guide=True,
                review_id=f"old_{i}",
            )
            for i in range(6)
        ]
        en_reviews = [
            Review(
                author="IntlGuide",
                text="English local guide insight",
                rating=4.0,
                published_at=recent_iso,
                is_local_guide=True,
                review_id="en_1",
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ, {"TLDREVIEW_CACHE_DIR": tmpdir}
        ), patch(
            "core.tldreview_tool.fetch_serpapi_place_details",
            return_value=PlaceInfo(
                name="老張牛肉麵",
                address="台北市大安區, 台灣",
                types=["麵食餐廳"],
                rating_summary={"5": 100, "1": 20},
                average_rating=4.3,
                total_reviews=120,
            ),
        ) as mock_details, patch(
            "core.tldreview_tool.fetch_serpapi_reviews",
            side_effect=[
                (zh_reviews, 120, None, {}),
                (en_reviews, 120, None, {}),
            ],
        ) as mock_reviews:
            url = "https://www.google.com/maps/place/Test/data=!3m1!1s0x3442abb6da9c9e1f:0x123456789abcdef0"
            res1 = run_pipeline(url, api_key="dummy_key", preferred_lang="zh-TW", use_cache=True)
            self.assertEqual(res1["stats"]["fetch_language"], "zh-tw+en")
            self.assertEqual(mock_reviews.call_count, 2)

            # Second call with a DIFFERENT preferred_lang ("en") should hit the same cache
            res2 = run_pipeline(url, api_key="dummy_key", preferred_lang="en", use_cache=True)
            self.assertTrue(res2.get("cached"))
            self.assertEqual(res2["stats"]["preferred_summary_language"], "en")
            self.assertEqual(mock_details.call_count, 1)

            md = format_markdown_output(res2, include_agent_instructions=True)
            self.assertIn("📈 星級分布: 5★(100) | 1★(20)", md)
            self.assertIn("TL;DReview 分析與排版規範", md)

    def test_resolve_short_url_unwraps_eu_consent_links(self) -> None:
        from core.tldreview_tool import resolve_short_url

        consent_url = (
            "https://consent.google.com/m?continue=https%3A%2F%2Fwww.google.com"
            "%2Fmaps%2Fplace%2FTest%2Fdata%3D!3m1!1s0x3442abb6da9c9e1f%3A0x123456789abcdef0"
        )
        resolved = resolve_short_url(consent_url)
        self.assertEqual(
            resolved,
            "https://www.google.com/maps/place/Test/data=!3m1!1s0x3442abb6da9c9e1f:0x123456789abcdef0",
        )

    def test_http_get_json_retries_on_transient_error(self) -> None:
        import io
        import urllib.error
        from core.tldreview_tool import http_get_json

        class DummyResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self) -> bytes:
                return b'{"ok": true}'

        http_503 = urllib.error.HTTPError(
            url="https://serpapi.com/search.json?api_key=my_secret_key",
            code=503,
            msg="Service Unavailable",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"temporary overload"),
        )

        with patch("urllib.request.urlopen", side_effect=[http_503, DummyResp()]) as mock_open, patch(
            "time.sleep"
        ):
            data = http_get_json("https://serpapi.com/search.json", {"api_key": "my_secret_key"})
            self.assertEqual(data, {"ok": True})
            self.assertEqual(mock_open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
