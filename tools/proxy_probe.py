# -*- coding: utf-8 -*-
"""tools/proxy_probe.py —— M3 B1 验收探针：真实解题链不再 10061。

链路：起 server 子进程（CX_READY）→ POST /solve **自造 1 题、不限 providers**
（=盘上真实后端链，Pollinations 首位、其 pollinations_chat 无条件用 proxy——
即用户报告的炸点；自造题不碰学习通会话，判据与真领卷链的环境抖动解耦）。
断言：
  A) 全事件不含 10061/ConnectionRefused（连接拒绝被根除——直连后拿到的是
     上游 HTTP 语义，500/429 属服务健康态，非 B1 修复面）；
  B) 「后端链: A > B」= 盘上 providers 的启用序（I1：数组顺序即调用优先级）。
证据展示（不硬判，取决于盘上代理是否可达）：代理不通时的「临时直连」警告行、
或代理可达时警告缺席（=行为与旧版一致仍走代理）。
C)（M3 B1b）单家后端故障不得打穿 /solve 顶层：全事件无 {"type":"error"}，
   且每题 item 都带 status 字段（失败题=status=provider_err，非整链炸）。
退出码 0=A、B、C 全过。用法：python tools/proxy_probe.py
"""
import json
import re
import subprocess
import sys
import threading
import urllib.request

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))


def main():
    proc = subprocess.Popen([sys.executable, "-m", "server", "--port", "0"],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8")
    box = {}

    def rd():
        for line in proc.stdout:
            m = re.search(r"CX_READY port=(\d+) token=(\S+)", line)
            if m:
                box.update(port=int(m.group(1)), token=m.group(2))
                return
    t = threading.Thread(target=rd, daemon=True)
    t.start()
    t.join(20)
    if "port" not in box:
        proc.kill()
        print("FAIL: 20s 无 CX_READY")
        return 2
    base = "http://127.0.0.1:%d" % box["port"]
    hdr = {"X-CX-Token": box["token"], "Content-Type": "application/json"}

    def sse(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                     headers=hdr, method="POST")
        evs = []
        with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data: "):
                    try:
                        evs.append(json.loads(line[6:]))
                    except Exception:
                        pass
        return evs

    try:
        settings = json.loads(urllib.request.urlopen(
            urllib.request.Request(base + "/settings", headers=hdr), timeout=30).read())
        disk_chain = [c["name"] for c in settings["settings"].get("providers", []) if c.get("enabled")]
        print("[probe] 盘上 proxy=%r use_proxy_for=%r；在链=%r" % (
            settings["settings"].get("proxy"), settings["settings"].get("use_proxy_for"), disk_chain))
        evs = sse("/solve", {"questions": [{"qid": "pp1", "type": "single",
                                           "stem": "1+1 等于几？",
                                           "options": {"A": "1", "B": "2", "C": "3", "D": "4"}}],
                             "ref": {"courseId": "0", "classId": "0", "cpi": "0",
                                     "workId": "0", "answerId": "0"},
                             "job_id": "pp:1"})
        for e in evs:
            print("  ", json.dumps(e, ensure_ascii=False)[:230])
        txt = json.dumps(evs, ensure_ascii=False)

        ok_a = not any(k in txt for k in ("10061", "ConnectionRefused", "积极拒绝"))
        chain = next((e["msg"] for e in evs if e.get("type") == "log"
                      and str(e.get("msg", "")).startswith("后端链")), "")
        ok_b = (" > ".join(disk_chain)) in chain if disk_chain else bool(chain)
        warn = next((e["msg"] for e in evs if "临时直连" in str(e.get("msg", ""))), None)
        # C) M3 B1b：单家故障不得打穿顶层——无 type=error 事件，且每道领到的题都有带 status 的结果 item
        errs = [e for e in evs if e.get("type") == "error"]
        allq = {e["data"]["qid"] for e in evs if e.get("type") == "item" and "qid" in e.get("data", {})}
        statused = {e["data"]["qid"] for e in evs if e.get("type") == "item"
                    and "status" in e.get("data", {})}
        ok_c = (not errs) and bool(allq) and allq <= statused
        print("A) 全事件无 10061/拒绝: %s" % ("PASS" if ok_a else "FAIL"))
        print("B) 后端链日志=盘上启用序: %s | %r" % ("PASS" if ok_b else "FAIL", chain))
        print("C) B1b 无顶层 error 且逐题有 status: %s | errs=%s 题=%s 有结果=%s" % (
            "PASS" if ok_c else "FAIL", [e.get("msg") for e in errs], sorted(allq), sorted(statused)))
        print("证据) 代理不可达警告行: %r" % (warn or "（无——本次盘上代理可达，走代理属既有行为）"))
        return 0 if (ok_a and ok_b and ok_c) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
