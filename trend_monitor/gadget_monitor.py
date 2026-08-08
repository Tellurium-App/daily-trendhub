import xml.etree.ElementTree as ET
import urllib.request
import re
from typing import List, Dict, Any

# 「49万9,800円」「1万6,980円」「3,320円」を1件として捉える
_PRICE_RE = re.compile(r'(?:(\d+)\s*万)?\s*(\d{1,3}(?:,\d{3})+|\d+)?\s*円')

# 金額の直後にこれが続くときは値引き額なので、販売価格として採らない
_DISCOUNT_SUFFIX_RE = re.compile(r'^\s*(?:引き|引|オフ|OFF|off|安く|安|分|相当|お得|得|還元)')

# 金額の直後にこれが続くときは販売価格である見込みが高い
_PRICE_SUFFIX_RE = re.compile(r'^\s*(?:で(?:販売|発売|提供|購入|登場|発表|投入)|に値下げ|になる)')

PRICE_UNKNOWN = "価格は記事を参照"


def _amounts(text: str):
    """文中の金額を (数値, 直後の文字列) の並びで返します。万表記に対応。"""
    for m in _PRICE_RE.finditer(text):
        man, rest = m.group(1), m.group(2)
        if not man and not rest:
            continue  # 「3千円」のように数字を伴わない「円」は拾わない
        value = int(man) * 10000 if man else 0
        value += int(rest.replace(",", "")) if rest else 0
        yield value, text[m.end():m.end() + 8]


def extract_price(text: str) -> str:
    """記事の文面から販売価格を取り出します。

    セール記事は「2,920円引きの1万6,980円で販売」のように値引き額を先に書くので、
    単純に最初の金額を採ると割引額を価格として表示してしまう。
    「で販売」等が続く金額を優先し、無ければ値引き額でないものを採る。
    """
    candidates = list(_amounts(text))
    if not candidates:
        return PRICE_UNKNOWN

    for value, after in candidates:
        if _PRICE_SUFFIX_RE.match(after):
            return f"{value:,}円"

    for value, after in candidates:
        if not _DISCOUNT_SUFFIX_RE.match(after):
            return f"{value:,}円"

    return PRICE_UNKNOWN


def get_gadget_trends() -> List[Dict[str, Any]]:
    """
    主要なガジェット・テック系RSSフィードから最新のセール・新製品トレンド情報を取得します。
    """
    # 巡回するRSSフィードのリスト（PC WatchはRDF形式、GizmodoはRSS 2.0形式）
    feeds = [
        {"name": "Gizmodo Japan", "url": "https://www.gizmodo.jp/index.xml"},
        {"name": "PC Watch", "url": "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf"}
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    trends = []
    
    # フィルタ用のキーワード（セールや新製品に関連するもの）
    keywords = ["セール", "特価", "割引", "Amazon", "新発売", "登場", "レビュー", "発売", "解禁", "値引き", "クーポン", "プライム"]
    
    for feed in feeds:
        req = urllib.request.Request(feed["url"], headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                
                items = []
                
                # 1. RSS 2.0 (Gizmodoなど) のパース
                for item in root.findall(".//item"):
                    title = item.find("title")
                    link = item.find("link")
                    desc = item.find("description")
                    
                    title_text = title.text if title is not None else ""
                    link_text = link.text if link is not None else ""
                    desc_text = desc.text if desc is not None else ""
                    
                    items.append((title_text, link_text, desc_text))
                
                # 2. RDF 1.0 (PC Watchなど) のパース
                ns = {
                    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#', 
                    'default': 'http://purl.org/rss/1.0/'
                }
                for item in root.findall(".//default:item", ns):
                    title = item.find("default:title", ns)
                    link = item.find("default:link", ns)
                    desc = item.find("default:description", ns)
                    
                    title_text = title.text if title is not None else ""
                    link_text = link.text if link is not None else ""
                    desc_text = desc.text if desc is not None else ""
                    
                    items.append((title_text, link_text, desc_text))
                
                # 重複排除しながらフィルタリングと整形
                seen_urls = set()
                for title, link, desc in items:
                    if not link or link in seen_urls:
                        continue
                    seen_urls.add(link)
                    
                    # タイトルにキーワードが含まれるかチェック
                    matched_keyword = next((kw for kw in keywords if kw in title), None)
                    if matched_keyword:
                        # 本文のほうが「N円引きのM円で販売」と書くので、本文を先に見る
                        price = extract_price(desc + " " + title)
                        
                        # セール系か新製品系か種別判定
                        is_sale = any(kw in title for kw in ["セール", "特価", "割引", "値引き", "クーポン", "プライム"])
                        item_type = "gadget_sale" if is_sale else "gadget_new"
                        headline = f"【ガジェットセール! ({matched_keyword})】" if is_sale else f"【ガジェット新着! ({matched_keyword})】"
                        
                        trends.append({
                            "id": link,
                            "title": title,
                            "price_info": price,
                            "url": link,
                            "type": item_type,
                            "headline": headline,
                            "description": re.sub(r'<[^>]*>', '', desc)[:100] + "..." if desc else "",  # HTMLタグを除去して100文字要約
                            "source": feed["name"]
                        })
                        
        except Exception as e:
            print(f"フィード {feed['name']} の取得中にエラーが発生しました: {e}")
            
    return trends

if __name__ == "__main__":
    print("ガジェットのトレンド情報を取得中...")
    results = get_gadget_trends()
    print(f"取得完了: {len(results)} 件のアイテムが見つかりました。")
    for r in results[:5]:
        print(f"- {r['headline']} {r['title']} ({r['source']})")
        print(f"  URL: {r['url']}")
        print(f"  価格目安: {r['price_info']}")
        print(f"  概要: {r['description']}")
