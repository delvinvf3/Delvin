import os

import stock_screener_app as app


REBUILT_BADGE = """
      <div class="metric" style="border-color:#2f6f4e;background:#eff8f2;margin-top:8px;">
        <small>Current app version</small>
        <strong>Rebuilt RSI version</strong>
        <div class="muted">Use this page if older ports are not showing changes. RSI is visible above the chart.</div>
      </div>
"""


app.HTML = (
    app.HTML.replace("<title>Delvin Stock Screener</title>", "<title>Delvin Stock Screener - Rebuilt RSI</title>")
    .replace("<h1>Delvin Stock Screener</h1>", "<h1>Delvin Stock Screener</h1>")
    .replace('<span id="status">Ready</span>', '<span id="status">Rebuilt RSI version</span>')
    .replace('<div class="metrics" id="quoteMetrics"></div>', REBUILT_BADGE + '\n        <div class="metrics" id="quoteMetrics"></div>')
)


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8890"))
    server = app.ThreadingHTTPServer((host, port), app.Handler)
    print(f"Rebuilt stock screener running on {host}:{port}")
    print(f"Local URL: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
