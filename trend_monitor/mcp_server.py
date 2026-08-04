import os
import requests
from fastmcp import FastMCP

# サーバーの初期化
mcp = FastMCP("Trend Monitor Trigger Server")

@mcp.tool()
def trigger_trend_monitor() -> str:
    """
    GitHub Actions のデプロイワークフローを即時起動し、
    最新のトレンド（Steamセール情報、ガジェットRSS）を取得・集計してブログを更新します。
    """
    # Cloud Run に設定する環境変数から GitHub トークンを取得
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return "エラー: 環境変数 GITHUB_TOKEN が設定されていません。Cloud Run の設定を確認してください。"
        
    owner = "Tellurium-App"
    repo = "daily-trendhub"
    workflow_id = "deploy_trendhub.yml"
    
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    data = {
        "ref": "main"
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 204:
            return (
                "GitHub Actions のブログ更新ワークフローを即時トリガーしました！\n"
                "数分以内に自動でトレンド情報の収集、アフィリエイトリンクの生成、"
                "ブログのビルド、および GitHub Pages へのデプロイが完了しますっ！🌻"
            )
        else:
            return f"ワークフローのトリガーに失敗しました。ステータスコード: {response.status_code}\n応答内容: {response.text}"
    except Exception as e:
        return f"GitHub API との通信中にエラーが発生しました: {str(e)}"

if __name__ == "__main__":
    # Gemini Sparkから外部アクセスできるように SSE トランスポートを有効化
    port = int(os.environ.get("PORT", 8080))
    mcp.run(transport="sse", host="0.0.0.0", port=port)
