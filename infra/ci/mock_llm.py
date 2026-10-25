"""Deterministic Chat Completions SSE server for deployment wiring checks only."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = data["messages"]
        system = str(messages[0].get("content", ""))
        delta, finish = {}, "stop"
        if "引用核对器" in system:
            delta = {"content": '{"verdict":"passed","issues":[]}'}
        elif messages[-1]["role"] == "tool":
            delta = {"content": "课程资料说明：导数表示函数在一点的瞬时变化率。[1]"}
        else:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "ci-course-lookup",
                        "type": "function",
                        "function": {
                            "name": "search_course_materials",
                            "arguments": '{"query":"导数 瞬时变化率"}',
                        },
                    }
                ]
            }
            finish = "tool_calls"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        events = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 10}},
        ]
        for event in events:
            self.wfile.write(
                ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode()
            )
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
