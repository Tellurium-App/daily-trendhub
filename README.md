# TrendHub

**https://tellurium-app.github.io/daily-trendhub/**

Steamのセール・売上上位ゲーム、Epic Gamesの無料配布、Steamの基本プレイ無料ゲーム、放送中アニメの話題作、ガジェット系ニュースを毎日自動で集計して公開している情報サイトです。

## 見られるもの

- **日別ページ**：毎日のセール・無料配布・ランキングの記録（`/YYYY/MM/DD/`）
- **月間ランキング**：1か月間Steamの売上上位に載り続けたゲームのTOP10（`/monthly/YYYY/MM/`）
- **履歴からわかる情報**：初登場、何日連続でランクインしているか、記録上何回目のセールか、前回のセールはいつまでだったか

## データの取得元

| 種類 | 取得元 |
|---|---|
| ゲーム（セール・売上上位） | Steamストア |
| 無料ゲーム | Epic Games ストア（無料配布）、Steamストア（基本プレイ無料） |
| アニメ | AniList |
| ガジェット | Gizmodo Japan、PC Watch のRSS |

## 仕組み

GitHub Actions が毎日1回 `trend_monitor/main_aggregator.py` を実行し、`data/trend_report.csv` に記録を追記して `docs/` に静的ページを生成します。公開は GitHub Pages です。
