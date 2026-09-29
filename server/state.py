# -*- coding: utf-8 -*-
"""server 全局内存态：Client 单例 / 合并任务表 / job 表 / 审批态 / 长任务单锁。

语义对齐 app_v2 的 state（norm_stat2/norm_works/merge_tasks/apply_audit_result），
但零 GUI 依赖：不 import flet / core.theme（urgency_pill 为纯算术，本地复刻）。
core/ 一行未动——本文件只是引擎 API 的调用方。
"""
import threading
import time

from core.client import Client
from core import audit as _audit

# 与 app_v2 相同的审核礼貌间隔（引擎行为不缩水：串行逐条 + 抖动）
AUDIT_SLEEP = 1.5


class BusyError(Exception):
    """长任务占用中（全局单锁语义）。"""

    def __init__(self, current):
        super().__init__("busy")
        self.current = current


class CancelledError(BaseException):
    """M3b R2：/cancel 置位后由 emit 抛出，借 worker 既有 finally 释放单锁。

    继承 BaseException 而非 Exception 是【机制必需】，不是笔误：
    core/works.py:111/123 把 progress 回调包在 `except Exception: pass` 里——
    Exception 子类的取消信号会被引擎吞掉，/scan 只能跑完 36 门才停。
    BaseException 能穿透引擎的一切 `except Exception`（solve_job 只捕
    pv.ProviderError、audit_task 内部自捕获后返回，同样穿透），
    同时绝不与网络/协议异常混淆——本类只在 emit 的取消检查处抛出。
    core/ 一行未动：取消完全发生在 server 适配层的 emit 闭包里。"""


class State:
    def __init__(self):
        self.long_lock = threading.Lock()   # 全局单锁：/scan /audit /solve /submit 同时只允许一个
        self.busy_mode = None               # "scan" | "audit" | "solve" | "submit"
        self.client = None
        self._client_lock = threading.Lock()
        self.tasks = []                     # 合并任务表（来自 /scan；含审核态字段）
        self.jobs = {}                      # job_id -> {"job","qs","ctx","ref","exported",
                                            #               "mode","started","finished","low"}
        self.approvals = {}                 # job_id -> [qid...]（/approve 人工勾选）
        self.cancel_events = {}             # mode -> threading.Event（M3b R2；仅长任务在跑时存在）
        self.submit_posted = False          # submit 是否已越过 confirm 闸门发出真 POST（红线留痕）

    # ---------- 取消事件（M3b R2） ----------
    def new_cancel(self, mode):
        ev = threading.Event()
        self.cancel_events[mode] = ev
        return ev

    def drop_cancel(self, mode, ev=None):
        """摘除取消事件；带属主校验——release/drop 与下一任务 acquire/new_cancel 之间
        若无属主判断，可能误摘新任务刚登记的事件。"""
        if ev is not None and self.cancel_events.get(mode) is not ev:
            return
        self.cancel_events.pop(mode, None)

    # ---------- 单锁 ----------
    def acquire(self, mode):
        if not self.long_lock.acquire(blocking=False):
            raise BusyError(self.busy_mode or "?")
        self.busy_mode = mode

    def release(self):
        self.busy_mode = None
        try:
            self.long_lock.release()
        except RuntimeError:
            pass

    # ---------- Client 单例（ensure_login 由业务入口显式调用，重登一次=引擎既有行为） ----------
    def get_client(self):
        with self._client_lock:
            if self.client is None:
                self.client = Client()
        return self.client

    def reset_client(self):
        """丢弃当前业务会话实例：下次 get_client 从 cookies.txt 重新加载干净会话。
        2026-09-29 实施记录实锤：全量扫描（36 门逐课 GET）的响应轮换共享 cookie，
        中毒该实例后续的 mooc-ans 领卷链——isExpire 降级返回登录页 HTML（4527B），
        JSON 解析炸 "Expecting value"；全新实例不受影响、且领卷链自身不会自我中毒。
        /scan 因此用独立扫描实例并在收尾重置业务单例（GUI 同构问题属 v0.3.2 议题，
        不在 M0 干预面）。"""
        with self._client_lock:
            self.client = None


STATE = State()


# ---------- 任务表（app_v2 同语义复刻，去 GUI 字段） ----------

def urgency_pill(due_ts=None):
    """纯算术紧急度（与 core.theme.urgency_pill 同阈值；此处不 import theme——flet 依赖隔离）。"""
    if due_ts:
        left_h = (due_ts / 1000 - time.time()) / 3600
        if left_h < 0:
            return "grey"
        if left_h <= 24:
            return "urgent"
        if left_h <= 96:
            return "warn"
        return "ok"
    return "grey"


def norm_stat2_tasks(snap):
    """stat2 临期快照 -> 任务行（同 app_v2.norm_stat2 字段口径）。"""
    out = []
    for t in snap["tasks"]:
        rem = t.remain_str or ""
        try:
            urgent = int(rem.split("天")[0]) <= 1 if "天" in rem else "小时" in rem
        except Exception:
            urgent = False
        out.append({"key": "w:%s:%s" % (t.courseid, t.task_id), "course": t.course,
                    "courseId": t.courseid, "classId": t.clazzid, "cpi": t.cpi,
                    "workId": t.task_id, "answerId": "0", "title": t.name,
                    "sub": "截止 %s" % t.end_date, "pill": rem or t.end_date,
                    "pcls": "urgent" if urgent else "warn", "qn": t.question_num,
                    "ts": t.end_time, "status": "待做", "src": "stat2", "url": t.url,
                    "etype": t.event_type or "work"})
    return out


def norm_works_rows(ws):
    """works.refresh_all 结果 -> 任务行（同 app_v2.norm_works：只留待做族状态）。"""
    out = []
    for w in ws:
        if w.status not in ("待做", "待完成", "未完成", ""):
            continue
        out.append({"key": "w:%s:%s" % (w.courseid, w.work_id), "course": w.course,
                    "courseId": w.courseid, "classId": w.clazzid, "cpi": w.cpi,
                    "workId": w.work_id, "answerId": w.answer_id or "0", "title": w.title,
                    "sub": "截止 %s" % (w.deadline or "未设"), "pill": "",
                    "pcls": urgency_pill(w.deadline_ts), "qn": 0,
                    "ts": w.deadline_ts, "status": w.status or "待做",
                    "src": "getAllWork", "url": "", "etype": "work"})
    return out


def merge_tasks(new):
    """按 key 去重合并进内存任务表，返回新增条数（同 app_v2.merge_tasks）。"""
    have = {t["key"] for t in STATE.tasks}
    n = 0
    for t in new:
        if t["key"] not in have:
            STATE.tasks.append(t)
            have.add(t["key"])
            n += 1
    return n


def apply_audit_result(t, r):
    """审核结果回填任务行（同 app_v2.apply_audit_result；server 无勾选集合，剔除逻辑交前端）。"""
    t["audited"] = True
    t["solvable"] = bool(r.get("solvable"))
    t["qreal"] = int(r.get("qreal", 0))
    t["preview"] = r.get("preview", "")
    if t["solvable"]:
        if t["qreal"]:
            t["qn"] = t["qreal"]
    else:
        reason = r.get("reason", "")
        t["pill"] = {"非作业": "非作业", "无题": "无题",
                     "缺班级信息": "读不到题"}.get(reason, "读不到题")
        t["pcls"] = "grey"


def tasks_view():
    """GET /tasks：纯内存态（不发网络），未审核行顺带读 4h 磁盘缓存（命中也是零请求）。"""
    out = []
    for t in STATE.tasks:
        row = dict(t)
        if not row.get("audited"):
            hit = _audit._cache_get(_audit.cache_key(row))
            if hit is not None:
                apply_audit_result(row, hit)
                row["from_cache"] = True
        out.append(row)
    return out


# ---------- M3b R3：GET /jobs（零网络，纯内存）——R1 对账 + 前端显示的数据源 ----------

def job_state_view(jid, ent):
    """单个 job 的诚实状态：job.state 悬在 init/running 但对应流已结束/被取消
    （busy 不在该 mode 且流未正常收尾）→ 报 interrupted。
    否则重启对账时「被取消的 solve」会以 running 复活，R1 自愈失效。"""
    job = ent.get("job")
    st = job.state if job else (ent.get("state") or "unknown")
    if st in ("init", "running") and not ent.get("finished"):
        if STATE.busy_mode != ent.get("mode", "solve"):
            return "interrupted"
    return st


def jobs_view():
    """GET /jobs 的 jobs 数组：{job_id, mode, state, progress, started, low}。"""
    out = []
    for jid, ent in list(STATE.jobs.items()):
        job = ent.get("job")
        out.append({"job_id": jid,
                    "mode": ent.get("mode", "solve"),
                    "state": job_state_view(jid, ent),
                    "progress": job.progress() if job else "",
                    "started": ent.get("started", 0),
                    "low": ent.get("low", [])})
    return out
