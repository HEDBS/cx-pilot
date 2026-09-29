# -*- coding: utf-8 -*-
"""server/app.py —— FastAPI 薄封装：12 路由 + token 中间件 + SSE + 异常归一。

纪律（M0 任务书：sidecar 规格文档 §1 红线/§2 端点表 + M3b 任务书 R2/R3）：
- 只调 core/ 引擎 API，不重造任何协议；提交链全走 submitter（dry_run 默认，confirm 双闸门）。
- 不 import flet / core.theme / app_v2 / ui。
- 长任务（/scan /audit /solve /submit）全局单锁，占用中再来 → 409 {"detail":"busy","current":…}。
- 取消（R2）在 server 层的 emit 检查里抛 CancelledError 借既有 finally 放锁——core/ 零改动；
  取消绝不改变提交协议、绝不触发任何 POST；submit 真 POST 不可回滚（红线，见 /cancel note）。
- 日志/错误信息不回显 token/cookie；错误截 200 字。
"""
import base64
import hmac
import io
import json
import os
import queue
import random
import re
import secrets
import threading
import time
import urllib.parse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core import audit as core_audit
from core import providers as pv
from core import questions as core_questions
from core import solver as core_solver
from core import stat2 as core_stat2
from core import submitter as core_submitter
from core import works as core_works
# RiskControl=超星风控罚站；scan worker 显式归因用，绝不再静默 0 条
from core.client import Client, NoCredentials, RiskControl, SessionExpired, save_credentials

from server import ENGINE, VERSION
from server.state import (AUDIT_SLEEP, BusyError, CancelledError, STATE,
                          apply_audit_result, jobs_view, merge_tasks,
                          norm_stat2_tasks, norm_works_rows, tasks_view)

CANCELLABLE_MODES = ("scan", "audit", "solve", "submit")   # M3b R2
_CANCEL_WAIT_S = 5.0                                        # /cancel 等锁释放上限（任务书 ≤5s）

# uvicorn 侧禁止 access log 打印（配合下面 sanitize：绝不落 token/cookie）
_SCAN_DELAY = (0.2, 0.4)   # 与 e2e_check 同节奏，避免全量扫描过慢


# ---------- 脱敏 ----------

def sanitize(msg):
    """错误归一：压空白、截 200 字，抹掉 cookie/token/凭据形态。"""
    s = re.sub(r"\s+", " ", str(msg))
    s = re.sub(r"(?i)(cookie|set-cookie|token|password|passwd|pwd|authorization)\s*[=:]\s*[^;&\s]+",
               r"\1=***", s)
    s = re.sub(r"sk-[A-Za-z0-9._-]{4,}", "sk-***", s)
    return s[:200]


def mask_key(v):
    v = str(v or "")
    return ("sk-***" + v[-4:]) if len(v) >= 4 else ("***" if v else "")


# ---------- SSE 管线 ----------

def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


class _Capture(io.TextIOBase):
    """引擎 print()（works.refresh_all 等）捕获 -> SSE log 事件。
    顺带把真实课程名留在本机 HTTP 流里（用户自己的数据），不进 server stdout。"""

    def __init__(self, q):
        self.q = q
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.q.put({"type": "log", "msg": line.strip()})
        return len(s)

    def flush(self):
        if self._buf.strip():
            self.q.put({"type": "log", "msg": self._buf.strip()})
        self._buf = ""


def stream_response(mode, worker):
    """worker(emit) 在独立线程跑引擎阻塞调用；事件经队列出 SSE。
    单锁在 worker 线程结束时释放（客户端断开不提前放锁——任务还在跑就该 busy）。

    M3b R2 真取消：emit 兼任取消检查点——/cancel 置位本 mode 的 Event 后，
    下一次 emit 抛 CancelledError（BaseException 子类：穿透引擎一切 except
    Exception——含 works.refresh_all 对 progress 回调的吞异常，core/ 零改动），
    worker 沿既有 finally 收场（scan_worker 的 reset_client、audit 的 undo 照常），
    wrap 捕获后发 {"type":"error","msg":"cancelled"}，放锁后才摘事件
    （/cancel 短轮询等「本事件被摘除」→ 返回即重新可占，不存在返回 200 秒后
    再 POST /scan 撞 409 的竞态）；属主校验防摘除后新任务刚登记的同 mode 事件。"""
    STATE.acquire(mode)   # 占用中 → BusyError → 409（进流之前，HTTP 层归一）
    cancel_ev = STATE.new_cancel(mode)
    q = queue.Queue()

    def emit(ev):
        if cancel_ev.is_set():
            raise CancelledError(mode)
        q.put(ev)

    def wrap():
        cap = _Capture(q)
        old = __import__("sys").stdout
        __import__("sys").stdout = cap
        try:
            worker(emit)
        except CancelledError:
            q.put({"type": "error", "msg": "cancelled"})
        except SessionExpired:
            q.put({"type": "error", "msg": "session_expired"})
        except NoCredentials:
            q.put({"type": "error", "msg": "no_credentials"})
        except Exception as e:
            q.put({"type": "error", "msg": sanitize(e)})
        finally:
            cap.flush()
            __import__("sys").stdout = old
            STATE.release()
            STATE.drop_cancel(mode, cancel_ev)   # 放锁之后再摘——/cancel 等到即安全
            q.put(None)   # 哨兵

    threading.Thread(target=wrap, daemon=True).start()

    def gen():
        while True:
            ev = q.get()
            if ev is None:
                return
            yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


def _counted_client():
    """给当前 Client 实例包一层请求计数（server 侧 instrumentation，core 文件零改动）。
    返回 (client, counter)；counter["n"] 累计 raw_get+get_json+raw_post 调用。"""
    c = STATE.get_client()
    counter = {"n": 0}
    orig_get = c.raw_get
    orig_json = c.get_json
    orig_post = c.raw_post

    def g(*a, **k):
        counter["n"] += 1
        return orig_get(*a, **k)

    def j(*a, **k):
        counter["n"] += 1
        return orig_json(*a, **k)

    def p(*a, **k):
        counter["n"] += 1
        return orig_post(*a, **k)

    c.raw_get, c.get_json, c.raw_post = g, j, p

    def undo():
        c.raw_get, c.get_json, c.raw_post = orig_get, orig_json, orig_post
    return c, counter, undo


# ---------- 长任务 worker ----------

def scan_worker(emit):
    """全量扫描：stat2 临期 + works 逐课（同 do_refresh 的 stat2→works 两段 merge 语义）。

    扫描用独立 Client，不用业务单例：2026-09-29 实施记录实锤——36 门逐课 GET 会轮换
    共享 cookie 并中毒该实例的 mooc-ans 领卷链（isExpire 降级返回登录页 HTML→领卷炸），
    而 cookies.txt 磁盘态始终干净、新实例立刻可用（对照实验：扫描实例 len=4527 HTML /
    新实例 len=102 JSON）。扫描若触发重登，jar.save 已落盘，故收尾 reset 让业务链
    从磁盘态重载。
    """
    c = Client()
    try:
        c.ensure_login()
        emit({"type": "log", "msg": "扫描开始：stat2 临期 + works 逐课"})
        snap = core_stat2.fetch_near_tasks(c)
        near = norm_stat2_tasks(snap)
        n1 = merge_tasks(near)
        emit({"type": "log", "msg": "临期任务 %d 条（官方缓存 %s），新增 %d" %
             (len(near), snap.get("generated", ""), n1)})
        # 逐课增量入库：每解析完一门课就把新增卡片推给前端（用户实测：等整轮 36 门扫完
        # 才一次性冒出全部卡片，干等难受）。merge_tasks 幂等，重复合并不会多算。
        added = {"n": 0}

        def _on_rows(one):
            one_rows = norm_works_rows(one)
            if not one_rows:
                return
            a = merge_tasks(one_rows)
            added["n"] += a
            if a:
                emit({"type": "tasks", "data": {"added": a, "tasks": len(STATE.tasks)}})

        ws = core_works.refresh_all(c, progress=lambda i, n: emit(
            {"type": "progress", "n": i, "total": n}), on_rows=_on_rows, delay=_SCAN_DELAY)
        rows = norm_works_rows(ws)
        n2 = merge_tasks(rows) + added["n"]   # 收尾兜底 + 增量已入库的总数
        if not ws and (near or STATE.tasks):
            # 兜底：探测漏网时也不许静默说「0 条」——用户会误以为真没作业
            emit({"type": "log", "msg": "⚠ 作业列表为 0 条，但账号有任务记录：可能被风控"
                                        "拦截或页面结构变化。请按下方提示重试。"})
        emit({"type": "done", "data": {"near": len(near), "near_added": n1,
                                       "works": len(ws), "works_todo": len(rows),
                                       "works_added": n2, "tasks": len(STATE.tasks)}})
    except RiskControl as e:
        # 用户侧可见的明确归因 + 两步解决指引（旧版这里会静默变成「扫描完成 0 条」）
        emit({"type": "error", "msg": "risk_control: %s" % e})
        emit({"type": "log", "msg": "!! 被超星风控拦截：%s" % e})
        emit({"type": "log", "msg": "   这不是掉线，重新登录无法解决，需要人工过验证码："})
        emit({"type": "log", "msg": "   1) 打开手机学习通 App 或浏览器网页版，正常登录一次"})
        emit({"type": "log", "msg": "   2) 按页面提示输入图片验证码，完成后回到本软件点「扫描」重试"})
    finally:
        STATE.reset_client()   # 业务单例重载磁盘干净会话（扫描实例随闭包丢弃）


def make_audit_worker(keys, all_flag, force):
    def worker(emit):
        c = STATE.get_client()
        c.ensure_login()
        by_key = {t["key"]: t for t in STATE.tasks}
        if all_flag:
            pend = [t for t in STATE.tasks if not t.get("audited")]
        else:
            pend = [by_key[k] for k in keys if k in by_key]
        miss = [k for k in (keys or []) if k not in by_key]
        if miss:
            emit({"type": "log", "msg": "!! 未入库的 key（先 /scan）：%s" %
                  json.dumps(miss, ensure_ascii=False)})
        tot = len(pend)
        if not tot:
            emit({"type": "done", "data": {"audited": 0, "solvable": 0,
                                           "from_cache": 0, "requests": 0}})
            return
        cl, counter, undo = _counted_client()
        done = solv = cache_hits = 0
        try:
            for t in pend:
                r = core_audit.audit_task(cl, t, force=force)   # 永不抛出；只 GET
                done += 1
                apply_audit_result(t, r)
                if r.get("from_cache"):
                    cache_hits += 1
                if t["solvable"]:
                    solv += 1
                emit({"type": "progress", "n": done, "total": tot})
                emit({"type": "item", "data": dict(
                    {"key": t["key"], "courseId": t["courseId"], "workId": t["workId"],
                     "etype": t.get("etype", "work")}, **r)})
                if done < tot:
                    time.sleep(AUDIT_SLEEP + random.random())   # 礼貌间隔（引擎既有）
        finally:
            undo()
        emit({"type": "done", "data": {"audited": done, "solvable": solv,
                                       "from_cache": cache_hits,
                                       "requests": counter["n"]}})
    return worker


_PROVIDER_HINTS = (
    ("402", "该后端已不再免费，需要充值"),
    ("429", "免费额度已用完（通常按天重置）"),
    ("401", "key 无效或未填写"),
    ("403", "key 无权访问该模型"),
    ("10061", "连不上（代理/网络不通）"),
    ("timeout", "连接超时"),
)


def _why_provider_down(perrs):
    """把 provider 的原始英文报错翻成人话，供 no_provider 提示用。"""
    seen = []
    for r in perrs:
        s = str(r.get("err") or "")
        if not s:
            continue
        human = next((h for k, h in _PROVIDER_HINTS if k in s), s[:60])
        src = str(r.get("source") or "")
        item = ("%s：%s" % (src, human)) if src else human
        if item not in seen:
            seen.append(item)
    return "；".join(seen[:4]) if seen else "后端均无有效 key，或本机网络不通"


def make_solve_worker(body):
    """两种入口：keys=[任务key]（真领卷+解题）；或 questions=[自造题]+ref（自检/离线，
    不领卷）。providers=[key_ref…] 限定后端链（对应 e2e「只让 openrouter 在链上」）。
    solve=false 时只领卷建 job 不调模型（questions 环节独立验证用）。"""
    def worker(emit):
        c = STATE.get_client()
        if body.get("keys"):   # 真领卷才需要学习通会话；自造题路径不碰会话（同 e2e_check 5/6/7 环口径）
            c.ensure_login()
        settings = pv.load_settings()
        allowed = body.get("providers")
        if allowed:
            import copy
            settings = copy.deepcopy(settings)
            for cfg in settings.get("providers", []):
                cfg["enabled"] = cfg.get("key_ref") in set(allowed)
        keys = pv.load_keys()
        jobs_meta = []
        specs = []
        if body.get("questions") is not None:
            ref = dict(body.get("ref") or {})
            jid = (body.get("job_id") or
                   "%s:%s" % (ref.get("courseId", "0"), ref.get("workId", "0")))
            specs.append((jid, ref, body["questions"], body.get("ctx")))
        for k in body.get("keys") or []:
            t = next((x for x in STATE.tasks if x["key"] == k), None)
            if t is None:
                emit({"type": "log", "msg": "!! 未入库的 key（先 /scan）：%s" % k})
                continue
            if t.get("solvable") is False:
                emit({"type": "log", "msg": "!! %s solvable=false 跳过（不可作答不进解题队列）" % k})
                continue
            ref = {"course": t["course"], "title": t["title"], "courseId": t["courseId"],
                   "classId": t["classId"], "cpi": t["cpi"], "workId": t["workId"],
                   "answerId": t.get("answerId") or "0"}
            specs.append((k, ref, None, None))
        if not specs:
            emit({"type": "error", "msg": "没有可解的 job（keys/questions/ref 均无效）"})
            return
        do_solve = body.get("solve", True)
        for job_id, ref, qs, ctx in specs:
            if qs is None:
                qs, ctx = core_questions.fetch_work_questions(
                    c, ref["courseId"], ref["classId"], ref["cpi"], ref["workId"],
                    answerid=ref.get("answerId") or "0")
                if not qs:
                    emit({"type": "log", "msg": "!! %s 领到 0 题（任务点门槛/接口变化），跳过" % job_id})
                    continue
            if not ctx:
                ctx = {}
            for q in qs:   # 领卷逐题播报（不解题也有——e2e 第4环字段断言用）
                # M1 必要适配：stem 截断 40→140 + options 全文（审批屏「逐题预览」§2.3），
                # 环4 为字段存在性断言（qid/type/stem/n_options），增量兼容。
                emit({"type": "item", "data": {
                    "job_id": job_id, "qid": q["qid"], "type": q["type"],
                    "stem": (q.get("stem") or "")[:140], "n_options": len(q.get("options") or {}),
                    "options": {k: str(v)[:60] for k, v in (q.get("options") or {}).items()},
                    "image_flag": bool(q.get("image_flag")),
                    "blank_count": q.get("blank_count")}})
            job = core_solver.Job(ref, qs)
            job.ctx = ctx
            # mode/started/finished/low = M3b R3 GET /jobs 的数据源；finished 由收尾显式置
            STATE.jobs[job_id] = {"job": job, "qs": qs, "ctx": ctx, "ref": ref,
                                  "exported": None, "mode": "solve",
                                  "started": time.time(), "finished": None,
                                  "low": []}
            emitted = set()

            def on_event(kind, msg, _job=job, _emitted=emitted, _jid=job_id):
                if kind == "q":
                    for qid, res in _job.results.items():   # 有序字典：补发未播报的最新结果
                        if qid not in _emitted:
                            _emitted.add(qid)
                            emit({"type": "progress", "n": len(_emitted),
                                  "total": len(_job.questions)})
                            emit({"type": "item", "data": dict(res, job_id=_jid)})
                else:
                    emit({"type": "log", "msg": ("!! " if kind == "error" else
                                                 "⚠ " if kind == "warn" else "") + str(msg)})
            if do_solve:
                # client 仅在真领卷 job 传入（识图链可用）；自造题保持 solve_job 旧行为（client=None）
                core_solver.solve_job(job, on_event=on_event, settings=settings,
                                      keys=keys, client=(c if body.get("keys") else None))
            for qid in job.results:
                if qid not in emitted:
                    emit({"type": "item", "data": dict(job.results[qid], job_id=job_id)})
            # 后端全挂时给可操作指引（用户实测：只看到 Pollinations 402 / OpenRouter 429
            # 两行天书，不知道该干什么）。判定=本 job 所有题都是 provider_err。
            perrs = [r for r in job.results.values() if r.get("status") == "provider_err"]
            if perrs and len(perrs) == len(job.results):
                why = _why_provider_down(perrs)
                emit({"type": "error", "msg": (
                    "no_provider: 所有解题后端都不可用——%s。"
                    "请到「设置 → 后端」添加并启用一个你自己的后端（自带 key，例如"
                    "DeepSeek、硅基流动），或等免费额度按天重置后重试。" % why)})
            low_th = settings.get("confidence_threshold", 0.75)
            low = [qid for qid, r in job.results.items()
                   if r["status"] == "ok" and r["confidence"] < low_th]
            exported = (core_solver.export_job(job)
                        if (do_solve or body.get("export")) else None)
            STATE.jobs[job_id]["exported"] = exported
            STATE.jobs[job_id]["finished"] = time.time()   # 流正常收束标记（/jobs 诚实态）
            STATE.jobs[job_id]["low"] = low
            jobs_meta.append({"job_id": job_id, "state": job.state,
                              "progress": job.progress(), "low": low,
                              "exported": exported})
        emit({"type": "done", "data": {"jobs": jobs_meta}})
    return worker


# ---------- 提交前验证码闸门（HANDOFF §3：work/validate code==2 → 人工） ----------
# 核实：core/ 现行代码不含该调用（grep 全仓零命中），按 TASK §3 在 server 适配层实现，
# 只读 GET，submitter 协议零改动；真实提交（confirm=True）之前必经此闸。

def pre_submit_validate(c, ref, ctx):
    action = ctx.get("form_action") or ""
    qs = urllib.parse.parse_qs(action.split("?", 1)[1]) if "?" in action else {}
    params = {"courseId": ref.get("courseId", ""), "classId": ref.get("classId", ""),
              "cpi": ref.get("cpi", ""),
              "answerId": ref.get("answerId", "0"),
              "enc": qs.get("enc", [""])[0],
              "standardEnc": ctx.get("standardEnc", "")}
    url = ("https://mooc1.chaoxing.com/mooc-ans/work/validate?" +
           urllib.parse.urlencode(params))
    try:
        j = c.get_json(url, referer="https://mooc1.chaoxing.com/")
    except Exception:
        return None   # 闸门自身不可达不拦真提交（服务端对验证码的强制仍由真 POST 兜底）
    if isinstance(j, dict) and j.get("code") == 2:
        return j
    return None


# ---------- App ----------

def create_app(token=None):
    token = token or os.environ.get("CX_TOKEN") or secrets.token_urlsafe(32)
    app = FastAPI(title="cx-pilot sidecar", version=VERSION, docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.state.cx_token = token   # __main__ 打 CX_READY 用；绝不写日志

    class TokenAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/health" or request.method == "OPTIONS":
                return await call_next(request)
            given = request.headers.get("x-cx-token", "")
            if not hmac.compare_digest(given, token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            return await call_next(request)

    # 顺序要害：Starlette add_middleware = insert(0)，后添加者在外层。
    # 必须 TokenAuth 先添加（内层）、CORS 后添加（外层）——否则 401 在 TokenAuth
    # 即返回、不经 CORS 包装 → 浏览器只见 "Failed to fetch"，前端无法按状态码
    # 触发重登（M1 任务书 §1「401→重登」在 webview 态失效；M0 只测了 200 路径未暴露）。
    # OPTIONS 预检不带 token：TokenAuth 显式放行，鉴权只发生在带 token 的实际请求上。
    app.add_middleware(TokenAuth)

    # 注意：Tauri 2 在 Windows/Linux 的 WebView2 origin 是 http://tauri.localhost（非 tauri://localhost），
    # macOS 才是 tauri://localhost —— 少一个即 fetch 静默 Failed to fetch。
    # M1 实测再+1 形态：tauri dev 且未配 devUrl 时 CLI 起内置静态服务器，webview origin 是
    # http://127.0.0.1:1430（CDP 取证定案，同 M0.5 坑3 类）；全白名单仍限本机回环，红线不放开。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                       "http://localhost", "http://localhost:1420",
                       "http://127.0.0.1", "http://127.0.0.1:1420", "http://127.0.0.1:1430"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["X-CX-Token", "Content-Type"],
    )

    # 异常归一（TASK §2）：SessionExpired→401 语义化 / NoCredentials→428 / Busy→409 / 其他→500
    @app.exception_handler(BusyError)
    async def _busy(request, exc):
        return JSONResponse({"detail": "busy", "current": exc.current}, status_code=409)

    @app.exception_handler(SessionExpired)
    async def _expired(request, exc):
        return JSONResponse({"detail": "session_expired"}, status_code=401)

    @app.exception_handler(NoCredentials)
    async def _nocred(request, exc):
        return JSONResponse({"detail": "no_credentials"}, status_code=428)

    @app.exception_handler(Exception)
    async def _other(request, exc):
        return JSONResponse({"detail": sanitize(exc)}, status_code=500)

    # ---------- 1. health（免 token）/ 2. version ----------
    @app.get("/health")
    def health():
        return {"ok": True, "version": VERSION, "engine": ENGINE}

    @app.get("/version")
    def version():
        return {"version": VERSION, "engine": ENGINE}

    # ---------- 3. settings 读写（脱敏 + 原子替换） ----------
    def settings_masked():
        s = json.loads(json.dumps(pv.load_settings()))  # 深拷贝，不动原对象
        for grp in ("providers", "vision_providers"):
            for cfg in s.get(grp, []):
                if cfg.get("api_key"):
                    cfg["api_key"] = mask_key(cfg["api_key"])
        return s

    @app.get("/settings")
    def get_settings():
        keys = pv.load_keys()
        act = [c["name"] for c in pv.active_providers(pv.load_settings(), keys)]
        # 「已配 key 但没启用」的 provider：侧栏要如实说出来。
        # 用户报「配了硅基流动的 api，左侧栏没显示」——key 存了但 enabled=false，
        # 不在后端链里，侧栏只列链内成员，看起来就像"配置没生效"。
        off = [c["name"] for c in pv.load_settings().get("providers", [])
               if not c.get("enabled")
               and ((c.get("key_ref") and keys.get(c.get("key_ref"))) or c.get("api_key"))]
        return {"settings": settings_masked(),
                "keys": {ref: mask_key(v) for ref, v in keys.items()},
                "active": act, "keyed_off": off}

    def _atomic_write(path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def _merge_provider_cfgs(old_list, new_list):
        """按 name（无 name 时退化按下标）逐 provider merge：前端只回传表单字段；
        api_key 缺省=保留盘上真 key（M1 任务书 §2.5「sk-*** 不覆盖真 key」的必要适配），
        显式回传脱敏值仍按 M0 红线拒绝。
        M3 B2 修正：有 name 但盘上无同名项=「新增后端」——不得按下标兜底继承
        不相干旧项的 key_ref/api_key（串染），base 置空。"""
        by_name = {c.get("name"): c for c in old_list or []}
        out = []
        for i, cfg in enumerate(new_list or []):
            if not isinstance(cfg, dict):
                raise ValueError("providers 项必须是对象")
            if str(cfg.get("api_key", "")).startswith("sk-***"):
                raise ValueError("api_key 是脱敏回显值，不能原样写回")
            if cfg.get("name") in by_name:
                base = dict(by_name[cfg.get("name")])
            elif not cfg.get("name") and old_list and i < len(old_list):
                base = dict(old_list[i])                        # 原「无 name 按下标」语义保留
            else:
                base = {}                                       # 新增项：无盘上继承
            base.update(cfg)
            out.append(base)
        return out

    @app.post("/settings")
    def post_settings(body: dict):
        data = pv.DATA
        if "settings" in body:
            s = body["settings"]
            if not isinstance(s, dict):
                raise ValueError("settings 必须是对象")
            merged = pv.load_settings()
            for grp in ("providers", "vision_providers"):
                if grp in s:
                    s[grp] = _merge_provider_cfgs(merged.get(grp), s[grp])
            # 与默认结构 merge（保 load_settings 的字段补全语义），原子替换落盘
            merged.update(s)
            _atomic_write(os.path.join(data, "settings.json"), merged)
        if "keys" in body:
            k = body["keys"]
            if not isinstance(k, dict):
                raise ValueError("keys 必须是对象")
            for ref, v in k.items():
                if str(v).startswith("sk-***"):
                    raise ValueError("key[%s] 是脱敏回显值，不能原样写回" % ref)
            keys = pv.load_keys()
            keys.update(k)
            _atomic_write(os.path.join(data, "api_keys.json"), keys)
        return get_settings()

    # ---------- 4. auth/login（GUI 首登） ----------
    @app.post("/auth/login")
    def auth_login(body: dict):
        user = str(body.get("user") or "").strip()
        pwd = str(body.get("pwd") or "")
        if not user or not pwd:
            raise ValueError("user/pwd 必填")
        save_credentials(user, pwd)               # core 落盘并 chmod 0600
        c = STATE.get_client()
        ok = c.ensure_login()                     # 续跑一次 ensure_login（引擎既有重登链）
        return {"ok": bool(ok)}

    # ---------- 5. scan（SSE） ----------
    @app.post("/scan")
    def scan():
        return stream_response("scan", scan_worker)

    # ---------- 6. tasks（纯内存，零网络） ----------
    @app.get("/tasks")
    def tasks():
        return {"tasks": tasks_view(), "count": len(STATE.tasks)}

    # ---------- 7. audit（SSE，只 GET，4h 缓存） ----------
    @app.post("/audit")
    def audit_ep(body: dict):
        keys = body.get("keys") or []
        all_flag = bool(body.get("all"))
        force = bool(body.get("force"))
        if not keys and not all_flag:
            raise ValueError("需要 {keys:[...]} 或 {all:true}")
        return stream_response("audit", make_audit_worker(keys, all_flag, force))

    # ---------- 8. solve（SSE） ----------
    @app.post("/solve")
    def solve_ep(body: dict):
        if not (body.get("keys") or body.get("questions")):
            raise ValueError("需要 {keys:[...]} 或 {questions:[...], ref:{…}}")
        return stream_response("solve", make_solve_worker(body))

    # ---------- 9. approve（内存审批态 + M1 手动面板写回） ----------
    @app.post("/approve")
    def approve(body: dict):
        jid = body.get("job_id")
        accepted = body.get("accepted") or []
        if jid not in STATE.jobs:
            return JSONResponse({"detail": "job_not_found"}, status_code=404)
        if not isinstance(accepted, list):
            raise ValueError("accepted 必须是 qid 数组")
        # M1 必要适配（M1 任务书 §2.2 手动面板）：manual={qid:答案} 写回 job.results，
        # 语义逐字对齐 app_v2.manual_panel「采用」（status=ok/confidence=1.0/
        # source=manual/need_manual=False）；提交协议本身零改动（仍走 submitter）。
        manual = body.get("manual") or {}
        if not isinstance(manual, dict):
            raise ValueError("manual 必须是 {qid: 答案} 对象")
        ent = STATE.jobs[jid]
        for qid, ans in manual.items():
            r = ent["job"].results.setdefault(str(qid), {})
            r.update(answer=ans, status="ok", confidence=1.0,
                     source="manual", need_manual=False)
        # 只带 manual 不带 accepted 时不清空既有审批态（M0 语义 {accepted:[…]} 不变）
        if "accepted" in body:
            STATE.approvals[jid] = [str(q) for q in accepted]
        return {"ok": True, "job_id": jid,
                "accepted": len(STATE.approvals.get(jid) or []),
                "manual": len(manual)}

    # ---------- 10. submit（confirm 双闸门：/approve + validate） ----------
    # M3b R2 红线：submit 可取消点=confirm 闸门之前（dry_run/预检阶段，POST 尚未发出）；
    # 真 POST 已在途不可回滚——/cancel 响应 note 如实写明，绝不假称拦下了。
    @app.post("/submit")
    def submit_ep(body: dict):
        STATE.acquire("submit")
        cancel_ev = STATE.new_cancel("submit")
        STATE.submit_posted = False
        try:
            jid = body.get("job_id")
            ent = STATE.jobs.get(jid)
            if not ent:
                return JSONResponse({"detail": "job_not_found"}, status_code=404)
            confirm = bool(body.get("confirm"))
            job, qs, ctx, ref = ent["job"], ent["qs"], ent["ctx"], ent["ref"]
            results = job.results
            if confirm:
                accepted = STATE.approvals.get(jid) or []
                if not accepted:
                    return JSONResponse({"detail": "approval_required"}, status_code=409)
                if cancel_ev.is_set():
                    return JSONResponse({"detail": "cancelled_before_submit"}, status_code=409)
                ak = set(accepted)
                results = {qid: r for qid, r in job.results.items() if qid in ak}
                c = STATE.get_client()
                if pre_submit_validate(c, ref, ctx):
                    return JSONResponse({"detail": "captcha_required"}, status_code=409)
                STATE.submit_posted = True   # 越过 confirm 闸门：此后取消不可回滚（红线）
                r = core_submitter.submit(c, qs, results, ctx, ref, confirm=True)
                STATE.approvals.pop(jid, None)
            else:
                r = core_submitter.submit(None, qs, results, ctx, ref, confirm=False)
                # M1 必要适配：审批屏预检要展示「将真实发送」的答案字段摘要
                # （form_preview 只截前 20 键看不到答案；纯汇总，协议零改动）
                form = core_submitter.build_form(qs, results, ctx, ref)
                r["form_fields"] = len(form)
                r["answers"] = {k: v for k, v in form.items()
                                if (k.startswith("answer") and k != "answerwqbid")
                                or k.startswith("tiankong") or k.startswith("answertype")}
            return r
        finally:
            STATE.release()
            STATE.drop_cancel("submit", cancel_ev)

    # ---------- 11. cancel（M3b R2：真取消 + 幂等） ----------
    # 置位 mode 的 Event → 等 worker 收尾放锁（短轮询 ≤5s）→ ok:true。
    # 无该 mode 在跑 → {ok:false, reason:"not_running"}（幂等，不报错不抛异常）。
    # job_id 可选：仅原样回显（mode 级取消即任务书语义；前端删卡时带上便于日志追溯）。
    @app.post("/cancel")
    def cancel_ep(body: dict):
        mode = str(body.get("mode") or "")
        if mode not in CANCELLABLE_MODES:
            raise ValueError("mode 需为 scan|audit|solve|submit 之一")
        jid = body.get("job_id")
        ev = STATE.cancel_events.get(mode)
        if ev is None:
            return {"ok": False, "reason": "not_running", "mode": mode,
                    "job_id": jid, "detail": "该 mode 当前没有长任务在跑（幂等空操作，未发任何请求）"}
        ev.set()
        deadline = time.time() + _CANCEL_WAIT_S
        while time.time() < deadline:
            if STATE.cancel_events.get(mode) is not ev:   # 被自身 finally 摘除=已收尾
                break
            time.sleep(0.05)
        if STATE.cancel_events.get(mode) is ev:
            # 5s 没等到收尾：任务仍在跑（audit 礼貌间隔/在途请求可能超窗），如实回报
            return {"ok": False, "reason": "timeout_still_running", "mode": mode,
                    "job_id": jid,
                    "detail": "取消已置位，但 %ss 内未观察到锁释放；任务仍在推进，可稍后再试"
                              % int(_CANCEL_WAIT_S)}
        if mode == "submit":
            # 红线留痕（任务书 R2）：真 POST 不可回滚，note 必须如实区分两种结局
            note = ("真实 POST 已在发送中：取消无法回滚（红线：取消只作用在 confirm 闸门之前）"
                    if STATE.submit_posted else
                    "取消作用于 confirm 闸门之前：未发出任何真实 POST")
        else:
            note = "emit 检查点抛 CancelledError → 既有 finally 已释放单锁"
        return {"ok": True, "cancelled": mode, "mode": mode, "job_id": jid, "note": note}

    # ---------- 12. jobs（M3b R3：零网络纯内存查询，R1 对账数据源） ----------
    @app.get("/jobs")
    def jobs_ep():
        return {"jobs": jobs_view(),
                "busy": {"mode": STATE.busy_mode} if STATE.busy_mode else None}

    # ---------- 风控验证码（【9010】罚站的人工出口）----------
    # 只做「取图给人看 + 代提交」：自动识别验证码图 = 绕过人机验证，不做。
    # 用同一个 Client——验证码绑在会话上，换个实例取到的图提交不认。
    @app.get("/captcha")
    def captcha_get():
        c = STATE.get_client()
        try:
            data, ct = c.fetch_captcha()
        except Exception as e:
            return {"ok": False, "msg": "取验证码失败：%s" % sanitize(e)[:90]}
        if data[:4] != b"\x89PNG":
            return {"ok": False, "msg": "没取到图片验证码（当前会话可能不需要验证码）"}
        return {"ok": True, "png": base64.b64encode(data).decode("ascii"),
                "content_type": ct or "image/png"}

    @app.post("/captcha")
    def captcha_post(body: dict):
        """提交验证码。成败判据是**回验**（课程列表不再被【9010】拦），不信响应文案。"""
        c = STATE.get_client()
        ok = c.submit_captcha(body.get("code"))
        return {"ok": bool(ok),
                "msg": "验证码通过，风控已解除" if ok
                       else "验证码不对或已过期，点「换一张」再试"}

    return app
