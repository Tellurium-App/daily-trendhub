import os
import collections
import csv
import datetime
import glob
import html
import re
import sys
import urllib.parse

# パスを追加して同一ディレクトリ内のモジュールを確実にインポートできるようにする
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from steam_monitor import get_steam_trends
from gadget_monitor import get_gadget_trends

# アソシエイトIDが未設定のうちは tag を付けず、ただのAmazon検索リンクとして出す。
# 広告にならないので、この間はステマ規制の表記義務も発生しない。
# GitHub の Secrets に本物のIDを入れた時点で、リンクと表記が同時にアフィリエイト用へ切り替わる。
AMAZON_ASSOCIATE_ID = os.environ.get("AMAZON_ASSOCIATE_ID", "").strip()
IS_AFFILIATE = bool(AMAZON_ASSOCIATE_ID)

# 独自ドメインを取得したら SITE_BASE_URL を差し替えるだけで
# canonical / OGP / sitemap の URL が一斉に切り替わる。
SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://tellurium-app.github.io/daily-trendhub"
).rstrip("/")

# 一覧に載せるアーカイブの最大件数
ARCHIVE_LIST_LIMIT = 30

# data/trend_report.csv の列。末尾2列は 2026-08-08 に追加（それ以前の行は空）。
CSV_HEADER = ["ID", "タイプ", "見出し", "タイトル", "価格情報", "URL", "情報源", "取得日時",
              "元価格", "割引率"]

# 「〇日連続ランクイン」を出す下限。短すぎると全部に付いて意味がなくなる。
STREAK_BADGE_MIN_DAYS = 3

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


def load_game_history(csv_path: str) -> dict:
    """CSVの全履歴から、ゲームID別の「掲載された日」と「セール中だった日」を集めます。

    今日ぶんの行を書き込んだ後に呼ぶ前提。日付を集合で持つので、
    1日に複数回実行しても二重に数えられない。
    """
    history = collections.defaultdict(lambda: {"days": set(), "sale_days": set()})
    if not os.path.exists(csv_path):
        return history

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not (row.get("タイプ") or "").startswith("game"):
                continue
            gid, day = row.get("ID"), (row.get("取得日時") or "")[:10]
            if not gid or not day:
                continue
            history[gid]["days"].add(day)
            if parse_discount(row) > 0:
                history[gid]["sale_days"].add(day)
    return history


def consecutive_days(days: set, today: datetime.date) -> int:
    """today から1日ずつ遡って、何日連続で days に含まれるかを数えます。"""
    count, d = 0, today
    while d.isoformat() in days:
        count += 1
        d -= datetime.timedelta(days=1)
    return count


def item_stats(item: dict, history: dict, today: datetime.date) -> dict:
    """1件ぶんの履歴指標をまとめます。Steamのストアページには出ていない情報。"""
    stat = history.get(str(item.get("id")), {"days": set(), "sale_days": set()})
    return {
        "listed_total": len(stat["days"]),
        "listed_run": consecutive_days(stat["days"], today),
        "sale_run": consecutive_days(stat["sale_days"], today),
        "discount": item.get("discount_percent", 0) or 0,
    }


def build_history_badges(stats: dict) -> str:
    """履歴からしか分からない情報だけをバッジにします。"""
    badges = []
    if stats["listed_total"] <= 1:
        badges.append('<span class="badge badge-new">🆕 初登場</span>')
    if stats["sale_run"] >= 2:
        badges.append(f'<span class="badge badge-streak">🔥 セール{stats["sale_run"]}日目</span>')
    # 掲載日数がセール日数と同じなら数字が二重になるだけなので、長い時だけ出す
    if stats["listed_run"] >= STREAK_BADGE_MIN_DAYS and stats["listed_run"] > stats["sale_run"]:
        badges.append(f'<span class="badge badge-regular">👑 {stats["listed_run"]}日連続</span>')
    return "".join(badges)


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
        return g, f"現在 {s['sale_run']} 日連続でセールを継続中です。価格は予告なく変更される場合があります。"

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
                <h3>{html.escape(pick['title'])}</h3>
                <p class="pick-reason">{html.escape(reason)}</p>
                <div class="pick-price">{price:.0f}円</div>
                <a href="{html.escape(pick['url'], quote=True)}" target="_blank" class="btn btn-primary">Steamで詳細を見る</a>
            </div>
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
                <p>日別のアーカイブから、過去のセール状況をさかのぼって見られます。</p>
            </div>
            <ul class="archive-list">
                {"".join(items)}
            </ul>
        </section>
"""


def build_page(title: str, description: str, canonical_url: str, heading: str,
               date_label: str, game_cards_html: str, gadget_cards_html: str,
               archive_html: str, depth: int, pick_html: str = "",
               about_html: str = "") -> str:
    """1ページ分のHTMLを組み立てます。depth はサイトルートからの階層の深さ。"""
    prefix = "../" * depth
    esc_title = html.escape(title)
    esc_desc = html.escape(description)

    # アフィリエイトIDが設定されている時だけ、広告表示とアソシエイト表示を出す
    notice_html = (
        f'\n    <div class="affiliate-notice"><div class="container">{html.escape(AFFILIATE_NOTICE)}</div></div>'
        if IS_AFFILIATE else ""
    )
    disclosure_html = (
        f'\n            <p class="disclosure">{html.escape(AFFILIATE_DISCLOSURE)}</p>'
        if IS_AFFILIATE else ""
    )

    if about_html:
        main_content = about_html
    else:
        main_content = f"""{pick_html}
        <section class="game-section">
            <div class="section-title">
                <h2><span>🎮</span> Steamゲームトレンド</h2>
                <p>Steamで現在セール中、または売上上位にランクインしている人気タイトルです。</p>
            </div>
            <div class="grid">
                {game_cards_html}
            </div>
        </section>

        <section class="gadget-section">
            <div class="section-title">
                <h2><span>🔌</span> 最新ガジェットトレンド</h2>
                <p>ガジェット系メディアの新着テック・製品関連ニュースです。</p>
            </div>
            <div class="grid">
                {gadget_cards_html}
            </div>
        </section>
{archive_html}"""

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
    <meta name="twitter:card" content="summary">
    <link rel="stylesheet" href="{prefix}style.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&amp;family=Outfit:wght@400;700;900&amp;display=swap" rel="stylesheet">
</head>
<body>
    <div class="glass-bg"></div>

    <header>
        <div class="container header-container">
            <div class="logo"><a href="{prefix}">TrendHub</a></div>
            <div class="date-badge">{date_label}</div>
            <h1>{html.escape(heading)}</h1>
            <p class="subtitle">Steamのセール・売上上位ゲームと、ガジェット系メディアの新着ニュースを毎日自動集計してお届けする情報サイトです。</p>
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
            <p class="credit">© 2026 TrendHub. Crafted with love by Seren &amp; Trainer.</p>
        </div>
    </footer>
</body>
</html>"""


def write_sitemap(docs_dir: str, dates: list) -> None:
    """トップ、Aboutページ、全アーカイブを載せた sitemap.xml を出力します。"""
    today = datetime.date.today()
    entries = [
        (f"{SITE_BASE_URL}/", today),
        (f"{SITE_BASE_URL}/about/", today)
    ]
    entries += [(f"{SITE_BASE_URL}/{archive_rel_path(d)}", d) for d in dates]

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

    NOTE: GitHub Pages のプロジェクトサイト（*.github.io/daily-trendhub/）では
    robots.txt はドメイン直下しか読まれないため、このファイルが効くのは
    独自ドメインを割り当ててから。それまでは Search Console から
    sitemap.xml を直接送信すること。
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
    gadgets = get_gadget_trends()
    
    all_items = games + gadgets
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
    history = load_game_history(csv_path)
    print(f"掲載履歴を読み込み: {len(history)} タイトル")

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
                <div>
                    <div class="card-header">
                        <span class="badge {badge_class}">{badge_text}</span>
                        <span class="source">Steam Store</span>
                    </div>
                    <h3>{html.escape(item['title'])}</h3>
                    <div class="history-badges">{history_badges}</div>
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

    today = datetime.date.today()
    page_description = (
        f"{today_str}時点のSteamセール・売上上位ゲームと、"
        f"ガジェット・製品情報の新着ニュースを毎日自動集計。過去アーカイブも掲載中。"
    )

    # 既存のアーカイブ＋今日ぶんを新しい順に並べる
    archive_dates = sorted(set(collect_archive_dates(docs_dir)) | {today}, reverse=True)

    pick, pick_reason = pick_of_the_day(games[:6], history, today)
    pick_html = build_pick_section(pick, pick_reason)
    if pick:
        print(f"今日の一本: {pick['title']} / {pick_reason}")

    top_html = build_page(
        title=f"TrendHub - ゲーム＆ガジェットトレンド自動集計速報 ({today_str})",
        description=page_description,
        canonical_url=f"{SITE_BASE_URL}/",
        heading="TrendHub - ゲーム＆ガジェットトレンド速報",
        date_label=f"{today_str} 更新",
        game_cards_html=games_joined,
        gadget_cards_html=gadgets_joined,
        archive_html=build_archive_section(archive_dates, depth=0, current=today),
        depth=0,
        pick_html=pick_html,
    )

    archive_page_html = build_page(
        title=f"TrendHub - ゲーム＆ガジェットトレンド速報 ({today_str} 時点の記録)",
        description=page_description,
        canonical_url=f"{SITE_BASE_URL}/{archive_rel_path(today)}",
        heading=f"TrendHub - {today_str} 時点のトレンド記録",
        date_label=f"{today_str} 時点の記録",
        game_cards_html=games_joined,
        gadget_cards_html=gadgets_joined,
        archive_html=build_archive_section(archive_dates, depth=3, current=today),
        depth=3,
        pick_html=pick_html,
    )

    archive_path = os.path.join(docs_dir, *archive_rel_path(today).strip("/").split("/"), "index.html")

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
    --font-sans: 'Inter', sans-serif;
    --font-display: 'Outfit', sans-serif;
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
    padding: 80px 0 60px;
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
    margin-bottom: 20px;
}

.logo a {
    color: inherit;
    text-decoration: none;
    -webkit-text-fill-color: inherit;
}

.date-badge {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    padding: 6px 16px;
    border-radius: 9999px;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 24px;
}

header h1 {
    font-family: var(--font-display);
    font-size: 3rem;
    font-weight: 800;
    margin-bottom: 16px;
    letter-spacing: -0.03em;
    background: linear-gradient(to right, #ffffff, #e2e8f0);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.subtitle {
    font-size: 1.1rem;
    color: var(--text-secondary);
    max-width: 600px;
}

main {
    padding: 60px 0;
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
        font-size: 2.2rem;
    }
    
    header {
        padding: 60px 0 40px;
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
                    当サイト「TrendHub」は、PCゲーム配信プラットフォーム「Steam」で現在セール中、または売上上位にランクインしているゲーム情報と、主要ガジェットメディア（Gizmodo Japan、PC Watch）の新着ニュース記事を毎日自動で集計し、一覧形式でご紹介する速報・まとめサイトです。
                </p>

                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">データの取得・更新頻度</h3>
                <p style="margin-bottom: 24px;">
                    当サイトに掲載されているデータは、プログラムによる自動集計を用いて毎日 16:00 JST 頃に取得・更新されています。データの主な取得元は以下の通りです。
                </p>
                <ul style="margin-bottom: 24px; padding-left: 20px; list-style-type: disc;">
                    <li><strong>ゲーム情報：</strong>Steamストア（セール情報・売上上位ゲームデータ）</li>
                    <li><strong>ガジェット・テック情報：</strong>Gizmodo Japan、PC Watch のRSSフィード</li>
                </ul>

                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">ご利用上の注意点</h3>
                <p style="margin-bottom: 24px;">
                    当サイトに掲載されている価格、割引率、セール実施状況、および製品仕様などの情報は、データの取得時点（毎日 16:00 JST 頃）のものであり、常に最新の情報を保証するものではありません。<br>
                    実際のセール実施の有無、販売価格、購入条件などにつきましてさ、必ずリンク先の各配信ストア（Steamストア）または公式販売元（Amazon等）にて直接ご確認ください。当サイトの情報を利用したことにより生じた、いかなるトラブルや不利益についても、当サイトの管理運営者は責任を負いかねます。
                </p>

                <h3 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 15px; color: #ffffff; border-bottom: 1px solid var(--card-border); padding-bottom: 8px;">お問い合わせ先</h3>
                <p style="margin-bottom: 0;">
                    ご意見、ご要望、お問い合わせなどがございましたら、以下の連絡先までご連絡いただきますようお願いいたします。<br>
                    連絡先メールアドレス：<strong>___@___</strong>
                </p>
            </div>
        </section>
        """

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
        write_sitemap(docs_dir, archive_dates)
        write_robots(docs_dir)
        print(f"sitemap.xml / robots.txt 出力完了！（登録URL {len(archive_dates) + 2} 件）")

    except Exception as e:
        print(f"Webサイトビルド中にエラーが発生しました: {e}")

if __name__ == "__main__":
    aggregate_and_draft()
