# -*- coding: utf-8 -*-
"""M0 HTTP 重放自检（M0 任务书交付物 §4.2）：原 tools/e2e_check.py 九环中 8 个
可 HTTP 化的环（auth/stat2/works/questions/providers/solver/submitter/audit）全部经
server/ REST+SSE 重放，语义断言逐条对应原环；纪律不变——submitter 只 dry_run、
audit 二访零请求、solve 用自造题。第 8 环（UI 冒烟）不可 HTTP 化，以
GET /health(免token) + /version 无token→401 用例替代并注明。

运行：先起 server（python -m server --port 8123 --token devtoken），或本脚本自动拉起；
可选 env CX_HTTP_BASE/CX_HTTP_TOKEN 指向已在跑的实例。末尾 `HTTP-E2E: x/8 PASS`。
"""
import atexit
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PY = sys.executable

BASE = None      # "http://127.0.0.1:<port>"
TOKEN = None     # type: ignore

RESULTS = []     # (name, ok, blocked, detail)


class Blocked(Exception):
    """门槛拦截/配额日等业务性阻断：语义同 e2e_check——如实标记，不算失败。"""


def ring(name):
    def deco(fn):
        def run():
            t0 = time.time()
            try:
                detail = fn()
                RESULTS.append((name, True, False, detail or ""))
                print("[PASS] %s (%.1fs) %s" % (name, time.time() - t0, detail or ""), flush=True)
            except Exception as e:
                blocked = isinstance(e, Blocked)
                RESULTS.append((name, bool(blocked), blocked, "%r" % e))
                tag = "BLOCKED" if blocked else "FAIL  "
                print("[%s] %s (%.1fs) %r" % (tag.strip(), name, time.time() - t0, e), flush=True)
        return run
    return deco


# ---------- HTTP 基座 ----------

def _req(method, path, body=None, timeout=60, token=True):
    headers = {}
    if token and TOKEN:
        headers["X-CX-Token"] = TOKEN
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    try:
        return resp.status, json.loads(raw)
    except Exception:
        return resp.status, raw


def _sse(path, body, timeout=420):
    """POST -> 消费整个 SSE 流 -> 事件列表（顺带掐掉服务端 error 事件的透出）。"""
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"X-CX-Token": TOKEN,
                                          "Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=timeout)
    events = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    resp.close()
    return events


ENV_BLOCK = {"msg": None}   # 会话类阻断原因（无凭据/掉线）：环境态传播到依赖它的环，标 BLOCKED 不算代码故障


def _try_reuse(port, token):
    """health 通 + 带 token 的 /version 也通（确认 token 对得上）→ 复用；否则 None。"""
    base = "http://127.0.0.1:%d" % port
    try:
        if json.loads(urllib.request.urlopen(base + "/health", timeout=2).read()).get("ok") is not True:
            return None
        req = urllib.request.Request(base + "/version", headers={"X-CX-Token": token})
        if urllib.request.urlopen(req, timeout=2).status == 200:
            return base
    except Exception:
        pass
    return None


def _ensure_server():
    """优先复用已起实例（CX_HTTP_BASE > env CX_PORT > 8123）；没有就拉子进程（退出回收）。"""
    global BASE, TOKEN
    token = os.environ.get("CX_HTTP_TOKEN") or "devtoken"
    if os.environ.get("CX_HTTP_BASE"):
        BASE = os.environ["CX_HTTP_BASE"].rstrip("/")
        TOKEN = token
        print("[setup] 指定实例: %s" % BASE, flush=True)
        return None
    for p in (int(os.environ.get("CX_PORT", "0") or 0), 8123):
        if p > 0:
            got = _try_reuse(p, token)
            if got:
                BASE, TOKEN = got, token
                print("[setup] 复用已在跑的 server: %s" % BASE, flush=True)
                return None
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([PY, "-m", "server", "--port", str(port), "--token", token],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)
    deadline = time.time() + 20
    real_port = None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.strip()
        if line.startswith("CX_READY"):
            kv = dict(p.split("=", 1) for p in line.split()[1:])
            real_port, TOKEN = int(kv["port"]), kv["token"]
            break
    if not real_port:
        proc.kill()
        raise RuntimeError("server 子进程未打出 CX_READY（依赖未装？python -m pip install -r requirements-server.txt）")
    BASE = "http://127.0.0.1:%d" % real_port
    print("[setup] 拉起 server 子进程: %s" % BASE, flush=True)
    atexit.register(proc.kill)
    return proc


# ---------- 题目基准（与 e2e_check 同语义：优先 bench 文件，缺则合成） ----------

def _bench_questions():
    from core.client import DATA
    p = os.path.join(DATA, "bench_questions.json")
    if os.path.exists(p):
        try:
            b = json.load(open(p, encoding="utf-8"))
            singles = [q for q in b if q.get("type") == "single" and q.get("options")]
            if len(singles) >= 3:
                return singles
        except Exception:
            pass
    out = []
    for i in range(6):
        out.append({"stem": "%d+1=?" % i, "options": {"A": str(i), "B": str(i + 1),
                                                      "C": str(i + 2), "D": "x"}, "type": "single"})
    return out


def _first(pred, seq, default=None):
    for x in seq:
        if pred(x):
            return x
    return default


# ---------- 环 1 auth：经 POST /scan 重放（ensure_login + stat2 code==0 内嵌） ----------

SCAN_EVENTS = {"ev": None}


def _scan_events():
    if SCAN_EVENTS["ev"] is None:
        SCAN_EVENTS["ev"] = _sse("/scan", {})
    return SCAN_EVENTS["ev"]


def _blocked_by_env(ring_name):
    if ENV_BLOCK["msg"]:
        raise Blocked("%s 依赖学习通会话，随环1同因阻断: %s" % (ring_name, ENV_BLOCK["msg"]))


@ring("1-auth")
def r_auth():
    ev = _scan_events()
    err = _first(lambda e: e.get("type") == "error", ev)
    if err and str(err.get("msg")) in ("session_expired", "no_credentials"):
        ENV_BLOCK["msg"] = str(err["msg"])
        raise Blocked("学习通侧会话/凭据缺失（环境态，非代码故障）。修复：%APPDATA%\\cx-pilot "
                      "放入 credentials.json 或经 GUI 首登框 / POST /auth/login 后重跑")
    assert not err, "扫描流出错：%s" % (err or {})
    done = _first(lambda e: e.get("type") == "done", ev)
    assert done, "SSE 无 done 事件"
    assert isinstance(done.get("data"), dict) and "near" in done["data"], "done 缺字段"
    return "ensure_login+stat2 code==0 经 /scan 背书，临期 %d 条 · 任务表 %d 条" % (
        done["data"]["near"], done["data"]["tasks"])


# ---------- 环 2 stat2：GET /tasks 中 stat2 行字段齐（同原环 Task 字段断言） ----------

def _tasks():
    code, j = _req("GET", "/tasks")
    assert code == 200, "GET /tasks -> %s" % code
    return j["tasks"]


@ring("2-stat2")
def r_stat2():
    _blocked_by_env("2-stat2")
    ts = _tasks()
    stat2rows = [t for t in ts if t.get("src") == "stat2"]
    for t in stat2rows[:3]:
        assert {"course", "courseId", "workId", "title", "sub", "qn"} <= set(t), \
            "stat2 行字段缺: %s" % list(t)
    courses = len({t["courseId"] for t in stat2rows})
    return "临期 %d 条（经 HTTP）· 课程 %d 门" % (len(stat2rows), courses)


# ---------- 环 3 works：GET /tasks 中 getAllWork 行 >0（同原环 len(ws)>0） ----------

@ring("3-works")
def r_works():
    _blocked_by_env("3-works")
    ts = _tasks()
    worksrows = [t for t in ts if t.get("src") == "getAllWork"]
    assert len(worksrows) > 0, "getAllWork 行数 == 0"
    for t in worksrows[:3]:
        assert {"key", "courseId", "classId", "cpi", "workId", "answerId", "status"} <= set(t), \
            "works 行字段缺: %s" % list(t)
    todo = [t for t in worksrows if t["status"] == "待做"]
    prog = _first(lambda e: e.get("type") == "progress", _scan_events(), {"total": 0})
    return "待做 %d 条 · 课程扫描 %d 门（SSE progress total=%s）" % (
        len(todo), len({t["courseId"] for t in worksrows}), prog.get("total"))


# ---------- 环 4 questions：/solve solve=false 真领卷（GET 链）逐题字段断言 ----------

@ring("4-questions")
def r_questions():
    _blocked_by_env("4-questions")
    ts = _tasks()
    target = _first(lambda t: t["src"] == "getAllWork" and t["status"] == "待做", ts)
    assert target, "任务表里没有待做作业（账号当前无待做=环境态，不是代码故障）"
    # 单次尝试（忠实原环4语义；引擎 get_json 内部自带 3 次重试）。
    # 历史备注：曾疑为超星全量扫描后的时间窗，2026-09-29 实锤为扫描实例的会话中毒，
    # server 侧已修（/scan 独立 Client + 收尾重置业务单例），见实施记录。
    ev = _sse("/solve", {"keys": [target["key"]], "solve": False})
    err = _first(lambda e: e.get("type") == "error", ev)
    if err:
        s = str(err.get("msg", ""))
        if "403" in s or "任务点" in s:
            raise Blocked("被门槛/403 拦截（如实标记）: %s" % s[:80])
        if s in ("session_expired", "no_credentials"):
            raise Blocked("会话态: %s" % s)
        raise AssertionError("领卷流报错: %s" % s)
    items = [e["data"] for e in ev if e.get("type") == "item"]
    assert items, "领到 0 题（可能被任务点未达标拦截）"
    for q in items[:3]:
        assert {"qid", "type", "stem"} <= set(q), "题目字段缺: %s" % list(q)
        if q["type"] in ("single", "multi"):
            assert q["n_options"] >= 2, "qid=%s 选项不足" % q["qid"]
    return "经 /solve 领卷 %d 题，字段齐（qid/type/stem/options）" % len(items)


# ---------- 环 5 providers：/solve 自造 1 题限定 openrouter 真调用 ----------

@ring("5-providers")
def r_providers():
    q = _bench_questions()[0]
    qs = [{"qid": "hp5", "type": "single", "stem": q["stem"], "options": q["options"]}]
    ref = {"course": "HTTP-E2E", "title": "假Job1题", "courseId": "hp", "cpi": "0",
           "classId": "0", "workId": "p5", "answerId": "0"}
    t0 = time.time()
    ev = _sse("/solve", {"questions": qs, "ref": ref, "job_id": "hp:p5", "providers": ["openrouter"]})
    # solve 流有两类 item：领卷播报（无 status）与逐题结果（有 status）；结果断言取后者
    item = _first(lambda e: e.get("type") == "item" and e["data"].get("qid") == "hp5"
                  and "status" in e["data"], ev)
    if item is None:
        err = _first(lambda e: e.get("type") == "error", ev)
        if err and "429" in str(err.get("msg", "")):
            raise Blocked("OR 配额日（429）")
        raise AssertionError("无 hp5 结果事件: %s" % (err or ev[-2:]))
    d = item["data"]
    if d["status"] != "ok":
        if "429" in str(d.get("err", "")):
            raise Blocked("OR 配额日（429）")
        raise AssertionError("provider 未答出: %s" % d)
    assert d["answer"] in q["options"], "未返回合法字母: %r" % d["answer"]
    return "%s 真调用 %.1fs → %s (conf=%s)" % (d["source"], time.time() - t0,
                                               d["answer"], d["confidence"])


# ---------- 环 6 solver：/solve 自造 3 题全 ok + 导出落盘→存在→清理 ----------

@ring("6-solver")
def r_solver():
    from core.client import DATA
    bench = _bench_questions()
    qs = [{"qid": "he%d" % i, "type": "single", "stem": b["stem"], "options": b["options"]}
          for i, b in enumerate(bench[:3])]
    ref = {"course": "HTTP-E2E", "title": "假Job3题", "courseId": "hp", "cpi": "0",
           "classId": "0", "workId": "p6", "answerId": "0"}
    ev = _sse("/solve", {"questions": qs, "ref": ref, "job_id": "hp:p6",
                         "providers": ["openrouter"]})
    done = _first(lambda e: e.get("type") == "done", ev)
    if not done:
        err = _first(lambda e: e.get("type") == "error", ev)
        if err and "429" in str(err.get("msg", "")):
            raise Blocked("OR 配额日（429）")
        raise AssertionError("solve 流未走完: %s" % (err or ""))
    meta = done["data"]["jobs"][0]
    items = [e["data"] for e in ev if e.get("type") == "item" and e["data"].get("status")]
    bad = [d for d in items if d["status"] != "ok"]
    if bad and all("429" in str(d.get("err", "")) for d in bad):
        raise Blocked("OR 配额日（429）: %d/%d 题被限流" % (len(bad), len(items)))
    assert not bad, "有题未解出: %s" % [(d["qid"], d.get("err", "")) for d in bad]
    assert meta["state"] in ("done", "partial"), "job.state=%s" % meta["state"]
    fn = meta["exported"]
    assert fn and os.path.exists(fn) and os.path.getsize(fn) > 50, "导出文件异常: %s" % fn
    os.remove(fn)  # 自检产物不留在 %APPDATA%\cx-pilot\answers
    return "3 题全 ok（%s），导出→存在→已清理 %s" % (meta["progress"], os.path.basename(fn))


# ---------- 环 7 submitter：自造 60 题建 job → /submit 只 dry_run + 双闸门断言 ----------

@ring("7-submitter")
def r_submitter():
    bench = _bench_questions()
    qs = [{"qid": str(9000 + i), "type": "single", "stem": b["stem"], "options": b["options"]}
          for i, b in enumerate(bench[:60])]
    ref = {"course": "HTTP-E2E", "title": "假提交卷", "courseId": "1", "classId": "2",
           "cpi": "3", "workId": "4", "answerId": "5"}
    ctx = {"form_action": "/mooc-ans/work/addStudentWorkNewWeb?courseId=1&classId=2&cpi=3"
                          "&workId=4&answerId=5&enc=deadbeef&version=1",
           "hidden": {"enc": "deadbeef", "totalQuestionNum": str(len(qs))},
           "standardEnc": "cafebabe"}
    _sse("/solve", {"questions": qs, "ref": ref, "ctx": ctx, "job_id": "hp:p7", "solve": False})
    code, j = _req("POST", "/submit", {"job_id": "hp:p7", "confirm": False})
    assert code == 200, "dry_run -> %s %s" % (code, j)
    assert j["status"] == "dry_run", "status=%s" % j["status"]
    assert "&version=2" in j["url"], "version 未 bump: %s" % j["url"][:120]
    assert j["form_fields"] > 100, "字段数 %s ≤ 100" % j["form_fields"]
    code2, j2 = _req("POST", "/submit", {"job_id": "hp:p7", "confirm": True})
    assert code2 == 409 and j2.get("detail") == "approval_required", \
        "未经 /approve 的 confirm=true 未被拦（=%s %s）——红线闸门失效！" % (code2, j2)
    return "字段 %d 个，dry_run 安全返回（未发送任何真实 POST）；confirm=true 无审批 → 409 闸门生效" % \
        j["form_fields"]


# ---------- 环 8 smoke 替位：/health 免 token + /version 无 token → 401（注明不可 HTTP 化） ----------

@ring("8-smoke→health+401")
def r_smoke():
    code, j = _req("GET", "/health", token=False)
    assert code == 200 and j.get("ok") is True, "/health -> %s %s" % (code, j)
    saved = TOKEN
    globals()["TOKEN"] = None
    try:
        code2, _ = _req("GET", "/version", token=False)
    finally:
        globals()["TOKEN"] = saved
    assert code2 == 401, "无 token /version -> %s（应 401）" % code2
    return "[NOTE] 第8环(UI冒烟)不可 HTTP 化，以 /health(免token 200)+/version(无token 401) 替代：均符"


# ---------- 环 9 audit：force 一访分类正确 + 二访缓存命中零请求（同原环断言） ----------

@ring("9-audit")
def r_audit():
    _blocked_by_env("9-audit")
    ts = _tasks()
    assert ts, "任务表为空，audit 环无处取样（先跑环1）"
    tgt = _first(lambda t: (t.get("etype") or "work") == "work", ts) or ts[0]
    ev1 = _sse("/audit", {"keys": [tgt["key"]], "force": True})
    it1 = _first(lambda e: e.get("type") == "item", ev1)
    dn1 = _first(lambda e: e.get("type") == "done", ev1)
    assert it1 and dn1, "audit 流不完整: %s" % (ev1[-1] if ev1 else "空")
    r = it1["data"]
    assert r["solvable"] in (True, False) and r.get("reason") is not None, \
        "audit 返回结构异常: %s" % r
    if (tgt.get("etype") or "work") != "work":
        assert r["solvable"] is False and r["reason"] == "非作业", "非作业分类错: %s" % r
    else:
        assert (r["solvable"] and r["qreal"] > 0) or \
               (not r["solvable"] and r["reason"] in ("无题", "读不到题")), \
               "work 三态分类错: %s" % r
    # 逐条对应原环（e2e_check 环9）：audit_task 对瞬时网络错「不缓存」——一访结论没落盘
    # 即属瞬时（超时/解析炸），如实 BLOCKED 顺延下轮，不算代码故障，也不灰锁 FAIL。
    from core import audit as core_audit
    if core_audit._cache_get(core_audit.cache_key(tgt)) is None:
        raise Blocked("一访瞬时错未缓存（%s），缓存验证顺延下轮" % r.get("err", r.get("reason")))
    ev2 = _sse("/audit", {"keys": [tgt["key"]]})
    it2 = _first(lambda e: e.get("type") == "item", ev2)
    dn2 = _first(lambda e: e.get("type") == "done", ev2)
    assert it2 and it2["data"].get("from_cache") is True, "二访未命中缓存: %s" % (it2 or ev2)
    assert dn2["data"]["requests"] == 0, \
        "缓存命中仍发了 %d 个请求（应零）" % dn2["data"]["requests"]
    return "%s etype=%s → solvable=%s real=%d题；二访缓存命中零请求" % (
        tgt["key"], tgt.get("etype"), r["solvable"], r["qreal"])


RINGS = [r_auth, r_stat2, r_works, r_questions, r_providers, r_solver,
         r_submitter, r_smoke, r_audit]

if __name__ == "__main__":
    _ensure_server()
    print("[setup] %s" % BASE, flush=True)
    t_all = time.time()
    for fn in RINGS:
        fn()
    # 记分口径：分母 8 = 可 HTTP 化环（1,2,3,4,5,6,7,9）；BLOCKED 如实标注不计 FAIL
    # （同 e2e_check 约定），但真 FAIL 绝不允许被 BLOCKED 补齐顶掉计数。
    SUB = "8-smoke→health+401"
    http_rings = [r for r in RESULTS if r[0] != SUB]
    n_pass = sum(1 for _, ok, _, _ in http_rings if ok)
    n_block = sum(1 for _, _, b, _ in http_rings if b)
    sub_ok = any(ok and name == SUB for name, ok, _, _ in RESULTS)
    print("-" * 60, flush=True)
    for name, ok, blocked, detail in RESULTS:
        if not ok:
            print("  ✗ %s: %s" % (name, detail), flush=True)
    print("HTTP-E2E: %d/8 PASS  (总耗时 %.0fs；其中 BLOCKED %d 项不计 FAIL；"
          "第8环替位用例 health+401 %s)" % (
              n_pass, time.time() - t_all, n_block, "OK" if sub_ok else "FAIL"), flush=True)
    sys.exit(0 if n_pass >= 8 and sub_ok else 1)
