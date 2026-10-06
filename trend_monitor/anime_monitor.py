import json
import urllib.request
from typing import List, Dict, Any

ANILIST_API = "https://graphql.anilist.co"

# 放送中・日本制作・成人向け除外で、AniList の「話題度」順に取る。
# 話題度は AniList 利用者全体（海外含む）の直近の反応で、日本国内の視聴ランキングではない。
QUERY = """
{
  Page(perPage: 10) {
    media(type: ANIME, sort: TRENDING_DESC, status: RELEASING, countryOfOrigin: "JP", isAdult: false) {
      id
      siteUrl
      coverImage { extraLarge }
      title { native romaji }
      nextAiringEpisode { episode }
      averageScore
      genres
    }
  }
}
"""

# AniList のジャンルは英語の固定19種
GENRE_JA = {
    "Action": "アクション", "Adventure": "冒険", "Comedy": "コメディ", "Drama": "ドラマ",
    "Ecchi": "お色気", "Fantasy": "ファンタジー", "Horror": "ホラー", "Mahou Shoujo": "魔法少女",
    "Mecha": "ロボット", "Music": "音楽", "Mystery": "ミステリー", "Psychological": "サイコ",
    "Romance": "恋愛", "Sci-Fi": "SF", "Slice of Life": "日常", "Sports": "スポーツ",
    "Supernatural": "超常", "Thriller": "スリラー", "Hentai": "成人向け",
}


def get_anime_trends() -> List[Dict[str, Any]]:
    """AniList から、いま話題の放送中アニメを取得します。"""
    body = json.dumps({"query": QUERY}).encode("utf-8")
    req = urllib.request.Request(
        ANILIST_API, data=body,
        # Python 既定の User-Agent だと 403 で弾かれるので名乗る
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "TrendHub/1.0 (+https://tellurium-app.github.io/daily-trendhub/)"},
    )
    trends = []
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            media = json.load(response)["data"]["Page"]["media"]
    except Exception as e:
        print(f"AniList の取得中にエラーが発生しました: {e}")
        return trends

    for m in media:
        title = m["title"].get("native") or m["title"].get("romaji") or ""
        genres = "・".join(GENRE_JA.get(g, g) for g in m.get("genres", [])[:3])

        parts = [genres] if genres else []
        next_ep = (m.get("nextAiringEpisode") or {}).get("episode")
        if next_ep and next_ep > 1:
            parts.append(f"第{next_ep - 1}話まで放送")
        if m.get("averageScore"):
            parts.append(f"平均スコア {m['averageScore']}")

        trends.append({
            # Steam のアプリIDと数字が重なるので、履歴で混ざらないよう接頭辞を付ける
            "id": f"anilist-{m['id']}",
            "title": title,
            "url": m["siteUrl"],
            "image": (m.get("coverImage") or {}).get("extraLarge", ""),
            "type": "anime_trending",
            "headline": "【放送中アニメ 話題作】",
            "price_info": "",
            "description": " / ".join(parts),
            "source": "AniList",
        })
    return trends


if __name__ == "__main__":
    print("アニメのトレンド情報を取得中...")
    results = get_anime_trends()
    print(f"取得完了: {len(results)} 件")
    for r in results:
        print(f"- {r['title']} ({r['description']})")
