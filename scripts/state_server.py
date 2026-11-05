"""RESTFUL 推送模式的只读端点：GET /sensors 返回快照 JSON，供 HA rest 集成拉取。"""
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from const import get_data_dir


def start_state_server():
    port = int(os.getenv("STATE_SERVER_PORT", "8300"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/sensors":
                path = os.path.join(get_data_dir(), "sensors_state.json")
                try:
                    with open(path) as f:
                        body = f.read().encode()
                except FileNotFoundError:
                    body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path == "/healthz":
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            else:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            logging.debug(f"state_server: {fmt % args}")

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logging.info(f"state_server 已启动，端口 {port}（GET /sensors）")
    server.serve_forever()
