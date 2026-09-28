# -*- coding: utf-8 -*-
"""cx-pilot 数据层共享客户端：登录、请求封装、cookie 持久化、掉线自动重登。
纯标准库。凭据存 data/credentials.json（本机明文，勿外传）。"""
import base64
import http.cookiejar
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 源码根
# 产品数据目录：%APPDATA%\cx-pilot（开发与打包一致；发布产品的配置不进代码目录）
DATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "cx-pilot")
if os.environ.get("CXPilot_DATADIR"):  # 测试覆盖
    DATA = os.environ["CXPilot_DATADIR"]
os.makedirs(DATA, exist_ok=True)
LEGACY_DATA = os.path.join(BASE, "data")
COOKIE_FILE = os.path.join(DATA, "cookies.txt")
CRED_FILE = os.path.join(DATA, "credentials.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TRANSFER_KEY = b"u2oh6Vu^HWe4_AES"


class SessionExpired(Exception):
    pass


class ApiError(Exception):
    pass


def aes_encrypt(s: str) -> str:
    from aes_stdlib import aes128_cbc_encrypt
    ct = aes128_cbc_encrypt(s.encode("utf-8"), TRANSFER_KEY, TRANSFER_KEY)
    return base64.b64encode(ct).decode()


class Client:
    def __init__(self):
        os.makedirs(DATA, exist_ok=True)
        self.jar = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
        if os.path.exists(COOKIE_FILE):
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.UA = UA  # B6a：vision.download_image 按 client.UA 取头，实例上补一份

    # ---------- 底层请求 ----------
    def raw_get(self, url, referer="https://mooc1.chaoxing.com/", timeout=30, headers=None):
        h = {"User-Agent": UA, "Referer": referer,
             "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        resp = self.op.open(req, timeout=timeout)
        body = resp.read().decode("utf-8", "replace")
        # 风控/掉线特征页：~820-910B 的「温馨提示/提交失败」
        if len(body) < 1200 and ("温馨提示" in body or "提交失败" in body or "没有此页面访问权限" in body):
            raise SessionExpired("error page: " + re.sub(r"\s+", " ", strip_html(body))[:80])
        return body

    def raw_post(self, url, form: dict, referer="https://mooc1.chaoxing.com/", headers=None, timeout=30):
        data = urllib.parse.urlencode(form).encode()
        h = {"User-Agent": UA, "Referer": referer,
             "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
             "Origin": re.match(r"https://[^/]+", url).group(0)}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h)
        return self.op.open(req, timeout=timeout).read().decode("utf-8", "replace")

    def get_json(self, url, referer=None, retries=2):
        for i in range(retries + 1):
            try:
                t = self.raw_get(url, referer=referer or "https://stat2-ans.chaoxing.com/")
                # 超星 ajax 防劫持前缀 '@'
                if t[:1] in "@{":
                    t = t.lstrip("@")
                j = json.loads(t)
                if isinstance(j, dict) and j.get("msg") == "未登录":
                    raise SessionExpired("stat2 says 未登录")
                return j
            except (json.JSONDecodeError, urllib.error.URLError) as e:
                if i >= retries:
                    raise ApiError("%s @ %s" % (e, url[:100]))
                time.sleep(1.5 + random.random())

    # ---------- 登录 ----------
    def login(self, user=None, pwd=None):
        if user is None and os.path.exists(CRED_FILE):
            cred = json.load(open(CRED_FILE, encoding="utf-8"))
            user, pwd = cred.get("user"), cred.get("pwd")
        if user is None:
            import getpass
            user = input("学习通账号(手机号): ").strip()
            pwd = getpass.getpass("密码(不回显): ")
            json.dump({"user": user, "pwd": pwd}, open(CRED_FILE, "w", encoding="utf-8"))
            os.chmod(CRED_FILE, 0o600)
        html = self.raw_get("https://passport2.chaoxing.com/login?fid=-1&refer=http%3A%2F%2Fi.chaoxing.com")

        def hidden(name, default=""):
            m = (re.search(r'id="%s"[^>]*value="([^"]*)"' % name, html) or
                 re.search(r'value="([^"]*)"[^>]*id="%s"' % name, html))
            return m.group(1) if m else default

        form = {
            "fid": hidden("fid", "-1"),
            "uname": aes_encrypt(user),
            "password": aes_encrypt(pwd),
            "refer": hidden("refer", "http%3A%2F%2Fi.chaoxing.com"),
            "t": hidden("t", "true"),
            "forbidotherlogin": hidden("forbidotherlogin", "0"),
            "validate": hidden("validate", ""),
            "doubleFactorLogin": hidden("doubleFactorLogin", "0"),
            "independentId": hidden("independentId", ""),
            "independentNameId": hidden("independentNameId", ""),
        }
        t = self.raw_post("https://passport2.chaoxing.com/fanyalogin", form,
                          headers={"X-Requested-With": "XMLHttpRequest"},
                          referer="https://passport2.chaoxing.com/login?fid=-1&refer=http%3A%2F%2Fi.chaoxing.com")
        j = json.loads(t)
        if not j.get("status"):
            raise ApiError("登录失败: " + str(j.get("msg2") or j.get("msg") or j)[:120])
        self.jar.save(ignore_discard=True, ignore_expires=True)
        return True

    def ensure_login(self):
        """用 stat2 JSON 接口探活：返回 code==0 才算登录态可用，否则重登。"""
        try:
            j = self.get_json("https://stat2-ans.chaoxing.com/stat2/learning/plan/recommended-course-list",
                              referer="https://stat2-ans.chaoxing.com/stat2-vue/studyPlanAssistant")
            if j.get("code") == 0:
                return True
        except (SessionExpired, ApiError):
            pass
        return self.login()


def strip_html(s):
    s = re.sub(r'<br\s*/?>', '\n', s)
    s = re.sub(r'<[^>]+>', '', s)
    for a, b in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&amp;", "&")):
        s = s.replace(a, b)
    return re.sub(r'[ \t]+', ' ', s).strip()
