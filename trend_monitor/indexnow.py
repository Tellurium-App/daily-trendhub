"""その日に更新したページを IndexNow で Bing などへ知らせます。

Google は IndexNow を使わない（Google には sitemap.xml と robots.txt で知らせる）。
鍵は公開してよい値で、docs/<KEY>.txt に同じ値を置いて所有を示す。
"""
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from main_aggregator import SITE_BASE_URL, archive_rel_path

KEY = "ef9993a5e05f62e0579d5d54ae17155d"


def main() -> None:
    today = datetime.date.today()
    urls = [
        f"{SITE_BASE_URL}/",
        f"{SITE_BASE_URL}/{archive_rel_path(today)}",
    ]
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
