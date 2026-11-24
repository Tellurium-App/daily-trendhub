import argparse
import os
import collections
import csv
import datetime
import glob
import html
import json
import re
import sys
import urllib.parse

# パスを追加して同一ディレクトリ内のモジュールを確実にインポートできるようにする
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from steam_monitor import get_steam_trends, get_steam_free_trends, get_steam_free_promos
from epic_monitor import get_epic_free_games
from gadget_monitor import get_gadget_trends
from anime_monitor import get_anime_trends

# アソシエイトIDが未設定のうちは tag を付けず、ただのAmazon検索リンクとして出す。
# 広告にならないので、この間はステマ規制の表記義務も発生しない。
# GitHub の Secrets に本物のIDを入れた時点で、リンクと表記が同時にアフィリエイト用へ切り替わる。
AMAZON_ASSOCIATE_ID = os.environ.get("AMAZON_ASSOCIATE_ID", "").strip()
IS_AFFILIATE = bool(AMAZON_ASSOCIATE_ID)

# Cloudflare Web Analytics のトークン。ページに埋め込まれて誰でも見える値なので、秘密情報ではない。
# 空のあいだは計測タグを出さない。Cookie を使わない計測なので同意バナーは不要。
CF_ANALYTICS_TOKEN = "cf082eb5e247424dba0d451ce0c78499"

ANALYTICS_NOTICE_HTML = """                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">アクセス解析について</h3>
                <p style="margin-bottom: 24px;">
                    当サイトでは、サイトの利用状況を把握するために Cloudflare Web Analytics を利用しています。Cookie は使用せず、閲覧者個人を特定する情報は収集しません。取得するのは、閲覧されたページ、参照元、ブラウザの種類、国・地域などの集計情報です。
                </p>"""

# 独自ドメインを取得したら SITE_BASE_URL を差し替えるだけで
# canonical / OGP / sitemap の URL が一斉に切り替わる。
SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://daily-trendhub.com"
).rstrip("/")

# 一覧に載せるアーカイブの最大件数
ARCHIVE_LIST_LIMIT = 30

# 2026-10-05 以前の日別ページは、タイトル・説明文を 2026-11-08 に今の型へ書き直した。
# sitemap の lastmod を書き直した日にして、再クロールを促す。
ARCHIVE_META_REVISED = (datetime.date(2026, 10, 5), datetime.date(2026, 11, 8))

# data/trend_report.csv の列。末尾2列は 2026-08-08 に追加（それ以前の行は空）。
CSV_HEADER = ["ID", "タイプ", "見出し", "タイトル", "価格情報", "URL", "情報源", "取得日時",
              "元価格", "割引率"]

# 「〇日連続ランクイン」を出す下限。短すぎると全部に付いて意味がなくなる。
STREAK_BADGE_MIN_DAYS = 3

# セール一覧から外れていた日数がこれ以下なら、同じセールが続いているとみなす。
# 7/18〜10/5 の実測で、間が1〜8日の再登場は割引率もほぼ同じ（一覧から一時的に外れただけ）、
# 12日以上空くと別のセールだった。
SALE_GAP_DAYS = 10

# 複数回のセールを比較できるゲームだけ、個別の履歴ページを作る。
GAME_PAGE_MIN_SALES = 2

# ゲーム別ページの群分け（CRITERIA.md「対象」より。実験の比べる側14本）。
# PR0では群分けだけを導入し、出力は変えない。比べる側に含まれないゲーム
# （直す側36本、および期間中に新しくできたページ）は直す側の作り方で出す。
CONTROL_GAME_IDS = frozenset({
    "2246340", "1364780", "1623730", "251570", "252490", "3240220",
    "3241660", "381210", "692569", "739630", "990080", "2050650",
    "1687950", "3375780",
})


def _normalize_game_id(gid) -> str:
    """SteamアプリIDを文字列に正規化する（str/intどちらも受け付ける）。"""
    return str(gid).strip()


def is_control_game(gid) -> bool:
    """比べる側（CRITERIA.mdの14本）のゲームかどうかを返す。"""
    return _normalize_game_id(gid) in CONTROL_GAME_IDS


def is_treatment_game(gid) -> bool:
    """直す側のゲームかどうかを返す。比べる側以外はすべて直す側とする。"""
    return not is_control_game(gid)


# Steam のセット商品（バンドル）なのに、記録の URL が /app/ になっている
# ことが確認できたゲーム。記録（CSV）には商品の種類の列が無く、URL は
# 収集時（steam_monitor.py）に /app/<ID>/ で組み立てられるため、記録から
# 種類を判別することはできない。ここに挙げた2本は、2026-11-24 に Steam
# のストアページで直接確かめたもの：
#   /bundle/32470/ → Cyberpunk 2077: Ultimate Edition（記録の題名と一致）
#   /app/32470/    → STAR WARS Empire at War - Gold Pack（別のゲーム）
#   /bundle/78920/ → Big Walk and PEAK（記録の題名と一致）
#   /app/78920/    → 該当するアプリが無い（ストアのトップへ飛ぶ）
# 収集側で API の type を見て直すのが本筋だが、steam_monitor.py は比べる
# 側の記録にも使われる共有ファイルなので、この PR では触らない。
STEAM_BUNDLE_IDS = frozenset({"32470", "78920"})


def game_store_url(gid, game: dict) -> str:
    """ゲーム別ページに出す Steam ストアへのリンク。

    比べる側は記録の URL のまま。直す側のうち、セット商品だと確かめられた
    ものだけ /app/ を /bundle/ に直す。
    """
    url = (game or {}).get("url") or ""
    gid = _normalize_game_id(gid)
    if is_treatment_game(gid) and gid in STEAM_BUNDLE_IDS:
        url = url.replace(f"store.steampowered.com/app/{gid}/",
                          f"store.steampowered.com/bundle/{gid}/", 1)
    return url


def _breadcrumb_json_ld(gid: str, title: str) -> str:
    """BreadcrumbList の JSON-LD。階層は記録にあるものだけ：
    トップ → Steamセール一覧 → このゲームのページ。"""
    items = [
        {"@type": "ListItem", "position": 1, "name": "トップ",
         "item": f"{SITE_BASE_URL}/"},
        {"@type": "ListItem", "position": 2, "name": "Steamセール",
         "item": f"{SITE_BASE_URL}/game/steam/"},
        {"@type": "ListItem", "position": 3, "name": title,
         "item": f"{SITE_BASE_URL}/{game_rel_path(gid)}"},
    ]
    data = {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": items}
    # </script> などで script 要素が壊れないよう、危険な文字を \u 逃げにする。
    return (json.dumps(data, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _game_webpage_json_ld(gid: str, game: dict) -> str:
    """直す側のゲーム別ページだけに head へ出す WebPage の JSON-LD。

    ページ自体（URL・名前・記録の最終日）と、主役のゲーム（名前と Steam
    ストアへのリンク）を機械可読にする。dateModified は生成日ではなく、
    記録にある最新のセールの最終日（ページの中身が最後に増えた日）。
    """
    title = game["title"]
    data = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "url": f"{SITE_BASE_URL}/{game_rel_path(gid)}",
        "name": f"{title}のSteamセール履歴",
        "inLanguage": "ja",
        "dateModified": game["sales"][-1]["end"].isoformat(),
        # isPartOf（WebSite） は入れない。サイト名の構造化データはトップ
        # ページだけに出す決まり（PR #3）で、ゲーム別ページには出さない。
        "mainEntity": {"@type": "VideoGame", "name": title,
                       "url": game_store_url(gid, game)},
    }
    return ('<script type="application/ld+json">'
            + (json.dumps(data, ensure_ascii=False)
               .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
            + "</script>")


def _average_text(values) -> str:
    """平均を小数第1位まで出し、末尾の .0 を落とす（55.0 → 55）。"""
    return f"{sum(values) / len(values):.1f}".rstrip("0").rstrip(".")


def _sale_trend_section(game: dict) -> str:
    """記録から計算できる「セールの傾向」の文章。数字はすべて記録のもの。

    既存の要約（回数・最大割引率・最安値・各回の間隔）とは重ならない、
    平均割引率・平均間隔・最初と最新のセール開始日を文章にする。
    予想や今後の日程は含まない。
    """
    sales = game["sales"]
    count = len(sales)
    avg_discount = _average_text([s["discount"] for s in sales])
    parts = [f"記録にある{count}回のセールの平均割引率は{avg_discount}%です。"]
    gaps = [s.get("gap") for s in sales if s.get("gap") is not None]
    if gaps:
        parts.append(f"前のセールからの間隔は平均{_average_text(gaps)}日でした。")
    first_start, latest_start = sales[0].get("start"), sales[-1].get("start")
    if first_start is not None and latest_start is not None:
        parts.append(f"最初のセールは{japanese_date(first_start)}に、"
                     f"最新のセールは{japanese_date(latest_start)}に始まっています。")
    return f"""
        <section class="sale-trend">
            <div class="section-title"><h2>セールの傾向</h2></div>
            <p>{''.join(parts)}</p>
        </section>"""


def _faq_pairs(game: dict, today: datetime.date) -> list:
    """「よくある質問」の質問と答えの組。答えの数字はすべて記録のもの。

    質問の形で聞かれたときに、そのまま答えとして抜き出せる形にする。
    次回のセールの予想は記録に無いので、質問にも答えにも入れない。
    答えは、それだけ抜き出しても記録の範囲が分かる文にする（どの答えにも
    記録の範囲＝起点の日付と「載った日だけ」を入れる）。

    「今、セール中ですか？」は聞かない。記録は売上上位・セール上位に
    載った日だけなので、記録に無いことはセール中でない根拠にならない
    （PR #4 の検査で、「セール中ではありません」と答えた30本のうち
    値段を照合できた28本中10本が実際にはセール中だった）。同じ理由で
    「最後のセールは〜」という言い方もしない。
    記録の項目が揃っていないゲーム（テスト用の最小限の辞書など）では、
    答えを作らず空の並びを返す。
    """
    sales = game.get("sales") or []
    if (not sales or "start" not in sales[0]
            or not game.get("sale_disc") or not game.get("record_start")):
        return []
    title = game["title"]
    scope = (f"TrendHub の記録（{japanese_date(game['record_start'])}以降・"
             f"Steam の売上上位とセール上位に載った日だけ）")
    pairs = []
    pairs.append((
        f"{title}はこれまでに何回セールになりましたか？",
        f"{scope}では、{title}はこれまでに{len(sales)}回セールになっています。"))
    max_discount = max(s["discount"] for s in sales)
    low = lowest_sale(sales)
    if low:
        answer = (f"{scope}での最安値は{yen_value(low['price']):,}円"
                  f"（{japanese_date(low['start'])}〜{japanese_date(low['end'])}のセール）で、"
                  f"最大割引率は{max_discount}%OFFです。")
        if low.get("original"):
            answer += f"最安値の回の通常価格は{low['original']:,}円でした。"
    else:
        answer = (f"{scope}では、価格の記録があるセールはありません。"
                  f"最大割引率は{max_discount}%OFFです。")
    pairs.append((f"{title}の最安値と最大割引率は？", answer))
    gaps = [s["gap"] for s in sales if s["gap"] is not None]
    if len(gaps) == 1:
        # 間隔が1つだけのときは、同じ数字を2回書かない。
        pairs.append((
            f"{title}のセールはどのくらいの間隔でありますか？",
            f"{scope}での前のセールからの間隔は、記録にある1回分で{gaps[0]}日です。"))
    elif gaps:
        pairs.append((
            f"{title}のセールはどのくらいの間隔でありますか？",
            f"{scope}での前のセールからの間隔は平均{_average_text(gaps)}日です。"
            f"これまでの間隔は{'日・'.join(str(g) for g in gaps)}日でした。"))
    return pairs


def _faq_json_ld(pairs: list) -> str:
    """FAQPage の JSON-LD。表示している質問・答えと一致させる。"""
    data = {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q,
                            "acceptedAnswer": {"@type": "Answer", "text": a}}
                           for q, a in pairs]}
    return (json.dumps(data, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _faq_section(game: dict, today: datetime.date) -> str:
    """直す側だけに出す「よくある質問」。質問と答えは _faq_pairs の組を
    表示と JSON-LD の両方に使う（食い違いを防ぐ）。"""
    pairs = _faq_pairs(game, today)
    if not pairs:
        return ""
    items = "".join(
        f'<div class="faq-item"><h3>{html.escape(q)}</h3><p>{html.escape(a)}</p></div>'
        for q, a in pairs)
    return f"""
        <section class="faq">
            <div class="section-title"><h2>よくある質問</h2></div>
            {items}
            <script type="application/ld+json">{_faq_json_ld(pairs)}</script>
        </section>"""


def _similarity_key(gid: str, game: dict, other_gid: str, other: dict) -> tuple:
    """記録から計算できる類似度の並べ替えキー。最大割引率の近さ、次に
    セール回数の近さ、最後にアプリIDの近さ。ジャンルなど記録に無い属性は
    使わない。"""
    cur_max = max(s["discount"] for s in game["sales"])
    cur_count = len(game["sales"])
    other_max = max(s["discount"] for s in other["sales"])
    other_count = len(other["sales"])
    try:
        id_gap = abs(int(other_gid) - int(gid))
    except (TypeError, ValueError):
        id_gap = 0
    return (abs(other_max - cur_max), abs(other_count - cur_count),
            id_gap, str(other_gid))


def _treatment_related_map(game_pages: dict) -> dict:
    """直す側全ページの内部リンク先マップ（gid → リンク先gidの並び）。

    1段階目：各ページが類似度のトップ3を選ぶ。2段階目：どこからも選ばれ
    なかったページ（受けリンク0本）を、類似度が最も近く出しリンクが
    4本未満のページのリンク先に加える。アプリID順に処理して決定的にする。
    結果として全ページの受けリンクは1本以上、1ページの出しリンクは最大
    4本になる。リンク先は直す側だけで、自分自身は含まない。
    """
    treatment = {str(g): other for g, other in game_pages.items()
                 if is_treatment_game(g) and other.get("sales")}
    ranked = {}
    for gid, game in treatment.items():
        candidates = sorted((o for o in treatment if o != gid),
                            key=lambda o: _similarity_key(gid, game, o, treatment[o]))
        ranked[gid] = candidates[:3]
    indegree = {gid: 0 for gid in treatment}
    for targets in ranked.values():
        for target in targets:
            indegree[target] += 1
    for orphan in sorted((g for g, n in indegree.items() if n == 0),
                         key=lambda g: (int(g) if g.isdigit() else 0, g)):
        hosts = sorted(
            (h for h in treatment if h != orphan),
            key=lambda h: (_similarity_key(h, treatment[h], orphan, treatment[orphan])[:3]
                           + (len(ranked[h]), h)))
        host = next((h for h in hosts if len(ranked[h]) < 4), hosts[0])
        ranked[host].append(orphan)
        indegree[orphan] += 1
    return ranked


def _related_treatment_games(gid: str, game: dict, game_pages: dict) -> list:
    """直す側の中で、gid のページがリンクするゲームの並びを返す。

    選び方は _treatment_related_map() に従う（類似度トップ3に、受けリンク
    0本のページへの補完を加えたもの）。比べる側は候補に入れない。
    自分自身も除く。
    """
    if not game_pages or not game.get("sales"):
        return []
    gid = _normalize_game_id(gid)
    link_map = _treatment_related_map(game_pages)
    if gid in link_map:
        targets = link_map[gid]
    else:
        # game_pages に含まれないページ（テスト用の架空IDなど）は、
        # 類似度トップ3をその場で計算する。補完の対象にはならない。
        candidates = sorted(
            (o for o, other in game_pages.items()
             if is_treatment_game(o) and other.get("sales")
             and _normalize_game_id(o) != gid),
            key=lambda o: _similarity_key(gid, game, str(o), game_pages[o]))
        targets = [str(o) for o in candidates[:3]]
    return [(target, game_pages[target]) for target in targets
            if target in game_pages]


# 直す側だけのスマホ用セール履歴表の見た目（修正4）。
# 共通のCSSテンプレートや .ranking-table / .game-history-table の決まりは
# 変えない。直す側の節だけに付く .treatment-history に限って効く。
# 640px以下では表を見出し行なしの積み重ね表示にし、6列すべての数字を
# 横スクロールなしで読めるようにする。見出しの文言は ::before の content
# で補い、セルの数字そのものは変えない。
_TREATMENT_MOBILE_TABLE_CSS = """
@media (max-width:640px){
.treatment-history .table-wrap{overflow-x:visible}
.treatment-history .game-history-table{min-width:0;width:100%;display:block}
.treatment-history .game-history-table thead{display:none}
.treatment-history .game-history-table tbody{display:block}
.treatment-history .game-history-table tr{display:block;border:1px solid var(--card-border);border-radius:12px;margin-bottom:12px;padding:2px 0}
.treatment-history .game-history-table th,.treatment-history .game-history-table td{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:8px 14px;border-bottom:1px solid var(--card-border);text-align:right;white-space:normal}
.treatment-history .game-history-table tr>:last-child{border-bottom:0}
.treatment-history .game-history-table th::before,.treatment-history .game-history-table td::before{flex:none;color:var(--text-secondary);font-weight:600;font-size:.85rem;text-align:left}
.treatment-history .game-history-table th[scope="row"]::before{content:"回"}
.treatment-history .game-history-table td:nth-child(2)::before{content:"期間"}
.treatment-history .game-history-table td:nth-child(3)::before{content:"掲載された日数"}
.treatment-history .game-history-table td:nth-child(4)::before{content:"最大割引率"}
.treatment-history .game-history-table td:nth-child(5)::before{content:"その回の価格"}
.treatment-history .game-history-table td:nth-child(6)::before{content:"前のセールからの間隔"}
}"""


def game_page_extra_sections(gid, game: dict = None, game_pages: dict = None) -> list:
    """直す側だけにページの先頭へ追加するセクションのHTML断片の並びを返す。

    ページの先頭に置くのはパンくず（と BreadcrumbList の JSON-LD）だけ。
    「セールの傾向」と「セールの傾向が近いゲーム」は表より下へ置くため、
    game_page_bottom_sections() が別に返す。比べる側は常に空のままにする。
    タイトルが無ければ（game が無い場合など）何も返さない。
    """
    if is_control_game(gid):
        return []
    title = (game or {}).get("title") or ""
    if not title:
        return []
    esc_title = html.escape(title)
    # パンくずのリンク色は、サイト内のほかのリンクと同じ決まり
    # （.ranking-table a / .archive-list a：color: var(--text-primary)、
    # ホバーで var(--accent-cyan)）に合わせる。色を指定しないと既定の青に
    # なり、暗い背景（--bg-dark: #0f111a）でほとんど読めない。
    # CSSテンプレートを変えると docs/style.css との一致が崩れるため、
    # 直す側だけに出るこの断片の中に <style> を置く。比べる側には出ない。
    breadcrumb = f"""
        <style>.breadcrumb a{{color:var(--text-primary);text-decoration:none}}.breadcrumb a:hover{{color:var(--accent-cyan)}}{_TREATMENT_MOBILE_TABLE_CSS}</style>
        <nav class="breadcrumb" aria-label="パンくずリスト" style="font-size: 0.9rem; margin-bottom: 16px;">
            <ol style="list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.4em;">
                <li><a href="/">トップ</a></li><li aria-hidden="true">/</li>
                <li><a href="/game/steam/">Steamセール</a></li><li aria-hidden="true">/</li>
                <li aria-current="page">{esc_title}</li>
            </ol>
        </nav>
        <script type="application/ld+json">{_breadcrumb_json_ld(gid, title)}</script>"""
    sections = [breadcrumb]
    return sections


def game_page_bottom_sections(gid, game: dict = None, game_pages: dict = None,
                              today: datetime.date = None) -> list:
    """直す側だけにセール履歴の表より下へ追加するセクションの並びを返す。

    ページの主役はセール履歴の表なので、「セールの傾向」「よくある質問」
    「セールの傾向が近いゲーム」は表より下に置く。比べる側は常に空のままにする。
    タイトルが無ければ（game が無い場合など）何も返さない。
    today は「よくある質問」の今の状態の答えに使う（生成日）。
    """
    if is_control_game(gid):
        return []
    title = (game or {}).get("title") or ""
    if not title:
        return []
    sections = []
    if game.get("sales"):
        sections.append(_sale_trend_section(game))
        faq = _faq_section(game, today or datetime.date.today())
        if faq:
            sections.append(faq)
    related = _related_treatment_games(gid, game, game_pages)
    if related:
        items = "".join(
            f'<li><a href="/{game_rel_path(other_gid)}">{html.escape(other["title"])}'
            f'（記録上{len(other["sales"])}回・最大{max(s["discount"] for s in other["sales"])}%OFF）</a></li>'
            for other_gid, other in related
        )
        sections.append(f"""
        <section class="related-games">
            <div class="section-title"><h2>セールの傾向が近いゲーム</h2></div>
            <p>記録上の最大割引率とセール回数が近い、ほかのゲームです。</p>
            <ul class="archive-list">{items}</ul>
        </section>""")
    return sections

# 月間ランキングの掲載件数
MONTHLY_TOP_N = 10

# ジャンル別ページ（URL は固定で、毎日中身を入れ替える）。(キー, パス, ナビの表示名)
# 日別ページは数日で古くなるが、固定 URL なら「今セール中のもの」を探す検索に同じページで応えられ、
# Search Console でもパスの先頭（/game/ /anime/ など）ごとに数字を分けて見られる。
GENRE_PAGES = [
    ("steam", "game/steam/", "Steamセール"),
    ("free", "game/free/", "無料ゲーム"),
    ("anime", "anime/", "アニメ"),
    ("gadget", "gadget/", "ガジェット"),
]

# 景表法（ステマ規制）が求める広告表示と、Amazonアソシエイト運営規約が求める表示。
# 文言は改定されうるので、申請前にアソシエイト・セントラルで最新版を確認すること。
AFFILIATE_NOTICE = "本ページはプロモーションを含みます。商品リンクの一部はアフィリエイトリンクです。"
AFFILIATE_DISCLOSURE = "Amazonのアソシエイトとして、TrendHubは適格販売により収入を得ています。"

def clean_keyword_for_amazon(title: str) -> str:
    """
    Amazonの検索でノイズになりそうな不要ワードを取り除き、商品名に近いクエリを抽出します。
    """
    noise_words = [
        "が登場か", "が登場", "を発表しました", "を発表", "を発売しました", "を発売", 
        "発売開始", "発売", "登場", "レビュー", "解禁", "値引き", "クーポン", "セール", "特価", "割引",
        "【", "】", "「", "」", "？", "?", "！", "!"
    ]
    
    clean_title = title
    for word in noise_words:
        clean_title = clean_title.replace(word, " ")
        
    # 前後の余計なスペースを調整
    clean_title = " ".join(clean_title.split())
    
    # 検索ヒット率向上のため、長すぎる場合は25文字にカット
    if len(clean_title) > 25:
        clean_title = clean_title[:25]
        
    return clean_title.strip()

def build_amazon_url(title: str) -> str:
    """Amazon検索URLを組み立てます。アソシエイトIDがある時だけ tag を付けます。"""
    encoded_kw = urllib.parse.quote(clean_keyword_for_amazon(title))
    url = f"https://www.amazon.co.jp/s?k={encoded_kw}"
    if IS_AFFILIATE:
        url += f"&tag={AMAZON_ASSOCIATE_ID}"
    return url


def parse_discount(row: dict) -> int:
    """CSV1行の割引率を返します。新しい列を優先し、無ければ見出しから読み取ります。"""
    raw = (row.get("割引率") or "").strip()
    if raw.isdigit():
        return int(raw)
    m = re.search(r"(\d+)%OFF", row.get("見出し") or "")
    return int(m.group(1)) if m else 0


def load_history(csv_path: str, type_prefix: str) -> dict:
    """CSVの全履歴から、タイプが type_prefix で始まる行のID別に「掲載された日」と「セール中だった日」を集めます。

    今日ぶんの行を書き込んだ後に呼ぶ前提。日付を集合で持つので、
    1日に複数回実行しても二重に数えられない。
    """
    history = collections.defaultdict(lambda: {"days": set(), "sale_disc": {}})
    if not os.path.exists(csv_path):
        return history

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not (row.get("タイプ") or "").startswith(type_prefix):
                continue
            gid, day = row.get("ID"), (row.get("取得日時") or "")[:10]
            if not gid or not day:
                continue
            history[gid]["days"].add(day)
            discount = parse_discount(row)
            if discount > 0:
                prev = history[gid]["sale_disc"].get(day, 0)
                history[gid]["sale_disc"][day] = max(prev, discount)
    return history


def consecutive_days(days: set, today: datetime.date) -> int:
    """today から1日ずつ遡って、何日連続で days に含まれるかを数えます。"""
    count, d = 0, today
    while d.isoformat() in days:
        count += 1
        d -= datetime.timedelta(days=1)
    return count


def sale_episodes(sale_disc: dict) -> list:
    """日別の割引率から、セールを (開始日, 終了日, 最大割引率) の並びに区切ります。

    間が SALE_GAP_DAYS 日以下なら、一覧から一時的に外れただけとして同じセールにまとめる。
    """
    episodes = []
    for day in sorted(datetime.date.fromisoformat(d) for d in sale_disc):
        disc = sale_disc[day.isoformat()]
        if episodes and (day - episodes[-1][1]).days - 1 <= SALE_GAP_DAYS:
            start, _, top = episodes[-1]
            episodes[-1] = (start, day, max(top, disc))
        else:
            episodes.append((day, day, disc))
    return episodes


def game_rel_path(gid: str) -> str:
    return f"game/steam/app/{gid}/"


def load_game_pages(csv_path: str) -> dict:
    """既存の区切り方を使い、個別ページの対象と各回のCSV情報を集める。"""
    history = load_history(csv_path, "game")
    info, sale_rows = {}, collections.defaultdict(dict)
    record_start = None
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            stamp = row.get("取得日時") or ""
            try:
                day = datetime.date.fromisoformat(stamp[:10])
            except ValueError:
                continue
            # 注記の起点はゲームに限らず、CSV全体の最初の日付。
            record_start = min(record_start, day) if record_start else day
            gid = row.get("ID") or ""
            if not re.fullmatch(r"[0-9]+", gid) or not (row.get("タイプ") or "").startswith("game"):
                continue
            if gid not in info or stamp >= info[gid][0]:
                info[gid] = (stamp, row)
            if parse_discount(row) > 0:
                previous = sale_rows[gid].get(day.isoformat())
                if previous is None or stamp >= previous[0]:
                    sale_rows[gid][day.isoformat()] = (stamp, row)

    pages = {}
    for gid in sorted(info, key=int):
        stat = history[gid]
        episodes = sale_episodes(stat["sale_disc"])
        if len(episodes) < GAME_PAGE_MIN_SALES:
            continue
        sales = []
        previous_end = None
        for start, end, discount in episodes:
            end_row = sale_rows[gid][end.isoformat()][1]
            price = (end_row.get("価格情報") or "").strip()
            original = (end_row.get("元価格") or "").strip()
            sales.append({
                "start": start, "end": end, "discount": discount,
                "listed_days": sum(start.isoformat() <= d <= end.isoformat() for d in stat["sale_disc"]),
                "price": "—" if not price or price == "価格情報なし" else price,
                "original": int(original) if original.isdigit() else None,
                "gap": (start - previous_end).days - 1 if previous_end else None,
            })
            previous_end = end
        row = info[gid][1]
        pages[gid] = {
            "title": row["タイトル"], "url": row["URL"], "sales": sales,
            "sale_disc": stat["sale_disc"], "record_start": record_start,
            "listed_dates": stat["days"],
            "last_listed": datetime.date.fromisoformat(max(stat["days"])),
        }
    return pages


def japanese_date(day: datetime.date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def yen_value(price: str):
    """「3742円」の形だけを数にする。それ以外（「—」など）は None。"""
    m = re.fullmatch(r"([0-9]+)円", price)
    return int(m.group(1)) if m else None


def lowest_sale(sales: list):
    """記録上の最安値の回。値段の無い回は除く。"""
    priced = [s for s in sales if yen_value(s["price"]) is not None]
    return min(priced, key=lambda s: yen_value(s["price"])) if priced else None


def game_summary(game: dict) -> str:
    """見出しの直下に置く答えの文。AI が一文だけ抜き出しても記録の条件が残るよう、条件を文頭に置く。"""
    sales = game["sales"]
    since = japanese_date(game["record_start"])
    max_discount = max(s["discount"] for s in sales)
    parts = [f"TrendHub の記録（{since}以降・Steam の売上上位とセール上位に載った日だけ）では、"
             f"{game['title']}は Steam で{len(sales)}回セールになっています。"]
    low = lowest_sale(sales)
    if low:
        regular = f"（通常価格 {low['original']:,}円）" if low.get("original") else ""
        parts.append(f"最大割引率は{max_discount}%OFF、記録上の最安値は{yen_value(low['price']):,}円{regular}です。")
    else:
        parts.append(f"最大割引率は{max_discount}%OFFです。")
    gaps = [f"{s['gap']}日" for s in sales if s["gap"] is not None]
    if gaps:
        parts.append(f"前のセールからの間隔は{'・'.join(gaps)}でした。")
    return "".join(parts)


def build_game_page(game: dict, docs_dir: str, today: datetime.date, gid: str,
                    game_pages: dict = None) -> str:
    """記録上のセール履歴。価格は各回の最終掲載日の行を使う。"""
    sales = game["sales"]
    latest = sales[-1]
    if today.isoformat() in game["sale_disc"]:
        run = (today - latest["start"]).days + 1
        status = f'現在セール中（{game["sale_disc"][today.isoformat()]}%OFF・{run}日目）'
    else:
        end = latest["end"]
        status = f"現在セール中の記録はありません（最後の記録は {end.month}月{end.day}日）"
    rows, daily_links = [], []
    for number, sale in reversed(list(enumerate(sales, 1))):
        gap = f'{sale["gap"]}日' if sale["gap"] is not None else "—"
        # 直す側だけ、日付に機械可読な datetime を付ける。表示する文字は
        # 変えない（<time> で囲むだけ）。
        if is_treatment_game(gid):
            period = (f'<time datetime="{sale["start"].isoformat()}">{japanese_date(sale["start"])}</time>'
                      f'〜<time datetime="{sale["end"].isoformat()}">{japanese_date(sale["end"])}</time>')
        else:
            period = f'{japanese_date(sale["start"])}〜{japanese_date(sale["end"])}'
        rows.append(f"""<tr><th scope="row">{number}</th><td class="num">{period}</td>
                    <td class="num">{sale['listed_days']}日</td><td class="num">{sale['discount']}%OFF</td>
                    <td>{html.escape(sale['price'])}</td><td class="num">{gap}</td></tr>""")
        start_path = archive_rel_path(sale["start"])
        if is_treatment_game(gid):
            label = (f'{number}回目：<time datetime="{sale["start"].isoformat()}">'
                     f'{japanese_date(sale["start"])}</time>')
        else:
            label = f'{number}回目：{japanese_date(sale["start"])}'
        if os.path.isfile(os.path.join(docs_dir, *start_path.strip("/").split("/"), "index.html")):
            label = f'<a href="/{start_path}">{label}</a>'
        daily_links.append(f"<li>{label}</li>")
    record_start = japanese_date(game["record_start"])
    # 直す側の節だけに目印のクラスを付ける。スマホ用の表の見た目（修正4）
    # はこの目印に限って効く。比べる側は従来のクラス名のままで、出力は
    # 1文字も変わらない。
    history_class = ("game-history treatment-history" if is_treatment_game(gid)
                     else "game-history")
    content = f"""
        <section class="{history_class}">
            <p class="sale-status">{status}</p>
            <div class="table-wrap" tabindex="0" role="region" aria-label="セール履歴の表">
                <table class="ranking-table game-history-table">
                    <thead><tr><th scope="col">回</th><th scope="col">期間（開始日〜最終掲載日）</th>
                    <th scope="col">掲載された日数</th><th scope="col">最大割引率</th>
                    <th scope="col">その回の価格</th><th scope="col">前のセールからの間隔</th></tr></thead>
                    <tbody>{''.join(rows)}</tbody>
                </table>
            </div>
            <p class="record-note">この表は TrendHub が {record_start}から毎日記録した範囲のものです。それ以前のセールは含みません。記録は Steam の売上上位・セール上位に載った日だけです。</p>
            <div class="section-title"><h2>各回の開始日の記録</h2></div>
            <ul class="archive-list">{''.join(daily_links)}</ul>
            <div class="btn-container history-actions">
                <a href="{html.escape(game_store_url(gid, game), quote=True)}" target="_blank" rel="noopener" class="btn btn-primary">Steamで詳細を見る</a>
                <a href="/game/steam/" class="btn btn-secondary">Steamセール一覧に戻る</a>
            </div>
        </section>"""
    # PR1の群分け：直す側だけの追加セクションを出す。パンくずはページの
    # 先頭（<main> の先頭）に置き、「セールの傾向」と「セールの傾向が近い
    # ゲーム」はページの主役であるセール履歴の表より下に置く。
    # 比べる側は両方とも空の並びを返すので、出力は変わらない。
    extra_sections = game_page_extra_sections(gid, game, game_pages)
    bottom_sections = game_page_bottom_sections(gid, game, game_pages, today=today)
    if extra_sections:
        content = "".join(extra_sections) + content
    if bottom_sections:
        content = content + "".join(bottom_sections)
    title = game["title"]
    max_discount = max(s["discount"] for s in sales)
    low = lowest_sale(sales)
    lowest = f"・最安値{yen_value(low['price']):,}円" if low else ""
    summary = game_summary(game)
    # 直す側の題名は日付を入れない。毎日変わる題名は、Google に「中身が
    # 毎日変わるページ」と見られ、lastmod や表示の安定を損なう。
    # 比べる側は従来どおり「（M月D日更新）」を残す。
    updated_suffix = "" if is_treatment_game(gid) else f"（{today.month}月{today.day}日更新）"
    return build_page(
        title=(f"{title}のSteamセール履歴：記録上{len(sales)}回・最大{max_discount}%OFF{lowest}"
               f"{updated_suffix} - TrendHub"),
        description=summary, subtitle=summary,
        canonical_url=f"{SITE_BASE_URL}/{game_rel_path(gid)}",
        heading=f"{title}のSteamセール履歴", date_label=f"{japanese_date(today)} 更新",
        game_cards_html="", gadget_cards_html="", archive_html="", depth=4, about_html=content,
        # 直す側だけ、ページとゲームの機械可読な情報（WebPage JSON-LD）を
        # head に出す。比べる側は structured_data を渡さないので変わらない。
        structured_data=(_game_webpage_json_ld(gid, game)
                         if is_treatment_game(gid) else ""),
    )


def write_game_pages(csv_path: str, docs_dir: str, today: datetime.date = None,
                     game_pages: dict = None) -> dict:
    """ネットワーク・CSV追記・日別や月間ページの出力をせず、ゲーム別ページだけ作る。"""
    today = today or datetime.date.today()
    pages = load_game_pages(csv_path) if game_pages is None else game_pages
    for gid, game in pages.items():
        path = os.path.join(docs_dir, *game_rel_path(gid).strip("/").split("/"), "index.html")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_game_page(game, docs_dir, today, gid, game_pages=pages))
    return pages


def link_game_history_cards(cards_html: str, game_pages: dict) -> str:
    """Steamボタンのあるカードにだけ、対象ゲームの履歴リンクを足す（再適用可能）。"""
    pattern = (r'(<a href="https://store\.steampowered\.com/app/([0-9]+)/[^"<>]*"'
               r'[^>]*class="btn btn-primary"[^>]*>Steamで詳細を見る</a>)'
               r'(?:\s*<a [^>]*class="btn btn-secondary game-history-link"[^>]*>セール履歴</a>)?')

    def replace(match):
        gid = match[2]
        link = (f'\n                        <a href="/{game_rel_path(gid)}" '
                'class="btn btn-secondary game-history-link">セール履歴</a>') if gid in game_pages else ""
        return match[1] + link

    return re.sub(pattern, replace, cards_html)


def build_game_history_index(game_pages: dict) -> str:
    if not game_pages:
        return ""
    items = "".join(
        f'<li><a href="/{game_rel_path(gid)}">{html.escape(game["title"])}</a></li>'
        for gid, game in sorted(game_pages.items(), key=lambda pair: (pair[1]["title"].casefold(), pair[0]))
    )
    return f"""<section class="history-games" id="game-sale-histories">
            <div class="section-title"><h2>セール履歴のあるゲーム</h2></div>
            <ul class="archive-list">{items}</ul>
        </section>"""


def refresh_game_history_index(docs_dir: str, game_pages: dict) -> None:
    """既存Steam一覧への履歴リンクだけ更新。日付・価格や過去ページには触れない。"""
    path = os.path.join(docs_dir, "game", "steam", "index.html")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        page = f.read()
    page = link_game_history_cards(page, game_pages)
    page = re.sub(r'<section class="history-games" id="game-sale-histories">.*?</section>',
                  "", page, flags=re.DOTALL)
    page = page.replace("    </main>", build_game_history_index(game_pages) + "    </main>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def rebuild_game_pages(csv_path: str, docs_dir: str) -> dict:
    """オフライン入口。ゲーム別HTMLとSteam一覧の履歴リンク、sitemapだけを更新。"""
    pages = write_game_pages(csv_path, docs_dir)
    refresh_game_history_index(docs_dir, pages)
    # 存在する月間ページを列挙し、未生成の月をsitemapに作り足さない。
    months = []
    for path in glob.glob(os.path.join(docs_dir, "monthly", "[0-9]" * 4, "[0-9]" * 2, "index.html")):
        y, m = path.replace("\\", "/").split("/")[-3:-1]
        months.append((int(y), int(m)))
    write_sitemap(docs_dir, collect_archive_dates(docs_dir), sorted(months, reverse=True), pages)
    return pages


def item_stats(item: dict, history: dict, today: datetime.date) -> dict:
    """1件ぶんの履歴指標をまとめます。Steamのストアページには出ていない情報。"""
    stat = history.get(str(item.get("id")), {"days": set(), "sale_disc": {}})
    episodes = sale_episodes(stat["sale_disc"])
    on_sale_today = bool(episodes) and episodes[-1][1] == today
    return {
        "listed_total": len(stat["days"]),
        "listed_run": consecutive_days(stat["days"], today),
        # 一覧から一時的に外れた日も含めて、今回のセールが始まってから何日目か
        "sale_run": (today - episodes[-1][0]).days + 1 if on_sale_today else 0,
        "sale_count": len(episodes) if on_sale_today else 0,
        "prev_sale": episodes[-2] if on_sale_today and len(episodes) >= 2 else None,
        "discount": item.get("discount_percent", 0) or 0,
    }


def build_history_badges(stats: dict) -> str:
    """履歴からしか分からない情報だけをバッジにします。"""
    badges = []
    if stats["listed_total"] <= 1:
        badges.append('<span class="badge badge-new">NEW 初登場</span>')
    if stats["sale_run"] >= 2:
        badges.append(f'<span class="badge badge-streak">SALE {stats["sale_run"]}日目</span>')
    if stats.get("sale_count", 0) >= 2:
        badges.append(f'<span class="badge badge-repeat">記録上{stats["sale_count"]}回目のセール</span>')
    # 掲載日数がセール日数と同じなら数字が二重になるだけなので、長い時だけ出す
    if stats["listed_run"] >= STREAK_BADGE_MIN_DAYS and stats["listed_run"] > stats["sale_run"]:
        badges.append(f'<span class="badge badge-regular">RANK {stats["listed_run"]}日連続</span>')
    return "".join(badges)


def short_title(title: str, limit: int = 18) -> str:
    """タイトル・説明文に入れるための短い作品名。商標記号とエディション名を落とす。"""
    name = re.sub(r"[™®©]", "", title).split(" - ")[0].strip()
    return name if len(name) <= limit else name[:limit] + "…"


def daily_highlights(games: list, epic_free: list) -> list:
    """その日のページを他の日と見分けるための目玉（最大割引のセールと、Epicの配布中タイトル）。"""
    highlights = []
    on_sale = [g for g in games if (g.get("discount_percent") or 0) > 0]
    if on_sale:
        top = max(on_sale, key=lambda g: g["discount_percent"])
        highlights.append(f"{short_title(top['title'])} {top['discount_percent']}%OFF")
    free_now = [g for g in epic_free if g["type"] == "free_epic"]
    if free_now:
        highlights.append(f"Epic無料 {short_title(free_now[0]['title'])}")
    return highlights


def card_image(item: dict, css_class: str = "card-img") -> str:
    """カード上端の画像。画像は取得元のサーバーから直接表示し、こちらには保存しない。"""
    if not item.get("image"):
        return ""
    return (f'<img class="{css_class}" src="{html.escape(item["image"], quote=True)}" '
            f'alt="{html.escape(item["title"], quote=True)}" loading="lazy" decoding="async">')


def build_sale_note(stats: dict, today: datetime.date) -> str:
    """前回のセールの終了日と割引率を1行で返します。2回目以降のセールの時だけ。"""
    prev = stats.get("prev_sale")
    if not prev:
        return ""
    _, end, top = prev
    return (f'<p class="sale-history">前回のセール：{end.month}月{end.day}日まで'
            f' 最大{top}%OFF（{(today - end).days}日前）</p>')


def pick_of_the_day(games: list, history: dict, today: datetime.date):
    """今日の一本と、その理由を返します。理由は必ず履歴データの裏付けがあるものだけ。"""
    if not games:
        return None, ""

    scored = [(g, item_stats(g, history, today)) for g in games]

    for g, s in scored:
        if s["listed_total"] <= 1 and s["discount"] > 0:
            return g, f"本日初めて上位に登場。現在 {s['discount']}%OFF のセール対象となっています。"

    g, s = max(scored, key=lambda x: x[1]["sale_run"])
    if s["sale_run"] >= 5:
        return g, f"当サイトの記録では、今回のセールは {s['sale_run']} 日目です。価格は予告なく変更される場合があります。"

    g, s = max(scored, key=lambda x: x[1]["listed_run"])
    if s["listed_run"] >= STREAK_BADGE_MIN_DAYS:
        return g, f"直近 {s['listed_run']} 日連続で売上上位にランクインしている定番のタイトルです。"

    g, s = max(scored, key=lambda x: x[1]["discount"])
    if s["discount"] > 0:
        return g, f"本日掲載されたセール対象タイトルの中で、最大の割引率（{s['discount']}%OFF）を記録しています。"

    return scored[0][0], "本日の売上上位ランキングよりピックアップしています。"


def build_pick_section(pick: dict, reason: str) -> str:
    """「今日の一本」セクションを組み立てます。"""
    if not pick:
        return ""

    price = pick.get("final_price", 0) or 0
    return f"""
        <section class="pick-section">
            <div class="section-title">
                <h2><span>📌</span> 本日のピックアップ</h2>
            </div>
            <div class="pick-card">
                {card_image(pick, "pick-img")}
                <h3>{html.escape(pick['title'])}</h3>
                <p class="pick-reason">{html.escape(reason)}</p>
                <div class="pick-price">{price:.0f}円</div>
                <a href="{html.escape(pick['url'], quote=True)}" target="_blank" class="btn btn-primary">Steamで詳細を見る</a>
            </div>
        </section>
"""


def complete_months(csv_path: str, today: datetime.date) -> list:
    """月初から記録があり、すでに終わっている月を新しい順に返します。"""
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        days = {(row.get("取得日時") or "")[:10] for row in csv.DictReader(f)}
    days.discard("")
    if not days:
        return []
    first = datetime.date.fromisoformat(min(days))
    if first.day == 1:
        y, m = first.year, first.month
    else:
        y, m = (first.year + 1, 1) if first.month == 12 else (first.year, first.month + 1)
    months = []
    while (y, m) < (today.year, today.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(months, reverse=True)


def month_last_day(y: int, m: int) -> datetime.date:
    return datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)


def monthly_ranking(csv_path: str, y: int, m: int) -> list:
    """その月に売上上位の一覧へ載っていた日数の多い順に、ゲームを並べて返します。"""
    prefix = f"{y:04d}-{m:02d}"
    listed = collections.defaultdict(set)
    sale = collections.defaultdict(dict)
    info = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            day = (row.get("取得日時") or "")[:10]
            if not day.startswith(prefix):
                continue
            gid, kind = row.get("ID"), row.get("タイプ") or ""
            if kind == "game_top_seller":
                listed[gid].add(day)
                info[gid] = (row["タイトル"].strip(), row["URL"])
            if kind.startswith("game") and parse_discount(row) > 0:
                sale[gid][day] = max(sale[gid].get(day, 0), parse_discount(row))

    ranked = sorted(listed, key=lambda g: (-len(listed[g]), min(listed[g])))
    return [{
        "title": info[g][0],
        "url": info[g][1],
        "days": len(listed[g]),
        "first": min(listed[g]),
        "last": max(listed[g]),
        "max_discount": max(sale[g].values()) if sale[g] else 0,
    } for g in ranked[:MONTHLY_TOP_N]]


def monthly_rel_path(y: int, m: int) -> str:
    return f"monthly/{y:04d}/{m:02d}/"


def build_monthly_content(y: int, m: int, ranking: list) -> str:
    """月間ランキングページの本文を組み立てます。"""
    days_in_month = month_last_day(y, m).day

    def md(iso):
        d = datetime.date.fromisoformat(iso)
        return f"{d.month}/{d.day}"

    rows = "".join(f"""
                <tr>
                    <td class="rank">{i}</td>
                    <td><a href="{html.escape(r['url'], quote=True)}" target="_blank">{html.escape(r['title'])}</a></td>
                    <td class="num">{r['days']}<span class="of">/{days_in_month}日</span></td>
                    <td class="num">{md(r['first'])}〜{md(r['last'])}</td>
                    <td class="num">{f"最大{r['max_discount']}%OFF" if r['max_discount'] else "—"}</td>
                </tr>""" for i, r in enumerate(ranking, 1))
    return f"""
        <section class="monthly-section">
            <div class="section-title">
                <h2><span>🏆</span> {y}年{m}月 Steam人気ゲームランキングTOP{len(ranking)}</h2>
                <p>Steamストアの売上上位一覧を毎日1回記録し、{y}年{m}月の{days_in_month}日間で掲載された日数が多い順に並べました。セール欄は、同じ月にセール一覧で確認できた最大の割引率です。</p>
            </div>
            <div class="table-wrap">
            <table class="ranking-table">
                <thead><tr><th>順位</th><th>タイトル</th><th>掲載日数</th><th>掲載期間</th><th>月内のセール</th></tr></thead>
                <tbody>{rows}
                </tbody>
            </table>
            </div>
        </section>
"""


def build_monthly_links(months: list, depth: int) -> str:
    """月間ランキングへのリンク一覧です。"""
    if not months:
        return ""
    prefix = "../" * depth
    items = "".join(
        f'<li><a href="{prefix}{monthly_rel_path(y, m)}">{y}年{m}月 Steam人気ゲームランキング</a></li>'
        for y, m in months
    )
    return f"""
        <section class="archive-section">
            <div class="section-title">
                <h2><span>🏆</span> 月間ランキング</h2>
                <p>1か月のあいだ、Steamの売上上位に長く載り続けたゲームのランキングです。</p>
            </div>
            <ul class="archive-list">
                {items}
            </ul>
        </section>
"""


def archive_rel_path(d: datetime.date) -> str:
    """日付からサイトルート起点のアーカイブパス（末尾スラッシュ付き）を組み立てます。"""
    return f"{d.year:04d}/{d.month:02d}/{d.day:02d}/"


def collect_archive_dates(docs_dir: str) -> list:
    """docs/YYYY/MM/DD/index.html を走査し、新しい順の日付一覧を返します。"""
    pattern = os.path.join(docs_dir, "[0-9]" * 4, "[0-9]" * 2, "[0-9]" * 2, "index.html")
    dates = set()
    for path in glob.glob(pattern):
        y, m, d = path.replace("\\", "/").split("/")[-4:-1]
        try:
            dates.add(datetime.date(int(y), int(m), int(d)))
        except ValueError:
            # 2026/02/30 のような実在しない日付のディレクトリは無視する
            continue
    return sorted(dates, reverse=True)


def build_archive_section(dates: list, depth: int, current: datetime.date = None) -> str:
    """過去アーカイブへの内部リンク一覧を組み立てます。depth はページの階層の深さ。"""
    prefix = "../" * depth
    items = [
        f'<li><a href="{prefix}{archive_rel_path(d)}">{d.strftime("%Y年%m月%d日")}のトレンド</a></li>'
        for d in dates[:ARCHIVE_LIST_LIMIT]
        if d != current
    ]
    if not items:
        return ""

    return f"""
        <section class="archive-section">
            <div class="section-title">
                <h2><span>🗂️</span> 過去のトレンド</h2>
                <p>日別のアーカイブから、過去のランキングやセール状況をさかのぼって見られます。</p>
            </div>
            <ul class="archive-list">
                {"".join(items)}
            </ul>
        </section>
"""


SITE_SUBTITLE = ("Steamのセール・売上上位ゲーム、Epic Gamesの無料配布と基本プレイ無料の人気作、"
                 "放送中アニメの話題作、ガジェット系メディアの新着ニュースを毎日自動集計してお届けする情報サイトです。")


def website_json_ld() -> str:
    """トップページだけに出す WebSite の構造化データ（JSON-LD）。

    Google の検索結果に出るサイト名を「TrendHub」にするためのもの。
    検索ボックスなどの項目は足さない。通信はしない。
    """
    data = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "TrendHub",
        "alternateName": ["Daily TrendHub", "Trend Hub"],
        "url": f"{SITE_BASE_URL}/",
    }
    return (
        '<script type="application/ld+json">'
        + json.dumps(data, ensure_ascii=False)
        + "</script>"
    )


def build_page(title: str, description: str, canonical_url: str, heading: str,
               date_label: str, game_cards_html: str, gadget_cards_html: str,
               archive_html: str, depth: int, pick_html: str = "",
               about_html: str = "", anime_cards_html: str = "",
               free_html: str = "", only: str = "", game_pages: dict = None,
               subtitle: str = None, structured_data: str = "") -> str:
    """1ページ分のHTMLを組み立てます。depth はサイトルートからの階層の深さ。

    only に GENRE_PAGES のキーを渡すと、そのジャンルの節だけを出す（ジャンル別ページ用）。
    """
    prefix = "../" * depth
    esc_title = html.escape(title)
    esc_desc = html.escape(description)
    if game_pages is not None:
        game_cards_html = link_game_history_cards(game_cards_html, game_pages)
        pick_html = link_game_history_cards(pick_html, game_pages)
        if only == "steam":
            archive_html += build_game_history_index(game_pages)

    # アフィリエイトIDが設定されている時だけ、広告表示とアソシエイト表示を出す
    notice_html = (
        f'\n    <div class="affiliate-notice"><div class="container">{html.escape(AFFILIATE_NOTICE)}</div></div>'
        if IS_AFFILIATE else ""
    )
    disclosure_html = (
        f'\n            <p class="disclosure">{html.escape(AFFILIATE_DISCLOSURE)}</p>'
        if IS_AFFILIATE else ""
    )

    analytics_html = (
        f"\n    <script defer src='https://static.cloudflareinsights.com/beacon.min.js' "
        f"data-cf-beacon='{{\"token\": \"{CF_ANALYTICS_TOKEN}\"}}'></script>"
        if CF_ANALYTICS_TOKEN else ""
    )

    sections = {}
    sections["steam"] = f"""
        <section class="game-section">
            <div class="section-title">
                <h2><span>🎮</span> Steamゲームトレンド</h2>
                <p>Steamで現在セール中、または売上上位にランクインしている人気タイトルです。</p>
            </div>
            <div class="grid">
                {game_cards_html}
            </div>
        </section>
"""
    sections["free"] = free_html
    sections["anime"] = f"""
        <section class="anime-section">
            <div class="section-title">
                <h2><span>📺</span> 放送中アニメの話題作</h2>
                <p>AniList の話題度（海外を含む利用者の直近の反応）が高い、放送中の日本のアニメです。</p>
            </div>
            <div class="grid">
                {anime_cards_html}
            </div>
        </section>
"""
    sections["gadget"] = f"""
        <section class="gadget-section">
            <div class="section-title">
                <h2><span>🔌</span> 最新ガジェットトレンド</h2>
                <p>ガジェット系メディアの新着テック・製品関連ニュースです。</p>
            </div>
            <div class="grid">
                {gadget_cards_html}
            </div>
        </section>
"""
    if about_html:
        main_content = about_html
    elif only:
        main_content = (pick_html if only == "steam" else "") + sections[only] + archive_html
    else:
        main_content = pick_html + "".join(sections[k] for k, _, _ in GENRE_PAGES) + archive_html

    # Actions は Python 3.11 なので、f-string の式の中にバックスラッシュを書かない
    current_attr = ' aria-current="page"'
    nav_html = "".join(
        f'<a href="{prefix}{path}"{current_attr if key == only else ""}>{html.escape(label)}</a>'
        for key, path, label in GENRE_PAGES
    )

    # structured_data を渡さない呼び出し（日別・月間・ジャンル別・ゲーム別・
    # About）は、ここが空文字のままになり、出力は1文字も変わらない。
    structured_data_html = f"\n    {structured_data}" if structured_data else ""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc_title}</title>
    <meta name="description" content="{esc_desc}">
    <link rel="canonical" href="{canonical_url}">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="TrendHub">
    <meta property="og:locale" content="ja_JP">
    <meta property="og:title" content="{esc_title}">
    <meta property="og:description" content="{esc_desc}">
    <meta property="og:url" content="{canonical_url}">
    <meta name="twitter:card" content="summary">{structured_data_html}
    <link rel="stylesheet" href="{prefix}style.css">
</head>
<body>
    <div class="glass-bg"></div>

    <header>
        <div class="container header-container">
            <div class="logo"><a href="{prefix}">TrendHub</a></div>
            <nav class="genre-nav">{nav_html}</nav>
            <div class="date-badge">{date_label}</div>
            <h1>{html.escape(heading)}</h1>
            <p class="subtitle">{html.escape(subtitle or SITE_SUBTITLE)}</p>
        </div>
    </header>
{notice_html}
    <main class="container">
{main_content}
    </main>

    <footer>
        <div class="container footer-container">
            <p class="about-link" style="margin-bottom: 16px;"><a href="{prefix}about/" style="color: var(--accent-cyan); text-decoration: none; font-weight: 600;">当サイトについて（運営者情報）</a></p>
            <p>当サイトの情報は自動集計による取得時点のものであり、最新の価格や在庫状況は各ストアにてご確認ください。</p>{disclosure_html}
            <p class="credit">掲載しているゲーム・アニメの画像および作品名の著作権は、各権利者に帰属します。<br>
            データ提供：Steam / Epic Games / AniList / Gizmodo Japan / PC Watch</p>
            <p class="credit">© 2026 TrendHub</p>
        </div>
    </footer>
{analytics_html}
</body>
</html>"""


def _game_lastmod(gid: str, game: dict) -> datetime.date:
    """ゲーム別ページの sitemap lastmod（中身が実際に変わった日）。

    直す側：最後のセールの最終掲載日。最終CSV掲載日（last_listed）は、
    セール無しで売上上位に載った日でも進むため、中身が変わっていない日を
    lastmod にしてしまう。それを防ぐため、セール記録の最終日を使う。
    比べる側：従来どおり最終CSV掲載日（出力を変えない）。
    """
    if is_treatment_game(gid) and game.get("sales"):
        return game["sales"][-1]["end"]
    return game["last_listed"]


def write_sitemap(docs_dir: str, dates: list, months: list = (), game_pages: dict = None) -> None:
    """従来のURLとゲーム別ページを掲載。ゲームのlastmodは最終CSV掲載日。"""
    today = datetime.date.today()
    entries = [
        (f"{SITE_BASE_URL}/", today),
        (f"{SITE_BASE_URL}/about/", today)
    ]
    entries += [(f"{SITE_BASE_URL}/{path}", today) for _, path, _ in GENRE_PAGES]
    revised_until, revised_on = ARCHIVE_META_REVISED
    entries += [(f"{SITE_BASE_URL}/{archive_rel_path(d)}", revised_on if d <= revised_until else d)
                for d in dates]
    # 月間ページの中身は月末で確定するので、lastmod は月末日にする
    entries += [(f"{SITE_BASE_URL}/{monthly_rel_path(y, m)}", month_last_day(y, m)) for y, m in months]
    entries += [(f"{SITE_BASE_URL}/{game_rel_path(gid)}", _game_lastmod(gid, game))
                for gid, game in (game_pages or {}).items()]

    body = "\n".join(
        f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{d.isoformat()}</lastmod>\n  </url>"
        for loc, d in entries
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )
    with open(os.path.join(docs_dir, "sitemap.xml"), mode="w", encoding="utf-8") as f:
        f.write(xml)


def write_robots(docs_dir: str) -> None:
    """robots.txt を出力します。

    NOTE: robots.txt はドメイン直下しか読まれない。*.github.io/daily-trendhub/ の頃は
    効かず、Google は2ヶ月で sitemap を一度も読まなかった（2026-10-28 に 85 URL 中
    登録5件）。独自ドメイン daily-trendhub.com に移してから効くようになった。
    """
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {SITE_BASE_URL}/sitemap.xml\n"
    )
    with open(os.path.join(docs_dir, "robots.txt"), mode="w", encoding="utf-8") as f:
        f.write(content)


def aggregate_and_draft():
    # ルートディレクトリからの絶対パスで動作するように調整
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    docs_dir = os.path.join(base_dir, "docs")
    
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(docs_dir, exist_ok=True)
    
    csv_path = os.path.join(data_dir, "trend_report.csv")
    draft_path = os.path.join(base_dir, "draft_report.md")
    html_path = os.path.join(docs_dir, "index.html")
    css_path = os.path.join(docs_dir, "style.css")
    
    print("データ収集中...")
    games = get_steam_trends()
    epic_free = get_epic_free_games()
    steam_free = get_steam_free_trends()
    # 期間限定0円は頻度を数えるためにCSVへ記録するだけ（ページには出さない）
    steam_free_promos = get_steam_free_promos()
    anime = get_anime_trends()
    gadgets = get_gadget_trends()

    all_items = games + epic_free + steam_free + steam_free_promos + anime + gadgets
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. CSVへの保存（Excelの文字化けを防ぐため utf-8-sig を使用）
    print(f"CSVファイル {csv_path} にデータを保存中...")
    file_exists = os.path.exists(csv_path)
    
    try:
        with open(csv_path, mode='a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(CSV_HEADER)

            for item in all_items:
                price = item.get("final_price")
                if price is not None:
                    price_str = f"{price:.0f}円"
                else:
                    price_str = item.get("price_info", "価格情報なし")

                # 元価格と割引率は見出しの文字列からではなく、取得元の数値をそのまま残す
                orig = item.get("original_price")
                orig_str = f"{orig:.0f}" if orig else ""

                writer.writerow([
                    item.get("id"),
                    item.get("type"),
                    item.get("headline"),
                    item.get("title"),
                    price_str,
                    item.get("url"),
                    item.get("source", "Steam Store"),
                    now_str,
                    orig_str,
                    item.get("discount_percent", 0),
                ])
        print("CSV保存完了！")
    except Exception as e:
        print(f"CSV保存中にエラーが発生しました: {e}")

    # 2. ブログ下書き（Markdown）の自動生成
    print(f"ブログ下書き {draft_path} を作成中...")
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    
    markdown_content = []
    markdown_content.append(f"# 【毎日更新】今日のトレンドゲーム＆ガジェット速報 - {today_str}\n")
    markdown_content.append("当サイトは、Steamで現在セール中・売上上位のゲーム情報と、ガジェット系メディアの新着ニュースを毎日自動集計してお届けする速報レポートです。\n")
    markdown_content.append("日々の製品チェックや最新トレンドの把握にご活用ください。\n")
    
    # ゲームセクション
    markdown_content.append("---")
    markdown_content.append("## 🎮 ゲームトレンド情報（Steamセール＆売上上位）\n")
    if games:
        for idx, g in enumerate(games[:5], 1):
            price_val = g.get("final_price", 0)
            orig_val = g.get("original_price", 0)
            discount = g.get("discount_percent", 0)
            
            price_line = ""
            if discount > 0:
                price_line = f"**価格：{price_val:.0f}円** (~~{orig_val:.0f}円~~ / {discount}%OFF!)"
            else:
                price_line = f"**価格：{price_val:.0f}円**"
                
            markdown_content.append(f"### {idx}. {g['title']}")
            markdown_content.append(f"> **{g['headline']}**  \n> {price_line}  \n> [Steamで詳細を見る]({g['url']})\n")
    else:
        markdown_content.append("※現在、対象のゲーム情報はありません。\n")
        
    # ガジェットセクション
    markdown_content.append("---")
    markdown_content.append("## 🔌 最新のテック＆ガジェットトレンド\n")
    if gadgets:
        for idx, g in enumerate(gadgets[:5], 1):
            amazon_url = build_amazon_url(g['title'])
            amazon_label = "Amazonで最安値をチェックする（アフィリエイト）" if IS_AFFILIATE else "Amazonで最安値をチェックする"
            
            markdown_content.append(f"### {idx}. {g['title']}")
            markdown_content.append(
                f"> **{g['headline']}** ({g['source']})  \n"
                f"> **価格目安**：{g['price_info']}  \n"
                f"> **概要**：{g['description']}  \n"
                f"> [{amazon_label}]({amazon_url})  \n"
                f"> [元記事・詳細はこちら]({g['url']})\n"
            )
    else:
        markdown_content.append("※現在、対象のガジェット情報はありません。\n")
        
    # 結びの言葉
    markdown_content.append("---")
    markdown_content.append("## 📝 本日のまとめ\n")
    markdown_content.append("紹介したセール情報や価格目安は、当サイトの集計時点（{now_str}）のものです。\n")
    markdown_content.append("価格やセール実施状況は各ストアにて予告なく変更される場合がありますので、必ずリンク先の公式ストアで最新情報をご確認ください。\n")
    markdown_content.append("それでは、明日も最新のトレンド情報をお届けします。\n")
    
    try:
        with open(draft_path, mode='w', encoding='utf-8') as f:
            f.write("\n".join(markdown_content))
        print("ブログ下書き作成完了！")
    except Exception as e:
        print(f"ブログ下書き作成中にエラーが発生しました: {e}")

    # 3. プレミアム静的ウェブサイト (HTML) の自動ビルド
    print(f"Webサイト {html_path} をビルド中...")

    # 過去の掲載履歴を読み込む（今日ぶんは上でCSVに書き込み済み）
    today_date = datetime.date.today()
    history = load_history(csv_path, "game")
    game_pages = load_game_pages(csv_path)
    anime_history = load_history(csv_path, "anime")
    free_history = load_history(csv_path, "free_steam")
    print(f"掲載履歴を読み込み: ゲーム {len(history)} / アニメ {len(anime_history)} タイトル")

    # ゲームのカードHTML構築
    game_cards_html = []
    if games:
        for item in games[:6]:  # 最大6件
            price_val = item.get("final_price", 0)
            orig_val = item.get("original_price", 0)
            discount = item.get("discount_percent", 0)

            stats = item_stats(item, history, today_date)
            history_badges = build_history_badges(stats)

            badge_class = "badge-sale" if discount > 0 else "badge-topseller"
            badge_text = f"{discount}% OFF" if discount > 0 else "TOP SELLER"
            
            if discount > 0:
                price_html = f"""
                <div class="price-sale-container">
                    <span class="price-original">{orig_val:.0f}円</span>
                    <span class="price-current">{price_val:.0f}円 <span class="price-discount">-{discount}%</span></span>
                </div>
                """
            else:
                price_html = f"""
                <div class="price-normal">{price_val:.0f}円</div>
                """
                
            card_html = f"""
            <div class="card">
                {card_image(item)}
                <div>
                    <div class="card-header">
                        <span class="badge {badge_class}">{badge_text}</span>
                        <span class="source">Steam Store</span>
                    </div>
                    <h3>{html.escape(item['title'])}</h3>
                    <div class="history-badges">{history_badges}</div>
                    {build_sale_note(stats, today_date)}
                </div>
                <div>
                    <div class="price-box">
                        {price_html}
                    </div>
                    <div class="btn-container">
                        <a href="{html.escape(item['url'], quote=True)}" target="_blank" class="btn btn-primary">Steamで詳細を見る</a>
                    </div>
                </div>
            </div>
            """
            game_cards_html.append(card_html)
    else:
        game_cards_html.append("<p class='no-data'>現在、対象のゲーム情報はありません。</p>")

    # 無料ゲームのカードHTML構築
    epic_cards = []
    for item in epic_free:
        is_now = item["type"] == "free_epic"
        # Actions は Python 3.11 なので、f文字列の中に同じ引用符の f文字列を入れ子にしない
        orig_html = f'<span class="price-original">{item["original_price"]:,}円</span>' if item["original_price"] else ""
        epic_cards.append(f"""
            <div class="card">
                {card_image(item)}
                <div>
                    <div class="card-header">
                        <span class="badge {'badge-free' if is_now else 'badge-next'}">{'FREE 配布中' if is_now else 'NEXT 予告'}</span>
                        <span class="source">Epic Games</span>
                    </div>
                    <h3>{html.escape(item['title'])}</h3>
                    <p class="free-period">{html.escape(item['period'])}</p>
                </div>
                <div>
                    <div class="price-box">
                        <div class="price-sale-container">
                            {orig_html}
                            <span class="price-current">{'0円' if is_now else '無料予定'}</span>
                        </div>
                    </div>
                    <div class="btn-container">
                        <a href="{html.escape(item['url'], quote=True)}" target="_blank" class="btn btn-primary">{'Epic Gamesで受け取る' if is_now else 'Epic Gamesで見る'}</a>
                    </div>
                </div>
            </div>
            """)

    steam_free_cards = []
    for item in steam_free[:6]:
        history_badges = build_history_badges(item_stats(item, free_history, today_date))
        steam_free_cards.append(f"""
            <div class="card">
                {card_image(item)}
                <div>
                    <div class="card-header">
                        <span class="badge badge-free">基本プレイ無料</span>
                        <span class="source">Steam Store</span>
                    </div>
                    <h3>{html.escape(item['title'])}</h3>
                    <div class="history-badges">{history_badges}</div>
                </div>
                <div>
                    <div class="btn-container">
                        <a href="{html.escape(item['url'], quote=True)}" target="_blank" class="btn btn-secondary">Steamで詳細を見る</a>
                    </div>
                </div>
            </div>
            """)

    epic_joined = "".join(epic_cards) or "<p class='no-data'>現在、無料配布の情報はありません。</p>"
    steam_free_joined = "".join(steam_free_cards) or "<p class='no-data'>現在、対象のゲーム情報はありません。</p>"
    free_html = f"""
        <section class="free-section">
            <div class="section-title">
                <h2><span>🎁</span> Epic Games 無料配布</h2>
                <p>Epic Games ストアで期間限定で無料配布中のゲームと、次回の配布予定です。期間内に受け取れば、その後もずっと遊べます。</p>
            </div>
            <div class="grid">
                {epic_joined}
            </div>
        </section>

        <section class="free-section">
            <div class="section-title">
                <h2><span>🆓</span> Steam 基本プレイ無料の人気ゲーム</h2>
                <p>Steamの基本プレイ無料ゲームを、売上順（ゲーム内課金を含む）に並べています。</p>
            </div>
            <div class="grid">
                {steam_free_joined}
            </div>
        </section>
"""

    # アニメのカードHTML構築（価格は無いので、履歴バッジと作品情報だけ出す）
    anime_cards_html = []
    if anime:
        for item in anime[:6]:  # 最大6件
            history_badges = build_history_badges(item_stats(item, anime_history, today_date))
            card_html = f"""
            <div class="card">
                {card_image(item)}
                <div>
                    <div class="card-header">
                        <span class="badge badge-anime">TRENDING</span>
                        <span class="source">AniList</span>
                    </div>
                    <h3>{html.escape(item['title'])}</h3>
                    <div class="history-badges">{history_badges}</div>
                    <p class="description">{html.escape(item['description'])}</p>
                </div>
                <div>
                    <div class="btn-container">
                        <a href="{html.escape(item['url'], quote=True)}" target="_blank" class="btn btn-secondary">AniListで作品情報を見る</a>
                    </div>
                </div>
            </div>
            """
            anime_cards_html.append(card_html)
    else:
        anime_cards_html.append("<p class='no-data'>現在、対象のアニメ情報はありません。</p>")

    # ガジェットのカードHTML構築
    gadget_cards_html = []
    if gadgets:
        for item in gadgets[:6]:  # 最大6件
            amazon_url = html.escape(build_amazon_url(item['title']), quote=True)
            amazon_btn_label = "Amazonで最安値を検索[PR]" if IS_AFFILIATE else "Amazonで最安値を検索"
            
            badge_type = item.get('type', 'gadget_new').replace('gadget_', '').upper()
            
            card_html = f"""
            <div class="card">
                <div>
                    <div class="card-header">
                        <span class="badge badge-gadget">{badge_type}</span>
                        <span class="source">{item['source']}</span>
                    </div>
                    <h3>{item['title']}</h3>
                    <p class="description">{item['description']}</p>
                </div>
                <div>
                    <div class="price-box">
                        <div class="price-normal" style="font-size: 1.2rem; color: var(--accent-cyan); font-weight:600;">{item['price_info']}</div>
                    </div>
                    <div class="btn-container">
                        <a href="{amazon_url}" target="_blank" class="btn btn-primary" style="background: var(--accent-cyan); color: #000;">{amazon_btn_label}</a>
                        <a href="{item['url']}" target="_blank" class="btn btn-secondary">元記事を読む</a>
                    </div>
                </div>
            </div>
            """
            gadget_cards_html.append(card_html)
    else:
        gadget_cards_html.append("<p class='no-data'>現在、対象のガジェット情報はありません。</p>")

    # ページの組み立て（トップと日別アーカイブで本文を共有する）
    games_joined = "".join(game_cards_html)
    gadgets_joined = "".join(gadget_cards_html)
    anime_joined = "".join(anime_cards_html)

    today = datetime.date.today()
    # タイトルと説明文に、その日の中身（作品名）を入れる。日付以外が同じページが並ぶと、
    # 似たページとしてインデックス登録から落とされやすいため。
    highlights = daily_highlights(games[:6], epic_free)
    md_label = f"{today.month}月{today.day}日"
    # 検索結果に表示されるのは先頭120字ほどなので、件数で絞る（文字数で切ると作品名の途中で切れる）
    top_sales = sorted((g for g in games[:6] if (g.get("discount_percent") or 0) > 0),
                       key=lambda g: -g["discount_percent"])[:3]
    sale_names = "、".join(f"{short_title(g['title'])}（{g['discount_percent']}%OFF）" for g in top_sales)
    free_names = "、".join(short_title(g["title"]) for g in epic_free if g["type"] == "free_epic")
    anime_names = "、".join(short_title(a["title"]) for a in anime[:2])
    desc_parts = [f"{today_str}時点の記録。"]
    if sale_names:
        desc_parts.append(f"Steamセール：{sale_names}。")
    if free_names:
        desc_parts.append(f"Epic無料配布：{free_names}。")
    if anime_names:
        desc_parts.append(f"話題のアニメ：{anime_names}。")
    desc_parts.append("Steamセール・無料ゲーム・アニメ・ガジェット情報を毎日自動集計しています。")
    page_description = "".join(desc_parts)
    daily_title = (f"{md_label}のSteamセール・無料ゲーム：{' / '.join(highlights)} ほか - TrendHub"
                   if highlights else f"{md_label}のSteamセール・無料ゲームまとめ - TrendHub")

    months = complete_months(csv_path, today)

    # 既存のアーカイブ＋今日ぶんを新しい順に並べる
    archive_dates = sorted(set(collect_archive_dates(docs_dir)) | {today}, reverse=True)

    pick, pick_reason = pick_of_the_day(games[:6], history, today)
    pick_html = build_pick_section(pick, pick_reason)
    if pick:
        print(f"今日の一本: {pick['title']} / {pick_reason}")

    top_html = build_page(
        title=f"TrendHub｜Steamセール・無料ゲーム・話題のアニメを毎日まとめ（{md_label}更新）",
        description=page_description,
        canonical_url=f"{SITE_BASE_URL}/",
        heading="TrendHub - ゲーム・アニメ・ガジェット速報",
        date_label=f"{today_str} 更新",
        game_cards_html=games_joined,
        gadget_cards_html=gadgets_joined,
        archive_html=build_monthly_links(months, depth=0) + build_archive_section(archive_dates, depth=0, current=today),
        depth=0,
        pick_html=pick_html,
        anime_cards_html=anime_joined,
        free_html=free_html,
        game_pages=game_pages,
        structured_data=website_json_ld(),
    )

    archive_page_html = build_page(
        title=daily_title,
        description=page_description,
        canonical_url=f"{SITE_BASE_URL}/{archive_rel_path(today)}",
        heading=f"{today_str}のSteamセール・無料ゲームまとめ",
        date_label=f"{today_str} 時点の記録",
        game_cards_html=games_joined,
        gadget_cards_html=gadgets_joined,
        archive_html=build_monthly_links(months, depth=3) + build_archive_section(archive_dates, depth=3, current=today),
        depth=3,
        pick_html=pick_html,
        anime_cards_html=anime_joined,
        free_html=free_html,
        game_pages=game_pages,
    )

    archive_path = os.path.join(docs_dir, *archive_rel_path(today).strip("/").split("/"), "index.html")

    # ジャンル別ページ（固定 URL・毎日上書き）。(タイトル, 説明文, 見出し)
    genre_meta = {
        "steam": (f"Steamセール中・人気ゲーム一覧（{md_label}更新） - TrendHub",
                  f"{today_str}時点でSteamのセール中・売上上位のゲーム。"
                  + (f"{sale_names}。" if sale_names else "")
                  + "セールが記録上何回目か、前回のセールはいつかも表示。毎日更新。",
                  "Steamセール中・人気ゲーム一覧"),
        "free": (f"今もらえる無料ゲーム：Epic無料配布とSteam基本無料（{md_label}更新） - TrendHub",
                 f"{today_str}時点の無料ゲーム。"
                 + (f"Epic無料配布：{free_names}。" if free_names else "")
                 + "Epic Gamesの無料配布（次回予定つき）とSteamの基本プレイ無料の人気作を毎日更新。",
                 "今もらえる無料ゲーム"),
        "anime": (f"放送中アニメの話題作（{md_label}更新） - TrendHub",
                  f"{today_str}時点で話題の放送中アニメ。"
                  + (f"{anime_names}など。" if anime_names else "")
                  + "AniListの話題度をもとに毎日更新。",
                  "放送中アニメの話題作"),
        "gadget": (f"ガジェット新着ニュースまとめ（{md_label}更新） - TrendHub",
                   f"{today_str}時点のガジェット・テック系メディアの新着ニュースを毎日まとめています。",
                   "ガジェット新着ニュース"),
    }
    genre_pages = []
    for key, path, _ in GENRE_PAGES:
        title, desc, heading = genre_meta[key]
        depth = path.strip("/").count("/") + 1
        genre_pages.append((path, build_page(
            title=title,
            description=desc,
            canonical_url=f"{SITE_BASE_URL}/{path}",
            heading=heading,
            date_label=f"{today_str} 更新",
            game_cards_html=games_joined,
            gadget_cards_html=gadgets_joined,
            archive_html=build_monthly_links(months, depth=depth) if key == "steam" else "",
            depth=depth,
            pick_html=pick_html,
            anime_cards_html=anime_joined,
            free_html=free_html,
            only=key,
            game_pages=game_pages,
        )))

    # CSSテンプレート
    css_template = """:root {
    --bg-dark: #0f111a;
    --card-bg: rgba(255, 255, 255, 0.03);
    --card-border: rgba(255, 255, 255, 0.08);
    --text-primary: #f3f4f6;
    --text-secondary: #9ca3af;
    --accent-purple: #a855f7;
    --accent-cyan: #06b6d4;
    --accent-pink: #ec4899;
    --grad-header: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%);
    /* 日本語が主なので、英字専用のWebフォントは読み込まず端末の日本語フォントを使う */
    --font-sans: system-ui, -apple-system, "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic UI", Meiryo, sans-serif;
    --font-display: var(--font-sans);
}

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    background-color: var(--bg-dark);
    color: var(--text-primary);
    font-family: var(--font-sans);
    line-height: 1.6;
    overflow-x: hidden;
    position: relative;
    min-height: 100vh;
}

.glass-bg {
    position: fixed;
    top: -20%;
    left: -20%;
    width: 140%;
    height: 140%;
    background: radial-gradient(circle at 20% 30%, rgba(168, 85, 247, 0.12) 0%, transparent 40%),
                radial-gradient(circle at 80% 70%, rgba(6, 182, 212, 0.12) 0%, transparent 40%);
    z-index: -1;
    pointer-events: none;
}

.container {
    width: 90%;
    max-width: 1200px;
    margin: 0 auto;
}

header {
    background: var(--grad-header);
    padding: 36px 0 28px;
    position: relative;
    border-bottom: 1px solid var(--card-border);
    text-align: center;
}

header::after {
    content: '';
    position: absolute;
    bottom: -1px;
    left: 0;
    width: 100%;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--accent-purple), var(--accent-cyan), transparent);
}

.header-container {
    display: flex;
    flex-direction: column;
    align-items: center;
}

.logo {
    font-family: var(--font-display);
    font-size: 1.5rem;
    font-weight: 900;
    letter-spacing: 0.1rem;
    text-transform: uppercase;
    background: linear-gradient(to right, var(--accent-purple), var(--accent-pink));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 10px;
}

.logo a {
    color: inherit;
    text-decoration: none;
    -webkit-text-fill-color: inherit;
}

.genre-nav {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
    margin-bottom: 14px;
}

.genre-nav a {
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 0.9rem;
    font-weight: 600;
    padding: 4px 12px;
    border-radius: 9999px;
    border: 1px solid var(--card-border);
}

.genre-nav a:hover,
.genre-nav a[aria-current="page"] {
    color: var(--text-primary);
    border-color: var(--accent-cyan);
}

.date-badge {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    padding: 6px 16px;
    border-radius: 9999px;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 12px;
}

header h1 {
    font-family: var(--font-display);
    font-size: 2rem;
    font-weight: 800;
    margin-bottom: 8px;
    line-height: 1.35;
    background: linear-gradient(to right, #ffffff, #e2e8f0);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.subtitle {
    font-size: 0.95rem;
    color: var(--text-secondary);
    max-width: 600px;
}

main {
    padding: 32px 0 60px;
}

section {
    margin-bottom: 80px;
}

.section-title {
    margin-bottom: 40px;
}

.section-title h2 {
    font-family: var(--font-display);
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 12px;
}

.section-title h2 span {
    font-size: 1.8rem;
}

.section-title p {
    color: var(--text-secondary);
    font-size: 1rem;
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 30px;
}

.card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 20px;
    padding: 30px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
    position: relative;
    overflow: hidden;
    backdrop-filter: blur(12px);
}

.card-img {
    display: block;
    width: calc(100% + 60px);
    margin: -30px -30px 20px;
    aspect-ratio: 460 / 215;
    object-fit: cover;
    background: rgba(255, 255, 255, 0.04);
}

.anime-section .card-img {
    /* AniList のキービジュアルは縦長なので、顔が入りやすい上寄りで切り出す */
    object-position: center 25%;
}

.pick-img {
    display: block;
    width: 100%;
    max-width: 460px;
    aspect-ratio: 460 / 215;
    object-fit: cover;
    border-radius: 10px;
    margin-bottom: 16px;
}

.card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.02) 0%, transparent 100%);
    pointer-events: none;
}

.card:hover {
    transform: translateY(-8px);
    border-color: rgba(255, 255, 255, 0.2);
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
}

.game-section .card:hover {
    box-shadow: 0 20px 40px rgba(168, 85, 247, 0.08);
}

.free-section .card:hover {
    box-shadow: 0 20px 40px rgba(34, 197, 94, 0.08);
}

.anime-section .card:hover {
    box-shadow: 0 20px 40px rgba(236, 72, 153, 0.08);
}

.gadget-section .card:hover {
    box-shadow: 0 20px 40px rgba(6, 182, 212, 0.08);
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 20px;
}

.badge {
    font-size: 0.75rem;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 6px;
    text-transform: uppercase;
    letter-spacing: 0.05rem;
}

.badge-sale {
    background: rgba(236, 72, 153, 0.15);
    color: var(--accent-pink);
    border: 1px solid rgba(236, 72, 153, 0.2);
}

.badge-topseller {
    background: rgba(168, 85, 247, 0.15);
    color: var(--accent-purple);
    border: 1px solid rgba(168, 85, 247, 0.2);
}

.badge-gadget {
    background: rgba(6, 182, 212, 0.15);
    color: var(--accent-cyan);
    border: 1px solid rgba(6, 182, 212, 0.2);
}

.badge-free {
    background: rgba(34, 197, 94, 0.15);
    color: #4ade80;
    border: 1px solid rgba(34, 197, 94, 0.25);
}

.badge-next {
    background: rgba(255, 255, 255, 0.06);
    color: var(--text-secondary);
    border: 1px solid var(--card-border);
}

.free-period {
    font-size: 0.9rem;
    color: var(--text-secondary);
    margin-bottom: 16px;
}

.badge-anime {
    background: rgba(236, 72, 153, 0.15);
    color: var(--accent-pink);
    border: 1px solid rgba(236, 72, 153, 0.2);
}

.source {
    font-size: 0.8rem;
    color: var(--text-secondary);
    font-weight: 500;
}

.card h3 {
    font-family: var(--font-display);
    font-size: 1.3rem;
    font-weight: 700;
    margin-bottom: 16px;
    color: #ffffff;
    line-height: 1.4;
}

.description {
    font-size: 0.9rem;
    color: var(--text-secondary);
    margin-bottom: 24px;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

.price-box {
    margin-bottom: 24px;
}

.price-sale-container {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.price-original {
    font-size: 0.85rem;
    text-decoration: line-through;
    color: var(--text-secondary);
}

.price-current {
    font-size: 1.6rem;
    font-weight: 800;
    color: #ffffff;
    display: flex;
    align-items: baseline;
    gap: 8px;
}

.price-discount {
    font-size: 0.9rem;
    font-weight: 700;
    background: var(--accent-pink);
    color: #ffffff;
    padding: 2px 8px;
    border-radius: 6px;
}

.price-normal {
    font-size: 1.5rem;
    font-weight: 800;
    color: #ffffff;
}

.btn-container {
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.btn {
    display: inline-flex;
    justify-content: center;
    align-items: center;
    width: 100%;
    padding: 12px 24px;
    border-radius: 12px;
    font-size: 0.95rem;
    font-weight: 600;
    text-decoration: none;
    transition: all 0.3s ease;
    text-align: center;
}

.btn-primary {
    background: #ffffff;
    color: var(--bg-dark);
}

.btn-primary:hover {
    background: #e2e8f0;
    transform: translateY(-2px);
}

.btn-secondary {
    background: rgba(255, 255, 255, 0.05);
    color: var(--text-primary);
    border: 1px solid var(--card-border);
}

.btn-secondary:hover {
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.2);
}

.no-data {
    color: var(--text-secondary);
    font-style: italic;
    grid-column: 1 / -1;
    text-align: center;
    padding: 40px 0;
}

.history-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 16px;
}

.history-badges:empty {
    display: none;
}

.badge-new {
    background: rgba(34, 197, 94, 0.15);
    color: #4ade80;
    border: 1px solid rgba(34, 197, 94, 0.25);
}

.badge-streak {
    background: rgba(249, 115, 22, 0.15);
    color: #fb923c;
    border: 1px solid rgba(249, 115, 22, 0.25);
}

.badge-regular {
    background: rgba(234, 179, 8, 0.15);
    color: #facc15;
    border: 1px solid rgba(234, 179, 8, 0.25);
}

.badge-repeat {
    background: rgba(168, 85, 247, 0.15);
    color: #c084fc;
    border: 1px solid rgba(168, 85, 247, 0.25);
}

.sale-history {
    font-size: 0.85rem;
    color: var(--text-secondary);
    margin-bottom: 16px;
}

.table-wrap {
    overflow-x: auto;
}

.game-history .table-wrap {
    max-width: 100%;
}

.game-history-table {
    min-width: 820px;
}

.sale-status,
.record-note {
    margin-bottom: 24px;
}

.record-note {
    margin-top: 24px;
    color: var(--text-secondary);
}

.history-actions {
    margin-top: 32px;
}

.game-history .archive-list a:hover {
    color: var(--accent-cyan);
}

.game-history a:focus-visible,
.game-history .table-wrap:focus-visible,
.history-games a:focus-visible,
.game-history-link:focus-visible {
    outline: 2px solid var(--accent-cyan);
    outline-offset: 4px;
}

.history-games a,
header h1 {
    max-width: 100%;
    overflow-wrap: anywhere;
}

.ranking-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.95rem;
}

.ranking-table th,
.ranking-table td {
    padding: 12px 14px;
    border-bottom: 1px solid var(--card-border);
    text-align: left;
}

.ranking-table th {
    color: var(--text-secondary);
    font-weight: 600;
    font-size: 0.85rem;
}

.ranking-table a {
    color: var(--text-primary);
    text-decoration: none;
    font-weight: 600;
}

.ranking-table a:hover {
    color: var(--accent-cyan);
}

.ranking-table .rank {
    font-family: var(--font-display);
    font-weight: 800;
    font-size: 1.2rem;
}

.ranking-table .num {
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
}

.ranking-table .of {
    color: var(--text-secondary);
    font-size: 0.8rem;
}

.pick-section {
    margin-bottom: 60px;
}

.pick-card {
    background: var(--card-bg);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-left: 3px solid var(--accent-pink);
    border-radius: 16px;
    padding: 28px 32px;
    backdrop-filter: blur(12px);
}

.pick-card h3 {
    font-family: var(--font-display);
    font-size: 1.5rem;
    font-weight: 700;
    color: #ffffff;
    margin-bottom: 10px;
}

.pick-reason {
    color: var(--text-primary);
    font-size: 1rem;
    margin-bottom: 16px;
}

.pick-price {
    font-size: 1.4rem;
    font-weight: 800;
    color: #ffffff;
    margin-bottom: 20px;
}

.pick-card .btn {
    width: auto;
    display: inline-flex;
    padding: 10px 28px;
}

.affiliate-notice {
    background: rgba(255, 255, 255, 0.04);
    border-bottom: 1px solid var(--card-border);
    padding: 12px 0;
    font-size: 0.85rem;
    color: var(--text-secondary);
    text-align: center;
}

.disclosure {
    font-size: 0.85rem;
    color: var(--text-secondary) !important;
}

.archive-list {
    list-style: none;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 12px;
}

.archive-list a {
    display: block;
    padding: 14px 18px;
    border: 1px solid var(--card-border);
    border-radius: 12px;
    background: var(--card-bg);
    color: var(--text-primary);
    text-decoration: none;
    font-size: 0.95rem;
    transition: all 0.3s ease;
}

.archive-list a:hover {
    border-color: rgba(255, 255, 255, 0.2);
    background: rgba(255, 255, 255, 0.06);
    transform: translateY(-2px);
}

footer {
    border-top: 1px solid var(--card-border);
    padding: 60px 0;
    text-align: center;
    background: rgba(0, 0, 0, 0.2);
}

.footer-container p {
    font-size: 1rem;
    color: var(--text-primary);
    margin-bottom: 16px;
}

.credit {
    font-size: 0.85rem;
    color: var(--text-secondary) !important;
}

@media (max-width: 768px) {
    header h1 {
        font-size: 1.35rem;
    }

    header {
        padding: 20px 0 16px;
    }

    .subtitle {
        font-size: 0.85rem;
    }
    
    .grid {
        grid-template-columns: 1fr;
    }
}"""

    try:
        # index.html（トップ＝最新版）の出力
        with open(html_path, mode='w', encoding='utf-8') as f:
            f.write(top_html)
        print("HTMLビルド完了！")

        # 日別アーカイブの出力（URLを日数ぶん積み上げていく）
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with open(archive_path, mode='w', encoding='utf-8') as f:
            f.write(archive_page_html)
        print(f"アーカイブ出力完了！ -> {archive_path}")

        for path, page_html in genre_pages:
            genre_path = os.path.join(docs_dir, *path.strip("/").split("/"), "index.html")
            os.makedirs(os.path.dirname(genre_path), exist_ok=True)
            with open(genre_path, mode='w', encoding='utf-8') as f:
                f.write(page_html)
        print(f"ジャンル別ページ出力完了！（{len(genre_pages)} ページ）")

        # docs/about/index.html の出力
        about_dir = os.path.join(docs_dir, "about")
        os.makedirs(about_dir, exist_ok=True)
        about_path = os.path.join(about_dir, "index.html")

        about_content_html = """
        <section class="about-section" style="max-width: 800px; margin: 0 auto 80px; padding: 0 20px;">
            <div class="section-title">
                <h2 style="font-family: var(--font-display); font-size: 2rem; font-weight: 700; margin-bottom: 20px; color: #ffffff;">当サイトについて（運営者情報）</h2>
            </div>
            <div class="about-card" style="background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 20px; padding: 40px; backdrop-filter: blur(12px); color: var(--text-primary); line-height: 1.8;">
                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">概要</h3>
                <p style="margin-bottom: 24px;">
                    当サイト「TrendHub」は、PCゲーム配信プラットフォーム「Steam」で現在セール中、または売上上位にランクインしているゲーム情報、Epic Games ストアの無料配布、Steamの基本プレイ無料ゲーム、アニメデータベース「AniList」で話題になっている放送中アニメ、主要ガジェットメディア（Gizmodo Japan、PC Watch）の新着ニュース記事を毎日自動で集計し、一覧形式でご紹介する速報・まとめサイトです。
                </p>

                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">データの取得・更新頻度</h3>
                <p style="margin-bottom: 24px;">
                    当サイトに掲載されているデータは、プログラムによる自動集計を用いて毎日 16:00 JST 頃に取得・更新されています。データの主な取得元は以下の通りです。
                </p>
                <ul style="margin-bottom: 24px; padding-left: 20px; list-style-type: disc;">
                    <li><strong>ゲーム情報：</strong>Steamストア（セール情報・売上上位ゲームデータ）</li>
                    <li><strong>無料ゲーム情報：</strong>Epic Games ストア（無料配布中・配布予定）、Steamストア（基本プレイ無料ゲームの売上順）</li>
                    <li><strong>アニメ情報：</strong>AniList（放送中の日本制作アニメを、利用者の直近の反応にもとづく話題度順に取得）</li>
                    <li><strong>ガジェット・テック情報：</strong>Gizmodo Japan、PC Watch のRSSフィード</li>
                </ul>

                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">ご利用上の注意点</h3>
                <p style="margin-bottom: 24px;">
                    当サイトに掲載されている価格、割引率、セール実施状況、および製品仕様などの情報は、データの取得時点（毎日 16:00 JST 頃）のものであり、常に最新の情報を保証するものではありません。<br>
                    実際のセール実施の有無、販売価格、購入条件などにつきましては、必ずリンク先の各配信ストア（Steamストア）または公式販売元（Amazon等）にて直接ご確認ください。当サイトの情報を利用したことにより生じた、いかなるトラブルや不利益についても、当サイトの管理運営者は責任を負いかねます。
                </p>

{ANALYTICS_NOTICE}
                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">お問い合わせ先</h3>
                <p style="margin-bottom: 0;">
                    ご意見、ご要望、掲載内容の誤りのご連絡などは、以下のお問い合わせフォームからお送りください。<br>
                    <a href="https://forms.gle/mNr4b147EpdZY9Cn6" target="_blank" rel="noopener" style="color: var(--accent-cyan); font-weight: 600;">TrendHub お問い合わせフォーム</a>
                </p>
            </div>
        </section>
        """

        # トークンを入れた時だけ、アクセス解析の説明を出す
        about_content_html = about_content_html.replace("{ANALYTICS_NOTICE}", ANALYTICS_NOTICE_HTML if CF_ANALYTICS_TOKEN else "")

        about_html = build_page(
            title="当サイトについて - TrendHub",
            description="TrendHub（トレンドハブ）のサイト概要、自動集計データに関する説明、および運営者情報・お問い合わせ先を掲載しているページです。",
            canonical_url=f"{SITE_BASE_URL}/about/",
            heading="当サイトについて",
            date_label="運営者情報",
            game_cards_html="",
            gadget_cards_html="",
            archive_html="",
            depth=1,
            pick_html="",
            about_html=about_content_html
        )

        with open(about_path, mode='w', encoding='utf-8') as f:
            f.write(about_html)
        print(f"Aboutページ出力完了！ -> {about_path}")

        # style.cssの出力
        with open(css_path, mode='w', encoding='utf-8') as f:
            f.write(css_template)
        print("CSSビルド完了！")

        # sitemap.xml / robots.txt の出力
        # 月間ランキング（終わった月ぶん。中身は確定しているので毎回作り直しても同じになる）
        for y, m in months:
            ranking = monthly_ranking(csv_path, y, m)
            monthly_html = build_page(
                title=f"{y}年{m}月 Steam人気ゲームランキングTOP{len(ranking)}（売上上位の掲載日数順） - TrendHub",
                description=(f"{y}年{m}月のSteam人気ゲームランキング。1位は{ranking[0]['title']}"
                             f"（{ranking[0]['days']}日掲載）。1か月間、売上上位に掲載された日数が多かったゲームを集計。"
                             f"TrendHubが毎日記録したデータから集計しています。"),
                canonical_url=f"{SITE_BASE_URL}/{monthly_rel_path(y, m)}",
                heading=f"{y}年{m}月 Steam人気ゲームランキング",
                date_label=f"{y}年{m}月の集計",
                game_cards_html="",
                gadget_cards_html="",
                archive_html="",
                depth=3,
                # about_html は本文の差し替え口として使う
                about_html=build_monthly_content(y, m, ranking) + build_monthly_links(months, depth=3),
            )
            monthly_path = os.path.join(docs_dir, *monthly_rel_path(y, m).strip("/").split("/"), "index.html")
            os.makedirs(os.path.dirname(monthly_path), exist_ok=True)
            with open(monthly_path, mode='w', encoding='utf-8') as f:
                f.write(monthly_html)
        print(f"月間ランキング出力完了！（{len(months)} か月）")

        write_game_pages(csv_path, docs_dir, today, game_pages)
        print(f"ゲーム別ページ出力完了！（{len(game_pages)} ページ）")
        write_sitemap(docs_dir, archive_dates, months, game_pages)
        write_robots(docs_dir)
        print(f"sitemap.xml / robots.txt 出力完了！（登録URL {len(archive_dates) + len(months) + len(GENRE_PAGES) + len(game_pages) + 2} 件）")

    except Exception as e:
        print(f"Webサイトビルド中にエラーが発生しました: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-pages-only", action="store_true",
                        help="CSVのみからゲーム別ページ・Steam一覧の履歴リンク・sitemapを更新する")
    args = parser.parse_args()
    if args.game_pages_only:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pages = rebuild_game_pages(os.path.join(base_dir, "data", "trend_report.csv"),
                                   os.path.join(base_dir, "docs"))
        print(f"ゲーム別ページ出力完了！（{len(pages)} ページ）")
    else:
        aggregate_and_draft()
