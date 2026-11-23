import ast
import contextlib
import csv
import datetime as dt
import io
import json
import re
from pathlib import Path
import shutil
import socket
import sys
import unittest
import uuid
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trend_monitor"))
import main_aggregator as agg
import indexnow


class GamePagesTest(unittest.TestCase):
    def setUp(self):
        # Python 3.13のWindows一時ディレクトリACLを避け、テスト配下に隔離する。
        self.root = Path(__file__).resolve().parent / (".tmp-game-pages-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.remove_test_root)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.csv = self.root / "data" / "trend_report.csv"
        self.csv.parent.mkdir()
        self.title = 'A & <B> "C"'
        self.rows = []
        self.add_row("other", "2026-08-01", 0, kind="gadget_new")
        self.add_row("100", "2026-09-01", 50, "900円")
        self.add_row("100", "2026-09-10", 40, "800円")
        self.add_row("100", "2026-09-10", 30, "850円", kind="game_top_seller", hour="17")
        self.add_row("100", "2026-10-01", 60, "700円")
        self.add_row("100", "2026-10-02", 40, "価格情報なし")
        self.add_row("100", "2026-10-03", 0, "1500円", kind="game_top_seller")
        self.add_row("200", "2026-09-01", 10)
        self.add_row("200", "2026-09-12", 10)
        self.add_row("300", "2026-09-01", 25, "", title="Zulu")
        self.add_row("300", "2026-10-02", 25, "", title="Zulu")
        for gid in ("../bad", "１２３"):
            self.add_row(gid, "2026-09-01", 10)
            self.add_row(gid, "2026-10-02", 10)
        self.save_csv()
        self.today = dt.date(2026, 10, 2)
        self.archive = self.docs / "2026/10/01/index.html"
        self.archive.parent.mkdir(parents=True)
        self.archive.write_text("PAST_SENTINEL", encoding="utf-8")
        self.month = self.docs / "monthly/2026/09/index.html"
        self.month.parent.mkdir(parents=True)
        self.month.write_text("MONTH_SENTINEL", encoding="utf-8")

    def remove_test_root(self):
        root = self.root.resolve()
        self.assertEqual(root.parent, Path(__file__).resolve().parent)
        self.assertTrue(root.name.startswith(".tmp-game-pages-"))
        shutil.rmtree(root)

    def add_row(self, gid, day, discount, price="1000円", kind="game_sale", title=None, hour="16"):
        self.rows.append(dict(zip(agg.CSV_HEADER, [
            gid, kind, f"{discount}%OFF", title or self.title, price,
            f"https://store.steampowered.com/app/{gid}/?a=1&b=2", "Steam Store",
            f"{day} {hour}:00:00", "2000", str(discount),
        ])))

    def save_csv(self):
        with self.csv.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=agg.CSV_HEADER)
            writer.writeheader()
            writer.writerows(self.rows)

    def pages(self):
        return agg.load_game_pages(str(self.csv))

    def generate(self):
        return agg.write_game_pages(str(self.csv), str(self.docs), self.today)

    def generated_html(self, gid="100"):
        return (self.docs / agg.game_rel_path(gid) / "index.html").read_text(encoding="utf-8")

    def test_only_multiple_sales_and_ascii_numeric_ids(self):
        pages = self.generate()
        self.assertEqual(set(pages), {"100", "300"})
        self.assertEqual(len(list((self.docs / "game/steam/app").glob("*/index.html"))), 2)
        self.assertFalse((self.docs / "game/steam/app/200/index.html").exists())

    def test_gap_is_exclusive_and_listed_days_are_unique(self):
        game = self.pages()["100"]
        self.assertEqual([s["gap"] for s in game["sales"]], [None, 20])
        self.assertEqual([s["listed_days"] for s in game["sales"]], [2, 2])
        self.assertEqual(game["record_start"], dt.date(2026, 8, 1))

    def test_sale_gap_boundary_uses_existing_rule(self):
        start = dt.date(2026, 9, 1)
        boundary = start + dt.timedelta(days=agg.SALE_GAP_DAYS + 1)
        separate = boundary + dt.timedelta(days=agg.SALE_GAP_DAYS + 2)
        episodes = agg.sale_episodes({start.isoformat(): 10, boundary.isoformat(): 20, separate.isoformat(): 30})
        self.assertEqual(episodes, [(start, boundary, 20), (separate, separate, 30)])

    def test_final_sale_price_and_missing_price(self):
        pages = self.pages()
        self.assertEqual(pages["100"]["sales"][0]["price"], "850円")
        self.assertEqual(pages["100"]["sales"][0]["discount"], 50)
        self.assertEqual(pages["100"]["sales"][1]["price"], "—")
        self.assertEqual(pages["300"]["sales"][0]["price"], "—")

    def test_special_characters_and_page_metadata(self):
        self.generate()
        page = self.generated_html()
        self.assertIn('A &amp; &lt;B&gt; &quot;C&quot;のSteamセール履歴', page)
        self.assertNotIn(self.title, page)
        answer = ('TrendHub の記録（2026年8月1日以降・Steam の売上上位とセール上位に載った日だけ）では、'
                  'A &amp; &lt;B&gt; &quot;C&quot;は Steam で2回セールになっています。'
                  '最大割引率は60%OFF、記録上の最安値は850円（通常価格 2,000円）です。前のセールからの間隔は20日でした。')
        self.assertIn(f'<meta name="description" content="{answer}">', page)
        self.assertIn(f'<h1>A &amp; &lt;B&gt; &quot;C&quot;のSteamセール履歴</h1>\n            <p class="subtitle">{answer}</p>', page)
        self.assertIn('<title>A &amp; &lt;B&gt; &quot;C&quot;のSteamセール履歴：記録上2回・最大60%OFF・最安値850円（10月2日更新）', page)
        self.assertIn('href="https://daily-trendhub.com/game/steam/app/100/"', page)
        self.assertIn('property="og:url" content="https://daily-trendhub.com/game/steam/app/100/"', page)
        self.assertIn('href="../../../../style.css"', page)
        self.assertIn('?a=1&amp;b=2"', page)

    def test_summary_without_price_or_original(self):
        game = self.pages()["300"]
        summary = agg.game_summary(game)
        self.assertIn("Zuluは Steam で2回セールになっています。最大割引率は25%OFFです。", summary)
        self.assertNotIn("最安値", summary)
        self.assertNotIn("最安値", agg.build_game_page(game, str(self.docs), self.today, "300").split("</title>")[0])
        game["sales"][0]["original"] = None
        game["sales"][0]["price"] = "900円"
        self.assertIn("記録上の最安値は900円です。", agg.game_summary(game))

    def test_other_pages_keep_site_subtitle(self):
        page = agg.build_page(title="t", description="d", canonical_url="u", heading="h", date_label="x",
                              game_cards_html="", gadget_cards_html="", archive_html="", depth=0)
        self.assertIn(f'<p class="subtitle">{agg.SITE_SUBTITLE}</p>', page)

    def test_status_current_discount_and_newest_first(self):
        self.generate()
        page = self.generated_html()
        self.assertIn("現在セール中（40%OFF・2日目）", page)
        self.assertLess(page.index('<th scope="row">2'), page.index('<th scope="row">1'))
        inactive = agg.build_game_page(self.pages()["100"], str(self.docs), dt.date(2026, 10, 4), "100")
        self.assertIn("現在セール中の記録はありません（最後の記録は 10月2日）", inactive)

    def test_archive_links_only_for_existing_days(self):
        self.generate()
        page = self.generated_html()
        self.assertIn('href="/2026/10/01/"', page)
        self.assertNotIn('href="/2026/09/01/"', page)
        self.assertIn("1回目：2026年9月1日</li>", page)

    def test_sitemap_preserves_categories_and_adds_all_games_with_last_listed(self):
        dates = [dt.date(2026, 10, 1)]
        months = [(2026, 9)]
        agg.write_sitemap(str(self.docs), dates, months)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        def entries():
            root = ET.parse(self.docs / "sitemap.xml").getroot()
            return {u.find("s:loc", ns).text: u.find("s:lastmod", ns).text for u in root}

        before = entries()
        self.assertEqual(len(before), 8)  # top, about, four genres, daily, monthly
        agg.write_sitemap(str(self.docs), dates, months, self.pages())
        after = entries()
        self.assertEqual({url: after[url] for url in before}, before)
        self.assertEqual(len(after), 10)
        self.assertEqual(after[agg.SITE_BASE_URL + "/game/steam/app/100/"], "2026-10-03")

    def test_offline_rebuild_side_effects_and_repeatability(self):
        steam = self.docs / "game/steam/index.html"
        steam.parent.mkdir(parents=True)
        steam.write_text('    <main><a href="https://store.steampowered.com/app/100/" target="_blank" class="btn btn-primary">Steamで詳細を見る</a>    </main>', encoding="utf-8")
        before_csv = self.csv.read_bytes()
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            agg.rebuild_game_pages(str(self.csv), str(self.docs))
            before = {str(p): p.read_bytes() for p in self.docs.rglob("*") if p.is_file()}
            agg.rebuild_game_pages(str(self.csv), str(self.docs))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.docs.rglob("*") if p.is_file()})
        self.assertEqual(self.csv.read_bytes(), before_csv)
        self.assertEqual(self.archive.read_text(), "PAST_SENTINEL")
        self.assertEqual(self.month.read_text(), "MONTH_SENTINEL")
        self.assertFalse((self.docs / "index.html").exists())
        self.assertEqual(steam.read_text(encoding="utf-8").count('class="btn btn-secondary game-history-link"'), 1)

    def test_card_links_and_index_in_top_daily_and_steam_builds(self):
        cards = ''.join(f'<a href="https://store.steampowered.com/app/{gid}/" target="_blank" class="btn btn-primary">Steamで詳細を見る</a>' for gid in ("100", "200"))
        for depth, only in ((0, ""), (3, ""), (2, "steam")):
            page = agg.build_page("t", "d", "https://example.test/", "h", "date", cards, "", "", depth,
                                  only=only, game_pages=self.pages())
            self.assertIn('href="/game/steam/app/100/"', page)
            self.assertNotIn('href="/game/steam/app/200/"', page)
            self.assertEqual("セール履歴のあるゲーム" in page, only == "steam")
        index = agg.build_game_history_index(self.pages())
        self.assertLess(index.index("A &amp;"), index.index("Zulu"))
        self.assertIn('/game/steam/app/300/', index)

    def test_indexnow_today_all_pages_and_dry_run_never_sends(self):
        urls = indexnow.updated_urls(str(self.csv), self.today)
        self.assertEqual(urls, [agg.SITE_BASE_URL + "/", agg.SITE_BASE_URL + "/2026/10/02/", agg.SITE_BASE_URL + "/game/steam/app/100/", agg.SITE_BASE_URL + "/game/steam/app/300/"])
        all_urls = indexnow.updated_urls(str(self.csv), self.today, all_game_pages=True)
        self.assertEqual(len(all_urls), 4)
        with patch.object(indexnow, "__file__", str(self.root / "trend_monitor/indexnow.py")), \
             patch.object(sys, "argv", ["indexnow.py", "--dry-run", "--all-game-pages"]), \
             patch.object(indexnow.urllib.request, "urlopen", side_effect=AssertionError("must not submit")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            indexnow.main()
        self.assertIn("IndexNow dry-run: 4 URL", output.getvalue())

    def test_daily_pipeline_in_isolated_fixture_connects_same_generator(self):
        game = {"id": 100, "type": "game_sale", "title": self.title,
                "url": "https://store.steampowered.com/app/100/", "headline": "20%OFF",
                "final_price": 1000, "original_price": 1250, "discount_percent": 20}
        # 本番入口を起動せず、一時CSV・一時docs・全取得元モックで接続を検証する。
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(agg, "__file__", str(self.root / "trend_monitor/main_aggregator.py")))
            stack.enter_context(patch.object(socket, "socket", side_effect=AssertionError("network forbidden")))
            stack.enter_context(patch.object(agg, "complete_months", return_value=[]))
            for name in ("get_epic_free_games", "get_steam_free_trends", "get_steam_free_promos", "get_anime_trends", "get_gadget_trends"):
                stack.enter_context(patch.object(agg, name, return_value=[]))
            stack.enter_context(patch.object(agg, "get_steam_trends", return_value=[game]))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            agg.aggregate_and_draft()
        self.assertNotIn("エラー", output.getvalue())
        paths = [self.docs / "index.html", self.docs / agg.archive_rel_path(dt.date.today()) / "index.html",
                 self.docs / "game/steam/index.html"]
        for path in paths:
            self.assertIn('href="/game/steam/app/100/"', path.read_text(encoding="utf-8"))
        self.assertIn("セール履歴のあるゲーム", paths[-1].read_text(encoding="utf-8"))
        self.assertTrue((self.docs / "game/steam/app/100/index.html").exists())
        self.assertEqual(self.archive.read_text(), "PAST_SENTINEL")
        self.assertEqual(self.month.read_text(), "MONTH_SENTINEL")

    def test_control_game_ids_are_exactly_the_criteria_14(self):
        expected = {
            "2246340", "1364780", "1623730", "251570", "252490", "3240220",
            "3241660", "381210", "692569", "739630", "990080", "2050650",
            "1687950", "3375780",
        }
        self.assertEqual(set(agg.CONTROL_GAME_IDS), expected)
        self.assertEqual(len(agg.CONTROL_GAME_IDS), 14)

    def test_treatment_game_ids_do_not_overlap_control(self):
        treatment = {
            "32470", "78920", "275850", "489830", "648800", "1086940",
            "1091500", "1203620", "1462040", "1478500", "1771300", "1808500",
            "1817070", "1984270", "2060160", "2138330", "2161700", "2215430",
            "2399830", "2406770", "2455640", "2483190", "2552430", "2651280",
            "2679460", "2909400", "3101040", "3321460", "3357650", "3527290",
            "3768760", "3892270", "4001890", "4108000", "4594150", "4656000",
        }
        self.assertEqual(len(treatment), 36)
        self.assertFalse(treatment & set(agg.CONTROL_GAME_IDS))
        for gid in treatment:
            self.assertTrue(agg.is_treatment_game(gid))
            self.assertFalse(agg.is_control_game(gid))
        for gid in agg.CONTROL_GAME_IDS:
            self.assertTrue(agg.is_control_game(gid))
            self.assertFalse(agg.is_treatment_game(gid))

    def test_game_group_helpers_accept_str_and_int(self):
        for gid in ("2246340", 2246340, " 2246340 "):
            self.assertTrue(agg.is_control_game(gid))
            self.assertFalse(agg.is_treatment_game(gid))
        for gid in ("32470", 32470):
            self.assertTrue(agg.is_treatment_game(gid))
            self.assertFalse(agg.is_control_game(gid))
        # 新しくできたページ（比べる側に無いID）は直す側になる。
        self.assertTrue(agg.is_treatment_game("9999999"))

    def test_pr1_extra_sections_breadcrumb_only_for_treatment(self):
        game = self.pages()["100"]
        # 比べる側14本はすべて空のまま。
        for gid in agg.CONTROL_GAME_IDS:
            self.assertEqual(agg.game_page_extra_sections(gid, game), [])
        # 直す側はパンくず＋JSON-LDを1断片だけ返す。
        for gid in ("32470", "100"):
            sections = agg.game_page_extra_sections(gid, game)
            self.assertEqual(len(sections), 1)
            self.assertIn("BreadcrumbList", sections[0])
            self.assertIn('aria-label="パンくずリスト"', sections[0])
            self.assertIn('href="/game/steam/"', sections[0])
        # タイトルが無ければ何も足さない。
        self.assertEqual(agg.game_page_extra_sections("32470", None), [])
        self.assertEqual(agg.game_page_extra_sections("32470", {}), [])

    def test_pr1_breadcrumb_json_ld_matches_visible_breadcrumb(self):
        game = self.pages()["100"]
        page = agg.build_game_page(game, str(self.docs), self.today, "32470")
        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        self.assertEqual(data["@type"], "BreadcrumbList")
        items = data["itemListElement"]
        self.assertEqual([(i["position"], i["name"]) for i in items],
                         [(1, "トップ"), (2, "Steamセール"), (3, self.title)])
        self.assertEqual(items[2]["item"], agg.SITE_BASE_URL + "/game/steam/app/32470/")
        # 目に見えるパンくずも同じ階層で、現在地がタイトルになっている。
        self.assertIn('<a href="/">トップ</a>', page)
        self.assertIn('<a href="/game/steam/">Steamセール</a>', page)
        self.assertIn('<li aria-current="page">A &amp; &lt;B&gt; &quot;C&quot;</li>', page)
        # パンくずは <main> の先頭（セール履歴の節より前）に出る。
        self.assertLess(page.index("パンくずリスト"), page.index('class="game-history"'))

    def test_pr1_control_page_has_no_breadcrumb(self):
        game = self.pages()["100"]
        control = agg.build_game_page(game, str(self.docs), self.today, "2246340")
        treatment = agg.build_game_page(game, str(self.docs), self.today, "32470")
        self.assertNotIn("BreadcrumbList", control)
        self.assertNotIn("パンくずリスト", control)
        self.assertIn("BreadcrumbList", treatment)

        # canonical URL（gidで決まる）以外、比べる側と直す側の違いは
        # パンくず断片だけであること。断片そのものを除去して比べる。
        sections = agg.game_page_extra_sections("32470", game)
        self.assertEqual(len(sections), 1)
        stripped = treatment.replace(sections[0], "", 1)
        self.assertEqual(
            control.replace("/game/steam/app/2246340/", "/game/steam/app/GID/"),
            stripped.replace("/game/steam/app/32470/", "/game/steam/app/GID/"),
        )

    def test_pr1_treatment_counts_prices_match_records(self):
        self.generate()
        page = self.generated_html()  # gid "100" は比べる側に無いので直す側
        game = self.pages()["100"]
        self.assertIn("BreadcrumbList", page)
        # 記録から数え直した回数・最大割引率・最安値がページの要約と一致する。
        self.assertEqual(len(game["sales"]), 2)
        self.assertEqual(max(s["discount"] for s in game["sales"]), 60)
        low = agg.lowest_sale(game["sales"])
        self.assertEqual(agg.yen_value(low["price"]), 850)
        self.assertIn("記録上2回・最大60%OFF・最安値850円", page)

    def test_python_311_syntax_and_stylesheet_template_match(self):
        source = Path(agg.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 11))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "css_template" for t in node.targets):
                css = ast.literal_eval(node.value)
                self.assertEqual(css.rstrip("\n"), (Path(agg.__file__).parents[1] / "docs/style.css").read_text(encoding="utf-8").rstrip("\n"))
                break
        else:
            self.fail("CSS template missing")


if __name__ == "__main__":
    unittest.main()
