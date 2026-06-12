# -*- coding: utf-8 -*-
"""簡易Webモニタ

測定PCのアプリ内でHTTPサーバーを動かし、離れたPCのブラウザから
測定状態・結果の閲覧と再測定指示（自動測定の起動）を行う。

- GET  /            … モニタページ（2秒ごとに自動更新）
- GET  /api/status  … 状態・メタ・結果のJSON
- GET  /plot.png    … ホイールグラフのPNG
- POST /api/remeasure … 再測定指示（自動測定を開始。SwitchBotで機械起動）

社内LAN内での利用を前提とする。settings.json の web_token を設定すると
URLに ?token=… を付けたアクセスだけ許可する。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>分割測定モニタ</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:sans-serif;margin:12px;background:#f4f4f4}
table{border-collapse:collapse;background:#fff;margin:4px 0}
td,th{border:1px solid #aaa;padding:2px 8px;font-size:13px}
#g{max-width:100%;border:1px solid #aaa;background:#000}
.state{font-size:20px;font-weight:bold;margin:6px 0}
.ng{color:red}
button{font-size:16px;padding:8px 24px}
#meta{color:#333;font-size:14px}
</style></head><body>
<h2>分割測定モニタ</h2>
<div class="state" id="state">--</div>
<div id="meta"></div>
<p><img id="g" src="/plot.png" alt="グラフ"></p>
<div id="results"></div>
<p><button onclick="remeasure()">再測定指示</button> <span id="msg"></span></p>
<script>
const token = new URLSearchParams(location.search).get('token') || '';
async function poll(){
  try{
    const r = await fetch('/api/status?token=' + token);
    if(!r.ok){document.getElementById('state').textContent = 'アクセス拒否'; return;}
    const s = await r.json();
    document.getElementById('state').textContent = s.state;
    const m = s.meta;
    document.getElementById('meta').textContent =
      `${m.model} ／ 機番 ${m.machine} ／ ${m.operator} ／ ${m.temperature}°C ／ ` +
      `${m.mode} （更新 ${s.timestamp}）`;
    let html = '<table>';
    for(const [k, v] of s.results){
      const cls = (typeof v === 'string' && v.startsWith('NG')) ? ' class="ng"' : '';
      html += `<tr><td>${k}</td><td${cls}>${v}</td></tr>`;
    }
    html += '</table>';
    document.getElementById('results').innerHTML = html;
    document.getElementById('g').src = '/plot.png?token=' + token + '&ts=' + Date.now();
  }catch(e){
    document.getElementById('state').textContent = '接続エラー（測定PCのアプリ停止中？）';
  }
}
async function remeasure(){
  if(!confirm('再測定を指示しますか？（現場の機械がSwitchBotで起動します）')) return;
  try{
    const r = await fetch('/api/remeasure?token=' + token, {method:'POST'});
    document.getElementById('msg').textContent = await r.text();
  }catch(e){ document.getElementById('msg').textContent = '送信失敗'; }
}
setInterval(poll, 2000); poll();
</script></body></html>"""


class WebMonitor:
    """get_status: () -> dict / get_png: () -> bytes|None /
    on_command: (str) -> None（GUIスレッドへはシグナルで渡すこと）"""

    def __init__(self, get_status, get_png, on_command, port=8765, token=""):
        self.get_status = get_status
        self.get_png = get_png
        self.on_command = on_command
        self.port = int(port)
        self.token = str(token or "")
        self.server = None

    def start(self):
        monitor = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 標準エラーへのログを抑制
                pass

            def _authorized(self):
                if not monitor.token:
                    return True
                query = parse_qs(urlparse(self.path).query)
                return (query.get("token") or [""])[0] == monitor.token

            def _send(self, code, content_type, body):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if not self._authorized():
                    self._send(403, "text/plain; charset=utf-8", "forbidden".encode())
                    return
                if path == "/":
                    self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
                elif path == "/api/status":
                    body = json.dumps(monitor.get_status(), ensure_ascii=False)
                    self._send(200, "application/json; charset=utf-8", body.encode("utf-8"))
                elif path == "/plot.png":
                    png = monitor.get_png()
                    if png:
                        self._send(200, "image/png", png)
                    else:
                        self._send(404, "text/plain; charset=utf-8", "no image".encode())
                else:
                    self._send(404, "text/plain; charset=utf-8", "not found".encode())

            def do_POST(self):
                path = urlparse(self.path).path
                if not self._authorized():
                    self._send(403, "text/plain; charset=utf-8", "forbidden".encode())
                    return
                if path == "/api/remeasure":
                    monitor.on_command("remeasure")
                    self._send(200, "text/plain; charset=utf-8",
                               "再測定指示を送りました".encode("utf-8"))
                else:
                    self._send(404, "text/plain; charset=utf-8", "not found".encode())

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server = None
