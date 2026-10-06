import datetime
import json
import re
import urllib.request
from typing import List, Dict, Any

# Epic Games ストアの無料配布ページ自身が読んでいるデータ
EPIC_FREE_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=ja&country=JP&allowCountries=JP"

JST = datetime.timezone(datetime.timedelta(hours=9))


def _jst(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST)


def _free_offers(promotions: dict, key: str) -> list:
    """割引率0%（＝無料）の配布期間だけを取り出します。"""
    return [
        offer
        for group in (promotions or {}).get(key, [])
        for offer in group.get("promotionalOffers", [])
        if offer.get("discountSetting", {}).get("discountPercentage") == 0
    ]


def get_epic_free_games() -> List[Dict[str, Any]]:
    """Epic Games ストアで配布中・配布予定の無料ゲームを取得します。"""
    req = urllib.request.Request(EPIC_FREE_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            elements = json.load(response)["data"]["Catalog"]["searchStore"]["elements"]
    except Exception as e:
        print(f"Epic Games の取得中にエラーが発生しました: {e}")
        return []

    games = []
    for el in elements:
        promotions = el.get("promotions")
        current = _free_offers(promotions, "promotionalOffers")
        upcoming = _free_offers(promotions, "upcomingPromotionalOffers")
        if not current and not upcoming:
            continue

        mappings = (el.get("catalogNs") or {}).get("mappings") or [{}]
        slug = (el.get("productSlug") or mappings[0].get("pageSlug") or "").removesuffix("/home")
        orig = int(re.sub(r"\D", "", el["price"]["totalPrice"]["fmtPrice"]["originalPrice"]) or 0)
        offer = (current or upcoming)[0]
        start, end = _jst(offer["startDate"]), _jst(offer["endDate"])

        if current:
            kind, headline = "free_epic", "【Epic Games 無料配布中】"
            period = f"{end.month}月{end.day}日 {end:%H:%M} まで"
        else:
            kind, headline = "free_epic_next", "【Epic Games 次回の無料配布】"
            period = f"{start.month}月{start.day}日 {start:%H:%M} から"

        images = {img["type"]: img["url"] for img in el.get("keyImages", [])}
        image = images.get("OfferImageWide") or images.get("Thumbnail") or images.get("OgImage") or ""

        games.append({
            "id": f"epic-{el['id']}",
            "image": image,
            "title": el["title"],
            "url": f"https://store.epicgames.com/ja/p/{slug}" if slug else "https://store.epicgames.com/ja/free-games",
            "type": kind,
            "headline": headline,
            "original_price": orig,
            "discount_percent": 100 if current else 0,
            "price_info": f"無料（通常{orig:,}円）" if orig else "無料",
            "period": period,
            "source": "Epic Games",
        })
    # 配布中を先に並べる
    return sorted(games, key=lambda g: g["type"] != "free_epic")


if __name__ == "__main__":
    for g in get_epic_free_games():
        print(f"- {g['headline']} {g['title']} / {g['price_info']} / {g['period']} ({g['url']})")
