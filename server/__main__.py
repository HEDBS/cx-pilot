# -*- coding: utf-8 -*-
"""python -m server —— 起 sidecar（uvicorn，仅绑 127.0.0.1）。

端口：--port > env CX_PORT > 0（0=自选空闲端口）。
Token：--token > env CX_TOKEN > secrets.token_urlsafe(32) 自生成。
启动后 stdout 单行 `CX_READY port=<n> token=<t>` 供 Rust 侧读取（勿在此行前打印任何内容）。
"""
import argparse
import os
import socket
import sys

import uvicorn

from server import VERSION
from server.app import create_app


def pick_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m server",
                                 description="cx-pilot sidecar (FastAPI)")
    ap.add_argument("--port", type=int, default=None, help="缺省读 env CX_PORT；<=0 自选空闲端口")
    ap.add_argument("--token", default=None, help="缺省读 env CX_TOKEN；空则自生成")
    ap.add_argument("--version", action="version", version="cx-pilot sidecar " + VERSION)
    args = ap.parse_args(argv)

    port = args.port if args.port is not None else int(os.environ.get("CX_PORT", "0") or 0)
    if port <= 0:
        port = pick_free_port()

    app = create_app(args.token or os.environ.get("CX_TOKEN") or None)
    token = app.state.cx_token
    sys.stdout.write("CX_READY port=%d token=%s\n" % (port, token))
    sys.stdout.flush()

    config = uvicorn.Config(app, host="127.0.0.1", port=port,   # 红线：绝不 bind 0.0.0.0
                             log_level="warning", access_log=False)
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
