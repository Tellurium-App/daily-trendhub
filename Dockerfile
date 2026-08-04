FROM python:3.11-slim

WORKDIR /app

# 依存ライブラリのインストール
RUN pip install --no-cache-dir fastmcp requests

# サーバーコードが含まれるディレクトリをコピー
COPY trend_monitor/ /app/trend_monitor/

EXPOSE 8080

# サーバーの起動
CMD ["python", "trend_monitor/mcp_server.py"]
