"""その日に更新したページを IndexNow で Bing などへ知らせます。

Google は IndexNow を使わない（Google には sitemap.xml と robots.txt で知らせる）。
鍵は公開してよい値で、docs/<KEY>.txt に同じ値を置いて所有を示す。
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from main_aggregator import SITE_BASE_URL, archive_rel_path, game_rel_path, load_game_pages

KEY = "ef9993a5e05f62e0579d5d54ae17155d"


def updated_urls(csv_path: str, today: datetime.date, all_game_pages: bool = False) -> list:
    """送信とは分離し、当日CSV掲載分（初回指定時は全件）のURLを選ぶ。"""
    urls = [
        f"{SITE_BASE_URL}/",
        f"{SITE_BASE_URL}/{archive_rel_path(today)}",
    ]
    pages = load_game_pages(csv_path)
    urls.extend(f"{SITE_BASE_URL}/{game_rel_path(gid)}" for gid, game in pages.items()
                if all_game_pages or today.isoformat() in game["listed_dates"])
    return urls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-game-pages", action="store_true",
                        help="初回登録用に対象ゲーム別ページを全件送る")
    parser.add_argument("--dry-run", action="store_true", help="送信せずURL一覧だけ表示する")
    args = parser.parse_args()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    urls = updated_urls(os.path.join(base_dir, "data", "trend_report.csv"),
                        datetime.date.today(), args.all_game_pages)
    if args.dry_run:
        print("\n".join(urls))
        print(f"IndexNow dry-run: {len(urls)} URL（送信なし）")
        return
    body = {
        "host": urllib.parse.urlparse(SITE_BASE_URL).netloc,
        "key": KEY,
        "keyLocation": f"{SITE_BASE_URL}/{KEY}.txt",
        "urlList": urls,
    }
    req = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        # 200 = 受理、202 = 受理（鍵の確認待ち）
        print(f"IndexNow: {res.status} {len(urls)} URL")


if __name__ == "__main__":
    main()
