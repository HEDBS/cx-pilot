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
# 真环境抓到的「被弹登录页」形态（真掉线）
PLOGIN = """<!DOCTYPE html><html><head><title>用户登录</title></head><body>
<img src="https://passport2.chaoxing.com/images/fanya/readlogo.png"/>
<h3>用户登录</h3>
<a href="https://passport2.chaoxing.com/login?refer=https%3A%2F%2Fmooc1.chaoxing.com%2Fmooc-ans%2Fwork%2FisExpire">扫码登录</a>
<div>手机号登录</div><div>新用户注册 验证码登录</div>
</body></html>"""

BODIES = {"/risk": P9010, "/normal": NORMAL, "/big": BIGOTHER, "/login": PLOGIN}


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
check("掉线登录页→SessionExpired", lambda: c.raw_get(base + "/login"), "SessionExpired")


def distinct():
    """风控页与掉线页必须抛不同异常（重登只对掉线有意义）。"""
    try:
        c.raw_get(base + "/risk")
    except RiskControl:
        pass
    else:
        raise AssertionError("风控页未抛 RiskControl")
    try:
        c.raw_get(base + "/login")
    except SessionExpired:
        pass
    else:
        raise AssertionError("登录页未抛 SessionExpired")


check("风控/掉线异常类型不混", distinct, "no-raise")


def retry_helper_exists():
    # 掉线自愈入口必须存在（领卷路径靠它自动重登一次）
    assert callable(getattr(c, "get_json_retry_login", None)), "缺 get_json_retry_login"
    assert callable(getattr(c, "recover_session", None)), "缺 recover_session"


check("自愈入口齐备", retry_helper_exists, "no-raise")


def login_page_not_self_blocked():
    """要害回归：login() 自己要 GET passport2 登录页取 token；
    请求本身就是登录页时必须放行，否则登录功能自我判死（曾真炸过）。"""
    class FakeResp:
        def __init__(self, final_url):
            self._u = final_url

        def geturl(self):
            return self._u

        def read(self):
            return PLOGIN.encode()

    class FakeOp:
        def __init__(self, final_url):
            self._u = final_url

        def open(self, req, timeout=None):
            return FakeResp(self._u)

    c2 = Client()
    # a) 请求别的页面却被弹到登录页 → 必须抛 SessionExpired
    c2.op = FakeOp("https://passport2.chaoxing.com/login?fid=-1")
    try:
        c2.raw_get("https://mooc1.chaoxing.com/mooc-ans/work/isExpire?x=1")
    except SessionExpired:
        pass
    else:
        raise AssertionError("请求非登录页被弹登录页时未抛 SessionExpired")
    # b) 请求本身就是 passport2 登录页 → 必须放行（login() 靠它取 token）
    c2.op = FakeOp("https://passport2.chaoxing.com/login?fid=-1")
    c2.raw_get("https://passport2.chaoxing.com/login?fid=-1&refer=x")


check("登录页自身请求不被误判掉线", login_page_not_self_blocked, "no-raise")


def recovery_cascade():
    """两级自愈：①重载 cookie ②真重登；每级一次，且必须真的有重试。"""
    c3 = Client()
    calls = {"n": 0, "reload": 0, "relogin": 0}

    # 前两次抛风控，第三次成功 —— 证明「重载后重试」这条路存在
    def flaky_json(url, referer=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RiskControl("【9010】测试")
        return {"code": 0, "ok": True}

    c3.get_json = flaky_json
    c3.reload_cookies = lambda: (calls.__setitem__("reload", calls["reload"] + 1), True)[1]
    c3.recover_session = lambda: (calls.__setitem__("relogin", calls["relogin"] + 1), True)[1]
    out = c3.get_json_recovering("https://mooc1.chaoxing.com/x")
    assert out.get("ok") is True, "自愈未返回结果"
    assert calls["n"] == 3, "重试次数不对: %s" % calls
    assert calls["reload"] == 1, "未走磁盘重载: %s" % calls

    # 一路失败时必须抛原异常，而不是静默吞掉
    c4 = Client()
    c4.get_json = lambda url, referer=None: (_ for _ in ()).throw(RiskControl("【9010】一直拦"))
    c4.reload_cookies = lambda: True
    c4.recover_session = lambda: True
    try:
        c4.get_json_recovering("https://mooc1.chaoxing.com/x")
    except RiskControl:
        pass
    else:
        raise AssertionError("自愈失败后未抛出原异常（静默吞错）")


check("两级自愈级联（重载→重登→抛出）", recovery_cascade, "no-raise")


def lingjuan_path_is_wrapped():
    """锁死「领卷路径不许有裸请求」——同一类 bug 已经栽过两次：
    先是 isExpire（get_json），后是首跳 getWorkStuUrl（raw_get）。两者都会让
    风控异常绕过自愈壳直接冒到用户面前（日志特征：2 秒内就失败，冷却都没跑到）。"""
    from core import questions  # noqa: E402

    PAGE = """<html><body>
    <div class="padBom50 questionLi" id="question101" typeName="单选题">
      <h3 class="mark_name">1. <span class="colorShallow">(单选题)</span>1+1=?</h3>
      <input type="hidden" id="answertype101" name="answertype101" value="0"/>
      <input type="hidden" id="answer101" name="answer101" value=""/>
      <span data="A" class="choice101 num_option fl">A</span>
      <div class="fl answer_p"><p>1</p></div>
    </div>
    <form action="/mooc-ans/work/addStudentWorkNewWeb?x=1">
      <input type="hidden" name="enc" value="e"/>
    </form></body></html>"""

    class FakeClient:
        def __init__(self):
            self.bare = []

        def raw_get(self, url, **kw):          # 裸请求：命中即说明路径没套自愈壳
            self.bare.append(url)
            raise RiskControl("【9010】测试")

        def raw_get_recovering(self, url, **kw):
            return PAGE

        def get_json_retry_login(self, url, **kw):
            return {"standardEnc": "x"}

        def get_json(self, url, **kw):
            self.bare.append(url)
            raise RiskControl("【9010】测试")

    fc = FakeClient()
    qs, ctx = questions.fetch_work_questions(fc, "1", "2", "3", "4")
    assert len(qs) == 1, "领卷没解析出题: %s" % qs
    assert not fc.bare, "领卷路径还有裸请求漏网（绕过自愈壳）: %s" % fc.bare


check("领卷路径无裸请求（全部走自愈壳）", lingjuan_path_is_wrapped, "no-raise")


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
