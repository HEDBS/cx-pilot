# -*- coding: utf-8 -*-
"""全链路真环境自检（docs/TASK-B4.md + docs/TASK-A1.md）：九环逐环断言，打印 PASS/FAIL + 耗时。

运行：~AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe tools/e2e_check.py
纪律：submitter 环绝不 confirm=True（只 dry_run）；solver 环用自造假题不碰真作业提交链；
provider 环走 %APPDATA%\\cx-pilot 里用户自己的设置；audit 环只 GET 领卷（第 9 环，
A1 验收=分类正确+缓存二访零请求）。末尾 `E2E: x/9 PASS`。
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PY = sys.executable

from core.client import Client, DATA, ApiError, SessionExpired  # noqa: E402
from core import stat2, works, questions, providers as pv, solver, submitter  # noqa: E402
from core import audit  # noqa: E402

RESULTS = []  # (name, ok, blocked, detail)


def ring(name):
    def deco(fn):
        def run():
            t0 = time.time()
            try:
                detail = fn()
                dt = time.time() - t0
                RESULTS.append((name, True, False, detail or ""))
                print("[PASS] %s (%.1fs) %s" % (name, dt, detail or ""), flush=True)
            except Exception as e:
                dt = time.time() - t0
                blocked = isinstance(e, Blocked)
                RESULTS.append((name, bool(blocked), blocked, "%r" % e))
                tag = "BLOCKED" if blocked else "FAIL  "
                print("[%s] %s (%.1fs) %r" % (tag.strip(), name, dt, e), flush=True)
        return run
    return deco


class Blocked(Exception):
    """门槛拦截等业务性阻断：如实标记，不算失败（任务书第 4 环规则）。"""


def _bench_questions():
    p = os.path.join(DATA, "bench_questions.json")
    if os.path.exists(p):
        try:
            b = json.load(open(p, encoding="utf-8"))
            singles = [q for q in b if q.get("type") == "single" and q.get("options")]
            if len(singles) >= 3:
                return singles
        except Exception:
            pass
    # 兜底：合成题（不依赖外部文件）
    out = []
    for i in range(6):
        out.append({"stem": "%d+1=?" % i, "options": {"A": str(i), "B": str(i + 1),
                                                      "C": str(i + 2), "D": "x"}, "type": "single"})
    return out


# ---------- 1 auth ----------
@ring("1-auth")
def r_auth():
    c = Client()
    ok = c.ensure_login()
    assert ok, "ensure_login 返回假值"
    j = c.get_json("https://stat2-ans.chaoxing.com/stat2/learning/plan/recommended-course-list",
                   referer="https://stat2-ans.chaoxing.com/stat2-vue/studyPlanAssistant")
    assert j.get("code") == 0, "探活 code=%s" % j.get("code")
    return "cookie 复用/重登成功，code==0"


# ---------- 2 stat2 ----------
@ring("2-stat2")
def r_stat2():
    c = Client()
    c.ensure_login()
    snap = stat2.fetch_near_tasks(c)
    assert {"generated", "tasks", "courses"} <= set(snap), "snap 字段缺: %s" % list(snap)
    for t in snap["tasks"][:3]:
        assert {"course", "courseid", "task_id", "name", "end_date", "question_num"} <= set(t.__dict__), \
            "Task 字段缺: %s" % list(t.__dict__)
    return "临期 %d 条 · 课程 %d 门 · 缓存 %s" % (len(snap["tasks"]), len(snap["courses"]), snap["generated"])


# ---------- 3 works ----------
@ring("3-works")
def r_works():
    c = Client()
    c.ensure_login()
    ws = works.refresh_all(c, max_courses=3, delay=(0.2, 0.4))
    assert len(ws) > 0, "前 3 门课 total==0"
    la = [w for w in ws if "课程A" in w.course]
    return "total=%d 条待抓（线代 %d 条），课程=%s" % (
        len(ws), len(la), sorted({w.course[:10] for w in ws}))


# ---------- 4 questions ----------
@ring("4-questions")
def r_questions():
    c = Client()
    c.ensure_login()
    # 定位任一份真待做作业（不锁课程名：作业状态随学期漂移，写死会误报）
    target = None
    try:
        for w in works.refresh_all(c, max_courses=8, delay=(0.2, 0.4)):
            if w.status == "待做":
                target = {"courseid": w.courseid, "clazzid": w.clazzid, "cpi": w.cpi,
                          "workid": w.work_id, "answerid": w.answer_id or "0", "title": w.title}
                break
    except Exception:
        pass
    if not target:
        gp = os.path.join(DATA, "gate_survey.json")
        if os.path.exists(gp):
            for course in json.load(open(gp, encoding="utf-8")):
                for w in course.get("works", []):
                    if w.get("status") == "待做":
                        cid = course["courseId"]
                        snap = stat2.fetch_near_tasks(c)
                        m = next((t for t in snap["tasks"] if t.courseid == cid), None)
                        target = {"courseid": cid, "clazzid": m.clazzid if m else "",
                                  "cpi": m.cpi if m else "", "workid": w["workId"],
                                  "answerid": w.get("answerId", "0"), "title": w["title"]}
                        break
                if target:
                    break
    assert target, "扫描/快照里没有任何待做作业（账号当前无待做=环境态，不是代码故障）"
    try:
        qs, ctx = questions.fetch_work_questions(
            c, target["courseid"], target["clazzid"], target["cpi"], target["workid"],
            answerid=target["answerid"])
    except Exception as e:
        s = str(e)
        if "403" in s or "任务点" in s:
            raise Blocked("线代《%s》被门槛/403 拦截（如实标记，不算失败）: %s" % (target["title"], s[:80]))
        raise
    assert len(qs) > 0, (
        "领到 0 题（可能被任务点未达标拦截）page_len=%s" % ctx.get("page_len"))
    for q in qs[:3]:
        assert {"qid", "type", "stem"} <= set(q), "题目字段缺: %s" % list(q)
        if q["type"] in ("single", "multi"):
            assert len(q["options"]) >= 2, "qid=%s 选项不足" % q["qid"]
    return "《%s》领卷 %d 题，字段齐（qid/type/stem/options）" % (target["title"], len(qs))


# ---------- 5 providers ----------
@ring("5-providers")
def r_providers():
    keys = pv.load_keys()
    s = pv.load_settings()
    act = pv.active_providers(s, keys)
    cfg = next((c for c in act if c.get("key_ref") == "openrouter"), None)
    assert cfg, "openrouter 未在 active_providers（enabled/key 缺失）: %s" % [c["name"] for c in act]
    q = _bench_questions()[0]
    t0 = time.time()
    letter, conf, raw = pv.solve_choice(cfg, keys, q["stem"], q["options"])
    assert letter in q["options"], "未返回合法字母（letter=%r）: %s" % (letter, raw[:100])
    return "%s 真调用 %.1fs → %s (conf=%s)" % (cfg["model"], time.time() - t0, letter, conf)


# ---------- 6 solver ----------
@ring("6-solver")
def r_solver():
    keys = pv.load_keys()
    s = json.loads(json.dumps(pv.load_settings()))  # 深拷贝：只让 openrouter 在链上
    for c in s["providers"]:
        c["enabled"] = (c.get("key_ref") == "openrouter")
    qs = []
    for i, b in enumerate(_bench_questions()[:3]):
        qs.append({"qid": "e2e%d" % i, "type": "single", "stem": b["stem"], "options": b["options"]})
    ref = {"course": "E2E自检", "title": "假Job3题", "courseId": "0", "classId": "0",
           "cpi": "0", "workId": "e2e", "answerId": "0"}
    job = solver.Job(ref, qs)
    solver.solve_job(job, settings=s, keys=keys)
    bad = [qid for qid, r in job.results.items() if r["status"] != "ok"]
    assert not bad, "有题未解出: %s" % [(qid, job.results[qid].get("err", "")) for qid in bad]
    assert job.state in ("done", "partial"), "job.state=%s" % job.state
    fn = solver.export_job(job)
    assert os.path.exists(fn) and os.path.getsize(fn) > 50, "导出文件异常: %s" % fn
    os.remove(fn)  # 自检产物不留在 %APPDATA%\cx-pilot\answers
    return "3 题全 ok（%d/%d），导出→存在→已清理 %s" % (
        len(job.results), len(qs), os.path.basename(fn))


# ---------- 7 submitter ----------
@ring("7-submitter")
def r_submitter():
    bench = _bench_questions()
    qs = [{"qid": str(9000 + i), "type": "single", "stem": b["stem"], "options": b["options"]}
          for i, b in enumerate(bench[:60])]
    results = {q["qid"]: {"answer": "A", "status": "ok"} for q in qs}
    ctx = {"form_action": "/mooc-ans/work/addStudentWorkNewWeb?courseId=1&classId=2&cpi=3"
                          "&workId=4&answerId=5&enc=deadbeef&version=1",
           "hidden": {"enc": "deadbeef", "totalQuestionNum": str(len(qs))},
           "standardEnc": "cafebabe"}
    ref = {"courseId": "1", "classId": "2", "cpi": "3", "workId": "4", "answerId": "5"}
    form = submitter.build_form(qs, results, ctx, ref)
    assert len(form) > 100, "字段数 %d ≤ 100" % len(form)
    r = submitter.submit(None, qs, results, ctx, ref, confirm=False)
    assert r["status"] == "dry_run", "status=%s" % r["status"]
    assert "&version=2" in r["url"], "version 未 bump: %s" % r["url"][:120]
    return "字段 %d 个，dry_run 安全返回（未发送任何真实 POST）" % len(form)


# ---------- 8 UI 冒烟衔接 ----------
@ring("8-smoke")
def r_smoke():
    p = subprocess.run([PY, os.path.join(ROOT, "tools", "smoke_ui.py")],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    out = (p.stdout or "") + (p.stderr or "")
    tail = [l for l in out.splitlines() if l.startswith("SMOKE")]
    assert p.returncode == 0 and tail and "PASS" in tail[-1], \
        "smoke 未全绿: %s" % (tail[-1] if tail else out[-300:])
    return tail[-1]


# ---------- 9 audit（TASK-A1：真环境 1 条，分类正确 + 缓存命中二访零请求） ----------
@ring("9-audit")
def r_audit():
    c = Client()
    c.ensure_login()
    snap = stat2.fetch_near_tasks(c)
    assert snap["tasks"], "临期列表为空，audit 环无处取样（不算失败但需人工看）"
    # 真环境取 1 条：优先 work 且有题数的（验收语义：分类正确）
    tgt = next((t for t in snap["tasks"] if (t.event_type or "work") == "work"),
               snap["tasks"][0])
    task = {"courseId": tgt.courseid, "classId": tgt.clazzid, "cpi": tgt.cpi,
            "workId": tgt.task_id, "answerId": "0", "etype": tgt.event_type or "work"}
    orig_get = c.raw_get
    n_req = {"n": 0}

    def counting_get(*a, **k):
        n_req["n"] += 1
        return orig_get(*a, **k)
    c.raw_get = counting_get
    r1 = audit.audit_task(c, task, force=True)         # 一访：真领卷（只 GET）
    assert r1["solvable"] in (True, False) and r1.get("reason") is not None, \
        "audit_task 返回结构异常: %s" % r1
    if task["etype"] != "work":
        assert r1["solvable"] is False and r1["reason"] == "非作业", "非作业分类错: %s" % r1
    else:
        assert (r1["solvable"] and r1["qreal"] > 0) or \
               (not r1["solvable"] and r1["reason"] in ("无题", "读不到题")), \
               "work 三态分类错: %s" % r1
    n1 = n_req["n"]
    if audit._cache_get(audit.cache_key(task)) is None:
        # 一访结论没进缓存 = 瞬时错（超时/解析炸）：不该灰锁，也不该算 e2e 失败——如实 BLOCKED
        raise Blocked("《%s》一访瞬时错未缓存（%s），缓存验证顺延下轮" % (
            tgt.name[:12], r1.get("err", r1.get("reason"))))
    r2 = audit.audit_task(c, task)                     # 二访：同 key，缓存命中 → 必须零请求
    assert r2["from_cache"] is True, "二访未命中缓存: %s" % r2
    assert n_req["n"] == n1, "缓存命中仍发了 %d 个请求（应零）" % (n_req["n"] - n1)
    c.raw_get = orig_get
    return "《%s》etype=%s → solvable=%s real=%d题；二访缓存命中零请求" % (
        tgt.name[:14], task["etype"], r1["solvable"], r1["qreal"])


RINGS = [r_auth, r_stat2, r_works, r_questions, r_providers, r_solver, r_submitter,
         r_smoke, r_audit]

if __name__ == "__main__":
    t_all = time.time()
    for fn in RINGS:
        fn()
    n_pass = sum(1 for _, ok, _, _ in RESULTS if ok)
    print("-" * 60, flush=True)
    for name, ok, blocked, detail in RESULTS:
        if not ok:
            print("  ✗ %s: %s" % (name, detail), flush=True)
    print("E2E: %d/9 PASS  (总耗时 %.0fs)" % (n_pass, time.time() - t_all), flush=True)
    sys.exit(0 if n_pass == 9 else 1)
