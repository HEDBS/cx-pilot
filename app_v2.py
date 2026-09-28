# -*- coding: utf-8 -*-
"""cx-pilot 主界面 v2（DESIGN.md 实现）。五屏：作业计划 / 解题队列 / 运行日志 / 提交审批 / 设置。

B3c 架构（任务书 docs/TASK-B3c.md，根因已实锤勿再诊断）：
- UI 树按会话重建：main(page) 每次调用 new 一个 Session（PlanView、五屏 builder、全部控件
  实例都是本会话私有的）；跨会话共享的只有数据 state（tasks/selected/excluded/jobs/log）。
- state["pages"] = dict(session_id → Session)，safe_update() 遍历所有活跃 page try-update，
  一个死会话不拖垮其它；断开回调里 del。
- 禁止换父重挂载（见 ui/plan_view.py 顶部说明）。

B3d 桌面四修（任务书 docs/TASK-B3d.md）：分段点击事件内同步广播（Flet 0.28 事件后不自动
update）；stack 子级顺序恒定保 450ms 位移动画；列头眼睛常显；忙碌文案写真序列化属性 +
顶部进度条。

B3e/B4 清账（任务书 docs/TASK-B4.md）：两按钮合并为单个 PopupMenuButton「刷新」
（菜单=临期刷新/全课程扫描）；忙碌文字改独立 Text（按钮标签永不改写，修一闪而过 bug）；
眼睛图标矢量化见 ui/plan_view.py。

A1 作业审核（任务书 docs/TASK-A1.md）：刷新 merge 完成后 start_audit() 起后台线程
【串行】逐条 core.audit.audit_task（只 GET 领卷，4h 缓存零请求），每条结果回填 task
dict 后经 safe_update 链路原地刷卡（三态渲染见 ui/plan_view.py）；audit_gen 代际号
取消旧线程（同 scan_gen）；解题队列入口 queueable_keys() 滤掉不可作答。红线：
submitter/confirm 闸门零改动。
"""
import datetime
import os
import random
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flet as ft

from core.client import Client, DATA, NoCredentials, save_credentials
from runtime.tray import build_tray
from core import providers as pv
from core import stat2, works, questions, solver, submitter, audit
from core import theme as T
from ui.plan_view import PlanView, build_card, qn_label

# ============ 全局状态 ============
# 只放「数据」——控件实例一律归各 Session 所有，绝不进 state（B3c 根因：跨会话共享控件树，
# 标签页重连 replay + 后台线程改树 + layout 换父重挂载 → 服务端记录树漂移 → diff 发非法
# remove → AssertionError "control with ID 'None' not found" / KeyError '_58' → 新会话白屏）。
state = {"client": None, "tasks": [], "jobs": {}, "log": [],
         "selected": set(), "excluded": set(),
         "stop_flag": threading.Event(),
         "running": False, "approve": set(), "update_errors": [],
         "pages": {},          # B3c: session_id → Session（每会话一棵独立控件树）
         "update_count": 0, "refreshing": False,
         "audit_gen": 0}       # A1: 审核线程代际号（同 scan_gen 取消模式）

_pages_lock = threading.Lock()


def _sessions():
    with _pages_lock:
        return list(state["pages"].values())


def _session_of(page):
    if page is None:
        return None
    sid = getattr(page, "session_id", None) or "anon-%s" % id(page)
    with _pages_lock:
        return state["pages"].get(sid)


def _each(fn, what):
    """在每个活跃会话的树上跑 fn。一个坏会话不能拖垮其它（异常打日志+留痕，绝不静默）。"""
    for s in _sessions():
        try:
            fn(s)
        except Exception:
            state["update_errors"].append("render session=%s %s: %r" %
                                          (s.session_id, what, sys.exc_info()[1]))
            print("[_each] session=%s %s 渲染异常（已不再吞掉）:" % (s.session_id, what),
                  flush=True)
            print(traceback.format_exc(), flush=True)


def safe_update():
    """B3c：广播——遍历所有活跃会话逐个 page.update()。
    旧版只把 update 发给「最近被指定的那一个 page」，而控件树是全进程共享一份：
    多标签/重连时服务端记录树与真实树漂移，diff 发出非法 remove，首推即炸且永远白屏。
    现在每会话一棵私有树，广播只碰各自的树；死会话 update 抛异常也只标记它自己。"""
    ok = 0
    for s in _sessions():
        try:
            s.page.update()
            ok += 1
        except Exception:
            # B3 起就不吞异常：完整打进 stdout（run_v2.log）并留痕供 --selftest/冒烟断言。
            state["update_errors"].append("session=%s %r" %
                                          (s.session_id, sys.exc_info()[1]))
            print("[safe_update] session=%s page.update() 异常（已不再吞掉）:" %
                  s.session_id, flush=True)
            print(traceback.format_exc(), flush=True)
    if ok:
        state["update_count"] = state.get("update_count", 0) + 1


def make_fake_tasks():
    """内置 fake 数据：3 课程 7 作业，字段 = norm_works 输出。供 --selftest 与冒烟脚本共用。"""
    day = 86400 * 1000
    now = time.time() * 1000
    rows = [
        ("软件开发安全", "1001", "2026第一次作业（覆盖第1-3章：安全模型、威胁分类与访问控制机制）",
         "截止 9-27 23:59", "剩余 4 小时", "urgent", 56, now + 4 * 3600 * 1000),
        ("软件开发安全", "1001", "实验一：威胁建模实践", "截止 10-05 23:59", "8 天", "ok", 6,
         now + 8 * day),
        ("前端开发技术(2025级)", "1002", "线上学习作业2 — 内置对象 Math、Date、RegExp",
         "截止 9-27 23:23", "剩余 4 小时", "urgent", 12, now + 4 * 3600 * 1000),
        ("前端开发技术(2025级)", "1002", "实验一 JavaScript 基本使用", "9-27 23:23", "4 小时",
         "urgent", 5, now + 4 * 3600 * 1000),
        ("前端开发技术(2025级)", "1002", "线上学习作业3 — DOM 事件流", "10-01 23:59", "4 天",
         "warn", 10, now + 4 * day),
        ("示例课程A", "1003", "作业B1", "9-30 17:12", "3 天", "warn", 8, now + 3 * day),
        ("示例课程A", "1003", "向量空间的基与维数", "未设截止", "—", "grey", 0,
         now + 30 * day),
    ]
    out = []
    for i, (course, cid, title, sub, pill, pcls, qn, ts) in enumerate(rows):
        out.append({"key": "w:%s:f%d" % (cid, i), "course": course, "courseId": cid,
                    "classId": "c1", "cpi": "1", "workId": "f%d" % i, "answerId": "0",
                    "title": title, "sub": sub, "pill": pill, "pcls": pcls, "qn": qn,
                    "ts": ts, "status": "待做", "src": "fake", "url": "", "etype": "work"})
    return out


def log_line(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    state["log"].insert(0, "%s  %s" % (ts, msg))
    del state["log"][200:]
    render_log()


def set_status(msg):
    txt = "%s · %s" % (datetime.datetime.now().strftime("%H:%M"), msg)
    _each(lambda s: setattr(s.status_bar, "value", txt), "set_status")
    safe_update()


def pill(text, pcls):
    bg, fg = T.pill_style(pcls)
    return ft.Container(ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=fg),
                        bgcolor=bg, border_radius=999,
                        padding=ft.padding.symmetric(vertical=3.5, horizontal=10))


def card_box(content, pad=True):
    return ft.Container(content, bgcolor=T.CARD, border_radius=T.RADIUS,
                        shadow=T.SHADOW_CARD,
                        padding=ft.padding.symmetric(vertical=13, horizontal=16) if pad else 0)


def grey_btn(label, on_click, color=None, size=12.5):
    """样张 .btn.grey：白底、ink2 字、发丝描边、圆角 9。"""
    return ft.FilledButton(label, on_click=on_click,
                           style=ft.ButtonStyle(bgcolor=T.WHITE, color=color or T.INK2,
                                                elevation=0, side=ft.BorderSide(1, T.SEP),
                                                shape=ft.RoundedRectangleBorder(
                                                    radius=T.RADIUS_BTN),
                                                padding=ft.padding.symmetric(
                                                    vertical=7, horizontal=15),
                                                text_style=ft.TextStyle(size=size)))


def solid_btn(label, on_click, bg, size=12.5, pad_v=7, pad_h=15):
    """样张 .btn.blue/.green/.dark：纯色白字圆角 9。"""
    return ft.FilledButton(label, on_click=on_click,
                           style=ft.ButtonStyle(bgcolor=bg, color=T.WHITE, elevation=0,
                                                shape=ft.RoundedRectangleBorder(
                                                    radius=T.RADIUS_BTN),
                                                padding=ft.padding.symmetric(
                                                    vertical=pad_v, horizontal=pad_h),
                                                text_style=ft.TextStyle(size=size,
                                                                        weight=ft.FontWeight.W_600)))


# ============ 数据 ============
def get_client():
    if state["client"] is None:
        state["client"] = Client()
    state["client"].ensure_login()
    return state["client"]


def norm_stat2():
    if state.get("selftest"):          # --selftest：0 条临期 → 驱动空数据引导态路径
        return [], "selftest"
    snap = stat2.fetch_near_tasks(get_client())
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
                    "etype": t.event_type or "work"})   # A1 透传：审核据此零请求判非作业
    return out, snap.get("generated", "")


def norm_works(progress=None):
    if state.get("selftest"):          # --selftest：fake 数据走完整 merge→layout→update 链路
        if progress:                   # 忙碌反馈也要自检到：模拟 36 课节奏回调 0..36
            for i in range(37):
                progress(i, 36)
                time.sleep(0.04)
        return [dict(t) for t in make_fake_tasks()]
    out = []
    for w in works.refresh_all(get_client(), progress=progress):
        if w.status not in ("待做", "待完成", "未完成", ""):
            continue
        out.append({"key": "w:%s:%s" % (w.courseid, w.work_id), "course": w.course,
                    "courseId": w.courseid, "classId": w.clazzid, "cpi": w.cpi,
                    "workId": w.work_id, "answerId": w.answer_id or "0", "title": w.title,
                    "sub": "截止 %s" % (w.deadline or "未设"), "pill": "",
                    "pcls": T.urgency_pill(w.deadline_ts), "qn": 0,
                    "ts": w.deadline_ts, "status": w.status or "待做",
                    "src": "getAllWork", "url": "", "etype": "work"})  # 扫描链只收作业
    return out


def merge_tasks(new):
    have = {t["key"] for t in state["tasks"]}
    n = 0
    for t in new:
        if t["key"] not in have:
            state["tasks"].append(t)
            have.add(t["key"])
            n += 1
    return n


# ============ A1 作业审核（扫描后自动"验卷"，只 GET 领卷） ============
AUDIT_SLEEP = 1.5           # 串行逐条间隔（+0~1s 抖动）；冒烟 monkeypatch 调小


def apply_audit_result(t, r):
    """审核结果回填 task dict（共享数据），灰卡即时剔除已勾选项。
    可作答 → qn 回填真实题数（B4h 同链路，卡上「待领卷」刷成 N 题）。"""
    t["audited"] = True
    t["solvable"] = bool(r.get("solvable"))
    t["qreal"] = int(r.get("qreal", 0))
    t["preview"] = r.get("preview", "")
    if t["solvable"]:
        if t["qreal"]:
            t["qn"] = t["qreal"]
    else:
        reason = r.get("reason", "")
        t["pill"] = {"非作业": "非作业", "无题": "无题"}.get(reason, "读不到题")
        t["pcls"] = "grey"
        state["selected"].discard(t["key"])   # 不可作答永不进解题队列（含审核前被勾上的）


def queueable_keys():
    """解题队列入口：state["selected"] 过滤掉 solvable=False（A1）。"""
    byk = {t["key"]: t for t in state["tasks"]}
    return [k for k in state["selected"] if byk.get(k, {}).get("solvable") is not False]


def start_audit():
    """刷新 merge 完成后触发（do_refresh finally）。gen 代际号取消旧线程——重入/再次
    刷新时旧审核循环下个边界自毙（同 scan_gen 模式）。--selftest 0 网络：跳过。"""
    if state.get("selftest"):
        return
    state["audit_gen"] = state.get("audit_gen", 0) + 1
    threading.Thread(target=_audit_thread, args=(state["audit_gen"],),
                     daemon=True).start()


def _audit_thread(gen):
    try:
        c = get_client()
    except Exception as e:
        log_line("审核未执行（客户端不可用）：%s" % str(e)[:80])
        return
    _audit_pass(c, gen)


def _audit_pass(c, gen):
    """后台【串行】逐条 audit_task（内部缓存命中零请求），每条出结果经 safe_update
    链路广播更新对应卡片；顶部 prog_text 显示「审核中 n/N」。audit_task 永不抛出。"""
    pend = [t for t in state["tasks"] if not t.get("audited")]
    tot = len(pend)
    if not tot:
        return
    done = solv = 0
    for t in pend:
        if state.get("audit_gen", 0) != gen:
            return                                    # 新刷新已接管，本线程自毙
        try:
            r = audit.audit_task(c, t)
        except Exception:                             # 理论不可达（audit_task 不抛）；防御
            continue
        if state.get("audit_gen", 0) != gen:
            return                                    # 领卷期间被接管：本份数据不回填，
                                                      # 结果已在缓存里，新线程命中秒补
        done += 1
        apply_audit_result(t, r)
        if t["solvable"]:
            solv += 1
        _busy_set("audit", "审核中 %d/%d…" % (done, tot))
        sync_plans()
        bottom_update()
        safe_update()
        log_line("审核：%s | %s → %s" % (
            t["course"][:10], t["title"][:18],
            "可作答 %d 题" % t["qreal"] if t["solvable"] else t.get("pill", "不可作答")))
        if done < tot:
            time.sleep(AUDIT_SLEEP + random.random())  # 礼貌间隔，避免领卷链连击
    if state.get("audit_gen", 0) == gen:
        _busy_set("audit", None)
        safe_update()
    log_line("审核完成 %d/%d：可作答 %d · 不可作答 %d" % (done, tot, solv, done - solv))


def set_badge(name, count, color=None):
    """B3c：徽标是控件实例——每个会话自己的 Shell 里各有一份，逐会话改，绝不共享。"""
    def _b(s):
        shell = s.shell
        b = shell.badges.get(name)
        if not b:
            return
        b.content.value = str(count) if count else ""
        b.visible = bool(count)
        if color:
            b.bgcolor = color
    _each(_b, "set_badge")


# ============ 侧栏与路由 ============
class Shell:
    """每个 Session 各有一个 Shell 实例（B3c 根因修复：旧版模块级单份，被多会话共享）。"""

    def __init__(self):
        self.views = {}
        self.nav_items = {}
        self.body = ft.Container(expand=True, padding=ft.padding.only(16, 20, 16, 20))
        self.badges = {}
        col = ft.Column(spacing=2, expand=True)
        for name, icon, label in NAV:
            # 样张 .side-badge：红底数字胶囊（min17×17, radius9, 10.5/700 白字）
            badge = ft.Container(ft.Text("", size=10.5, weight=ft.FontWeight.W_700,
                                         color=T.WHITE),
                                 height=17, border_radius=9, visible=False,
                                 bgcolor=T.RED, alignment=ft.alignment.center,
                                 padding=ft.padding.symmetric(horizontal=5))
            self.badges[name] = badge
            it = ft.Container(
                content=ft.Row([ft.Icon(icon, size=17, color=T.INK3),
                                ft.Text(label, size=13, color=T.INK2),
                                badge], spacing=9),
                padding=ft.padding.symmetric(vertical=8, horizontal=12),
                border_radius=9, ink=True,
                on_click=lambda e, n=name: self.goto(n))
            self.nav_items[name] = it
            col.controls.append(it)
        self.provider_chip = ft.Column([], spacing=4)
        self.backend_block = ft.Column(
            [ft.Divider(color=T.SEP, height=1),
             ft.Text("解题后端", size=10.5, color=T.INK3, weight=ft.FontWeight.W_600),
             self.provider_chip],
            spacing=4, visible=False)
        self.sidebar = ft.Container(
            ft.Column([col, ft.Container(expand=True), self.backend_block],
                      spacing=2, expand=True),
            width=210, padding=ft.padding.only(10, 16, 10, 10),
            bgcolor=T.SIDEBAR)

    def register(self, name, ctl):
        self.views[name] = ctl

    def goto(self, name):
        for n, it in self.nav_items.items():
            active = n == name
            it.bgcolor = T.ACCENT_SOFT if active else None
            row = it.content
            row.controls[0].color = T.ACCENT if active else T.INK3
            row.controls[1].color = T.ACCENT if active else T.INK2
            row.controls[1].weight = ft.FontWeight.W_600 if active else None
        self.body.content = self.views.get(name)
        if name == "log":
            render_log()
        safe_update()

    def update_providers(self):
        self.provider_chip.controls = []
        keys = pv.load_keys()
        for cfg in pv.load_settings()["providers"]:
            ok = cfg.get("enabled") and (cfg["kind"] == "pollinations" or
                                         cfg.get("base_url") and
                                         (not cfg.get("key_ref") or keys.get(cfg["key_ref"])))
            if not ok:
                continue          # 只显示配置好且启用的后端
            self.provider_chip.controls.append(
                ft.Row([ft.Container(width=8, height=8, border_radius=4, bgcolor=T.GREEN),
                        ft.Text(cfg["name"], size=11.5, color=T.INK)], spacing=6))
        self.backend_block.visible = bool(self.provider_chip.controls)


NAV = [("course", ft.Icons.CALENDAR_MONTH, "作业计划"),
       ("queue", ft.Icons.PLAY_CIRCLE_OUTLINE, "解题队列"),
       ("log", ft.Icons.TERMINAL, "运行日志"),
       ("approve", ft.Icons.POLICY, "提交审批"),
       ("settings", ft.Icons.TUNE, "设置")]


# ============ 广播式渲染（B3c：改数据一次，逐会话各画各的树） ============
def sync_plans():
    """把所有会话的 PlanView 与共享数据对齐（refresh 建缺失的卡 + layout 落坐标/态）。"""
    def _c(s):
        s.plan.refresh(state["tasks"])
        s.plan.layout()
    _each(_c, "sync_plans")


def bottom_update():
    sel = [t for t in state["tasks"] if t["key"] in state["selected"]]
    qn = sum(t["qn"] for t in sel)
    txt = "已选 %d 份 · %d 题" % (len(sel), qn)
    _each(lambda s: setattr(s.bottom_sel, "value", txt), "bottom_update")
    safe_update()


def render_queues():
    _each(lambda s: s.render_queue(), "render_queues")
    safe_update()


def render_log():
    _each(lambda s: s.render_log(), "render_log")
    safe_update()


def render_approvals():
    _each(lambda s: s.render_approvals(), "render_approvals")
    safe_update()


def _busy_set(mode, txt):
    """忙碌文案广播到所有会话（每会话各有一份实例，B3c 前只改单份共享按钮）。

    B3e-4/6：按钮合并为 PopupMenuButton 后不再往按钮里塞文字（它只渲染 content，
    写它的字段 = 「一闪而过/看不见」事故本体）——忙碌/进度文字一律写独立
    Session.prog_text（ft.Text）的 value（property → _set_attr，进 diff 真上屏）。
    txt=None 清空并隐藏（仅在扫描真实结束后的 finally 调用，绝不中途清）。"""
    def _b(s):
        txt_c = s.prog_text
        if txt_c is None:
            return
        if txt is None:
            txt_c.value = ""
            txt_c.visible = False
        else:
            txt_c.value = txt
            txt_c.visible = True
    _each(_b, "_busy_set")


def _progress_set(n, total):
    """B3d-4：全课程扫描进度条广播（用户点名要的横向条）。ProgressBar 在 Column 里
    天然横满内容区；value 随 n/total 推进。只改属性，不新建/重挂载实例。"""
    v = 0.0 if not total else max(0.0, min(1.0, float(n) / float(total)))

    def _p(s):
        bar = s.progress_bar
        if bar is not None:
            bar.value = v
            bar.visible = True
    _each(_p, "_progress_set")


def _progress_hide_later(delay=0.5):
    """完成 delay 秒后隐藏进度条（任务书：完成 0.5s 后隐藏）。代际号防串场：
    期间若又开了一轮新扫描，旧定时器不得碰新进度条。"""
    gen = state.get("scan_gen", 0)

    def _h():
        if state.get("scan_gen", 0) != gen:
            return

        def _s(s):
            bar = s.progress_bar
            if bar is not None:
                bar.visible = False
                bar.value = 0
        _each(_s, "progress_hide")
        safe_update()
    t = threading.Timer(delay, _h)
    t.daemon = True
    t.start()


def show_login_dialog(sess, retry_mode):
    """GUI 登录：账号+密码 → save_credentials → Client.login(user,pwd) → 重跑刷新。"""
    if sess is None:
        log_line("需要登录学习通，但当前无可用窗口")
        return
    uf = ft.TextField(label="学习通账号(手机号)", width=320, border_radius=T.RADIUS_BTN)
    pf = ft.TextField(label="密码", password=True, can_reveal_password=True, width=320,
                      border_radius=T.RADIUS_BTN)
    err = ft.Text("", size=11.5, color=T.RED, visible=False)

    def close(e):
        sess.page.close(dlg)

    def ok(e):
        u, pw = (uf.value or "").strip(), (pf.value or "").strip()
        if not u or not pw:
            err.value, err.visible = "账号和密码都要填", True
            sess.page.update()
            return
        def _do():
            try:
                cl = state["client"] or Client()
                state["client"] = cl
                cl.login(user=u, pwd=pw)
                sess.page.close(dlg)
                log_line("登录成功，凭据已存本机（%s）" % DATA)
                do_refresh(retry_mode, page=sess.page)
            except Exception as e2:
                err.value, err.visible = "登录失败：%s" % str(e2)[:80], True
                try:
                    sess.page.update()
                except Exception:
                    pass
        threading.Thread(target=_do, daemon=True).start()

    dlg = ft.AlertDialog(modal=True, title=ft.Text("登录学习通（首次使用）"),
                        content=ft.Column([
                            ft.Text("账号仅存本机 %s，用于会话过期后自动重登。" % DATA,
                                    size=11.5, color=T.INK2),
                            uf, pf, err], spacing=10))
    dlg.actions = [ft.TextButton("取消", on_click=close),
                   solid_btn("登录", ok, bg=T.ACCENT)]
    sess.page.open(dlg)


def do_refresh(mode, page=None):
    if state.get("refreshing"):
        set_status("已有刷新任务在跑，稍候…")
        return
    state["refreshing"] = True
    _busy_set(mode, "扫描中…" if mode == "works" else "刷新中…")  # 点击即变忙碌态
    if mode == "works":            # B3d-4：扫描一开始进度条就上屏（0/未知 → 随首回调推进）
        state["scan_gen"] = state.get("scan_gen", 0) + 1
        _progress_set(0, 1)
    safe_update()
    sess = _session_of(page)      # 仅用于 [DBG] 采样本会话树；渲染目标 = 全部活跃会话

    def _prog(n, total):
        _busy_set(mode, "扫描中 %d/%d…" % (n, total))
        _progress_set(n, total)    # 进度条与按钮文本同一批广播，实时推进
        safe_update()

    def _r():
        try:
            try:
                if mode == "stat2":
                    ts, gen = norm_stat2()
                    n = merge_tasks(ts)
                    log_line("临期任务刷新（官方缓存 %s）：新增 %d 条" % (gen, n))
                else:
                    ts = norm_works(progress=_prog if mode == "works" else None)
                    n = merge_tasks(ts)
                    log_line("全课程扫描完成：新增 %d 条待做" % n)
                set_status("刷新完成")
            except NoCredentials:
                state["refreshing"] = False
                set_status("请先登录学习通")
                show_login_dialog(_session_of(page), mode)
            except Exception as e:
                log_line("刷新失败：%s" % str(e)[:120])
                set_status("刷新失败，见日志")
            print("[DBG] _r(%s) merge 完成 tasks=%d" % (mode, len(state["tasks"])), flush=True)
            sync_plans()          # 每会话自己的树各自 refresh+layout，不共享控件
            dbg = sess or (_sessions()[0] if _sessions() else None)
            if dbg is not None:
                p = dbg.plan
                print("[DBG] _r(%s) layout 完成 len(stack.controls)=%d stack=%sx%s placeholder=%s" %
                      (mode, len(p.stack.controls), p.stack.width, p.stack.height,
                       p.showing_placeholder), flush=True)
            bottom_update()
            safe_update()   # 扫描完成后卡片立即可见（B3 主诉：以前异常被吞导致永远刷不出来）
            print("[DBG] _r(%s) safe_update 完成" % mode, flush=True)
        finally:
            # A1：merge 完成后串行验卷。必须排在 refreshing 复位【之前】——冒烟/自测轮询
            # refreshing=False 后会把 selftest 关掉，若那时 start_audit 还没跑，就会起真网络线程。
            start_audit()         # selftest 模式内部跳过（0 网络）
            state["refreshing"] = False
            _busy_set(mode, None)   # 扫描真实结束才清忙碌文字（B3e-6：绝不中途清）
            if mode == "works":
                _progress_hide_later()   # 完成 0.5s 后隐藏（异常路径同样收口，不留挂起的条）
            safe_update()
    threading.Thread(target=_r, daemon=True).start()


def replay_ui(page=None):
    """会话重入/重连（on_connect，同 sessionId 不再走 main）时，把断连期间变过的数据
    重新广播同步到所有会话的树：refresh+layout+render_*，用户 tab 立刻看到已有卡片/忙碌态/
    日志，而不是空壳。B3c：树是按会话私有的，replay 不碰别人的实例。"""
    try:
        sync_plans()
        _each(lambda s: s.render_queue(), "replay_queue")
        bottom_update()
    except Exception:
        print("[DBG] replay_ui 异常:", flush=True)
        print(traceback.format_exc(), flush=True)
    render_log()
    render_approvals()
    sess = _session_of(page) or (_sessions()[-1] if _sessions() else None)
    print("[DBG] replay_ui 完成 session=%s stack=%d tasks=%d refreshing=%s" %
          (getattr(page, "session_id", "?"),
           len(sess.plan.stack.controls) if sess else -1,
           len(state["tasks"]), bool(state.get("refreshing"))), flush=True)


# ============ 屏 1 辅助 ============
def h1(text):
    return ft.Text(text, size=20, weight=ft.FontWeight.W_700, color=T.INK)


# ============ 屏 2 辅助 ============
def active_names():
    keys = pv.load_keys()
    names = [c["name"] for c in pv.active_providers(pv.load_settings(), keys)]
    return " > ".join(names) if names else "无（去设置启用）"


def queue_card(t):
    job = state["jobs"].get(t["key"])
    man = 0
    if job:
        j = job[0]
        frac = (len(j.questions) - len(j.pending())) / max(1, len(j.questions))
        st = {"running": "解题中", "done": "完成 √", "partial": "完成(有待审)",
              "failed": "失败"}.get(j.state, "排队中")
        pcls = {"done": "ok", "running": "run", "partial": "warn", "failed": "urgent"}.get(j.state, "grey")
        man = sum(1 for r in j.results.values() if r.get("need_manual"))  # B6a 识图全挂计数
    else:
        frac, st, pcls = 0, "排队中", "grey"
    bar = ft.Container(ft.Container(width=130 * frac, height=6, bgcolor=T.ACCENT,
                                    border_radius=3),
                       width=130, height=6, bgcolor=T.PROG_TRACK, border_radius=3)  # 样张 .prog
    meta = [bar]
    if man:
        meta.insert(0, ft.Text("需人工 %d" % man, size=11.5, color=T.RED,
                               weight=ft.FontWeight.W_600))
    return card_box(ft.Row([ft.Column([ft.Text(t["title"], size=14.5, weight=ft.FontWeight.W_600, color=T.INK),
                           ft.Text("%s · %s" % (t["course"], qn_label(t["qn"])),
                                   size=12, color=T.INK2)], spacing=2, expand=True)] + meta +
                [pill(st, pcls)], spacing=10,
               vertical_alignment=ft.CrossAxisAlignment.CENTER))


# ============ 运行队列（后台） ============
def run_queue(keys):
    from core.solver import Job, solve_job
    state["running"] = True
    try:
        c = get_client()
    except Exception as e:
        log_line("登录失败：%s" % str(e)[:120])
        state["running"] = False
        return
    by_key = {t["key"]: t for t in state["tasks"]}
    for k in keys:
        if state["stop_flag"].is_set():
            break
        t = by_key.get(k)
        if not t or state["jobs"].get(k):
            continue
        if t.get("solvable") is False:
            continue                                  # A1 双保险：队列循环里再滤一次
        log_line("领卷：%s | %s" % (t["course"][:10], t["title"][:20]))
        try:
            qs_, ctx = questions.fetch_work_questions(
                c, t["courseId"], t["classId"], t["cpi"], t["workId"],
                answerid=t.get("answerId", "0"))
            if not qs_:
                log_line("!! %s 无可解题目（可能被任务点门槛拦截或接口变化）" % t["title"][:14])
                continue
            log_line("题目解析 %d 题（选择%d 填空%d 主观%d）" % (
                len(qs_), sum(q["type"] in ("single", "multi") for q in qs_),
                sum(q["type"] == "blank" for q in qs_),
                sum(q["type"] == "subjective" for q in qs_)))
            ref = {"course": t["course"], "title": t["title"], "courseId": t["courseId"],
                   "classId": t["classId"], "cpi": t["cpi"], "workId": t["workId"],
                   "answerId": ctx.get("answerId") or t.get("answerId") or "0"}
            job = Job(ref, qs_)
            job.ctx = ctx
            state["jobs"][k] = (job, qs_, ctx, ref)
            job._t0 = time.time()
            # B4h P0-2：领卷成功 → 回填真实题数到共享 state，计划卡「待领卷」就地刷新为 N 题
            t["qn"] = len(qs_)
            sync_plans()
            bottom_update()

            def ev(kind, msg, _t=t):
                if kind == "q":
                    log_line("%s · %s" % (_t["title"][:12], msg))
                elif kind in ("error", "warn"):
                    log_line("⚠ %s" % msg[:100])
                render_queues()

            solve_job(job, on_event=ev, client=c)   # B6a：传入 client 启用识图链
            low = sum(1 for r in job.results.values()
                      if r.get("status") == "ok" and r.get("confidence", 0) < 0.75)
            log_line("完成 %s（低置信 %d）用时 %.0fs" %
                     (t["title"][:14], low, time.time() - job._t0))
            state["approve"].add(k)
        except Exception as e:
            log_line("!! %s 失败：%s" % (t["title"][:14], str(e)[:120]))
        render_queues()
        render_approvals()
        set_badge("approve", len(state["approve"]), T.RED)
        safe_update()
    state["running"] = False
    set_badge("approve", len(state["approve"]), T.GREEN)   # 样张：全部完成后徽标转绿
    safe_update()
    set_status("队列处理结束 → 提交审批")


def _log_color(msg):
    """样张 .log 语义色：err 红 / warn 橙 / ok 绿 / info 蓝 / 其余 ink2。"""
    if "!!" in msg or "✗" in msg or "失败" in msg:
        return T.RED
    if "⚠" in msg:
        return T.WARN_FG
    if "✓" in msg or "√" in msg:
        return T.OK_FG
    return T.INK2


# ============ 屏 4 辅助 ============
def _img_src(q):
    """题图首图 URL（ananas CDN 常为 // 开头协议相对，Flet Image 需完整 https）。"""
    for u in (q.get("img_urls") or []):
        if u:
            return "https:" + u if u.startswith("//") else u
    return ""


def manual_panel(job_key, q):
    """B6a need_manual 题卡：徽标+原图+每空人工输入+「采用」写回 job.results。
    红线：只动 results 字典（既有提交字段 answer/status），confirm 闸门与 submitter 零改动。"""
    n = max(1, int(q.get("blank_count", 1) or 1)) if q["type"] == "blank" else 1
    fields = [ft.TextField(label="第 %d 空" % (i + 1), width=200,
                           border_radius=T.RADIUS_BTN) for i in range(n)]

    def adopt(e):
        ent = state["jobs"].get(job_key)
        if not ent:
            return
        job = ent[0]
        r = job.results.setdefault(q["qid"], {"qid": q["qid"], "type": q["type"],
                                              "answer": "", "confidence": 0.0,
                                              "source": "", "tries": 0})
        vals = [(f.value or "").strip() for f in fields]
        r["answer"] = vals if q["type"] == "blank" else vals[0]
        r.update(status="ok", confidence=1.0, source="manual", need_manual=False)
        log_line("采用人工答案：%s qid=%s" % (q["type"], q["qid"]))
        render_approvals()

    body = [ft.Row([pill("需人工", "urgent"),
                    ft.Text("%s · 题 %s" % (q.get("type_name") or q["type"], q["qid"]),
                            size=12.5, color=T.INK2),
                    ft.Text((q.get("stem") or "")[:40], size=12.5, color=T.INK2)], spacing=8)]
    src = _img_src(q)
    if src:
        body.append(ft.Image(src=src, width=260, border_radius=8))
    body.append(ft.Row(fields + [solid_btn("采用", adopt, bg=T.ACCENT)], spacing=8, wrap=True))
    return ft.Container(ft.Column(body, spacing=8),
                        bgcolor=T.WARN_STRIP_BG, border_radius=T.RADIUS,
                        border=ft.border.all(1, T.SEP),
                        padding=ft.padding.symmetric(vertical=10, horizontal=12))


_checked = {}


def _on_approve_check(e, kk):
    _checked[kk] = e.control.value
    render_approvals()   # 广播：各会话树的勾选态都跟共享 _checked 对齐


def export_one(k):
    job = state["jobs"][k][0]
    fn = solver.export_job(job)
    log_line("答案导出 → %s" % fn)
    set_status("已导出答案文件")


def submit_thread(ready, sess):
    def _s():
        c = get_client()
        for k in ready:
            job, qs_, ctx, ref = state["jobs"][k]
            if not ctx.get("form_action"):
                log_line("!! %s 缺提交上下文，跳过" % ref["title"][:12])
                continue
            try:
                r = submitter.submit(c, qs_, job.results, ctx, ref, confirm=True)
                log_line("提交 %s → %s" % (ref["title"][:12], r["status"]))
                state["approve"].discard(k)
            except Exception as ex:
                log_line("提交失败 %s：%s" % (ref["title"][:12], str(ex)[:120]))
            render_approvals()
            set_badge("approve", len(state["approve"]))
            time.sleep(35 + 25 * (hash(k) % 3))
    threading.Thread(target=_s, daemon=True).start()
    if sess is not None:
        sess.shell.goto("log")


# ============ 设置屏辅助 ============
def save_key(val, cfg):
    import json as _json
    if not cfg.get("key_ref"):
        return
    p = os.path.join(DATA, "api_keys.json")
    keys = pv.load_keys()
    keys[cfg["key_ref"]] = val
    _json.dump(keys, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _each(lambda s: s.shell.update_providers(), "update_providers")
    safe_update()


def toggle_prov(e, cfg):
    cfg["enabled"] = e.control.value
    pv.save_settings(pv.load_settings())  # 保底结构
    s = pv.load_settings()
    for c in s["providers"]:
        if c["name"] == cfg["name"]:
            c["enabled"] = e.control.value
    pv.save_settings(s)

    def _t(ss):
        ss.shell.update_providers()
        ss.queue_info.value = "%d 份在队列 · 后端 %s" % (len(state["selected"]), active_names())
    _each(_t, "toggle_prov")
    safe_update()


def set_model(key_ref, model):
    s = pv.load_settings()
    for c in s["providers"]:
        if c.get("key_ref") == key_ref:
            c["model"] = model
    pv.save_settings(s)


# ============ 会话（B3c 核心：一浏览器会话一整套控件树） ============
class Session:
    """main(page) 每次调用新建一个。self.* 全部是本会话私有控件实例；
    跨会话共享的只有模块级 state（数据）。断开即从 state["pages"] 摘除。"""

    def __init__(self, page):
        self.page = page
        self.session_id = getattr(page, "session_id", None) or ("anon-%s" % id(page))
        self.plan = PlanView(state, on_change=self._on_plan_change)
        self.shell = Shell()
        self.status_bar = ft.Text("", size=11.5, color=T.INK3)
        self.bottom_sel = ft.Text("已选 0 份 · 0 题", size=12.5, color=T.INK2)
        self.btn_menu = None       # B3e-4：合并后的单个「刷新」PopupMenuButton（build_plan_screen 实例化）
        self.prog_text = None      # B3e-4/6：独立忙碌/进度文字（不塞按钮，修一闪而过 bug）
        self.progress_bar = None   # B3d-4：卡片区顶部全课程扫描进度条（build_plan_screen 实例化）
        self.log_list = None
        self.stats_texts = None
        self.queue_list = None
        self.queue_info = None
        self.approve_list = None
        self.appr_strip = None
        self.appr_strip_txt = None
        self.vision_col = None   # B6a：设置屏「视图API」区块容器（build_settings_screen 实例化）
        # 新树 = 空树，但数据可能是旧的（重开 tab / 进程复用）：把现有数据一次性铺进来
        self.plan.refresh(state["tasks"])
        self.plan.layout()
        self.root = self.build()

    def _on_plan_change(self):
        # 选中/排除集合变了：广播，所有会话的树各画各的（忙碌回调统一 broadcast，任务书 B3c-3）
        sync_plans()
        bottom_update()

    # ---------- 屏 1: 作业计划 ----------
    def build_plan_screen(self):
        # B3e-4：两按钮 → 单个「刷新」PopupMenuButton（点按钮弹菜单选方式：
        # 临期刷新=秒级官方聚合 / 全课程扫描=逐课兜底约 2 分钟）。忙碌文字走
        # 独立 prog_text（B3e-6），按钮标签永不改写——上一版写按钮 label 就是「看不见」。
        self.prog_text = ft.Text("", size=12.5, color=T.WARN_FG, visible=False)
        self.btn_menu = ft.PopupMenuButton(
            items=[
                ft.PopupMenuItem(text="临期刷新", icon=ft.Icons.REFRESH,
                                 on_click=lambda e: do_refresh("stat2", page=self.page)),
                ft.PopupMenuItem(text="全课程扫描(约2分钟)", icon=ft.Icons.SYNC,
                                 on_click=lambda e: do_refresh("works", page=self.page)),
            ],
            content=ft.Row([ft.Icon(ft.Icons.REFRESH, size=16, color=T.INK2),
                            ft.Text("刷新", size=12.5, color=T.INK2,
                                    weight=ft.FontWeight.W_600)],
                           spacing=6),
            tooltip="刷新：临期=官方聚合秒级；全课程=逐课扫描约 2 分钟",
            icon_size=18, bgcolor=T.WHITE, icon_color=T.INK2,
            shape=ft.RoundedRectangleBorder(radius=T.RADIUS_BTN),
            padding=ft.padding.symmetric(vertical=7, horizontal=12))
        head = ft.Row([h1("作业计划"), self.plan.seg, ft.Container(expand=True),
                       self.prog_text, self.btn_menu], spacing=10,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.plan.scroller.expand = True
        # B3d-4：扫描进度条——卡片区顶部横向条，宽=内容区（Column 内天然撑满），
        # 随 progress n/total 推进，完成 0.5s 后隐藏。常驻实例只切属性（DESIGN 规则4）。
        self.progress_bar = ft.ProgressBar(value=0, visible=False, bar_height=6,
                                           color=T.ACCENT, bgcolor=T.PROG_TRACK,
                                           border_radius=3)
        return ft.Column([head, self.progress_bar, self.plan.scroller],
                         spacing=10, expand=True)

    # ---------- 屏 2: 解题队列 ----------
    def build_queue_screen(self):
        q = ft.ListView(spacing=10, expand=True, padding=ft.padding.only(top=4))
        self.queue_list = q

        def start(e):
            if state["running"]:
                set_status("正在解题中…")
                return
            sel_keys = queueable_keys()   # A1：不可作答（含被灰置剔除）不进队列
            if not sel_keys:
                set_status("先在作业计划里勾选（可作答作业才能入队）")
                return
            self.shell.goto("log")
            threading.Thread(target=run_queue, args=(sel_keys,), daemon=True).start()

        btn_start = solid_btn("▶ 开始解题", start, bg=T.GREEN, pad_v=8, pad_h=18)
        btn_stop = grey_btn("停止", lambda e: state["stop_flag"].set())
        info = ft.Text("", size=12.5, color=T.INK2)
        self.queue_info = info
        return ft.Column([ft.Row([h1("解题队列"), info, ft.Container(expand=True),
                                  btn_stop, btn_start], spacing=10),
                          q], spacing=12, expand=True)

    def render_queue(self):
        tasks = [t for t in state["tasks"] if t["key"] in state["selected"]]
        self.queue_list.controls = [queue_card(t) for t in tasks] or \
            [ft.Text("队列为空 —— 到「作业计划」勾选作业后回来", color=T.INK3, size=13)]
        self.queue_info.value = "%d 份在队列 · 后端 %s" % (len(tasks), active_names())

    # ---------- 屏 3: 日志 ----------
    def build_log_screen(self):
        lb = ft.ListView(spacing=6, expand=True, padding=ft.padding.all(4))
        self.log_list = lb
        st = {i: ft.Text("", size=20, weight=ft.FontWeight.W_700, color=T.INK) for i in range(4)}
        self.stats_texts = st
        keys = ["作业完成", "题目已解", "低置信待审", "平均延迟"]   # 样张 .stat 文案

        def refresh_btn(e):
            self.stats_update()
            safe_update()

        cards = ft.Row([ft.Container(ft.Column([st[i], ft.Text(keys[i], size=11.5, color=T.INK2)],
                                               spacing=1,
                                               horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                     bgcolor=T.CARD, border_radius=T.RADIUS, shadow=T.SHADOW_CARD,
                                     padding=ft.padding.symmetric(vertical=12, horizontal=16),
                                     expand=True)
                        for i in range(4)], spacing=10)
        return ft.Column([ft.Row([h1("运行日志"), ft.Container(expand=True),
                                  grey_btn("刷新", refresh_btn)],
                                 spacing=8),
                          cards,
                          ft.Container(lb, bgcolor=T.CARD, border_radius=T.RADIUS,
                                       padding=ft.padding.symmetric(vertical=14, horizontal=16),
                                       expand=True, shadow=T.SHADOW_CARD)],
                         spacing=10, expand=True)

    def stats_update(self):
        jobs = list(state["jobs"].values())
        done = sum(1 for (j, *_) in jobs if j.state in ("done", "partial"))
        qn = sum(len(j.questions) for (j, *_) in jobs)
        qd = sum(len(j.questions) - len(j.pending()) for (j, *_) in jobs)
        low = sum(1 for (j, *_) in jobs for r in j.results.values()
                  if r.get("status") == "ok" and r.get("confidence", 0) < 0.75)
        vals = ["%d/%d" % (done, len(jobs)), "%d/%d" % (qd, qn), str(low),
                "%.1fs" % (sum((time.time() - j._t0) for (j, *_) in jobs if hasattr(j, "_t0")) / max(1, len(jobs)))]
        for i, v in enumerate(vals):
            self.stats_texts[i].value = v

    def render_log(self):
        rows = []
        for l in state["log"][:150]:
            ts, _, msg = l.partition("  ")
            rows.append(ft.Row([ft.Text(ts, size=12, color=T.INK3, font_family="Consolas"),
                                ft.Text(msg, size=12, color=_log_color(msg),
                                        font_family="Consolas", expand=True)], spacing=8))
        self.log_list.controls = rows
        self.stats_update()

    # ---------- 屏 4: 提交审批 ----------
    def build_approve_screen(self):
        lv = ft.ListView(spacing=10, expand=True, padding=ft.padding.only(top=4))
        self.approve_list = lv
        strip_txt = ft.Text("", size=12.5, color=T.WARN_STRIP_FG)
        strip = ft.Container(strip_txt, bgcolor=T.WARN_STRIP_BG, border_radius=10,
                             padding=ft.padding.symmetric(vertical=9, horizontal=14),
                             visible=False)
        self.appr_strip, self.appr_strip_txt = strip, strip_txt   # 样张 .warn-strip

        def do_submit(e):
            ready = [k for k in state["approve"] if _checked.get(k)]
            if not ready:
                set_status("没有已勾选批准的作业")
                return
            names = ", ".join(state["jobs"][k][3]["title"][:12] for k in ready)

            def confirm(e2):
                self.page.close(dlg)     # 对话框只开/关在「点击者」自己的会话上
                submit_thread(ready, self)

            dlg = ft.AlertDialog(
                modal=True, title=ft.Text("确认真实提交？"),
                content=ft.Text("将向学习通提交 %d 份作业：%s\n提交后服务器留痕，不可自动撤回。" % (len(ready), names)),
                actions=[ft.TextButton("取消", on_click=lambda e3: self.page.close(dlg)),
                         solid_btn("确认提交", confirm, bg=T.RED)])
            self.page.open(dlg)

        btn = solid_btn("提交已批准的作业", do_submit, bg=T.DARK, size=13, pad_v=9, pad_h=18)
        note = ft.Text("规则：仅勾选的才提交 · 份间 35~85s · 23:00-07:00 禁止 · 验证码即暂停",
                       size=11.5, color=T.INK2)
        return ft.Column([ft.Row([h1("提交审批"), ft.Container(expand=True), btn], spacing=8),
                          note, strip, lv], spacing=10, expand=True)

    def render_approvals(self):
        lv = self.approve_list
        rows = []
        pending_flag = 0
        for k in sorted(state["approve"]):
            job, qs_, ctx, ref = state["jobs"][k]
            res = job.results.values()
            okc = sum(1 for r in res if r.get("status") == "ok")
            low = sum(1 for r in res if r.get("status") == "ok" and r.get("confidence", 0) < 0.75)
            fail = sum(1 for r in res if r.get("status") != "ok")
            if low or fail:
                pending_flag += 1
            _checked.setdefault(k, False)
            manual_qs = [qq for qq in qs_ if isinstance(qq, dict)   # B6a（冒烟旧数据可能塞裸 int）
                         and (job.results.get(qq["qid"]) or {}).get("need_manual")]
            sub = "已答 %d/%d" % (okc, len(qs_))
            if low:
                sub += " · 低置信 %d" % low
            if fail:
                sub += " · 失败 %d" % fail
            if manual_qs:
                sub += " · 需人工 %d" % len(manual_qs)
            pcls = "urgent" if (fail or manual_qs) else ("warn" if low else "ok")
            ptxt = "有失败可重试" if fail else ("含待审项" if low else "可提交")
            if manual_qs:
                ptxt = "需人工 %d 题" % len(manual_qs)
            ck = ft.Checkbox(value=_checked[k], on_change=lambda e, kk=k: _on_approve_check(e, kk))
            main_row = ft.Row([ck,
                               ft.Column([ft.Text(ref["title"], size=14.5,
                                                  weight=ft.FontWeight.W_600, color=T.INK),
                                          ft.Text("%s · %s" % (ref["course"][:14], sub),
                                                  size=12, color=T.INK2)],
                                         spacing=2, expand=True),
                               ft.Column([pill(ptxt, pcls),
                                          grey_btn("导出", lambda e, kk=k: export_one(kk))],
                                         spacing=6,
                                         horizontal_alignment=ft.CrossAxisAlignment.END)],
                              spacing=12)
            if manual_qs:   # 题卡在作业卡下方：徽标+原图+人工输入（采用后自动消失）
                rows.append(card_box(ft.Column([main_row] + [manual_panel(k, qq) for qq in manual_qs],
                                               spacing=10)))
            else:
                rows.append(card_box(main_row))
        lv.controls = rows or [ft.Text("暂无可提交的作业（先跑完队列）", color=T.INK3, size=13)]
        self.appr_strip_txt.value = (
            "⚠ %d 份作业含低置信或失败项 —— 未勾选的作业不会提交" % pending_flag)
        self.appr_strip.visible = pending_flag > 0

    # ---------- B6a 视图API 区块 ----------
    VISION_HINT = (
        "白嫖视觉供应商（校园网实测直连可达，注册送免费额度）：\n"
        "① SiliconFlow 硅基流动 —— cloud.siliconflow.cn 注册 → 控制台 API Keys「新建」复制\n"
        "    base_url=https://api.siliconflow.cn/v1  model=Qwen/Qwen2.5-VL-7B-Instruct（送15M token≈数千张图）\n"
        "② Moonshot 月之暗面 —— platform.moonshot.cn 注册 → API Key 页创建（送代金券）\n"
        "    base_url=https://api.moonshot.cn/v1  model=kimi-latest（支持视觉）\n"
        "③ OpenRouter —— openrouter.ai 登录→Keys（免费池限流，识图慎选）\n"
        "    base_url=https://openrouter.ai/api/v1  带 :free 的支持图片模型\n"
        "④ 智谱 bigmodel.cn glm-4v-flash 永久免费但新号常风控401；Gemini 校园网直连不通\n"
        "填法：base_url/model/key 三项照抄，长串直接粘=存 key 本体。注册步骤见本页底部「注册教程」")

    def render_vision_block(self):
        """与后端芯片同风格「只显示配置好的」：无配置 → 折叠为一行「+ 添加视图API」。"""
        if self.vision_col is None:
            return
        vs = pv.load_settings().get("vision_providers") or []
        self.vision_col.controls = []
        if not vs:
            self.vision_col.controls.append(grey_btn("+ 添加视图API", lambda e: self._vision_add()))
            return
        for i, cfg in enumerate(vs):
            self.vision_col.controls.append(self._vision_row(i, cfg))
        self.vision_col.controls.append(
            ft.Row([grey_btn("+ 添加视图API", lambda e: self._vision_add())]))
        self.vision_col.controls.append(ft.Text(self.VISION_HINT, size=11.5, color=T.INK2))

    def _vision_add(self):
        s = pv.load_settings()
        lst = s.setdefault("vision_providers", [])
        lst.append({"kind": "openai_compat", "name": "硅基流动",
                    "base_url": "https://api.siliconflow.cn/v1",
                    "model": "Qwen/Qwen2.5-VL-7B-Instruct", "key_ref": "",
                    "enabled": True})
        pv.save_settings(s)
        log_line("已添加视图API条目（现共 %d 条），填好 base_url/model/key 后保存" % len(lst))
        _each(lambda ss: ss.render_vision_block(), "vision_block")
        safe_update()

    VISION_PRESETS = {
        "SiliconFlow 硅基流动(快,推荐)": {
            "base_url": "https://api.siliconflow.cn/v1",
            "models": ["Qwen/Qwen2.5-VL-7B-Instruct", "Qwen/Qwen2-VL-72B-Instruct",
                        "deepseek-ai/deepseek-vl2"]},
        "OpenRouter 免费池(免注册费,慢)": {
            "base_url": "https://openrouter.ai/api/v1",
            "models": ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                        "google/gemma-4-26b-a4b-it:free", "qwen/qwen3.8-27b:free"]},
        "Moonshot 月之暗面": {
            "base_url": "https://api.moonshot.cn/v1",
            "models": ["kimi-latest", "moonshot-v1-8k-vision-preview"]},
        "智谱 GLM(永久免费但新号易风控)": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "models": ["glm-4v-flash", "glm-4.5v"]},
        "自定义 OpenAI 兼容端点": {"base_url": "", "models": [""]},
    }

    def _vision_row(self, idx, cfg):
        name_f = ft.TextField(label="名称", width=130, value=cfg.get("name", ""),
                              border_radius=T.RADIUS_BTN)
        cur_preset = next((pn for pn, p in self.VISION_PRESETS.items()
                           if p["base_url"] == cfg.get("base_url", "")), "自定义 OpenAI 兼容端点")
        preset_f = ft.Dropdown(label="供应商", width=250, value=cur_preset,
                               options=[ft.dropdown.Option(k) for k in self.VISION_PRESETS],
                               border_radius=T.RADIUS_BTN)
        url_f = ft.TextField(label="base_url", width=300, value=cfg.get("base_url", ""),
                             border_radius=T.RADIUS_BTN)
        _mopts = self.VISION_PRESETS[cur_preset]["models"]
        if cfg.get("model") and cfg["model"] not in _mopts:
            _mopts = _mopts + [cfg["model"]]
        model_f = ft.Dropdown(label="model", width=300, value=cfg.get("model") or _mopts[0],
                              options=[ft.dropdown.Option(m) for m in _mopts],
                              border_radius=T.RADIUS_BTN)
        _shown = cfg.get("key_ref", "") or (cfg.get("api_key", "")[:6] + "…" if cfg.get("api_key") else "")
        key_f = ft.TextField(label="key引用/粘贴key", width=320, value=_shown,
                             border_radius=T.RADIUS_BTN)

        def pick_preset(e):
            pn = e.control.value
            p = self.VISION_PRESETS[pn]
            url_f.value = p["base_url"]
            model_f.options = [ft.dropdown.Option(m) for m in p["models"] if m]
            if p["models"] and p["models"][0]:
                model_f.value = p["models"][0]
            e.control.page.update()

        preset_f.on_change = pick_preset

        def save(e):
            s = pv.load_settings()
            lst = s.setdefault("vision_providers", [])
            if idx >= len(lst):
                return
            c = lst[idx]
            c["name"] = (name_f.value or "").strip() or c.get("name", "视图API")
            c["base_url"] = (url_f.value or "").strip()
            c["model"] = (model_f.value or "").strip()
            kv = (key_f.value or "").strip()
            if kv.endswith("…") and len(kv) <= 10:
                kv = ""          # 掩码回显未改动 = 保持原 key，不写坏
            if kv and " " not in kv and len(kv) >= 24:
                c["api_key"] = kv      # 直接粘贴 key 本体
                c["key_ref"] = ""
            elif kv:
                c["key_ref"] = kv      # 短串=api_keys.json 引用名
            c["enabled"] = True
            pv.save_settings(s)
            log_line("视图API已保存：%s（%s @ %s）" % (c["name"], c["model"],
                                                    c["base_url"][:40] or "未填地址"))
            _each(lambda ss: ss.render_vision_block(), "vision_block")
            safe_update()

        return card_box(ft.Column([
            ft.Row([preset_f, name_f], spacing=8, wrap=True),
            ft.Row([url_f, model_f, key_f,
                    solid_btn("保存", save, bg=T.ACCENT)], spacing=8, wrap=True)],
            spacing=8))

    # ---------- 屏 5: 设置 ----------
    def build_settings_screen(self):
        s = pv.load_settings()
        keys = pv.load_keys()

        def provider_desc(cfg):
            if cfg["kind"] == "pollinations":
                return "无 Key 匿名 · 单并发很慢 · 只当最后兜底"
            if cfg["kind"] == "local":
                return "llama.cpp 本地 · 基准 49% 准确率，勿当主力；离线兜底用"
            tips = {"openrouter": "注册即送 · 免费池 :free 后缀有日限额，429 自动退避",
                    "groq": "注册送 key · 推理最快，推荐主力",
                    "siliconflow": "国内直连 · 注册送额度",
                    "custom": "任何 OpenAI 兼容端点"}
            return tips.get(cfg.get("key_ref", ""), "OpenAI 兼容端点")

        prov_rows = []
        for cfg in s["providers"]:
            has_key = bool(keys.get(cfg.get("key_ref", ""), ""))
            kf = ft.TextField(label="API Key", password=True, can_reveal_password=True,
                              width=560, text_size=13,
                              value=keys.get(cfg.get("key_ref", ""), ""),
                              disabled=has_key,                    # 配好的锁灰防改坏
                              bgcolor=None if has_key else T.CARD,
                              border_radius=T.RADIUS_BTN,
                              on_change=lambda e, c=cfg: save_key(e.control.value, c))
            if has_key:
                kf.hint_text = "已配置（锁定）。换 key：删除内容后解锁需重启应用，或直接改 api_keys.json"
            sw = ft.Switch(value=cfg.get("enabled", False),
                           active_color=T.GREEN,
                           on_change=lambda e, c=cfg: toggle_prov(e, c))
            prov_rows.append(card_box(ft.Column([
                ft.Row([ft.Column([ft.Text(cfg["name"], size=13.5, weight=ft.FontWeight.W_600, color=T.INK),
                                   ft.Text(provider_desc(cfg), size=11.5, color=T.INK2)], spacing=2,
                                   expand=True), sw], spacing=12),
                kf], spacing=8)))

        mf = ft.TextField(label="OpenRouter 模型", width=430,
                          value=next((c.get("model") for c in s["providers"]
                                      if c.get("key_ref") == "openrouter"), ""),
                          border_radius=T.RADIUS_BTN,
                          on_change=lambda e: set_model("openrouter", e.control.value))

        def save_threshold(e):
            s.update(confidence_threshold=round(e.control.value, 2))
            pv.save_settings(s)

        def save_proxy(e):
            s.update(proxy=e.control.value)
            pv.save_settings(s)

        th = ft.Slider(label="置信阈值", min=0.5, max=0.99, divisions=49,
                       value=s.get("confidence_threshold", 0.75), width=300,
                       on_change=save_threshold)
        pf = ft.TextField(label="代理（境外后端走此代理）", width=280, value=s.get("proxy", ""),
                          border_radius=T.RADIUS_BTN, on_change=save_proxy)

        def test_all(e):
            set_status("连通测试中…")
            def _t():
                for cfg in pv.active_providers(pv.load_settings(), pv.load_keys()):
                    try:
                        out = pv.chat(cfg, pv.load_keys(),
                                      [{"role": "user", "content": "回答：OK"}], max_tokens=200)
                        log_line("连通 ✓ %s：%s" % (cfg["name"], out.strip()[:24]))
                    except Exception as ex2:
                        log_line("连通 ✗ %s：%s" % (cfg["name"], str(ex2)[:90]))
                set_status("测试完成，见日志")
            threading.Thread(target=_t, daemon=True).start()

        btn_test = solid_btn("测试全部连通", test_all, bg=T.ACCENT)
        # B6c：底部注册教程（展开式，不挤占日常视图）
        tut = ft.ExpansionTile(
            title=ft.Text("免费视觉/解题 API 注册教程", size=13, weight=ft.FontWeight.W_600, color=T.INK),
            controls=[ft.Column([
                ft.Text("▍识图（视图API）推荐 · SiliconFlow 硅基流动", size=12, weight=ft.FontWeight.W_600, color=T.ACCENT),
                ft.Text("1. 浏览器打开 cloud.siliconflow.cn 注册（手机号/邮箱，实名即可领 15M token 额度）\n"
                        "2. 登录后右上「API Keys」→「+ 新建 API 密钥」→ 点复制按钮整串复制\n"
                        "3. 回本页「视图API」→ + 添加：名称=硅基流动，base_url 和 model 从上表照抄，key 粘进「key引用」框\n"
                        "4. 保存后下方「测试全部连通」旁边可加测视图：跑一份带图作业看日志是否出现「识图(vision_api)」", size=12.5, color=T.INK, selectable=True,
                    ),
                ft.Text("▍备选 · Moonshot 月之暗面", size=12, weight=ft.FontWeight.W_600, color=T.ACCENT),
                ft.Text("platform.moonshot.cn 注册 → 右上角 API → 创建密钥（新用户送代金券）；"
                        "base_url=https://api.moonshot.cn/v1，model=kimi-latest。", size=11.5, color=T.INK2, selectable=True),
                ft.Text("▍解题文本模型推荐 · Groq", size=12, weight=ft.FontWeight.W_600, color=T.ACCENT),
                ft.Text("console.groq.com 用 Google/GitHub 账号注册 → API Keys 页 Create → 粘进上方对应行的「API Key」框。\n"
                        "速度快免费额度大，但校园网直连常被拦——设置里「代理」填 http://127.0.0.1:7897（开梯子时）。", size=11.5, color=T.INK2, selectable=True),
                ft.Text("▍已试过避坑", size=12, weight=ft.FontWeight.W_600, color=T.INK3),
                ft.Text("Gemini(直连不通) / 智谱(新号易风控401) / OpenRouter 免费视觉池(识图常 429)。", size=11.5, color=T.INK2, selectable=True),
            ], spacing=6)])
        # B6a：视图API 区块——现有后端面板下方；未配置时折叠为一行「+ 添加视图API」
        self.vision_col = ft.Column([], spacing=8)
        self.render_vision_block()
        vision_card = card_box(ft.Column([
            ft.Text("视图API", size=13.5, weight=ft.FontWeight.W_600, color=T.INK),
            ft.Text("OCR 判为公式损伤时用来读题图", size=11, color=T.INK2),
            self.vision_col], spacing=8))
        # B6d：整页单纵向滚动（此前 prov_rows 内滚+页外滚并存，教程展开后够不着）
        return ft.ListView([h1("设置"), ft.Column(prov_rows, spacing=10),
                            vision_card,
                            card_box(ft.Column([mf, th, pf], spacing=12)),
                            btn_test, tut], spacing=12, expand=True,
                            padding=ft.padding.only(top=4))

    # ---------- 组装 ----------
    def build(self):
        self.shell.register("course", self.build_plan_screen())
        self.shell.register("queue", self.build_queue_screen())
        self.shell.register("log", self.build_log_screen())
        self.shell.register("approve", self.build_approve_screen())
        self.shell.register("settings", self.build_settings_screen())
        self.shell.update_providers()
        bottom = ft.Container(
            ft.Row([ft.Text("cx-pilot", size=12, weight=ft.FontWeight.W_700, color=T.INK2),
                    ft.Text("v0.2.0", size=11, color=T.INK3),
                    self.bottom_sel,
                    ft.Container(expand=True), self.status_bar], spacing=8),
            padding=ft.padding.symmetric(vertical=6, horizontal=14),
            bgcolor=T.BOTTOMBAR, border=ft.border.only(top=ft.BorderSide(1, T.SEP)))
        return ft.Row([self.shell.sidebar,
                       ft.Column([self.shell.body,
                                  ft.Container(bottom, )], expand=True)],
                      spacing=0, expand=True,
                      vertical_alignment=ft.CrossAxisAlignment.STRETCH)


# ============ 组装 / 入口 ============
def main(page: ft.Page):
    os.environ["CXPilot_GUI"] = "1"
    # B3c：每个浏览器 tab/每次刷新都会重新进入 main()——每次都新建一整套控件树。
    # 旧版共享全局单份树 + 单份「当前 page」，多标签/重连时服务端记录树漂移 →
    # diff 非法 remove → 新会话首推就炸、永远白屏（run_v2.log 实锤）。
    sess = Session(page)
    with _pages_lock:
        state["pages"][sess.session_id] = sess
    print("[DBG] main() 会话进入 session=%s page_id=%s 活跃会话=%d tasks=%d（本会话新建全套控件树）" %
          (sess.session_id, id(page), len(state["pages"]), len(state["tasks"])), flush=True)

    def _on_disc(e, s=sess):
        with _pages_lock:
            if state["pages"].get(s.session_id) is s:
                del state["pages"][s.session_id]
            remain = len(state["pages"])
        print("[DBG] 会话断开 session=%s → 剩余活跃=%d" % (s.session_id, remain), flush=True)
    page.on_disconnect = _on_disc
    # WS 瞬断重连走同一 sessionId：flet 不再调用 main()，树还完整，只补数据同步
    page.on_connect = lambda e, pg=page: replay_ui(pg)
    page.title = "cx-pilot 学习通作业助手"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = T.WHITE
    page.padding = 0
    page.window.width = 1180
    page.window.height = 760
    page.window.min_width = 980
    page.window.min_height = 640

    page.add(sess.root)
    sess.shell.goto("course")
    log_line("欢迎使用 cx-pilot。点右上「刷新」菜单 → 临期刷新，读取学习通作业。")
    if state["tasks"]:
        replay_ui(page)     # 重入/刷新：新树立刻铺上已有数据/忙碌态，不再白屏等 stat2
    else:
        do_refresh("stat2")

    if state.get("selftest"):
        _selftest_watchdog(sess)


def _selftest_watchdog(sess):
    """--selftest 端到端自检（无浏览器也能验证渲染链路）：
    第一轮（引导卡）与第二轮（merge 数据后）都断言零渲染异常（B3b 任务 3）；
    扫描期间独立忙碌文字「扫描中 n/36…」实时刷新、完成清空隐藏；
    stack 尺寸从 10×10 变为数据尺寸。B3c：只观察本会话的树，但 update 走广播链路。"""
    plan = sess.plan

    def _st():
        try:
            time.sleep(1.5)
            empty_ok = plan.showing_placeholder          # stat2 返回 0 条 → 引导态已上屏
            errs1 = list(state["update_errors"])         # 第一轮（引导卡）异常快照
            upd1 = state.get("update_count", 0)
            time.sleep(1.5)
            do_refresh("works")                          # 自测内 37×0.04s 进度回调 ≈1.5s
            time.sleep(1.0)
            busy_txt = sess.prog_text.value if sess.prog_text else ""   # B3e-4：忙碌文字=独立 prog_text
            bar_busy = (sess.progress_bar is not None and sess.progress_bar.visible
                        and (sess.progress_bar.value or 0) > 0)         # 进度条应已随 n/36 推进
            time.sleep(4.0)
            n_cards = sum(1 for c in plan.stack.controls if getattr(c, "_chk", None))
            n_heads = len(plan.stack.controls) - n_cards
            errs2 = [x for x in state["update_errors"] if x not in errs1]
            upd2 = state.get("update_count", 0)
            btn = sess.btn_menu
            problems = []
            if not empty_ok:
                problems.append("空数据引导态未渲染")
            if plan.showing_placeholder:
                problems.append("扫描后引导态未撤除")
            if n_cards < 7 or n_heads < 3:
                problems.append("stack 卡片/列头数量不足 cards=%d heads=%d" % (n_cards, n_heads))
            if not (plan.stack.width > 100 and plan.stack.height > 100):
                problems.append("stack 尺寸未随数据撑开 %sx%s" % (plan.stack.width, plan.stack.height))
            if errs1:
                problems.append("第一轮(引导卡) update 异常：%s" % errs1[:2])
            if errs2:
                problems.append("第二轮(merge 数据后) update 异常：%s" % errs2[:2])
            if upd1 < 1 or upd2 - upd1 < 1:
                problems.append("两轮 update 计数不足 upd1=%d upd2=%d" % (upd1, upd2))
            if not busy_txt.startswith("扫描中"):
                problems.append("扫描中独立文字未显示忙碌反馈（采样值=%r）" % busy_txt)
            if not bar_busy:
                problems.append("扫描中进度条未显示/未推进（visible/value 采样失败）")
            if sess.prog_text is None or (sess.prog_text.visible and sess.prog_text.value):
                problems.append("扫描完成后忙碌文字未清空隐藏（当前=%r）" %
                                (sess.prog_text.value if sess.prog_text else None))
            if btn is None:
                problems.append("合并后的单个 PopupMenuButton「刷新」缺失（B3e-4）")
            if sess.progress_bar is not None and sess.progress_bar.visible:
                problems.append("扫描完成 0.5s 后进度条未隐藏")
            if state.get("refreshing"):
                problems.append("refreshing 标志未复位")
            if problems:
                print("SELFTEST FAIL: " + "; ".join(problems), flush=True)
                os._exit(1)
            print("SELFTEST PASS (cards=%d heads=%d stack=%sx%s, 两轮 update 零异常 "
                  "upd=%d→%d, 独立忙碌文字+完成清空)" %
                  (n_cards, n_heads, plan.stack.width, plan.stack.height, upd1, upd2), flush=True)
            os._exit(0)
        except Exception:
            traceback.print_exc()
            print("SELFTEST FAIL: watchdog 异常", flush=True)
            os._exit(1)
    threading.Thread(target=_st, daemon=True).start()


def main_entry():
    if "--version" in sys.argv:
        print("cx-pilot 0.2.0")
        return
    if "--selftest" in sys.argv:
        # 端到端渲染自检：隐藏窗口起真 Flet 客户端（不抢单实例锁，可与主程序并行）
        state["selftest"] = True
        ft.app(target=main, view=ft.AppView.FLET_APP_HIDDEN)
        print("SELFTEST FAIL: ft.app 意外返回", flush=True)
        sys.exit(1)
    from runtime.tray import acquire_single_instance
    if not acquire_single_instance():
        print("cx-pilot 已在运行（托盘）。")
        return
    mode = os.environ.get("CXTASK_VIEW", "")
    if mode == "web" or os.environ.get("CXTASK_WEB"):
        ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=8550)
    elif mode == "hidden":
        ft.app(target=main, view=ft.AppView.FLET_APP_HIDDEN)
    else:
        ft.app(target=main)


if __name__ == "__main__":
    main_entry()
