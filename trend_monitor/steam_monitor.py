import json
import urllib.request
import urllib.parse
from typing import List, Dict, Any

def get_steam_trends() -> List[Dict[str, Any]]:
    """
    Steamの公開APIからセール中（specials）および売上上位（top_sellers）のゲーム情報を取得します。
    """
    url = "https://store.steampowered.com/api/featuredcategories/?l=japanese&cc=jp"
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            trends = []
            
            # セール商品（specials）の抽出
            specials = data.get("specials", {}).get("items", [])
            for item in specials:
                # 価格の調整（Steam APIでは日本円の場合、通常100倍された値が整数で返る）
                orig_price_raw = item.get("original_price")
                final_price_raw = item.get("final_price")
                
                # 文字列で整形された価格も入っている場合があるが、数値での計算用
                orig_price = orig_price_raw / 100 if isinstance(orig_price_raw, (int, float)) else 0
                final_price = final_price_raw / 100 if isinstance(final_price_raw, (int, float)) else 0
                
                trends.append({
                    "id": item.get("id"),
                    "title": item.get("name"),
                    "discount_percent": item.get("discount_percent", 0),
                    "original_price": orig_price,
                    "final_price": final_price,
                    "currency": item.get("currency", "JPY"),
                    "url": f"https://store.steampowered.com/app/{item.get('id')}/",
                    "type": "game_sale",
                    "headline": f"【Steamセール中! {item.get('discount_percent')}%OFF】"
                })
            
            # 売上上位（top_sellers）の抽出
            top_sellers = data.get("top_sellers", {}).get("items", [])
            for item in top_sellers:
                # すでにセール情報で追加されているものはスキップ
                if any(t["id"] == item.get("id") for t in trends):
                    continue
                    
                orig_price_raw = item.get("original_price")
                final_price_raw = item.get("final_price")
                
                orig_price = orig_price_raw / 100 if isinstance(orig_price_raw, (int, float)) else 0
                final_price = final_price_raw / 100 if isinstance(final_price_raw, (int, float)) else 0
                
                trends.append({
                    "id": item.get("id"),
                    "title": item.get("name"),
                    "discount_percent": item.get("discount_percent", 0),
                    "original_price": orig_price,
                    "final_price": final_price,
                    "currency": item.get("currency", "JPY"),
                    "url": f"https://store.steampowered.com/app/{item.get('id')}/",
                    "type": "game_top_seller",
                    "headline": "【Steam売上上位！】"
                })
                
            return trends
            
    except Exception as e:
        print(f"Steam APIの取得中にエラーが発生しました: {e}")
        return []

if __name__ == "__main__":
    print("Steamのトレンド情報を取得中...")
    results = get_steam_trends()
    print(f"取得完了: {len(results)} 件のアイテムが見つかりました。")
    for r in results[:5]:
        print(f"- {r['headline']} {r['title']} : 現価格 {r['final_price']:.0f}円 (URL: {r['url']})")
