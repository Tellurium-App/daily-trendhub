import xml.etree.ElementTree as ET
import urllib.request
import re
from typing import List, Dict, Any

def get_gadget_trends() -> List[Dict[str, Any]]:
    """
    主要なガジェット・テック系RSSフィードから最新のセール・新製品トレンド情報を取得します。
    """
    # 巡回するRSSフィードのリスト（PC WatchはRDF形式、GizmodoはRSS 2.0形式）
    feeds = [
        {"name": "Gizmodo Japan", "url": "https://www.gizmodo.jp/index.xml"},
        {"name": "PC Watch", "url": "https://pc.watch.impress.co.jp/data/rss/pcw/index.rdf"}
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
                        # 簡易的な価格抽出（もしタイトルや説明にあれば）
                        # 例：「5,980円」「19,800円」などを正規表現で探す
                        price_match = re.search(r'(\d{1,3}(?:,\d{3})+|\d+)円', title + " " + desc)
                        price = price_match.group(0) if price_match else "オープン価格/価格情報なし"
                        
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
