# -*- coding: utf-8 -*-
"""RiskControl 检测单测：本地起 http 服务喂合成页面，零真实请求。"""
import http.server
import socketserver
import sys
import threading

sys.path.insert(0, ".")

from core.client import Client, RiskControl, SessionExpired  # noqa: E402

# 真环境抓到的 9010 罚站页（精简但保留判定特征）
P9010 = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>提示页面</title>
<style type="text/css">*{margin:0;padding:0;}</style></head><body>
<div class="yzmTips"><p>【9010】操作异常，请输入图片中的验证码</p>
<form action="/html/processVerify.ac" onsubmit="return /[0-9a-zA-Z]{4}/g.test(document.getElementById('ucode').value)">
<input type="hidden" name="app" value="0"/>
<input type="text" id="ucode" name="ucode" maxlength="4"/>
<img id="ccc" src="/processVerifyPng.ac?t=1" width="104" height="44"/>
<input type="submit" value="提交"/></form></div></body></html>"""
NORMAL = "<html><body>" + "x" * 3000 + "<a class='courseName' href='/visit/stucoursemiddle?courseid=1'>课</a></body></html>"
BIGOTHER = "<html><body>" + "y" * 9000 + "</body></html>"

BODIES = {"/risk": P9010, "/normal": NORMAL, "/big": BIGOTHER}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = BODIES.get(self.path, b"<html>other</html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

passed = failed = 0


def check(name, fn, expect):
    global passed, failed
    try:
        fn()
        got = "no-raise"
    except Exception as e:
        got = type(e).__name__
    ok = got == expect
    passed += ok
    failed += not ok
    print("  [%s] %-34s 期望=%-14s 实得=%s" % ("PASS" if ok else "FAIL", name, expect, got))


c = Client()
base = "http://127.0.0.1:%d" % port
print("=== RiskControl 检测 ===")
check("9010 罚站页", lambda: c.raw_get(base + "/risk"), "RiskControl")
check("常规页面(不误报)", lambda: c.raw_get(base + "/normal"), "no-raise")
check("大页面(不误报)", lambda: c.raw_get(base + "/big"), "no-raise")


def msg_has_code():
    try:
        c.raw_get(base + "/risk")
    except RiskControl as e:
        assert "9010" in str(e), "异常原因未带编号: %s" % e
        return
    raise AssertionError("未抛出")


check("异常原因含【9010】", msg_has_code, "no-raise")

print()
print("RISK: %d/%d PASS" % (passed, passed + failed))
srv.shutdown()
sys.exit(0 if failed == 0 else 1)
