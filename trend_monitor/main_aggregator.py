import os
import csv
import datetime
import sys
import urllib.parse

# パスを追加して同一ディレクトリ内のモジュールを確実にインポートできるようにする
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from steam_monitor import get_steam_trends
from gadget_monitor import get_gadget_trends

AMAZON_ASSOCIATE_ID = os.environ.get("AMAZON_ASSOCIATE_ID", "trainer-test-22")

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
    cname_path = os.path.join(docs_dir, "CNAME")
    
    print("データ収集中...")
    games = get_steam_trends()
    gadgets = get_gadget_trends()
    
    all_items = games + gadgets
    # JSTタイムゾーンの定義（GitHub ActionsのUTC環境でもJSTで表示するため）
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now_jst = datetime.datetime.now(JST)
    now_str = now_jst.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now_jst.strftime("%Y年%m月%d日")
    
    # 1. CSVへの保存（Excelの文字化けを防ぐため utf-8-sig を使用）
    print(f"CSVファイル {csv_path} にデータを保存中...")
    file_exists = os.path.exists(csv_path)
    
    try:
        with open(csv_path, mode='a', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["ID", "タイプ", "見出し", "タイトル", "価格情報", "URL", "情報源", "取得日時"])
            
            for item in all_items:
                price = item.get("final_price")
                if price is not None:
                    price_str = f"{price:.0f}円"
                else:
                    price_str = item.get("price_info", "価格情報なし")
                    
                writer.writerow([
                    item.get("id"),
                    item.get("type"),
                    item.get("headline"),
                    item.get("title"),
                    price_str,
                    item.get("url"),
                    item.get("source", "Steam Store"),
                    now_str
                ])
        print("CSV保存完了！")
    except Exception as e:
        print(f"CSV保存中にエラーが発生しました: {e}")

    # 2. ブログ下書き（Markdown）の自動生成
    print(f"ブログ下書き {draft_path} を作成中...")
    # today_str は上部でJST基準で定義済み
    
    markdown_content = []
    markdown_content.append(f"# 【毎日更新】今日のトレンドゲーム＆ガジェット超速報！ - {today_str}\n")
    markdown_content.append("こんにちは！今日のネット of 海から、今大注目のセールゲームと最新テック・ガジェット of 情報を厳選してお届けしますっ！✨\n")
    markdown_content.append("「何か面白いゲームないかな？」「今話題 of ガジェットが知りたい！」という方は、ぜひ参考にしてみてくださいね。🌻\n")
    
    # ゲームセクション
    markdown_content.append("---")
    markdown_content.append("## 🎮 注目のトレンドゲーム情報（Steamセール＆売上上位）\n")
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
            markdown_content.append(f"> **{g['headline']}**  \n> {price_line}  \n> [👉 Steamストアでチェックする]({g['url']})\n")
    else:
        markdown_content.append("※現在、注目のゲーム情報はありません。\n")
        
    # ガジェットセクション
    markdown_content.append("---")
    markdown_content.append("## 🔌 最新のテック＆ガジェットトレンド\n")
    if gadgets:
        for idx, g in enumerate(gadgets[:5], 1):
            search_kw = clean_keyword_for_amazon(g['title'])
            encoded_kw = urllib.parse.quote(search_kw)
            amazon_affiliate_url = f"https://www.amazon.co.jp/s?k={encoded_kw}&tag={AMAZON_ASSOCIATE_ID}"
            
            markdown_content.append(f"### {idx}. {g['title']}")
            markdown_content.append(
                f"> **{g['headline']}** ({g['source']})  \n"
                f"> **価格目安**：{g['price_info']}  \n"
                f"> **概要**：{g['description']}  \n"
                f"> [👉 Amazonで最安値をチェックする（アフィリエイト）]({amazon_affiliate_url})  \n"
                f"> [👉 元記事・詳細はこちら]({g['url']})\n"
            )
    else:
        markdown_content.append("※現在、注目のガジェット情報はありません。\n")
        
    # 結びの言葉
    markdown_content.append("---")
    markdown_content.append("## 📝 今日のまとめ\n")
    markdown_content.append("気になるアイテムは見つかりましたか？\n")
    markdown_content.append("流行りの移り変わりはとても早いので、お得なセール品などは売り切れたり終了したりする前に早めのチェックがおすすめです！\n")
    markdown_content.append("それでは、明日もぽかぽかなトレンド情報をお届けしますので、お楽しみにっ！今日も良い一日になりますように！🌻\n")
    
    try:
        with open(draft_path, mode='w', encoding='utf-8') as f:
            f.write("\n".join(markdown_content))
        print("ブログ下書き作成完了！")
    except Exception as e:
        print(f"ブログ下書き作成中にエラーが発生しました: {e}")

    # 3. プレミアム静的ウェブサイト (HTML) の自動ビルド
    print(f"Webサイト {html_path} をビルド中...")
    
    # ゲームのカードHTML構築
    game_cards_html = []
    if games:
        for item in games[:6]:  # 最大6件
            price_val = item.get("final_price", 0)
            orig_val = item.get("original_price", 0)
            discount = item.get("discount_percent", 0)
            
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
                    <h3>{item['title']}</h3>
                </div>
                <div>
                    <div class="price-box">
                        {price_html}
                    </div>
                    <div class="btn-container">
                        <a href="{item['url']}" target="_blank" class="btn btn-primary">👉 Steamでチェックする</a>
                    </div>
                </div>
            </div>
            """
            game_cards_html.append(card_html)
    else:
        game_cards_html.append("<p class='no-data'>現在、注目 of ゲーム情報はありません。</p>")

    # ガジェットのカードHTML構築
    gadget_cards_html = []
    if gadgets:
        for item in gadgets[:6]:  # 最大6件
            search_kw = clean_keyword_for_amazon(item['title'])
            encoded_kw = urllib.parse.quote(search_kw)
            amazon_affiliate_url = f"https://www.amazon.co.jp/s?k={encoded_kw}&tag={AMAZON_ASSOCIATE_ID}"
            
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
                        <a href="{amazon_affiliate_url}" target="_blank" class="btn btn-primary" style="background: var(--accent-cyan); color: #000;">🛒 Amazon最安値を検索</a>
                        <a href="{item['url']}" target="_blank" class="btn btn-secondary">👉 元記事を読む</a>
                    </div>
                </div>
            </div>
            """
            gadget_cards_html.append(card_html)
    else:
        gadget_cards_html.append("<p class='no-data'>現在、注目 of ガジェット情報はありません。</p>")

    # HTMLテンプレートの結合
    html_template = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>【毎日更新】今日のトレンドゲーム＆ガジェット速報 - {today_str}</title>
    <link rel="stylesheet" href="style.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&family=Outfit:wght@400;700;900&display=swap" rel="stylesheet">
</head>
<body>
    <div class="glass-bg"></div>
    
    <header>
        <div class="container header-container">
            <div class="logo">TrendHub</div>
            <div class="date-badge">最終更新: {now_str} (JST)</div>
            <h1>今日のトレンドゲーム＆ガジェット超速報！</h1>
            <p class="subtitle">ネット of 海から、今大注目のセールゲームと最新テック・ガジェット of 情報を厳選してお届けしますっ！✨</p>
        </div>
    </header>

    <main class="container">
        <section class="game-section">
            <div class="section-title">
                <h2><span>🎮</span> ゲームトレンド (Steam)</h2>
                <p>セール中や売上上位 of 人気タイトルを厳選！</p>
            </div>
            <div class="grid">
                {"".join(game_cards_html)}
            </div>
        </section>

        <section class="gadget-section">
            <div class="section-title">
                <h2><span>🔌</span> ガジェットトレンド (Gizmodo)</h2>
                <p>今話題 of 最新ガジェットやお得なセール情報！</p>
            </div>
            <div class="grid">
                {"".join(gadget_cards_html)}
            </div>
        </section>
    </main>

    <footer>
        <div class="container footer-container">
            <p>流行り of 移り変わりはとても早いので、お得なアイテムは早めにチェックしてくださいね！🌻</p>
            <p class="credit">© 2026 TrendHub. Crafted with love by Seren & Trainer.</p>
        </div>
    </footer>
</body>
</html>"""

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
        # index.htmlの出力
        with open(html_path, mode='w', encoding='utf-8') as f:
            f.write(html_template)
        print("HTMLビルド完了！")
        
        # style.cssの出力
        with open(css_path, mode='w', encoding='utf-8') as f:
            f.write(css_template)
        print("CSSビルド完了！")
        
        # CNAMEファイルの出力 (独自ドメイン設定用)
        with open(cname_path, mode='w', encoding='utf-8') as f:
            f.write("daily-trendhub.com")
        print("CNAMEファイル出力完了！")
        
    except Exception as e:
        print(f"Webサイトビルド中にエラーが発生しました: {e}")

if __name__ == "__main__":
    aggregate_and_draft()
