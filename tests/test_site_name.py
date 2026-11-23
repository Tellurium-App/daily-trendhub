"""トップページの WebSite 構造化データ（JSON-LD）のテスト。

トップページを作る処理（aggregate_and_draft）は外へ通信するため、
ここでは通信をしない部品だけを直接確かめる。
- website_json_ld(): 純粋な文字列生成（通信なし）
- build_page(): HTML の組み立て（通信なし）
"""
import datetime as dt
import json
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trend_monitor"))
import main_aggregator as agg


def extract_json_ld(page: str) -> dict:
    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL
    )
    assert match is not None, "JSON-LD の script タグが見つかりません"
    return json.loads(match.group(1))


class SiteNameTest(unittest.TestCase):
    def test_website_json_ld_has_exact_required_fields(self):
        """中身は指定どおりの5項目だけ。検索ボックスなどは足さない。"""
        data = extract_json_ld(agg.website_json_ld())
        self.assertEqual(data, {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "TrendHub",
            "alternateName": ["Daily TrendHub", "Trend Hub"],
            "url": "https://daily-trendhub.com/",
        })
        self.assertEqual(len(data["alternateName"]), 2)
        self.assertNotIn("potentialAction", data)

    def test_website_json_ld_and_build_page_are_offline(self):
        """通信を禁じても、JSON-LD の生成とページの組み立てができる。"""
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            script = agg.website_json_ld()
            page = agg.build_page(
                title="t", description="d", canonical_url="https://daily-trendhub.com/",
                heading="h", date_label="x", game_cards_html="", gadget_cards_html="",
                archive_html="", depth=0, structured_data=script,
            )
        self.assertIn(script, page)

    def test_build_page_without_structured_data_has_no_json_ld(self):
        """structured_data を渡さない呼び出しには JSON-LD が出ない。

        日別・月間・ジャンル別・ゲーム別・About はこの呼び方なので、
        それらの出力は変わらない。
        """
        page = agg.build_page(
            title="t", description="d", canonical_url="u", heading="h",
            date_label="x", game_cards_html="", gadget_cards_html="",
            archive_html="", depth=0,
        )
        self.assertNotIn("application/ld+json", page)
        self.assertNotIn("schema.org", page)
        self.assertNotIn('"WebSite"', page)

    def test_build_page_with_structured_data_only_adds_the_script(self):
        """JSON-LD を足しても、題名・説明文・見た目に関わる部分は変わらない。

        script の行を除けば、渡さない場合の出力と1文字も変わらない。
        """
        kwargs = dict(
            title="t", description="d", canonical_url="https://daily-trendhub.com/",
            heading="h", date_label="x", game_cards_html="", gadget_cards_html="",
            archive_html="", depth=0,
        )
        before = agg.build_page(**kwargs)
        after = agg.build_page(structured_data=agg.website_json_ld(), **kwargs)
        self.assertIn("application/ld+json", after)
        self.assertIn('"name": "TrendHub"', after)
        script_line = f"    {agg.website_json_ld()}\n"
        self.assertIn(script_line, after)
        self.assertEqual(after.replace(script_line, ""), before)
        for line in (
            "<title>t</title>",
            '<meta name="description" content="d">',
            '<link rel="canonical" href="https://daily-trendhub.com/">',
            '<link rel="stylesheet" href="style.css">',
        ):
            self.assertIn(line, after)

    def test_game_pages_have_no_json_ld(self):
        """ゲーム別ページには JSON-LD が出ない。"""
        game = {
            "title": "テストゲーム",
            "url": "https://store.steampowered.com/app/100/",
            "sales": [
                {"start": dt.date(2026, 9, 1), "end": dt.date(2026, 9, 5),
                 "discount": 50, "price": "1000円", "listed_days": 5, "gap": None},
                {"start": dt.date(2026, 10, 1), "end": dt.date(2026, 10, 3),
                 "discount": 60, "price": "800円", "listed_days": 3, "gap": 20},
            ],
            "sale_disc": {},
            "record_start": dt.date(2026, 9, 1),
        }
        with tempfile.TemporaryDirectory() as docs_dir:
            page = agg.build_game_page(game, docs_dir, dt.date(2026, 10, 2), "100")
        self.assertNotIn("application/ld+json", page)
        self.assertNotIn("schema.org", page)


if __name__ == "__main__":
    unittest.main()
