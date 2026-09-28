# -*- coding: utf-8 -*-
"""B3/B3c 静态冒烟测试：venv python tools/smoke_ui.py（无需浏览器/Flet 客户端进程）。

覆盖 TASK-B3 验收标准：
  fake 数据(3课程7作业) → 空数据引导态 → 科目列/时间流布局与卡片几何 → 滚动方向 →
  常驻实例(动画规则4) → 勾选/黑名单/恢复全路径 → 五屏构建切屏 → 日志着色 →
  审批渲染(含勾选回调) → do_refresh 后台线程链路 → safe_update 异常必须可见 →
  全树颜色字面量合法性审计（9位hex非法值 = 刷新后卡片空白事故的元凶）。
覆盖 TASK-B3c 新增标准（t_second_session）：
  同进程两次 main(fake page) → 两棵树控件实例 id 互不相交；safe_update 广播送达所有
  活跃会话且不抛；一个坏会话 update 抛异常不拖垮好会话；第一会话断开后第二会话正常更新。
覆盖 TASK-B3d 新增标准（桌面四修）：
  t_seg_switch_no_refresh 点击分段即切（不调 do_refresh 也广播 page.update）；
  t_eye_icon_visible 列头眼睛常显非 hover 依赖；
  t_progress_bar 假 36 课回调下进度条随 n/36 单调推进 + 实时「扫描中 n/36…」+ 完成 0.5s 隐；
  t_animation_450 卡片 animate=Animation(450ms)，且换模式/黑名单重排时 stack 内卡片
  相对顺序恒定（Flet SequenceMatcher diff 才走 equal→"set"，位移动画不灭）。
覆盖 TASK-B3e/B4 残留五项（2026-09-28）：
  t_eye_icon_visible 眼睛=矢量 ft.Icons.VISIBILITY_OUTLINED（禁 emoji，隐藏态切 OFF 变体）；
  t_head_click_split 列头大框=本科全选、右侧小图标=仅隐藏、hover 底色提示；
  t_refresh_menu_single 两按钮合并为单个 PopupMenuButton「刷新」+ 独立忙碌 Text；
  t_progress_bar 忙碌文字写 Text.value（修「一闪而过/看不见」）、真实结束才清；
  t_wheel_single_scrollable 外层单纵向 scrollable（滚轮可滚），stack_holder/scroller 不设 scroll；
  分段直切由 t_seg_switch_no_refresh 继续锁（B3d-1 已实现，防回退）。
覆盖 TASK-B4h 新增标准（2026-09-28）：
  B4h-1 P0-1：Stack 定位统一回退 left/top（t_course_layout / t_seg_switch_no_refresh 断言
  (left,top)==(_lx,_ly) 且 offset 必须为 None——offset/left-top 混用是科目列卡片消失事故本体）；
  卡片坐标散布在列头下方区域内（验收：每列=列头+该科作业卡竖排）；
  动效改 animate_position=450ms（t_animation_450 断言，offset 动画机制不得残留）。
  B4h-2 P0-2：t_works_blacklist 标题黑名单词表过滤（构造含「随堂练习/分组任务(PBL)/测验/作业B1」
  的假 getAllWork HTML，只有真作业入库）；t_qn_pending_and_backfill qn=0 显示「待领卷」、
  领卷回填后刷成新题数。
覆盖 TASK-B6a 新增标准（识图接线，2026-09-28）：
  t_vision_chain_present：vision 链函数齐 + solve_job 可选 client（默认 None 旧行为）+
  公式脏启发式在 solver + settings 默认含 vision_providers；
  t_vision_solver_chain：OCR脏→vision_api(0.75)/OCR净→直用(0.6)/全挂→need_manual不硬答/
  client=None 零变化，全 monkeypatch 0 网络 0 OCR；
  t_vision_settings_block：视图API区块空配置折叠一行「+ 添加视图API」→四输入+保存写回+
  key本体粘贴存 api_key+免费推荐文案（monkeypatch load/save，不碰 %APPDATA% 真文件）；
  t_need_manual_card：审批卡「需人工」徽标+https补全题图+每空输入+采用写回 results，
  队列卡计数；confirm/submitter 路径零接触（红线）。
覆盖 TASK-A1 新增标准（作业审核三态，2026-09-28）：
  t_audit_task_core：audit_task 三态分类（非作业零请求/可作答/无题/读不到题）、
  缓存二访零请求（Boom 客户端实锤）、TTL 过期重判、瞬时错不缓存、坏缓存不炸、预览截断+图占位；
  t_audit_card_render：三态渲染（待审小字/题数徽章+预览行/灰卡 0.45+pill 改写）×两布局模式、
  黑名单沉底 0.35 优先；
  t_audit_guard_and_queue：不可作答卡点击不 toggle、本科全选跳过、判不可作答剔除已勾、
  queueable_keys 过滤；
  t_audit_worker_and_trigger：selftest 跳过审核、_audit_pass 串行回填+「审核中 n/N」广播+
  收尾清空+完成日志、gen 代际不匹配即毙。
全绿输出 SMOKE PASS 并退出码 0（35 项）。

B3c 说明：控件树按会话重建后，冒烟用一个常驻「冒烟会话」（boot() 建的 fake page +
Session），各用例操作 S.plan / S.shell / S.log_list 等——与真会话同一条代码路径。
"""
import contextlib
import io
import json
import os
import re
import sys
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import flet as ft  # noqa: E402

import app_v2  # noqa: E402
from core import theme as T  # noqa: E402
from core import works as W  # noqa: E402
from core import providers as pv  # noqa: E402
from core import solver  # noqa: E402
from core import vision as VN  # noqa: E402
from core import audit as AUD  # noqa: E402
from core import client as CLIENT  # noqa: E402
from core import questions as QM  # noqa: E402
from ui.plan_view import HDR_H, PREVIEW_LH, UNSEL_OPACITY  # noqa: E402

HEX_RE = re.compile(r"^#[0-9A-Fa-f]+$")


class FakePage:
    """记录 add/update 命令的假 flet Page（B3c 冒烟指定要求）。"""

    def __init__(self, sid):
        self.session_id = sid
        self.added = []
        self.updates = 0
        self.window = SimpleNamespace(width=None, height=None,
                                      min_width=None, min_height=None)
        self.on_disconnect = None
        self.on_connect = None

    def add(self, *controls):
        self.added.extend(controls)

    def update(self):
        self.updates += 1

    def open(self, dlg):
        pass

    def close(self, dlg):
        pass


class BoomPage(FakePage):
    def update(self):
        raise RuntimeError("fake-render-error")


S = None  # 冒烟主会话（boot() 创建，注册进 app_v2.state["pages"]）


def boot():
    global S
    p = FakePage("smoke")
    S = app_v2.Session(p)
    S.page_obj = p
    with app_v2._pages_lock:
        app_v2.state["pages"]["smoke"] = S


# ---------- 控件树遍历 ----------
def walk_objs(o, seen=None):
    """深度遍历 Flet 控件树/属性值（含 _Control__attrs 里的标量属性）。"""
    if seen is None:
        seen = set()
    if id(o) in seen:
        return
    seen.add(id(o))
    if isinstance(o, (str, bytes, int, float, bool)) or o is None:
        yield o
        return
    if callable(o) and not isinstance(o, type):
        return  # lambda/函数不展开（闭包不是 UI 数据）
    yield o
    if isinstance(o, dict):
        vals = list(o.values()) + list(o.keys())
    elif isinstance(o, (list, tuple, set)):
        vals = list(o)
    else:
        d = getattr(o, "__dict__", {})
        if isinstance(d, dict):
            vals = [v for k, v in d.items() if k != "parent"]
        else:
            vals = []
        try:  # Flet 0.28 标量属性存在 _Control__attrs 里
            attrs = d.get("_Control__attrs") if isinstance(d, dict) else None
            if isinstance(attrs, dict):
                vals += [v[0] if isinstance(v, tuple) else v for v in attrs.values()]
        except Exception:
            pass
    for v in vals:
        yield from walk_objs(v, seen)


def collect_texts(root):
    out = []
    for o in walk_objs(root):
        if isinstance(o, ft.Text) and isinstance(o.value, str):
            out.append(o.value)
    return out


def all_strings(root):
    """树内所有字符串（Button 标签存 _Control__attrs['text'] 纯串，非 ft.Text）。"""
    return [o for o in walk_objs(root) if isinstance(o, str)]


def find_btn(root, text):
    """按标签找按钮（label 是 Button 的 text 属性，不走 content）。"""
    for o in walk_objs(root):
        if isinstance(o, ft.FilledButton):
            v = (o.__dict__.get("_Control__attrs") or {}).get("text")
            v = v[0] if isinstance(v, tuple) else v
            if v == text:
                return o
    raise AssertionError("找不到按钮 %r" % text)


def control_ids(root):
    return {id(o) for o in walk_objs(root) if isinstance(o, ft.Control)}


def audit_hex_colors(roots):
    bad = []
    for r in roots:
        for o in walk_objs(r):
            if isinstance(o, str) and HEX_RE.match(o) and len(o) not in (7, 9):
                bad.append(o)
    return sorted(set(bad))


def tree_roots():
    roots = [S.shell.sidebar, S.shell.body, S.plan.scroller, S.plan.seg]
    roots += list(S.shell.views.values())
    for name in dir(T):
        if name.startswith("__"):
            continue
        v = getattr(T, name)
        if isinstance(v, (str, list, ft.BoxShadow)):
            roots.append(v)
    return roots


# ---------- 测试用例 ----------
def t_fake_data():
    fakes = app_v2.make_fake_tasks()
    assert len(fakes) == 7, "应 7 条作业"
    assert len({t["course"] for t in fakes}) == 3, "应 3 个课程"
    need = {"key", "course", "courseId", "classId", "cpi", "workId", "answerId", "title",
            "sub", "pill", "pcls", "qn", "ts", "status", "src", "url"}
    for t in fakes:
        assert need <= set(t), "字段须与 norm_works 输出一致: %s" % (need - set(t))


def t_empty_placeholder():
    p = S.plan
    app_v2.state["tasks"] = []
    p.selected.clear()
    p.excluded.clear()
    p.refresh([])
    p.layout()
    assert p.showing_placeholder, "空数据应置引导态标志"
    assert p.stack.controls == [], "空数据 Stack 不应有卡片"
    texts = collect_texts(p.scroller)
    assert any("暂无临期任务" in s for s in texts), "引导文案缺失: %s" % texts[:5]
    assert any("全课程扫描" in s for s in texts), "引导文案应指向全课程扫描"


def t_course_layout():
    p = S.plan
    fakes = app_v2.make_fake_tasks()
    app_v2.state["tasks"] = fakes
    p.mode = "course"
    p.refresh(fakes)
    p.layout()
    kids = p.stack.controls
    cards = [k for k in kids if getattr(k, "_chk", None)]
    heads = [k for k in kids if not getattr(k, "_chk", None)]
    assert len(cards) == 7, "卡片数应为7，实际 %d" % len(cards)
    assert len(heads) == 3, "列头数应为3，实际 %d" % len(heads)
    for k in cards + heads:
        assert getattr(k, "offset", None) is None, \
            "B4h-1：定位必须统一 left/top，offset 残留=科目列卡片消失事故本体"
    for c in cards:
        assert c.width == T.COLW, "卡片宽应为列宽"
        assert isinstance(c._lx, (int, float)) and isinstance(c._ly, (int, float)), "卡片必须定位"
        assert (c.left, c.top) == (c._lx, c._ly), \
            "B4h-1（P0-1 回归修复）：渲染定位必须走 left/top（用户授权回退，全文件单一机制禁混 offset）"
        assert 50 <= (c.height or 0) <= 400, "卡片高度不合理: %s" % c.height
    for h in heads:
        assert (h.left, h.top) == (h._lx, h._ly), "B4h-1：列头同样 left/top 定位"
    # 验收：每列 = 列头 + 该科作业卡竖排（卡坐标散布在列头下方区域内）
    head_xs = {h._lx for h in heads}
    for c in cards:
        assert c._lx in head_xs and c._ly >= HDR_H - 2, \
            "B4h-1：作业卡必须落在某列头下方区域内: %s" % ((c._lx, c._ly),)
    xs = sorted({h._lx for h in heads})
    assert xs == [0, T.COLW + T.GAP, 2 * (T.COLW + T.GAP)], "列头 x 坐标: %s" % xs
    assert p.stack.width >= 3 * T.COLW + 2 * T.GAP, "Stack 应容纳3列"
    assert p.stack.height > 100, "Stack 高度应随内容撑开"
    inner = p.scroller.content
    assert isinstance(inner, ft.Column) and isinstance(inner.controls[0], ft.Row), \
        "科目模式 scroller 内容应为常驻 Column>Row"
    assert inner.controls[0].scroll == ft.ScrollMode.AUTO, "科目模式必须横向滚动"


def t_persistent_instances():
    p = S.plan
    ids1 = {t["key"]: id(p._cards[t["key"]]) for t in p.tasks}
    # B3c 反重挂载断言：layout()/模式切换前后，滚动骨架必须还是同一批实例
    vcol0, hrow0 = p.scroller.content, p.scroller.content.controls[0]
    scroller0 = p.scroller
    p.refresh(p.tasks)
    p.layout()
    ids2 = {t["key"]: id(p._cards[t["key"]]) for t in p.tasks}
    assert ids1 == ids2, "DESIGN规则4：layout/refresh 不得重建卡片实例（杀动画）"
    assert p.scroller is scroller0 and p.scroller.content is vcol0, \
        "B3c：layout 不得换父重挂载 scroller 内容"
    assert vcol0.controls[0] is hrow0, "B3c：滚动容器必须常驻，只切属性"
    p._seg_pick("time")
    assert p.scroller.content is vcol0 and vcol0.controls[0] is hrow0, \
        "B3c：模式切换也不得重挂载中间容器"
    assert hrow0.scroll is None and vcol0.scroll == ft.ScrollMode.AUTO, \
        "时间流应只切 scroll 属性为纵向"
    p._seg_pick("course")
    assert hrow0.scroll == ft.ScrollMode.AUTO, "回科目列应只切回横向滚动属性"


def t_select_and_bottombar():
    p = S.plan
    t0 = p.tasks[0]
    p.toggle_select(t0)
    assert t0["key"] in p.selected
    assert t0["key"] in app_v2.state["selected"], "选中态是共享数据（B3c：数据共享、实例不共享）"
    card = p._cards[t0["key"]]
    assert card._chk.content.visible is True, "选中应显示勾"
    assert card._chk.bgcolor == T.ACCENT, "选中圈应为 accent 蓝"
    assert "已选 1 份" in S.bottom_sel.value, S.bottom_sel.value
    p.toggle_select(t0)
    assert t0["key"] not in p.selected
    assert "已选 0 份" in S.bottom_sel.value


def t_time_mode():
    p = S.plan
    p._seg_pick("time")
    kids = p.stack.controls
    assert len(kids) == 7 and all(getattr(k, "_chk", None) for k in kids), "时间流应只剩7卡"
    inner = p.scroller.content
    assert isinstance(inner, ft.Column) and inner.scroll == ft.ScrollMode.AUTO, "时间流应纵向滚动"
    assert inner.horizontal_alignment == ft.CrossAxisAlignment.CENTER, "样张：两列居中，禁左对齐"
    xs = sorted({k._lx for k in kids})
    assert xs == [0, T.COLW + T.GAP], "应为两列瀑布: %s" % xs
    for x in xs:
        col = sorted([k for k in kids if k._lx == x], key=lambda k: k._ly)
        for i in range(len(col) - 1):
            assert col[i]._ly + col[i].height + 10 <= col[i + 1]._ly + 0.01, \
                "同列卡片不得重叠（重叠事故回归）"


def t_blacklist_and_restore():
    p = S.plan
    course = p.tasks[0]["course"]
    n_sunk = sum(1 for t in p.tasks if t["course"] == course)
    p.toggle_excluded(course)
    kids = p.stack.controls
    assert len(kids) == 7 + 1, "应出现恢复条，实际 %d" % len(kids)
    bar = p._restore
    assert bar is not None and bar in kids
    sunk = [k for k in kids if getattr(k, "_chk", None) and k.opacity == 0.35]
    assert len(sunk) == n_sunk, "黑名单科卡片应变灰(.35)且仍保留"
    below = [k for k in kids if k is not bar and k._ly > bar._ly]
    assert below, "恢复条下方应有沉底卡"
    assert min(k._ly for k in below) >= bar._ly + 44, "恢复条与卡片间必须留 44px 空档"
    # B3c：恢复条是常驻实例，进出 Stack 只切成员，绝不每次重建
    p.toggle_excluded(course)   # 取消
    p.toggle_excluded(course)   # 再隐藏
    assert p._restore is bar, "B3c：layout 不得重建恢复条实例"
    p._restore_all()
    assert not p.excluded and p._restore is None
    assert len(p.stack.controls) == 7, "恢复后卡片应归位（含变灰取消）"
    assert all(getattr(k, "opacity", 1.0) == 1.0 for k in p.stack.controls if getattr(k, "_chk", None))


def t_course_excluded_last():
    p = S.plan
    p._seg_pick("course")
    course = p.tasks[0]["course"]
    p.toggle_excluded(course)
    heads = [k for k in p.stack.controls if not getattr(k, "_chk", None)]
    assert heads[-1].content.controls[1].value == course, "科目模式黑名单列应沉到最右"
    p._restore_all()
    heads = [k for k in p.stack.controls if not getattr(k, "_chk", None)]
    assert heads[-1].content.controls[1].value != course


def t_five_screens():
    sh = S.shell
    assert set(sh.views) == {n for n, _, _ in app_v2.NAV}, "五屏应全部注册"
    for name, _, _ in app_v2.NAV:
        sh.goto(name)
        assert sh.body.content is sh.views[name], "切屏 %s 失败" % name


def t_log_coloring():
    st = app_v2.state
    st["log"].insert(0, "12:00:00  !! 测试失败：boom")
    st["log"].insert(0, "12:00:01  ✓ 完成 ok")
    app_v2.render_log()          # 广播渲染
    lb = S.log_list
    assert len(lb.controls) >= 2
    ok_row, err_row = lb.controls[0].controls, lb.controls[1].controls
    assert ok_row[1].value == "✓ 完成 ok"
    assert ok_row[1].color == T.OK_FG and err_row[1].color == T.RED, "样张日志语义色"
    assert ok_row[0].font_family == "Consolas", "日志应等宽字体（样张 .log）"


def t_queue_render():
    p = S.plan
    p.toggle_select(p.tasks[0])
    app_v2.render_queues()       # 广播渲染
    assert len(S.queue_list.controls) == 1, "勾选一份后队列应渲染一张卡"
    p.toggle_select(p.tasks[0])


def t_approvals():
    st = app_v2.state
    job = SimpleNamespace(
        results={"1": {"status": "ok", "confidence": 0.9},
                 "2": {"status": "ok", "confidence": 0.5},
                 "3": {"status": "error"}},
        questions=[1, 2, 3], pending=lambda: [3], state="done")
    key = "smoke:approve"
    st["jobs"][key] = (job, [1, 2, 3], {},
                       {"course": "软件开发安全", "title": "2026第一次作业", "workId": "f0"})
    st["approve"].add(key)
    app_v2.render_approvals()    # 广播渲染
    lv = S.approve_list
    assert len(lv.controls) == 1, "审批卡应渲染"
    strip_txt = S.appr_strip_txt
    assert S.appr_strip.visible and "低置信" in strip_txt.value, "警示条应出现（样张 .warn-strip）"
    app_v2._on_approve_check(SimpleNamespace(control=SimpleNamespace(value=True)), key)
    assert app_v2._checked.get(key) is True, "勾选回调（原 lambda 元组写法）应可用"
    st["approve"].discard(key)
    app_v2.render_approvals()
    assert not S.appr_strip.visible, "无待审项时警示条应隐藏"
    assert "暂无" in lv.controls[0].value
    st["jobs"].pop(key, None)
    app_v2._checked.pop(key, None)


def t_seg_visuals():
    p = S.plan
    seg = p.seg
    assert isinstance(seg, ft.Container) and seg.bgcolor == T.GRAY_SOFT, "分段容器应为样张灰底"
    assert seg.border_radius == T.RADIUS_BTN
    btns = seg.content.controls
    on = [b for b in btns if b.content.value == ("科目列" if p.mode == "course" else "时间流")][0]
    assert on.bgcolor == T.CARD and on.shadow, "选中段应白底+阴影（非 Material 紫）"
    off = [b for b in btns if b is not on][0]
    # 注意：Flet Container.shadow setter 把 None 规整成 []，判空用假值
    assert off.bgcolor is None and not off.shadow
    assert on.content.weight == ft.FontWeight.W_600 and on.content.color == T.INK


def t_hex_audit():
    bad = audit_hex_colors(tree_roots())
    assert not bad, "非法 hex 颜色（Flet 客户端 parseColor 会炸→整页 diff 报废→卡片空白）: %s" % bad


def t_safe_update_error_visible():
    st = app_v2.state
    old = st["pages"]
    boom = SimpleNamespace(session_id="boom", page=BoomPage("boom"))
    st["pages"] = {"boom": boom}
    st["update_errors"].clear()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            app_v2.safe_update()          # 不得抛出
        out = buf.getvalue()
        assert "Traceback" in out and "fake-render-error" in out, \
            "safe_update 必须把异常打进日志（曾 except:pass 吞异常）"
        assert len(st["update_errors"]) == 1, "update_errors 应留痕供 selftest 断言"
    finally:
        st["pages"] = old
        st["update_errors"].clear()


def t_do_refresh_thread():
    st = app_v2.state
    st["selftest"] = True                 # norm_stat2/norm_works 走 fake，不碰网络
    st["tasks"] = []
    S.plan.refresh([])
    S.plan.layout()
    app_v2.do_refresh("stat2")
    deadline = time.time() + 15
    while time.time() < deadline and not any("临期任务刷新" in l for l in st["log"]):
        time.sleep(0.1)
    assert any("临期任务刷新" in l for l in st["log"]), "stat2 刷新线程未完成"
    assert S.plan.showing_placeholder, "0 条 → 引导态应仍在"
    # 等 stat2 线程走完 finally 释放 refreshing 锁（日志先于 finally 写）
    dl = time.time() + 10
    while time.time() < dl and st.get("refreshing"):
        time.sleep(0.05)
    assert not st.get("refreshing"), "stat2 线程未释放刷新锁"
    app_v2.do_refresh("works")
    deadline = time.time() + 15
    while time.time() < deadline and not any("全课程扫描完成" in l for l in st["log"]):
        time.sleep(0.1)
    assert any("全课程扫描完成" in l for l in st["log"]), "扫描线程未完成"
    assert not S.plan.showing_placeholder, "扫描后引导态应撤除"
    cards = [k for k in S.plan.stack.controls if getattr(k, "_chk", None)]
    assert len(cards) == 7, "主诉场景：全课程扫描完成后卡片立即可见，实际 %d" % len(cards)
    assert not st["update_errors"], "后台线程 update 不应有异常: %s" % st["update_errors"]
    st["selftest"] = False


def t_card_visual_tokens():
    p = S.plan
    card = p._cards[p.tasks[0]["key"]]
    assert card.shadow == T.SHADOW_CARD and len(card.shadow) == 2, "样张双层阴影"
    assert card.border_radius == 14
    assert card.padding.top == 14 and card.padding.left == 16, "样张卡内边距 14/16"
    right_meta = card.content.controls[2].content   # Container→Row：题数 + 胶囊 横排
    assert isinstance(right_meta, ft.Row), "样张卡右侧 meta 为横排（题数+胶囊）"
    assert isinstance(right_meta.controls[1], ft.Container), "右二应为紧急度胶囊"
    assert right_meta.controls[1].border_radius == 999


def t_seg_switch_no_refresh():
    """B3d-1：点击分段控件的【真实事件路径】（构造 e 调 on_click），全程不调 do_refresh。
    断言：mode 已变、scroller 结构对应新 mode、卡片 left/top 按新布局重算、
    并且本会话 page.update() 真的被广播送达（旧版缺这一步 → 「必须再点刷新才切换」）。"""
    p = S.plan
    st = app_v2.state
    st["tasks"] = app_v2.make_fake_tasks()
    p.selected.clear()
    p.excluded.clear()
    p.mode = "course"
    p.refresh(st["tasks"])
    p.layout()
    btns = p.seg.content.controls
    tbtn = [b for b in btns if b.content.value == "时间流"][0]
    u0 = S.page_obj.updates
    errs0 = list(st["update_errors"])
    tbtn.on_click(SimpleNamespace(control=tbtn, name="click", data=""))   # 构造事件 e
    assert p.mode == "time", "点击分段后 mode 未变（_seg_change 断链）"
    assert S.page_obj.updates > u0, \
        "点击切换未在事件内广播 page.update——主诉『再点一次刷新才切』的根因"
    kids = p.stack.controls
    assert len(kids) == 7 and all(getattr(k, "_chk", None) for k in kids), \
        "scroller 结构未对应新 mode（应只剩 7 卡）"
    vcol = p.scroller.content
    assert vcol.scroll == ft.ScrollMode.AUTO and vcol.controls[0].scroll is None, \
        "时间流应纵滚唯一（scroll 属性未切到位）"
    xs = sorted({k._lx for k in kids})
    assert xs == [0, T.COLW + T.GAP], "卡片 _lx 未按新布局重算: %s" % xs
    assert all(isinstance(k._ly, (int, float)) for k in kids), "卡片 _ly 未重算"
    assert all((k.left, k.top) == (k._lx, k._ly) for k in kids), \
        "B4h-1：切模式后 left/top 必须同步重算（单一机制，禁 offset）"
    cbtn = [b for b in btns if b.content.value == "科目列"][0]
    cbtn.on_click(SimpleNamespace(control=cbtn, name="click", data=""))
    assert p.mode == "course" and len(p.stack.controls) == 10, \
        "回科目列应立即 7 卡 + 3 列头，实际 %d" % len(p.stack.controls)
    assert not [x for x in st["update_errors"] if x not in errs0], \
        "点击切换广播链路异常: %s" % st["update_errors"][-2:]


def _icon_name(ic):
    return getattr(ic.name, "value", ic.name)


def t_eye_icon_visible():
    """B3e-1：列头眼睛 = 【矢量图标】ft.Icons.VISIBILITY_OUTLINED（禁 emoji 👁）——
    常显、INK3 常态色、hover 变 ACCENT；隐藏态切 VISIBILITY_OFF_OUTLINED。
    visible=True 恒定（桌面 hover 不可靠，入口不能藏）。"""
    p = S.plan
    assert not hasattr(p, "_head_hover"), "旧 hover 显隐方法应已移除（防回退）"
    p.mode = "course"
    p.layout()
    heads = [k for k in p.stack.controls if not getattr(k, "_chk", None)]
    assert len(heads) == 3, "科目模式应 3 列头，实际 %d" % len(heads)
    for h in heads:
        assert h._eye.visible is True, "眼睛图标必须常显（visible=True）"
        ic = h._eye.content
        assert isinstance(ic, ft.Icon), "眼睛应为矢量 ft.Icon，实际 %r" % type(ic)
        assert _icon_name(ic) == "visibility_outlined", "常态应为 VISIBILITY_OUTLINED: %s" % _icon_name(ic)
        assert ic.color == T.INK3, "常显态眼睛应为 INK3 色"
        assert not any("👁" in t for t in collect_texts(h)), "列头禁止残留 emoji 👁"
    course = heads[0].content.controls[1].value
    heads[0]._eye.on_click(SimpleNamespace(control=heads[0]._eye, name="click"))   # 点击=隐藏
    assert course in p.excluded, "眼睛点击应隐藏该科目"
    p.layout()
    hid = [h for h in p.stack.controls if not getattr(h, "_chk", None)
           and h.content.controls[1].value == course][0]
    assert _icon_name(hid._eye.content) == "visibility_off_outlined", \
        "隐藏态眼睛应切 VISIBILITY_OFF_OUTLINED，实际 %s" % _icon_name(hid._eye.content)
    p._restore_all()
    assert not p.excluded, "复原：清除黑名单"


def t_head_click_split():
    """B3e-2：列头点击判定拆分——大框（色点+课程名+题数）= 本科全选/取消；
    右侧小图标 = 隐藏/恢复。大框 hover 有微弱选中底色提示可点击。"""
    p = S.plan
    st = app_v2.state
    p.selected.clear()
    p.excluded.clear()
    p.mode = "course"
    p.layout()
    heads = {h.content.controls[1].value: h for h in p.stack.controls
             if not getattr(h, "_chk", None)}
    course = p.tasks[0]["course"]
    keys = {t["key"] for t in p.tasks if t["course"] == course}
    other = {t["key"] for t in p.tasks if t["course"] != course}
    h = heads[course]
    ev = SimpleNamespace(control=h, name="click")
    h.on_click(ev)                       # 大框第一击 = 全选该科
    assert keys <= p.selected and not (other & p.selected), "大框点击应本科全选: %s" % p.selected
    card = p._cards[next(iter(keys))]
    assert card._chk.content.visible is True, "全选后卡片勾应上屏"
    h.on_click(ev)                       # 再击 = 取消
    assert not (keys & p.selected), "大框再击应取消该科全部选中"
    eev = SimpleNamespace(control=h._eye, name="click")
    h._eye.on_click(eev)                 # 小图标 = 隐藏（不吃大框事件，也不触发全选）
    assert course in p.excluded and not (keys & p.selected), \
        "小图标应仅隐藏该科（选中集不动）"
    p._restore_all()
    # hover 底色提示（微弱 ACCENT_SOFT，离开还原）
    hev = SimpleNamespace(control=h, name="hover", data="True")
    h.on_hover(hev)
    assert h.bgcolor, "大框 hover 应有选中底色提示"
    h.on_hover(SimpleNamespace(control=h, name="hover", data="False"))
    assert h.bgcolor in (None, ""), "大框离开应还原底色"


def t_progress_bar():
    """B3e-4/6 回归锁：忙碌文字放【独立 ft.Text】（prog_text）而非按钮 label
    （PopupMenuButton 只渲染 content，写它内部字段就是「一闪而过/看不见」的真凶）；
    假 36 课回调下进度条 value 随 n/36 单调推进，prog_text 实时「扫描中 n/36…」
    （写 Text.value，真序列化），扫描真实结束才清空隐藏，进度条 0.5s 后隐藏。"""
    st = app_v2.state
    dl = time.time() + 10
    while time.time() < dl and st.get("refreshing"):   # 等在途刷新收尾（refreshing 单飞锁）
        time.sleep(0.05)
    st["selftest"] = True
    st["tasks"] = []
    S.plan.refresh([])
    S.plan.layout()
    bar = S.progress_bar
    txt = S.prog_text
    assert bar is not None, "卡片区顶部应常驻 ProgressBar 实例"
    assert txt is not None, "顶栏应常驻独立忙碌文字 Text（B3e-6 修法）"
    # 上一轮扫描的 0.5s 收条定时器可能还在路上：等它落定再验静止态
    dl0 = time.time() + 2.5
    while time.time() < dl0 and bar.visible:
        time.sleep(0.05)
    assert not bar.visible, "扫描间隙进度条应处于隐藏态"
    assert not txt.visible and not txt.value, "扫描间隙忙碌文字应清空隐藏"
    errs0 = list(st["update_errors"])
    app_v2.do_refresh("works")
    vals, texts = [], []
    deadline = time.time() + 15
    while time.time() < deadline and st.get("refreshing"):
        if bar.visible:
            vals.append(bar.value)
        tx = txt.value or ""
        if tx.startswith("扫描中"):
            texts.append(tx)
        time.sleep(0.02)
    # 进度条：上屏、单调、触顶
    assert vals, "进度条未随 progress 回调上屏推进"
    assert all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), \
        "进度条 value 非单调增: %s" % vals
    assert len(set(vals)) >= 10, "进度条推进步数太少（未跟 n/36 走）: %d 个采样值" % len(set(vals))
    assert max(vals) >= 1.0 - 1e-9, "进度条完成应触顶 1.0，实际 %s" % max(vals)
    # 独立文字：实时「扫描中 n/36…」
    prog_re = re.compile(r"^扫描中 (\d+)/36…$")
    nums = [int(m.group(1)) for m in (prog_re.match(t) for t in texts) if m]
    assert nums, "prog_text 未出现『扫描中 n/36…』实时文案: %s" % texts[:5]
    assert nums == sorted(nums), "n 应随扫描推进: %s" % nums
    assert len(set(nums)) >= 3, "实时文本推进步数太少: %s" % sorted(set(nums))
    # 忙碌文字必须是真序列化属性（写 Text.value 进 _Control__attrs，才随 diff 上屏）
    assert "value" in (txt._Control__attrs or {}), "prog_text.value 未写入 _Control__attrs"
    # 完成：文字清空隐藏（不是「一闪而过」——结束前全程可见由上面采样证明）；进度条 0.5s 后隐
    assert not txt.visible and not txt.value, \
        "扫描完成后忙碌文字应清空隐藏（当前=%r）" % txt.value
    dl2 = time.time() + 2.5
    while time.time() < dl2 and bar.visible:
        time.sleep(0.05)
    assert not bar.visible, "扫描完成 0.5s 后进度条应隐藏"
    assert (bar.value or 0) == 0, "隐藏后进度条 value 应归零"
    assert not [x for x in st["update_errors"] if x not in errs0], \
        "扫描进度链路广播异常: %s" % st["update_errors"][-2:]
    st["selftest"] = False


def t_wheel_single_scrollable():
    """B3e-5：滚轮驱动滚动——外层【单一纵向 scrollable】（vcol）两种模式都开启（此前
    科目列 vcol.scroll=None，滚轮在 Stack 上无处可去=「滚不动」的主因）；stack_holder
    与 scroller 自身绝不设 scroll；hrow 仅科目模式承担横向（不同轴，可共存）。"""
    p = S.plan
    # flet 0.28：Container 根本没有 scroll 属性（0.8.x 才有）——红线天然成立，
    # 仍用 getattr 断言以防未来升级时误开（stack_holder/scroller 绝不可再套 scrollable）。
    assert getattr(p.stack_holder, "scroll", None) is None, "stack_holder 不得设 scroll（B3e-5 红线）"
    assert getattr(p.scroller, "scroll", None) is None, "scroller 不得再套一层 scrollable"
    p.mode = "course"
    p.layout()
    # B4i-1：科目列改由 Stack 撑满视口贴底滚动条；纵向滚关闭，横滚唯一 = hrow
    assert p.vcol.scroll is None and p.hrow.expand is True, \
        "科目列 B4i-1：vcol 关纵滚、hrow 撑满（滚动条贴底规格）"
    assert p.hrow.scroll == ft.ScrollMode.AUTO, "科目列仍需横向滚动"
    p._seg_pick("time")
    assert p.vcol.scroll == ft.ScrollMode.AUTO and p.hrow.scroll is None, \
        "时间流仍应纵滚唯一"
    p._seg_pick("course")
    assert getattr(p.stack_holder, "scroll", None) is None \
        and p.hrow.scroll == ft.ScrollMode.AUTO and p.vcol.scroll is None


def t_refresh_menu_single():
    """B3e-4：两按钮合并为【单个 PopupMenuButton「刷新」】+ 独立忙碌 Text；
    菜单项 = 临期刷新 / 全课程扫描；旧「刷新临期任务」「全课程扫描(约2分钟)」两按钮
    不得残留在顶栏；菜单项 on_click 已接 do_refresh。"""
    course_view = S.shell.views["course"]
    pmb = [o for o in walk_objs(course_view) if isinstance(o, ft.PopupMenuButton)]
    assert len(pmb) == 1, "作业计划屏应恰有 1 个 PopupMenuButton，实际 %d" % len(pmb)
    btn = pmb[0]
    menu_txts = [i.text for i in btn.items if getattr(i, "text", None)]
    assert any("临期刷新" in t for t in menu_txts), "菜单缺「临期刷新」: %s" % menu_txts
    assert any("全课程" in t for t in menu_txts), "菜单缺「全课程扫描」: %s" % menu_txts
    for i in btn.items:
        if i.text:
            assert callable(i.on_click), "菜单项 %r 未接回调" % i.text
    texts = collect_texts(course_view)
    assert not any("刷新临期任务" == t for t in texts), "旧按钮文案残留"
    heads = [t for t in texts if t.startswith("扫描中") or t.startswith("刷新中")]
    assert not heads, "常驻树里不应预写忙碌文案（应由 prog_text 动态承载）"
    assert S.prog_text is not None, "应常驻独立忙碌 Text（S.prog_text）"
    btn_texts = collect_texts(btn.content)
    assert any("刷新" == t for t in btn_texts), "主按钮文字应为「刷新」: %s" % btn_texts


def t_animation_450():
    """B3d-2：动效断链回归锁。①卡片 animate=Animation(450ms, EASE_IN_OUT_CUBIC_EMPHASIZED)
    （Flet 对象层可查）；②换模式/黑名单重排时，同一批卡片实例在 stack.controls 里的
    【相对顺序恒定】——Flet diff（control.py SequenceMatcher 按 id 匹配 children）只有
    命中 equal 块才发 "set"（触发 Flutter 端位移动画），顺序一变就是 remove+add 重建，
    动画必死（『全部瞬切』的另一半根因）。"""
    p = S.plan
    st = app_v2.state
    if not p.tasks:
        st["tasks"] = app_v2.make_fake_tasks()
        p.refresh(st["tasks"])
    p.mode = "course"
    p.layout()
    for t in p.tasks:
        c = p._cards[t["key"]]
        assert isinstance(c.animate, ft.Animation), "卡片 animate 应为 Animation 对象"
        assert c.animate.duration == 450, \
            "位移动画应为 450ms（DESIGN.md），实际 %s" % c.animate.duration
        assert c.animate.curve == ft.AnimationCurve.EASE_IN_OUT_CUBIC_EMPHASIZED, \
            "动画曲线应为 EASE_IN_OUT_CUBIC_EMPHASIZED，实际 %s" % c.animate.curve
        assert c.animate_opacity, "黑名单置灰应带 opacity 动画"
        assert getattr(c, "animate_offset", None) is None, \
            "B4h-1：offset 动画机制不得残留（定位统一 left/top 单机制）"
        assert isinstance(c.animate_position, ft.Animation) and \
            c.animate_position.duration == 450, \
            "B4h P1：left/top 回退后动效应走 animate_position=450ms"
    ids_c = [id(k) for k in p.stack.controls if getattr(k, "_chk", None)]
    assert len(ids_c) == 7
    course = p.tasks[0]["course"]
    p.toggle_excluded(course)                      # 黑名单沉底重排
    ids_bl = [id(k) for k in p.stack.controls if getattr(k, "_chk", None)]
    assert ids_bl == ids_c, "黑名单重排改变了 stack 卡片相对顺序——diff 将 remove+add 杀动画"
    sunk = [k for k in p.stack.controls if getattr(k, "_chk", None) and k.opacity == 0.35]
    assert sunk, "黑名单卡应置灰"
    p._restore_all()
    p._seg_pick("time")                            # 模式切换
    ids_t = [id(k) for k in p.stack.controls if getattr(k, "_chk", None)]
    assert ids_t == ids_c, "换模式后 stack 卡片相对顺序变化——位移动画被 diff 重建杀死"
    p._seg_pick("course")
    ids_c2 = [id(k) for k in p.stack.controls if getattr(k, "_chk", None)]
    assert ids_c2 == ids_c, "来回切换后卡片顺序仍须恒定"


def t_second_session():
    """B3c 任务书验证 1：模拟两个会话先后进入（同一进程 main(page) 调用两次）。
    断言：① 两棵树控件实例 id 互不相同（UI 树按会话重建，根因修复）；
    ② safe_update 广播送达所有活跃会话且不抛；一个坏会话不拖垮其它；
    ③ 第一会话断开（回调里 del）后第二会话更新正常；
    ④ 新会话首屏就铺上已有数据（重连不白屏）。"""
    st = app_v2.state
    st["selftest"] = False
    st["update_errors"].clear()
    st["tasks"] = app_v2.make_fake_tasks()
    p1, p2 = FakePage("tab1"), FakePage("tab2")
    app_v2.main(p1)            # 同进程两次会话进入（多标签/刷新重连时序）
    app_v2.main(p2)
    assert "tab1" in st["pages"] and "tab2" in st["pages"], "两会话都应注册进 state['pages']"
    s1, s2 = st["pages"]["tab1"], st["pages"]["tab2"]
    assert s1 is not s2 and s1.plan is not s2.plan and s1.shell is not s2.shell
    # ① 两棵树控件实例互不相同
    ids1 = control_ids(s1.root)
    ids2 = control_ids(s2.root)
    assert ids1 and ids2, "会话树不应为空"
    inter = ids1 & ids2
    assert not inter, "两会话不应共享任何控件实例，共 %d 个（样本 %s）" % (len(inter), list(inter)[:3])
    # ④ 两棵树都立即铺上了共享数据
    for name, s in (("tab1", s1), ("tab2", s2)):
        cards = [c for c in s.plan.stack.controls if getattr(c, "_chk", None)]
        assert len(cards) == 7, "%s 新会话应立即回放 7 卡，实际 %d" % (name, len(cards))
    # ② safe_update 广播不抛、全送达
    u1, u2 = p1.updates, p2.updates
    assert u1 > 0 and u2 > 0, "首屏应已 update"
    app_v2.safe_update()
    assert p1.updates == u1 + 1 and p2.updates == u2 + 1, "广播应送达所有活跃会话"
    assert not st["update_errors"], "广播不应产生异常: %s" % st["update_errors"][:2]
    # ③ 第一会话断开 → 回调里 del；第二会话照常
    p1.on_disconnect(SimpleNamespace())
    assert "tab1" not in st["pages"], "断开回调必须摘除会话"
    u2b = p2.updates
    app_v2.log_line("冒烟：第一会话断开后的广播")     # 走 render_log+safe_update 全链路
    assert p2.updates > u2b, "第一会话断开后第二会话更新应正常"
    assert p1.updates == u1 + 1, "已断开会话不应再收到 update"
    assert not st["update_errors"], "不应有异常: %s" % st["update_errors"][:2]
    # 坏会话（update 抛）不拖垮好会话：各会话独立 try
    bad = SimpleNamespace(session_id="bad", page=BoomPage("bad"))
    with app_v2._pages_lock:
        st["pages"]["bad"] = bad
    u2c = p2.updates
    with contextlib.redirect_stdout(io.StringIO()):
        app_v2.safe_update()
    assert p2.updates == u2c + 1, "坏会话不得阻止好会话更新"
    assert any("session=bad" in x for x in st["update_errors"]), "坏会话应留痕"
    st["update_errors"][:] = [x for x in st["update_errors"] if "session=bad" not in x]
    with app_v2._pages_lock:
        st["pages"].pop("bad", None)
    p2.on_disconnect(SimpleNamespace())
    with app_v2._pages_lock:
        st["pages"].pop("smoke", None)   # 冒烟主会话摘除，避免污染退出前状态


def t_works_blacklist():
    """v0.3.1 修订：随堂练习不再入口剔除（很多课的随堂练习本质是作业）——
    能不能做由审核层领卷验真定夺。入口只排除确定不是作业的「分组任务/PBL」。
    锁死：随堂练习必须放行（回归防护）。"""
    assert "分组任务" in W.TITLE_EXCLUDES and "PBL" in W.TITLE_EXCLUDES
    for kw in ("随堂练习", "测验", "考试"):
        assert kw not in W.TITLE_EXCLUDES, "「%s」不应再被入口误杀（审核层接管）" % kw
    assert not W._excluded_title("随堂练习1——List"), "「随堂练习」应入库交审核判定"
    assert not W._excluded_title("第五章测验"), "「测验」应入库交审核判定"
    assert W._excluded_title("分组任务(PBL)——第2周"), "「分组任务/PBL」应被剔除"
    assert not W._excluded_title("作业B1"), "真作业不应误杀"
    assert not W._excluded_title(""), "空标题不炸"
    # 解析级：假 getAllWork HTML 4 条 → 只有真作业入库
    titles = ["随堂练习1——List", "分组任务(PBL)", "期中测验", "作业B1"]
    wl = "".join('<div class="titTxt"><a class="inspectTask" data="%d" data2="%d" '
                 'data3="7" title="%s">t</a>'
                 '<strong>待做</strong><span>截止时间：</span>2026-10-01 23:59</div>'
                 % (i, i * 100, t) for i, t in enumerate(titles, 1))

    class FC:
        def raw_get(self, url, referer=None):
            if "stucoursemiddle" in url:
                return "<a href='/work/getAllWork?courseid=9&clazzid=8&cpi=7'>w</a>"
            return wl
    ws = W._works_of_course(FC(), "9", "8", "7", "《课程A》")
    assert [w.title for w in ws] == ["随堂练习1——List", "期中测验", "作业B1"], \
        "v0.3.1: 仅「分组任务(PBL)」入口剔除，其余交审核验真: %s" % [w.title for w in ws]


def t_unsolvable_sinks_to_bottom():
    """A1-排序（v0.3.1）：solvable=False 的卡在科目列与时间流都必须沉到最后，
    任何可作答卡不得出现在不可作答卡之下/之后。"""
    p = S.plan
    orig = list(app_v2.state["tasks"])
    try:
        f2 = sorted(app_v2.make_fake_tasks(), key=lambda t: t["ts"])
        f2[0]["solvable"] = False   # 最早截止的两份被判不可作答 → 若按旧逻辑它们会排最前
        f2[1]["solvable"] = False
        app_v2.state["tasks"] = f2
        p.refresh(f2)

        def order_check(mode):
            p.mode = mode
            p.layout()
            groups = ({c for c in (t["course"] for t in f2)} if mode == "course" else [None])
            for g in groups:
                arr = [t for t in f2 if g is None or t["course"] == g]
                arr.sort(key=lambda t: (p._cards[t["key"]]._lx, p._cards[t["key"]]._ly))
                seen_dead = False
                for t in arr:
                    if t.get("solvable") is False:
                        seen_dead = True
                    else:
                        assert not seen_dead, \
                            "%s/%s：可作答卡排在了不可作答卡后面" % (mode, g)
        order_check("course")
        order_check("time")
        # 精确位：科目列里死卡必须在该列最后一张
        c = f2[0]["course"]
        p.mode = "course"; p.layout()
        col = sorted([t for t in f2 if t["course"] == c], key=lambda t: p._cards[t["key"]]._ly)
        assert col[-1] is f2[0], "科目列：不可作答卡应为该列最底"
    finally:
        app_v2.state["tasks"] = orig
        p.refresh(orig)


def t_qn_pending_and_backfill():
    """B4h P0-2 题数显示：列表阶段（getAllWork 无题数字段）qn=0 → UI 显「待领卷」而非
    「0 题」；领卷后回填真实题数（run_queue 里 t['qn']=len(qs_)+sync_plans），显示刷新。"""
    p = S.plan
    st = app_v2.state
    st["tasks"] = app_v2.make_fake_tasks()
    p.selected.clear()
    p.excluded.clear()
    p.mode = "course"
    p.refresh(st["tasks"])
    p.layout()
    zeros = [t for t in st["tasks"] if not t["qn"]]
    assert zeros, "fake 数据应含 qn=0 条目"
    texts = collect_texts(p.scroller)
    assert any("待领卷" in s for s in texts), "qn=0 应显示「待领卷」: %s" % texts[:8]
    assert not any(s.strip() == "0 题" for s in texts), "不得显示「0 题」"
    # 队列卡同样走 qn_label（领卷前在队列里也不能是「? 题」样式）。
    # 注：本用例排在 t_second_session 之后，冒烟主会话已从 state['pages'] 摘除，
    # 广播到不了 S——直接调 S.render_queue() 验同一渲染函数。
    app_v2.state["selected"].add(zeros[0]["key"])
    S.render_queue()
    qt = collect_texts(S.queue_list)
    assert any("待领卷" in s for s in qt), "队列卡 qn=0 也应显示待领卷: %s" % qt[:5]
    app_v2.state["selected"].discard(zeros[0]["key"])
    # 模拟领卷回填（run_queue 领取成功后的同一动作）
    zeros[0]["qn"] = 6
    p.refresh(st["tasks"])
    p.layout()
    texts = collect_texts(p.scroller)
    assert any(s == "6 题" for s in texts), "回填后应显示「6 题」: %s" % texts[:8]
    assert not any("待领卷" in s for s in texts), "回填后不应残留「待领卷」"
    zeros[0]["qn"] = 0


def t_vision_chain_present():
    """B6a：识图链静态接线检查——core/vision 函数齐、solve_job 加可选 client 参数
    （默认 None 保持旧行为）、公式损伤启发式在 solver、settings 默认含 vision_providers。"""
    for fn in ("ocr_image", "vision_api", "resolve_image_text", "download_image"):
        assert hasattr(VN, fn), "core/vision.py 缺 %s" % fn
    import inspect
    sig = inspect.signature(solver.solve_job)
    assert "client" in sig.parameters, "solve_job 必须加 client 可选参数（B6a-1a）"
    assert sig.parameters["client"].default is None, "client 默认必须为 None→跳过图片链保持旧行为"
    assert hasattr(solver, "_ocr_dirty"), "solver 缺公式损伤启发式（B6a-1c）"
    assert "vision_providers" in pv.load_settings(), "settings 默认结构缺 vision_providers（B6a-2）"


def t_vision_solver_chain():
    """B6a-1 四场景（全 monkeypatch，0 网络 0 OCR）：
    A OCR脏→转vision_api留痕+置信0.75；B OCR干净→直用+置信0.6；
    C 全挂→need_manual 不硬答；D client=None→旧行为零变化。"""
    calls = {"stem": None, "n": 0, "vapi": 0}

    def fake_choice(cfg, keys, stem, options, max_tokens=400):
        calls["stem"] = stem
        calls["n"] += 1
        return "B", 0.9, "B"
    orig = (VN.download_image, VN.resolve_image_text, VN.vision_api, pv.solve_choice)
    VN.download_image = lambda client, url: b"PNGDATA"
    pv.solve_choice = fake_choice
    settings = {"providers": [{"kind": "openai", "name": "Fake", "base_url": "http://fake/v1",
                               "model": "m", "key_ref": "", "enabled": True}],
                "vision_providers": [{"kind": "openai_compat", "name": "GV",
                                      "base_url": "http://gv/v1", "model": "gm", "enabled": True}],
                "confidence_threshold": 0.75, "rate_limit_s": [0.001, 0.002]}

    def mkq(qid):
        return {"qid": qid, "type": "single", "stem": "", "options": {"A": "1", "B": "2"},
                "image_flag": True, "img_urls": ["//an.mooc/img/%s.png" % qid]}
    ref = {"courseId": "9", "workId": "v"}
    try:
        # A：OCR 脏（含 | 行列式残留）→ 转 vision_api
        VN.resolve_image_text = lambda d, s=None, k=None, hint="": ("|A| = 4\n2 3\n5 6", "ocr")
        def fake_vapi(d, cfg, keys, hint=""):
            calls["vapi"] += 1
            return "矩阵A的行列式等于4", 0.85
        VN.vision_api = fake_vapi
        job = solver.Job(ref, [mkq("v1")])
        solver.solve_job(job, settings=settings, keys={}, client=object())
        assert "行列式" in (calls["stem"] or ""), "vision_api 文本未追加进 stem"
        assert job.questions[0].get("_img_source") == "vision_api", "_img_source 留痕缺失"
        assert abs(job.results["v1"]["confidence"] - 0.75) < 1e-9, \
            "vision_api 置信应封顶 0.75: %s" % job.results["v1"]["confidence"]
        assert calls["vapi"] == 1, "OCR 脏应转试 vision_api"
        # B：OCR 干净 → 直用
        VN.resolve_image_text = lambda d, s=None, k=None, hint="": ("求下列微分方程的通解步骤", "ocr")
        VN.vision_api = lambda *a, **k: (_ for _ in ()).throw(AssertionError("干净OCR不应调vision_api"))
        job = solver.Job(ref, [mkq("v2")])
        solver.solve_job(job, settings=settings, keys={}, client=object())
        assert job.questions[0].get("_img_source") == "ocr", "干净OCR应直用并留痕"
        assert abs(job.results["v2"]["confidence"] - 0.6) < 1e-9, \
            "ocr 置信应封顶 0.6: %s" % job.results["v2"]["confidence"]
        # C：全挂 → need_manual，不调模型
        VN.resolve_image_text = lambda d, s=None, k=None, hint="": (None, "none")
        job = solver.Job(ref, [mkq("v3")])
        n0 = calls["n"]
        solver.solve_job(job, settings=settings, keys={}, client=object())
        r = job.results["v3"]
        assert r.get("need_manual") is True and r["status"] != "ok", \
            "全挂应标 need_manual 不硬答: %s" % r
        assert calls["n"] == n0, "need_manual 题不得走模型（不硬答）"
        # D：client=None → 旧行为（空 stem 照旧问模型，零新字段）
        job = solver.Job(ref, [mkq("v4")])
        solver.solve_job(job, settings=settings, keys={})
        assert calls["n"] == n0 + 1, "无 client 应走旧路径直问模型"
        assert "need_manual" not in job.results["v4"], "旧行为不应新增 need_manual"
        assert "_img_source" not in job.questions[0], "旧行为不应新增 _img_source"
    finally:
        VN.download_image, VN.resolve_image_text, VN.vision_api, pv.solve_choice = orig
    # 启发式细节（任务书 1c）
    assert solver._ocr_dirty("|A| = 4"), "|/｜ 行列式残留应判脏"
    assert solver._ocr_dirty("A² + B² = C"), "上标残留应判脏"
    assert solver._ocr_dirty("4\n5\n6\n7\nx + y = 1"), "纯数字孤行占比>40% 应判脏"
    assert not solver._ocr_dirty("求下列不定积分的计算方法与步骤"), "干净文本不得误伤"


def t_vision_settings_block():
    """B6a-2：设置屏「视图API」区块——无配置折叠为一行「+ 添加视图API」；
    添加后四输入（名称/base_url/model/key引用）+保存写回 vision_providers；含免费推荐文案。
    全程 monkeypatch load/save_settings，绝不碰用户 %APPDATA% 真文件。"""
    orig_load, orig_save = pv.load_settings, pv.save_settings
    store = {"providers": [], "vision_providers": [], "confidence_threshold": 0.75}
    pv.load_settings = lambda: json.loads(json.dumps(store))
    pv.save_settings = lambda s: store.update(json.loads(json.dumps(s)))
    try:
        assert S.vision_col is not None, "设置屏应常驻视图API区块容器（S.vision_col）"
        S.render_vision_block()
        texts = all_strings(S.vision_col)
        assert any("+ 添加视图API" in t for t in texts), "空配置应折叠为「+ 添加视图API」: %s" % texts
        assert len(S.vision_col.controls) == 1, \
            "空配置应折叠为一行（只显示配置好的），实际 %d 行" % len(S.vision_col.controls)
        add_btn = find_btn(S.vision_col, "+ 添加视图API")
        add_btn.on_click(SimpleNamespace(control=add_btn))
        assert len(store["vision_providers"]) == 1, "点击添加应写入一条默认条目"
        assert store["vision_providers"][0].get("kind") == "openai_compat", \
            "条目 kind 必须是 openai_compat（resolve_image_text 认这个）"
        S.render_vision_block()
        labels = {f.label for f in walk_objs(S.vision_col)
                  if isinstance(f, (ft.TextField, ft.Dropdown))}
        assert {"供应商", "名称", "base_url", "model", "key引用/粘贴key"} <= labels, \
            "B6d 五控件缺失: %s" % labels
        hint = collect_texts(S.vision_col)
        assert any("SiliconFlow" in t and "api.siliconflow.cn" in t for t in hint), \
            "缺白嫖供应商推荐文案（B6c: SiliconFlow 优先，Gemini 已除名）: %s" % hint
        # 保存写回
        fields = {f.label: f for f in walk_objs(S.vision_col)
                  if isinstance(f, (ft.TextField, ft.Dropdown))}
        # B6d: 默认模板=硅基流动预设，model 是 Dropdown
        assert fields["供应商"].value == "SiliconFlow 硅基流动(快,推荐)", \
            "默认模板应预置硅基流动: %s" % fields["供应商"].value
        fields["名称"].value = "Gemini"
        fields["base_url"].value = "https://generativelanguage.googleapis.com/v1beta/openai"
        fields["model"].value = "gemini-2.0-flash"
        fields["key引用/粘贴key"].value = "gemini"
        save_btn = find_btn(S.vision_col, "保存")
        save_btn.on_click(SimpleNamespace(control=save_btn))
        c = store["vision_providers"][0]
        assert c["name"] == "Gemini" and c["model"] == "gemini-2.0-flash" and c["key_ref"] == "gemini", \
            "保存未写回: %s" % c
        assert c["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai", "base_url 未写回"
        # 长串无空格=key本体 → 存 api_key（vision_api 回退字段），条目即刻可用
        fields["key引用/粘贴key"].value = "AIzaSyD1234567890abcdefghijklmnopqrst"
        save_btn.on_click(SimpleNamespace(control=save_btn))
        assert store["vision_providers"][0].get("api_key", "").startswith("AIza"), "粘贴 key 本体应存 api_key"
    finally:
        pv.load_settings, pv.save_settings = orig_load, orig_save
        S.render_vision_block()


def t_need_manual_card():
    """B6a-3：need_manual 题——审批卡显示「需人工」徽标+原图(https 补全)+每空一输入框+
    「采用」写回 answers（只动 job.results，confirm/submitter 零改动）；队列卡显示计数。"""
    st = app_v2.state
    key = "smoke:manual"
    q = {"qid": "m1", "type": "blank", "type_name": "填空题", "stem": "求行列式的值",
         "options": {}, "image_flag": True, "blank_count": 2,
         "img_urls": ["//ananas-m02.elab-cdn/m1.png"]}
    job = solver.Job({"courseId": "9", "workId": "m"}, [q])
    job.state = "partial"
    job.results["m1"] = {"qid": "m1", "type": "blank", "status": "need_manual", "answer": "",
                         "confidence": 0.0, "source": "", "tries": 0, "need_manual": True}
    ref = {"course": "冒烟课", "title": "识图测试", "courseId": "9", "classId": "c",
           "cpi": "1", "workId": "m", "answerId": "0"}
    st["jobs"][key] = (job, [q], {}, ref)
    task = dict(app_v2.make_fake_tasks()[0])
    task["key"] = key
    st["tasks"].append(task)
    try:
        st["approve"].add(key)
        S.render_approvals()
        texts = collect_texts(S.approve_list)
        assert any("需人工" in t for t in texts), "need_manual 应显示徽标: %s" % texts[:8]
        imgs = [o for o in walk_objs(S.approve_list) if isinstance(o, ft.Image)]
        assert len(imgs) == 1, "题图应渲染为 ft.Image，实际 %d" % len(imgs)
        assert imgs[0].src.startswith("https://"), "协议补全 // → https://: %s" % imgs[0].src
        tfs = [f for f in walk_objs(S.approve_list) if isinstance(f, ft.TextField)]
        assert len(tfs) == 2, "填空题应每空一个输入框，实际 %d" % len(tfs)
        # 队列卡计数
        st["selected"].add(key)
        S.render_queue()
        assert any("需人工" in t for t in collect_texts(S.queue_list)), "队列卡应显示需人工计数"
        st["selected"].discard(key)
        # 采用写回 → status ok、答案进 results（提交链 build_form 直接消费）
        tfs[0].value = "2"
        tfs[1].value = "-1"
        adopt = find_btn(S.approve_list, "采用")
        adopt.on_click(SimpleNamespace(control=adopt))
        r = job.results["m1"]
        assert r["status"] == "ok" and r["answer"] == ["2", "-1"] and not r.get("need_manual"), \
            "采用未写回/写错: %s" % r
        S.render_approvals()
        assert not any("需人工" in t for t in collect_texts(S.approve_list)), \
            "采用后该题不应再显需人工"
    finally:
        st["approve"].discard(key)
        st["jobs"].pop(key, None)
        st["tasks"][:] = [t for t in st["tasks"] if t["key"] != key]
        S.render_approvals()
        S.render_queue()


# ---------- TASK-A1 作业审核 ----------
class _BoomClient:
    """任何请求方法都被抓：审核路径应零网络（缓存命中/etype 本地判定）。"""
    def raw_get(self, *a, **k):
        raise AssertionError("audit 走了网络请求（应零请求：缓存命中或非作业本地判定）")

    def get_json(self, *a, **k):
        raise AssertionError("audit 走了网络请求（应零请求）")


def _mk_task(key, etype="work", **over):
    t = dict(app_v2.make_fake_tasks()[0])
    t.update({"key": key, "workId": key, "courseId": "9001", "classId": "c", "cpi": "1",
              "answerId": "0", "etype": etype})
    t.update(over)
    return t


def t_audit_task_core():
    """A1 核心：三态分类 / 缓存读写二访零请求 / TTL 过期重判 / 预览截断 / 异常不抛出。
    缓存文件重定向到临时目录，绝不碰 %APPDATA%\\cx-pilot 真数据；网络全 Boom/monkeypatch。"""
    tmp = os.path.join(ROOT, "build", "smoke_audit_data")
    os.makedirs(tmp, exist_ok=True)
    cf = os.path.join(tmp, "audit_cache.json")
    if os.path.exists(cf):
        os.remove(cf)
    orig_data = CLIENT.DATA
    CLIENT.DATA = tmp
    try:
        # ① 非作业：etype 本地判定，零请求 solvable=False，且不碰 questions 链
        t1 = _mk_task("au1", "sign")
        r1 = AUD.audit_task(_BoomClient(), t1)
        assert r1["solvable"] is False and r1["reason"] == "非作业", "①非作业分类: %s" % r1
        assert r1["from_cache"] is False
        assert os.path.exists(cf), "①缓存文件未落盘 %s" % cf
        # ② 二访：Boom 客户端也照常返回 → 实锤缓存命中零请求
        r1b = AUD.audit_task(_BoomClient(), t1)
        assert r1b["from_cache"] is True and r1b["reason"] == "非作业", "②缓存命中: %s" % r1b
        # ③ work：领卷成功 → solvable+题数+预览（第 1 题截 24 字、图题只标[图]、第 3 题不入预览）
        long_stem = "矩阵论期末复习" * 5          # 40 字 > 24
        qs = [{"qid": "1", "type": "single", "stem": long_stem, "image_flag": False,
               "options": {}},
              {"qid": "2", "type": "blank", "stem": "勿入预览文字", "image_flag": True,
               "options": {}},
              {"qid": "3", "type": "single", "stem": "第三题绝不该出现", "image_flag": False,
               "options": {}}]
        orig_fetch = QM.fetch_work_questions
        QM.fetch_work_questions = lambda c, *a, **k: (qs, {})
        try:
            t2 = _mk_task("au2")
            r2 = AUD.audit_task(object(), t2)   # 需一次领卷（monkeypatched，无真网络）
            assert r2["solvable"] is True and r2["qreal"] == 3, "③可作答: %s" % r2
            assert r2["preview"] == long_stem[:24] + " ▸ [图]", "③预览截断/图占位: %r" % r2["preview"]
            r2b = AUD.audit_task(_BoomClient(), t2)
            assert r2b["from_cache"] is True and r2b["preview"] == r2["preview"], "③二访缓存: %s" % r2b
            # ④ 0 题 → 无题（稳定判定，缓存）
            QM.fetch_work_questions = lambda c, *a, **k: ([], {})
            t3 = _mk_task("au3")
            r3 = AUD.audit_task(object(), t3)
            assert r3["solvable"] is False and r3["reason"] == "无题", "④无题: %s" % r3
            assert AUD.audit_task(_BoomClient(), t3)["from_cache"] is True, "④无题应缓存"
            # ⑤a 门槛/403 → 读不到题且缓存（4h 内不会变）
            def gate(c, *a, **k):
                raise Exception("HTTP Error 403: Forbidden")
            QM.fetch_work_questions = gate
            t4 = _mk_task("au4")
            r4 = AUD.audit_task(object(), t4)
            assert r4["solvable"] is False and r4["reason"] == "读不到题", "⑤a 403: %s" % r4
            assert AUD.audit_task(_BoomClient(), t4)["from_cache"] is True, "⑤a 403 应缓存"
            # ⑤b 瞬时错（超时）→ 不缓存，二访必须重新领卷——一次抖动绝不灰锁 4h
            n_fetch = {"n": 0}
            def flaky(c, *a, **k):
                n_fetch["n"] += 1
                raise Exception("timed out")
            QM.fetch_work_questions = flaky
            t5 = _mk_task("au5")
            r5 = AUD.audit_task(object(), t5)
            assert r5["solvable"] is False and r5["reason"] == "读不到题", "⑤b 超时分类: %s" % r5
            r5b = AUD.audit_task(object(), t5)
            assert n_fetch["n"] == 2 and not r5b["from_cache"], \
                "⑤b 超时结果被错误缓存（应未命中）: %s / fetch=%d" % (r5b, n_fetch["n"])
        finally:
            QM.fetch_work_questions = orig_fetch
        # ⑥ TTL 过期 → 重新判定（缓存条目改旧时间戳模拟）
        d = json.load(open(cf, encoding="utf-8"))
        d["9001:au1"]["ts"] -= AUD.TTL + 60
        json.dump(d, open(cf, "w", encoding="utf-8"))
        r1c = AUD.audit_task(_BoomClient(), _mk_task("au1", "sign"))
        assert r1c["from_cache"] is False, "⑥TTL 过期应重判"
        # ⑦ 缓存文件损坏 → 不炸，退化为实时判定
        open(cf, "w", encoding="utf-8").write("{not-json!!")
        r1d = AUD.audit_task(_BoomClient(), _mk_task("au1", "sign"))
        assert r1d["solvable"] is False and r1d["reason"] == "非作业", "⑦坏缓存应退化不炸: %s" % r1d
    finally:
        CLIENT.DATA = orig_data


def t_audit_card_render():
    """A1 三态渲染：未审核带「待审」小字；可作答=题数徽章+预览行+正常亮度；
    不可作答=灰卡 0.45+pill 改写+预览藏。两模式（科目列/时间流）都走同一 layout 通路。"""
    p = S.plan
    st = app_v2.state
    pend_t = _mk_task("ar-pend", "work", course="审核课", courseId="9101", title="待审的活")
    solv_t = _mk_task("ar-solv", "work", course="审核课", courseId="9101", title="可做的活",
                      qn=0, pill="剩余 2 小时", pcls="urgent")
    unsolv_t = _mk_task("ar-unsolv", "read", course="审核课", courseId="9101",
                        title="续签合同", pill="剩余 3 天")
    app_v2.apply_audit_result(solv_t, {"solvable": True, "qreal": 5,
                                       "preview": "第一题题干 ▸ [图]", "reason": ""})
    app_v2.apply_audit_result(unsolv_t, {"solvable": False, "qreal": 0,
                                         "preview": "", "reason": "非作业"})
    st["tasks"] = [pend_t, solv_t, unsolv_t]
    p.selected.clear()
    p.excluded.clear()
    p.mode = "course"
    p.refresh(st["tasks"])
    p.layout()
    for mode in ("course", "time"):
        if mode == "time":
            p._seg_pick("time")
        cards = {t["key"]: p._cards[t["key"]] for t in st["tasks"]}
        c0, c1, c2 = cards["ar-pend"], cards["ar-solv"], cards["ar-unsolv"]
        # 待审小字：仅未审核卡
        assert c0._pend.visible is True, "%s：未审核卡应显示「待审」" % mode
        assert c1._pend.visible is False and c2._pend.visible is False, "%s：已审核卡不应残留待审" % mode
        # 可作答：题数徽章（qn 回填 5）+ 预览行上屏 + 全亮
        assert c1._qn_txt.value == "5 题", "%s：可作答应显真实题数: %s" % (mode, c1._qn_txt.value)
        assert c1._preview.visible is True and c1._preview.value == "第一题题干 ▸ [图]", \
            "%s：预览行未渲染" % mode
        assert c1.opacity == 1.0, "%s：可作答卡不该灰: %s" % (mode, c1.opacity)
        assert c1._pill_box._txt.value == "剩余 2 小时", "可作答卡保留原紧急度 pill"
        # 不可作答：灰卡 0.45 + pill 写「非作业」+ 无预览
        assert c2.opacity == UNSEL_OPACITY, "%s：不可作答卡应灰置 0.45: %s" % (mode, c2.opacity)
        assert c2._pill_box._txt.value == "非作业" and \
            c2._pill_box.bgcolor == T.pill_style("grey")[0], "%s：pill 应写非作业灰底" % mode
        assert c2._preview.visible is False, "%s：不可作答卡不应有预览" % mode
        # 预览行为卡加高（仅可作答卡）
        from ui.plan_view import card_height
        assert card_height(solv_t, T.COLW) - card_height(dict(solv_t, preview=""), T.COLW) \
            == PREVIEW_LH, "预览行应加高 18px"
        if mode == "time":
            p._seg_pick("course")
    assert solv_t["qn"] == 5 and unsolv_t["pcls"] == "grey"
    # 黑名单沉底 0.35 与不可作答 0.45 叠加时更暗者优先（黑名单赢）
    p.toggle_excluded("审核课")
    assert p._cards["ar-unsolv"].opacity == 0.35, "沉底灰卡应保持 0.35（黑名单优先）"
    p._restore_all()


def t_audit_guard_and_queue():
    """A1 禁选与队列过滤：不可作答卡点击不 toggle；本科全选跳过之；审核判不可作答时
    把审核前抢勾的项从 selected 剔除；queueable_keys 兜底过滤。"""
    p = S.plan
    st = app_v2.state
    a = _mk_task("g-solv", "work", course="守卫课", courseId="9102", title="能做")
    b = _mk_task("g-no", "contract", course="守卫课", courseId="9102", title="合同")
    app_v2.apply_audit_result(a, {"solvable": True, "qreal": 2, "preview": "x", "reason": ""})
    app_v2.apply_audit_result(b, {"solvable": False, "qreal": 0, "preview": "", "reason": "非作业"})
    st["tasks"] = [a, b]
    p.selected.clear()
    p.excluded.clear()
    p.mode = "course"
    p.refresh(st["tasks"])
    p.layout()
    card_b = p._cards["g-no"]
    card_b.on_click(SimpleNamespace())                # 点击不可作答卡体 = 无效
    assert "g-no" not in p.selected, "不可作答卡点击不应入 selected"
    card_a = p._cards["g-solv"]
    card_a.on_click(SimpleNamespace())
    assert "g-solv" in p.selected, "可作答卡点击应正常 toggle"
    card_a.on_click(SimpleNamespace())
    assert not p.selected
    h = p._heads["守卫课"]
    ev = SimpleNamespace(control=h, name="click")
    h.on_click(ev)                                    # 本科全选
    assert "g-solv" in p.selected and "g-no" not in p.selected, "本科全选应跳过不可作答"
    h.on_click(ev)                                    # 可作答已全选 → 再点取消
    assert not (p.selected & {"g-solv"}), "再点应取消可作答项全选"
    # 审核前抢勾 → apply_audit_result 剔除
    p.selected.add("g-no")
    app_v2.apply_audit_result(b, {"solvable": False, "qreal": 0, "preview": "", "reason": "读不到题"})
    assert "g-no" not in st["selected"], "判不可作答时应把已勾选项踢出队列"
    assert b["pill"] == "读不到题", "读不到 pill 文案: %s" % b["pill"]
    # 队列入口过滤：selected 里硬塞可作答+不可作答，queueable_keys 只带得进可作答
    st["selected"].update({"g-solv", "g-no"})
    assert sorted(app_v2.queueable_keys()) == ["g-solv"], "queueable_keys 应滤掉不可作答: %s" \
        % app_v2.queueable_keys()
    st["selected"].clear()
    assert not st["selected"]


def t_audit_worker_and_trigger():
    """A1 触发与线程：selftest 模式 start_audit 必须直接跳过（0 网络）；gen 代际不匹配
    即毙；_audit_pass 串行逐条回填 + 「审核中 n/N」广播（_busy_set monkeypatch 捕获，
    audit_task monkeypatch 0 网络 0 真缓存）。"""
    st = app_v2.state
    g0 = st.get("audit_gen", 0)
    st["selftest"] = True
    app_v2.start_audit()
    assert st.get("audit_gen", 0) == g0, "selftest 模式不得触发审核（会打真网络）"
    st["selftest"] = False
    try:
        t1 = _mk_task("wk1", "work", course="线程课", courseId="9103")
        t2 = _mk_task("wk2", "exam", course="线程课", courseId="9103")
        t3 = _mk_task("wk3", "work", course="线程课", courseId="9103")
        t3.update({"audited": True, "solvable": True, "qreal": 1, "preview": ""})
        t3_qn0 = t3["qn"]
        st["tasks"] = [t1, t2, t3]
        S.plan.selected.clear()
        S.plan.mode = "course"
        S.plan.refresh(st["tasks"])
        S.plan.layout()
        seen_busy, calls = [], []
        orig_busy, orig_sleep, orig_at = app_v2._busy_set, app_v2.AUDIT_SLEEP, AUD.audit_task
        def fake_busy(mode, txt):
            seen_busy.append(txt)
        def fake_at(c, t, force=False):
            calls.append(t["key"])
            return {"audited": True, "solvable": t["key"] == "wk1",
                    "reason": "" if t["key"] == "wk1" else "非作业",
                    "qreal": 3 if t["key"] == "wk1" else 0, "preview": ""}
        app_v2._busy_set = fake_busy
        app_v2.AUDIT_SLEEP = 0.0
        AUD.audit_task = fake_at
        try:
            st["audit_gen"] = 77
            app_v2._audit_pass(object(), 77)
        finally:
            app_v2._busy_set, app_v2.AUDIT_SLEEP, AUD.audit_task = orig_busy, orig_sleep, orig_at
        assert calls == ["wk1", "wk2"], "串行审核应跳过已审条目: %s" % calls
        assert t1["solvable"] is True and t1["qn"] == 3, "可作答应回填 qn: %s" % t1.get("qn")
        assert t2["audited"] is True and t2["solvable"] is False and t2["pill"] == "非作业"
        assert "wk3" not in calls and t3["qn"] == t3_qn0, "已审条目不得被复审核动数据"
        assert any(x and x.startswith("审核中 1/2") for x in seen_busy), "进度广播缺: %s" % seen_busy
        assert any(x and x.startswith("审核中 2/2") for x in seen_busy), "进度广播缺: %s" % seen_busy
        assert seen_busy[-1] is None, "收尾应清忙碌文字: %s" % seen_busy[-1]
        assert any("审核完成 2/2" in l for l in st["log"]), "完成日志缺"
        # gen 不匹配：即毙，一条都不审
        t4 = _mk_task("wk4", "work", course="线程课", courseId="9103")
        t4.pop("audited", None)
        st["tasks"] = [t4]
        calls.clear()
        st["audit_gen"] = 78
        app_v2._busy_set = lambda m, x: None
        AUD.audit_task = fake_at
        try:
            app_v2._audit_pass(object(), 77)          # 旧代际 → 立即 return
        finally:
            app_v2._busy_set = orig_busy
            AUD.audit_task = orig_at
        assert calls == [], "gen 不匹配仍执行了审核: %s" % calls
        st["audit_gen"] = g0 + 1
    finally:
        st["selftest"] = False


TESTS = [t_fake_data, t_empty_placeholder, t_course_layout, t_persistent_instances,
         t_select_and_bottombar, t_time_mode, t_blacklist_and_restore,
         t_course_excluded_last, t_five_screens, t_log_coloring, t_queue_render,
         t_approvals, t_seg_visuals, t_card_visual_tokens, t_hex_audit,
         t_safe_update_error_visible, t_do_refresh_thread,
         t_seg_switch_no_refresh, t_eye_icon_visible, t_head_click_split,
         t_progress_bar, t_wheel_single_scrollable, t_refresh_menu_single,
         t_animation_450, t_second_session,
         t_works_blacklist, t_unsolvable_sinks_to_bottom, t_qn_pending_and_backfill,
         t_vision_chain_present, t_vision_solver_chain,
         t_vision_settings_block, t_need_manual_card,
         t_audit_task_core, t_audit_card_render,
         t_audit_guard_and_queue, t_audit_worker_and_trigger]


def main():
    boot()
    failures = []
    for fn in TESTS:
        try:
            fn()
            print("[PASS] %s" % fn.__name__)
        except Exception as e:
            import traceback
            failures.append(fn.__name__)
            print("[FAIL] %s: %r" % (fn.__name__, e))
            traceback.print_exc()
    print("-" * 50)
    if failures:
        print("SMOKE FAIL: %d/%d 通过，失败: %s" % (len(TESTS) - len(failures), len(TESTS), failures))
        sys.exit(1)
    print("SMOKE PASS: %d/%d 全绿" % (len(TESTS), len(TESTS)))


if __name__ == "__main__":
    main()
