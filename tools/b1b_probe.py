# -*- coding: utf-8 -*-
"""tools/b1b_probe.py —— M3 B1b 后端链容错 + C4 干净首启 取证探针。

B1b（单元级，确定性，不依赖上游健康度）：
  1) pollinations_chat 上游 HTTP 500 → 最终归一 ProviderError，开卷 3 次（1+2 重试）+ 短退避；
  2) HTTP 401 → 直接 ProviderError，只开卷 1 次（不重试）；
  3) socket 超时 → ProviderError；
  4) 成功路径仍返回文本（签名/返回类型不变）；
  5) solver 侧零改动：它只 except ProviderError（core/solver.py:174 区域），上游归一后链自然换下一家。
C4（模拟陌生机器干净首启：CXPilot_DATADIR 指向临时空目录起 server 子进程）：
  a) GET /settings 默认 proxy=="" / use_proxy_for==[] / vision_providers==[]（C1：默认无本机味道）；
  b) providers=["__none__"]（限定空链）自造题走 /solve：有「!! 没有可用的解题后端」log 事件、
     无 type=error 顶层事件、done 中 state=="failed"（不静默卡 running；UI 日志屏按 "!!" 红色渲染）；
  c) vision_providers 为空时 resolve_image_text 返回 (None,"none")——识图缺失不阻塞解题链。
退出码 0=全过。用法：python tools/b1b_probe.py
"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import providers as pv  # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  ✓ " if ok else "  ✗ ") + name + ("  " + detail if detail else ""))


def _http_error(code):
    return urllib.error.HTTPError("https://text.pollinations.ai/x", code,
                                  "Err", {}, io.BytesIO(b"upstream-body"))


def part_b1b():
    print("### B1b：pollinations_chat 异常归一（mock 决定论）")
    calls = {"open": 0}

    def opener_for(side_effect):
        op = mock.Mock()

        def _open(req, timeout=None):
            calls["open"] += 1
            se = side_effect(min(calls["open"], 3) - 1) if callable(side_effect) else side_effect
            if isinstance(se, BaseException):
                raise se
            r = mock.Mock()
            r.read.return_value = se
            return r
        op.open.side_effect = _open
        return op

    # 1) HTTP 500 ×3 → ProviderError，3 次尝试，退避 2s/5s（与 chat() 同梯度，末次不空等）
    messages = [{"role": "user", "content": "1+1?"}]
    for label, se, want_opens, want_sleeps in [
        ("500→重试后 ProviderError", lambda i: _http_error(500), 3, [2, 5]),
        ("401→直抛不重试", lambda i: _http_error(401), 1, []),
        ("超时→重试后 ProviderError", lambda i: socket.timeout("timed out"), 3, [2, 5]),
        ("成功→原样返回文本", lambda i: b"B", 1, []),
    ]:
        calls["open"] = 0
        sleeps = []
        with mock.patch.object(pv, "_opener", return_value=opener_for(se)), \
             mock.patch.object(pv.time, "sleep", side_effect=sleeps.append):
            try:
                got = pv.pollinations_chat({"model": "openai"}, {}, messages, timeout=1)
                err = None
            except pv.ProviderError as e:
                got, err = None, e
        if label.startswith("成功"):
            check(label, got == "B" and calls["open"] == want_opens,
                  "返回=%r open=%d" % (got, calls["open"]))
        else:
            check(label, isinstance(err, pv.ProviderError) and calls["open"] == want_opens
                  and sleeps == want_sleeps,
                  "err=%r open=%d sleeps=%r" % (type(err).__name__, calls["open"], sleeps))
    # 401 消息含状态码（对齐 chat() 的 "HTTP %s" 格式）
    calls["open"] = 0
    with mock.patch.object(pv, "_opener", return_value=opener_for(lambda i: _http_error(403))), \
         mock.patch.object(pv.time, "sleep"):
        try:
            pv.pollinations_chat({"model": "openai"}, {}, messages, timeout=1)
            m = ""
        except pv.ProviderError as e:
            m = str(e)
    check("401/403 消息含 HTTP 码", "HTTP 403" in m, repr(m[:40]))
    # 5) solver 视角：solve_choice 只会以 ProviderError 失败（不再有裸 HTTPError 打穿链）
    calls["open"] = 0
    leaked = None
    with mock.patch.object(pv, "_opener", return_value=opener_for(lambda i: _http_error(500))), \
         mock.patch.object(pv.time, "sleep"):
        try:
            pv.solve_choice({"kind": "pollinations", "name": "P", "model": "openai"},
                            {}, "1+1?", {"A": "1", "B": "2"})
        except pv.ProviderError:
            pass
        except Exception as e:  # 任何非 ProviderError 泄漏 = 链被打穿
            leaked = e
    check("solve_choice 对外只泄 ProviderError", leaked is None, repr(leaked))


def part_c4():
    print("### C4：干净 DATA 首启（CXPilot_DATADIR=临时空目录）")
    tmp = tempfile.mkdtemp(prefix="cx-clean-boot-")
    env = dict(os.environ, CXPilot_DATADIR=tmp)
    proc = subprocess.Popen([sys.executable, "-m", "server", "--port", "0"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    box = {}

    def rd():
        for line in proc.stdout:
            m = re.search(r"CX_READY port=(\d+) token=(\S+)", line)
            if m:
                box.update(port=int(m.group(1)), token=m.group(2))
                return
    threading.Thread(target=rd, daemon=True).start()
    import time as _t
    deadline = _t.time() + 20
    while _t.time() < deadline and "port" not in box:
        _t.sleep(0.1)
    if "port" not in box:
        proc.kill()
        check("server 冷启（空 DATA）", False, "20s 无 CX_READY")
        return
    check("server 冷启（空 DATA）", True, "port=%d" % box["port"])
    base = "http://127.0.0.1:%d" % box["port"]
    hdr = {"X-CX-Token": box["token"], "Content-Type": "application/json"}
    try:
        # a) 默认设置无本机味道
        st = json.loads(_req(base + "/settings", hdr))
        s = st["settings"]
        check("a1 默认 proxy==''（直连）", s.get("proxy") == "", repr(s.get("proxy")))
        check("a2 默认 use_proxy_for==[]", s.get("use_proxy_for") == [], repr(s.get("use_proxy_for")))
        check("a3 默认 vision_providers==[]", s.get("vision_providers") == [],
              repr(s.get("vision_providers")))
        check("a4 默认链仅匿名 Pollinations（零 key 可跑）",
              [p["name"] for p in s.get("providers", []) if p.get("enabled")] ==
              ["Pollinations(匿名,慢)"], repr([p["name"] for p in s.get("providers", []) if p.get("enabled")]))
        check("a5 DATA 落临时目录（未写真实 %APPDATA%）",
              os.path.isdir(tmp) and not os.path.exists(os.path.join(tmp, "settings.json")),
              "dir=%s" % os.path.basename(tmp))
        # b) 空链引导：限定 providers=["__none__"] → 全部禁用
        body = {"questions": [{"qid": "cb1", "type": "single", "stem": "1+1?",
                               "options": {"A": "1", "B": "2"}}],
                "ref": {"courseId": "0", "classId": "0", "cpi": "0", "workId": "0",
                        "answerId": "0"},
                "job_id": "cb:1", "providers": ["__none__"]}
        evs = _sse(base + "/solve", body, hdr)
        guide = next((e["msg"] for e in evs if "没有可用的解题后端" in str(e.get("msg", ""))), None)
        check("b1 空链引导事件（!! 前缀=UI 日志屏红色语义）",
              bool(guide) and guide.startswith("!! "), repr(guide))
        check("b2 无 type=error 顶层事件",
              not any(e.get("type") == "error" for e in evs),
              repr([e for e in evs if e.get("type") == "error"]))
        done = next((e for e in evs if e.get("type") == "done"), None)
        stt = (done or {}).get("data", {}).get("jobs", [{}])[0].get("state")
        check("b3 done 呈 state=failed（不静默卡 running）", stt == "failed", repr(stt))
        # c) 识图后端为空不阻塞（单元级：默认 settings 直查降级出口）
        from core import vision
        txt, src = vision.resolve_image_text(b"not-an-image", settings=s, keys={})
        check("c1 vision_providers 空 → (None,'none') 走人工不抛错",
              txt is None and src == "none", repr((txt, src)))
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()


def _req(url, hdr):
    return urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30).read()


def _sse(url, body, hdr):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=hdr, method="POST")
    evs = []
    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data: "):
                try:
                    evs.append(json.loads(line[6:]))
                except Exception:
                    pass
    return evs


def main():
    part_b1b()
    part_c4()
    print("B1B-C4: %d/%d PASS" % (len(PASS), len(PASS) + len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
