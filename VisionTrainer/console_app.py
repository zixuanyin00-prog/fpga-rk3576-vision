from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie
import argparse
import base64
import io
import json
import secrets
import console_store as db
from console_workflow import Workflow

WEB = db.ROOT / "console_web"


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Handler)
        self.token = secrets.token_urlsafe(32)
        self.workflow = Workflow()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):

        if (
            args
            and isinstance(args[0], str)
            and ("/api/state" in args[0] or "/api/preview" in args[0])
        ):
            return
        super().log_message(fmt, *args)

    def check_origin(self):
        allowed = {
            "127.0.0.1:" + str(self.server.server_port),
            "localhost:" + str(self.server.server_port),
        }
        if self.headers.get("Host") not in allowed:
            raise PermissionError("仅允许本机控制台访问")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in allowed}:
            raise PermissionError("跨源请求已拒绝")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise PermissionError("跨站请求已拒绝")

    def check_session(self, write=False):
        self.check_origin()
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        value = cookie.get("vision_console")
        if value is None or not secrets.compare_digest(value.value, self.server.token):
            raise PermissionError("页面会话已更新，请刷新页面；已有流程仍保留")
        if write and not secrets.compare_digest(
            self.headers.get("X-Console-Token", ""), self.server.token
        ):
            raise PermissionError("操作令牌无效，请刷新页面")

    def send(self, status, body, mime="application/json; charset=utf-8", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def fail(self, exc):
        self.send(
            403 if isinstance(exc, PermissionError) else 400, dict(error=str(exc))
        )

    def do_GET(self):
        try:
            self.check_origin()
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            app = self.server.workflow
            if url.path == "/api/health":
                return self.send(
                    200,
                    dict(
                        app="native-vision-console",
                        version=2,
                        active_jobs=sum(
                            j["state"] in ("queued", "running")
                            for j in app.jobs.state()
                        ),
                    ),
                )
            if url.path == "/api/bootstrap":
                return self.send(
                    200,
                    dict(token=self.server.token),
                    headers={
                        "Set-Cookie": "vision_console="
                        + self.server.token
                        + "; HttpOnly; SameSite=Strict; Path=/"
                    },
                )
            static = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if url.path in static:
                name = static[url.path]
                mime = {
                    "html": "text/html; charset=utf-8",
                    "js": "application/javascript; charset=utf-8",
                    "css": "text/css; charset=utf-8",
                }[name.split(".")[-1]]
                return self.send(200, (WEB / name).read_bytes(), mime)
            self.check_session()
            if url.path == "/api/state":
                return self.send(200, app.state(q.get("flow")))
            if url.path == "/api/board":
                return self.send(200, app.cached_board())
            if url.path == "/api/preview":
                raw, meta = app.preview()
                header = base64.b64encode(
                    json.dumps(meta, ensure_ascii=False).encode()
                ).decode()
                return self.send(200, raw, "image/bmp", {"X-Image-Meta": header})
            if url.path == "/api/image":
                p = db.load(q["flow"])
                if q.get("frame") == "probe":
                    f = p["probe"]
                else:
                    f = next(f for f in p["frames"] if f["id"] == q["frame"])
                im = db.image(p, f)
                stream = io.BytesIO()
                im.save(stream, format="BMP")
                return self.send(200, stream.getvalue(), "image/bmp")
            if url.path == "/api/download":
                p = db.load(q["flow"])
                run = q["run"]
                name = q["name"]
                if run not in {r["id"] for r in p["runs"]} or name not in {
                    "detector.pt",
                    "detector.onnx",
                    "detector_int8.rknn",
                    "detector_fp16.rknn",
                    "locator_fp16.rknn",
                    "config.json",
                    "training_manifest.json",
                }:
                    raise ValueError("下载文件不在白名单")
                return self.send(
                    200,
                    (db.folder(p["id"]) / "runs" / run / name).read_bytes(),
                    "application/octet-stream",
                    {"Content-Disposition": 'attachment; filename="' + name + '"'},
                )
            self.send(404, dict(error="接口不存在"))
        except Exception as exc:
            self.fail(exc)

    def do_POST(self):
        try:
            self.check_session(True)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请使用JSON请求")
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 4_000_000:
                raise ValueError("请求长度无效")
            args = json.loads(self.rfile.read(length))
            action = urlparse(self.path).path.removeprefix("/api/action/")
            if not isinstance(args, dict) or not self.path.startswith("/api/action/"):
                raise ValueError("操作请求无效")
            page = str(args.pop("page", ""))
            if not 1 <= len(page) <= 80:
                raise ValueError("缺少页面操作身份")
            result = self.server.workflow.dispatch(action, args, page)
            self.send(200, result)
        except Exception as exc:
            self.fail(exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18763)
    args = parser.parse_args()
    server = Server(("127.0.0.1", args.port))
    print("Native console: http://127.0.0.1:" + str(args.port), flush=True)
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.workflow.close()
        server.server_close()


if __name__ == "__main__":
    main()
