# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Temporary mock LLM endpoint for R6/R8-6 manual testing.

Returns HTTP 402 (insufficient balance) or 401 (key invalid) for
/chat/completions, so the relay error_class chain can be exercised
without a real zero-balance account. NOT part of the project; delete
after testing.

Modes (set via MOCK_MODE env or first arg): 402 | 401 | 401once
  402     -> always 402 with a 余额不足 body
  401     -> always 401 with an invalid_token body
  401once -> first /chat/completions call 401, subsequent calls 402
             (lets you see recoverFromKeyInvalid then balance)
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MOCK_MODE", "402")).strip()
_calls = {"chat": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
        sys.stderr.write("[mock] " + (fmt % args) + "\n")

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        if "chat/completions" in self.path:
            _calls["chat"] += 1
            if MODE == "402":
                self._send(402, {"error": {"message": "insufficient balance / 余额不足", "type": "insufficient_quota"}})
            elif MODE == "401":
                self._send(401, {"error": {"message": "invalid_token: device key expired", "type": "invalid_request_error"}})
            elif MODE == "401once":
                if _calls["chat"] == 1:
                    self._send(401, {"error": {"message": "invalid_token: device key expired", "type": "invalid_request_error"}})
                else:
                    self._send(402, {"error": {"message": "insufficient balance / 余额不足", "type": "insufficient_quota"}})
            else:
                self._send(500, {"error": {"message": "mock unknown mode"}})
        else:
            self._send(404, {"error": {"message": "mock: only /chat/completions"}})

    def do_GET(self):
        # /models probe etc.
        self._send(200, {"object": "list", "data": [{"id": "mock-model"}]})


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "8123"))
    print(f"[mock] relay mock listening on 127.0.0.1:{port} mode={MODE}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
