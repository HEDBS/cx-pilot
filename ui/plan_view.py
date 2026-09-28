# -*- coding: utf-8 -*-
"""作业计划屏（DESIGN.md 实现）：科目列 / 时间流 / 黑名单，Stack 绝对定位 + 位移动画。

常驻元素原则（DESIGN.md 交互规则 4）：卡片/列头/恢复条只构造一次，refresh/layout 只改
坐标与选中/排除态，绝不重建控件实例（重建会杀位移动画，且旧实例引用失效）。

B3c 根因修复（见 docs/claude-b3-log.md 与 docs/TASK-B3c.md）：
1. 本树按浏览器会话整体新建——一个 PlanView 实例只服务一个 page，绝不跨会话复用；
   selected/excluded/tasks 等「数据」通过构造时传入的共享 state 引用读取，数据共享、实例不共享。
2. 禁止换父重挂载：旧 layout() 每次新建 ft.Row/ft.Column 再 `scroller.content = self._inner`
   —— 服务端记录树与真实树漂移后，diff 会发出非法 remove（AssertionError: _process_remove_command:
   control with ID 'None' not found / KeyError '_58'），重连会话首推即炸、永远白屏。
   现改为骨架常驻：Container > Column(vcol) > Row(hrow) > holder > Stack，引导卡与恢复条也是
   一次性构造的常驻实例；layout() 只改「已挂载」控件的坐标/可见性/scroll/alignment 属性。
   红线：任何 layout 不得新建并塞入新的中间容器。
滚动方向（任务书方案）：科目列 = hrow.scroll=AUTO（横向）+ vcol 不滚；
时间流 = hrow.scroll=None + vcol.scroll=AUTO（纵向）+ 两列居中（样张禁左对齐）。

B3d 桌面四修（docs/TASK-B3d.md）：
- _seg_change/_seg_pick：点击事件内同步 layout+广播（Flet 0.28 事件后不会自动 update，
  旧版「由事件回传完成」是幻想——分段切换必须等下一次 do_refresh 才上屏）。
- layout()：stack.children 卡片恒定按 tasks 原序、列头/恢复条追加尾部——Flet diff 用
  SequenceMatcher 按对象 id 匹配 children，顺序一变就 remove+add 重建节点，450ms 位移
  动画直接死掉（「动效全部消失」根因；动画对象本身 build_card 一直没丢）。
- 列头 👁 常显（INK3→悬停 ACCENT，点击=隐藏）：桌面 hover 不可靠，原「悬停才显示」作废。
B3e/B4 残留清账（docs/TASK-B4.md）：眼睛换矢量 ft.Icons.VISIBILITY_*（禁 emoji，隐藏态
切 OFF 变体）；列头大框点击=本科全选（select_course），小眼睛=仅隐藏（嵌套 ink
Container 优先命中，等价 stopPropagation）；vcol 外层单纵向 scrollable 两模式常开
（修滚轮无反应），hrow 仅科目模式横向、expand 在可滚容器内关闭。

B4h（docs/TASK-B4h.md，用户授权机制）：
- P0-1 回归修复：B4f 手改的 offset 定位导致桌面科目列作业卡整体消失，全文件回退
  Stack 子级 left/top 定位——单一机制，禁混用 offset（冒烟断言 offset 必须为 None）。
- P1 动效：left/top 同批改值靠 Container.animate_position=Animation(450,
  EASE_IN_OUT_CUBIC_EMPHASIZED) 在 Flutter 端补间（flet 0.28 Container 有此参数），
  替代 offset 动画机制；animate 常驻实例只改属性 + safe_update 广播同批上屏。
- P0-2 题数：getAllWork 列表页无题数字段（qn=0），卡上显示「待领卷」而非「0 题」；
  领卷后 app_v2 回填 t["qn"] 并 sync_plans → refresh 原地改卡上 _qn_txt 文字。"""
import textwrap

import flet as ft

from core import theme as T

CARD_PAD_V = 28       # 卡内上下 padding 之和（样张 .row padding:14px 16px）
TITLE_LH = 21         # 14.5px * 1.45
SUB_LH = 19           # 12.5px * 1.5
HDR_H = 44
SUNK_GAP = 44         # 恢复条与沉底卡片区之间必须留 44px（重叠事故已修）
CARD_GAP = 10         # 卡纵向间距（样张 y+=mh+10）


def _wrap(text, width):
    lines = []
    for seg in str(text).split("\n"):
        lines.extend(textwrap.wrap(seg, width) or [""])
    return lines[:3]  # 样张规范：完整显示，超 3 行截断（实际作业标题极罕见）


def card_height(work, col_w):
    # 标题可用宽 ≈ 列宽 - 复选20 - 间距 - meta胶囊区
    tw = max(10, (col_w - 150) // 15)
    tl = len(_wrap(work["title"], tw)) or 1
    sub = "%s · %s" % (work["course"], work.get("sub", ""))
    sl = len(_wrap(sub, max(10, (col_w - 60) // 13))) or 1
    return CARD_PAD_V + tl * TITLE_LH + 4 + sl * SUB_LH + 4


def qn_label(qn):
    """题数文案（B4h P0-2）：列表阶段 qn=0 →「待领卷」；领卷回填后 →「N 题」。"""
    return "待领卷" if not qn else "%s 题" % qn


def _pill(work):
    pbg, pfg = T.pill_style(work.get("pcls", "grey"))
    return ft.Container(ft.Text(work.get("pill", ""), size=11.5,
                                weight=ft.FontWeight.W_600, color=pfg),
                        bgcolor=pbg, border_radius=999,
                        padding=ft.padding.symmetric(vertical=4, horizontal=11))


def build_card(work, col_w, selected, on_toggle):
    """构造一张卡片（本会话内常驻实例）。选中/排除态由 apply_card_state 原地改，不重建。"""
    tw = max(10, (col_w - 150) // 15)
    title_lines = "\n".join(_wrap(work["title"], tw))
    sub = "%s · %s" % (work["course"], work.get("sub", ""))
    chk = ft.Container(
        width=20, height=20, border_radius=10,
        border=ft.border.all(1.6, T.ACCENT if selected else T.INK3),
        bgcolor=T.ACCENT if selected else None,
        content=ft.Icon(ft.Icons.CHECK, size=13, color=T.WHITE),
        alignment=ft.alignment.center,
        margin=ft.margin.only(top=3),                    # 样张 .chk margin-top:3px
    )
    chk.content.visible = selected
    card = ft.Container(
        width=col_w,
        bgcolor=T.CARD, border_radius=T.RADIUS, shadow=T.SHADOW_CARD,
        padding=ft.padding.symmetric(vertical=14, horizontal=16),
        content=ft.Row(
            [chk,
             ft.Column(
                 [ft.Text(title_lines, size=14.5, weight=ft.FontWeight.W_600,
                          color=T.INK, style=ft.TextStyle(height=1.45)),
                  ft.Text(sub, size=12.5, color=T.INK2,
                          style=ft.TextStyle(height=1.5))],
                 spacing=4, expand=True),
             # 样张 .r-meta：横排「N 题 + 紧急度胶囊」右对齐（不是竖排！）
             # B4h P0-2：qn=0（getAllWork 列表页无题数）显「待领卷」，领卷回填后刷新
             ft.Container(
                 ft.Row([(qn_txt := ft.Text(qn_label(work.get("qn", 0)),
                                            size=12, color=T.INK2)),
                         _pill(work)], spacing=9,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER),
                 margin=ft.margin.only(top=2))],
            spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
        ink=True,
        on_click=lambda e: on_toggle(work),
        animate=ft.Animation(450, curve=ft.AnimationCurve.EASE_IN_OUT_CUBIC_EMPHASIZED),
        # TODO(B4h P1)：animate_position 是 left/top 回退后的动效载体（对象层冒烟已锁 450ms）。
        # 若桌面 exe 实测 Flutter 端不补间（仅瞬移），退化方案=layout 记旧坐标、后台线程
        # 4 批×100ms 逐帧逼近目标 + 每批 safe_update()——见 docs/TASK-B4h.md P1，勿用混机制。
        animate_position=ft.Animation(450, curve=ft.AnimationCurve.EASE_IN_OUT_CUBIC_EMPHASIZED),
        animate_opacity=250,
    )
    card._chk = chk
    card._qn_txt = qn_txt
    apply_card_state(card, selected, False)
    return card


def apply_card_state(card, selected, excluded):
    chk = card._chk
    chk.border = ft.border.all(1.6, T.ACCENT if selected else T.INK3)
    chk.bgcolor = T.ACCENT if selected else None
    chk.content.visible = selected
    card.opacity = 0.35 if excluded else 1.0              # 样张 .card.excluded{opacity:.35}


class PlanView:
    """一个会话一棵树：外部通过 refresh(tasks) 喂数据。
    data: 跨会话共享的 state dict（selected/excluded 从这里实时读——数据共享、控件实例绝不共享）。
    on_change: 选中/排除集合被本会话改动后的回调（app_v2 侧做全会话广播）。
    tasks: [{key,course,courseId,title,workId,end,remain,qnum,status,ts}]"""

    def __init__(self, data, on_change=None):
        self._d = data
        self.on_change = on_change or (lambda: None)
        self.tasks = []
        self.mode = "course"             # 视图态：科目列/时间流，每个会话各选各的
        self._cards = {}                 # key -> card（本会话常驻实例）
        self._heads = {}                 # course -> head（本会话常驻实例）
        # ---- 固定骨架，一次构造、一次挂载，layout() 永不换父重挂载 ----
        self.stack = ft.Stack([], width=10, height=10)
        self.stack_holder = ft.Container(content=self.stack)
        self.hrow = ft.Row([self.stack_holder], expand=True,
                           scroll=ft.ScrollMode.AUTO,
                           vertical_alignment=ft.CrossAxisAlignment.START)
        self.placeholder = ft.Container(self._empty_placeholder(), visible=False)
        self.vcol = ft.Column([self.hrow, self.placeholder], expand=True,
                              scroll=None,
                              alignment=ft.MainAxisAlignment.START,
                              horizontal_alignment=ft.CrossAxisAlignment.START)
        self.scroller = ft.Container(
            content=self.vcol, expand=True, bgcolor=T.BG, border_radius=12,
            padding=ft.padding.only(10, 6, 20, 4))
        # 恢复条常驻实例（旧版每次 layout 新建一个塞进 stack，现在只切坐标与在/离场）
        self._restore = None
        self._viewport_h = 0        # B4i-1：scroller.on_resize 记录的视口高，贴底滚动条用
        self.scroller.on_resize = self._on_viewport
        self._restore_bar = self.restore_bar()
        self._restore_bar.visible = True
        self.showing_placeholder = False   # 空数据引导态是否在屏（selftest/冒烟断言用）
        self.seg = self._build_seg()

    def _on_viewport(self, e):
        """B4i-1：记视口高，科目列 Stack 高度取 max(内容, 视口) → hrow 贴底 =
        横向滚动条位于工作区最下方（时间流纵向内容天然撑满，不受影响）。"""
        h = int(e.height or 0)
        if h == self._viewport_h:
            return
        self._viewport_h = h
        if self.tasks:
            self.layout()
            self.on_change()          # safe_update 广播新 stack 高度上屏

    # ---------- 共享数据只读入口（B3c：数据源来自 state 引用） ----------
    @property
    def selected(self):
        return self._d["selected"]

    @property
    def excluded(self):
        return self._d["excluded"]

    # ---------- 自制分段控件（样张 .seg：灰容器 + 白底选中段，替代 Material 紫色 SegmentedButton） ----------
    def _build_seg(self):
        self._seg_lbls = {}
        row = ft.Row([], spacing=0)
        for val, label in (("course", "科目列"), ("time", "时间流")):
            btn = ft.Container(
                ft.Text(label, size=12.5, color=T.INK2),
                padding=ft.padding.symmetric(vertical=5, horizontal=13),
                border_radius=7, ink=True,
                on_click=lambda e, v=val: self._seg_change(e, v))
            self._seg_lbls[val] = btn
            row.controls.append(btn)
        self._seg_paint()
        return ft.Container(row, bgcolor=T.GRAY_SOFT, border_radius=T.RADIUS_BTN,
                            padding=2)

    def _seg_paint(self):
        for v, b in self._seg_lbls.items():
            on = (v == self.mode)
            b.bgcolor = T.CARD if on else None
            b.shadow = T.SHADOW_SEG if on else None
            b.content.color = T.INK if on else T.INK2
            b.content.weight = ft.FontWeight.W_600 if on else None

    def _seg_change(self, e, v):
        """分段控件点击事件入口。B3d-1 根因：Flet 0.28 事件 handler 跑完后【不会】自动
        page.update()（flet/core/page.py on_event_async 只调 handler）——旧版注释幻想
        「外层整页 update 由事件回传完成」，坐标变化全停在 Python 树里，直到下一次别的
        路径广播（如 do_refresh）才上屏 → 用户实拍「点了不切、再点刷新才切」。"""
        self._seg_pick(v)

    def _seg_pick(self, v):
        if v == self.mode:
            return
        self.mode = v
        self._seg_paint()
        self.layout()                    # 同一批卡片改坐标 → 位移动画（顺序稳定性见 layout）
        self.on_change()                 # B3d-1：点击事件内同步广播 sync_plans+safe_update，
                                         # 画面 500ms 内必切换，不再依赖任何后续刷新动作

    # ---------- 事件 ----------
    def toggle_select(self, work):
        k = work["key"]
        self.selected.add(k) if k not in self.selected else self.selected.discard(k)
        apply_card_state(self._cards[k], k in self.selected,
                         work["course"] in self.excluded)   # 原地改态，不动坐标
        self.on_change()                 # 广播：其它会话的树也要落同一份选中态

    def toggle_excluded(self, course):
        self.excluded.add(course) if course not in self.excluded else self.excluded.discard(course)
        self.layout()                    # 本会话立刻重排（动画连续性）
        self.on_change()                 # 再广播同步其它会话

    def _restore_all(self, e=None):
        self.excluded.clear()
        self.layout()
        self.on_change()

    # ---------- 构建常驻元素（本会话内） ----------
    def refresh(self, tasks):
        """增量喂数据：只为新 key 建卡，旧卡保实例（动画依赖）。"""
        self.tasks = tasks
        keys = {t["key"] for t in tasks}
        self._cards = {k: c for k, c in self._cards.items() if k in keys}
        for t in tasks:
            if t["key"] not in self._cards:
                self._cards[t["key"]] = build_card(
                    t, T.COLW, t["key"] in self.selected, self.toggle_select)
            else:  # B4h P0-2：领卷回填 qn 后，常驻卡原地换字（待领卷 → N 题）
                self._cards[t["key"]]._qn_txt.value = qn_label(t.get("qn", 0))
        for c in self._courses():
            if c not in self._heads:
                self._heads[c] = self._build_head(c)
            else:  # 计数随 merge 变化，原地更新文字
                h = self._heads[c]
                h._cnt.value = str(sum(1 for t in tasks if t["course"] == c))

    def _courses(self):
        seen = []
        for t in self.tasks:
            if t["course"] not in seen:
                seen.append(t["course"])
        return seen

    def _build_head(self, course):
        cnt = sum(1 for t in self.tasks if t["course"] == course)
        ex = course in self.excluded
        cid = next((t["courseId"] for t in self.tasks if t["course"] == course), "")
        dot = ft.Container(width=10, height=10, border_radius=5, bgcolor=T.course_color(cid))
        cnt_t = ft.Text(str(cnt), size=11, weight=ft.FontWeight.W_600, color=T.INK3)
        # B3e-1：眼睛 = 矢量图标 ft.Icons.VISIBILITY_OUTLINED（👁 emoji 太丑，禁用）。
        # 常显（桌面 hover 不可靠，入口不能藏）、INK3 常态、hover 变 ACCENT、点击=隐藏该科目。
        eye_ic = ft.Icon(ft.Icons.VISIBILITY_OUTLINED, size=16, color=T.INK3)
        eye = ft.Container(eye_ic,
                           padding=ft.padding.symmetric(vertical=2, horizontal=5),
                           border_radius=6, ink=True, visible=True,
                           on_click=lambda e, c=course: self.toggle_excluded(c),
                           on_hover=lambda e, c=course: self._eye_hover(c, str(e.data).lower() == "true"))
        flag = ft.Container(ft.Text("已隐藏 · 点恢复", size=10.5, color=T.INK2),
                            bgcolor=T.GRAY_SOFT, border_radius=999,
                            padding=ft.padding.symmetric(vertical=3, horizontal=9),
                            visible=ex, ink=True,
                            on_click=lambda e, c=course: self.toggle_excluded(c))
        # B3e-2：点击判定拆分——大框（色点+课程名+题数）= 本科全选/取消；
        # 右侧小眼睛 = 仅隐藏/恢复。内层 Container(ink+on_click) 在 Flet 命中测试里
        # 优先于外层（Flutter 手势竞技场 child 胜），天然等价 stopPropagation。
        head = ft.Container(
            content=ft.Row([dot,
                            ft.Text(course, size=13.5, weight=ft.FontWeight.W_700, color=T.INK,
                                    no_wrap=True, width=210,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                            cnt_t, flag, eye], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
            width=T.COLW, height=32, ink=True,
            on_click=lambda e, c=course: self.select_course(c),
            on_hover=lambda e, c=course: self._head_bg(c, str(e.data).lower() == "true"))
        head._cnt, head._flag, head._eye, head._eye_icon = cnt_t, flag, eye, eye_ic
        return head

    def _eye_hover(self, course, hovering):
        h = self._heads.get(course)
        if h:
            h._eye_icon.color = T.ACCENT if hovering else T.INK3

    def _head_bg(self, course, hovering):
        """B3e-2：大框 hover 微弱选中底色——提示「可点击=全选」。"""
        h = self._heads.get(course)
        if h:
            h.bgcolor = T.ACCENT_SOFT if hovering else None

    def select_course(self, course):
        """列头大框点击：该科目全部作业全选；已全选再点则全部取消。"""
        keys = [t["key"] for t in self.tasks if t["course"] == course]
        if not keys:
            return
        all_on = all(k in self.selected for k in keys)
        for t in self.tasks:
            if t["course"] != course:
                continue
            if all_on:
                self.selected.discard(t["key"])
            else:
                self.selected.add(t["key"])
            apply_card_state(self._cards[t["key"]], t["key"] in self.selected,
                             t["course"] in self.excluded)
        self.on_change()

    def restore_bar(self):
        return ft.Container(
            content=ft.Container(
                ft.Text("以下科目已隐藏 · 点我全部恢复", size=10.5, color=T.INK2),
                bgcolor=T.GRAY_SOFT, border_radius=999,
                padding=ft.padding.symmetric(vertical=3, horizontal=9)),
            width=T.COLW, height=34, alignment=ft.alignment.center_left,
            on_click=self._restore_all)

    def _empty_placeholder(self):
        """空数据引导态：stat2=0 且未扫描时不能是一片空白。常驻实例，layout 只切 visible。"""
        return ft.Container(
            ft.Column([ft.Text("暂无临期任务", size=14, weight=ft.FontWeight.W_600,
                               color=T.INK2),
                       ft.Text("点右上「刷新」菜单中的「全课程扫描(约2分钟)」拉取所有科目的待做作业",
                               size=12.5, color=T.INK3)],
                      spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=T.CARD, border_radius=T.RADIUS, shadow=T.SHADOW_CARD,
            padding=ft.padding.symmetric(vertical=28, horizontal=36))

    # ---------- 布局 ----------
    # 红线：本函数（及一切 layout 路径）不得新建中间容器塞进树、不得换 .content 重挂载。
    # 只允许：改已挂载控件的 left/top/width/height/visible，以及 hrow/vcol 的 scroll、
    # alignment 等「属性开关」。
    def layout(self):
        if not self.tasks:
            self.stack.controls = []
            self.stack.width, self.stack.height = 10, 10
            self.stack_holder.width, self.stack_holder.height = 10, 10
            self._restore = None
            self.hrow.visible = False
            self.hrow.expand = False          # 空态：不撑满，让引导卡拿到 Column 的居中空间
            self.placeholder.visible = True
            self.vcol.scroll = None
            self.vcol.alignment = ft.MainAxisAlignment.CENTER
            self.vcol.horizontal_alignment = ft.CrossAxisAlignment.CENTER
            self.showing_placeholder = True
            return self
        self.showing_placeholder = False
        # 同步常驻元素的态（绝不重建实例，DESIGN 规则4）
        for t in self.tasks:
            apply_card_state(self._cards[t["key"]], t["key"] in self.selected,
                             t["course"] in self.excluded)
        courses = sorted(self._courses(),
                         key=lambda c: (c in self.excluded,))
        self._restore = None
        # B3d-2（动效断链根因）：Flet 的树 diff 用 SequenceMatcher 按对象 id 匹配 children
        # （flet/core/control.py build_update_commands）。换模式时若 stack.children 的相对
        # 顺序变了（哪怕都是同一批常驻实例），会产生 replace/insert 块 → 下发 remove+add →
        # Flutter 端节点重建，450ms 位移动画根本不触发（用户实拍「动效全部消失」）。
        # 红线：卡片在 stack 里【永远按 tasks 原序】排列，绝不按布局列重排；列头/恢复条
        # 只追加在尾部。模式切换 = 尾部成员增删 + 全体纯坐标变化 → diff 全 equal 块 →
        # 只发 "set" → animate=Animation(450) 生效。
        card_kids = [self._cards[t["key"]] for t in self.tasks]
        if self.mode == "course":
            head_kids = []
            for i, c in enumerate(courses):
                x = i * (T.COLW + T.GAP)
                h = self._heads[c]
                ex_c = c in self.excluded
                h._flag.visible = ex_c
                # B3e-1：隐藏态眼睛切 VISIBILITY_OFF_OUTLINED（常驻实例只改属性）
                h._eye_icon.name = ft.Icons.VISIBILITY_OFF_OUTLINED if ex_c \
                    else ft.Icons.VISIBILITY_OUTLINED
                h._lx, h._ly = x, 2
                h.left, h.top = x, 2          # B4h-1：left/top 单一定位机制（禁 offset 混用）
                h.height = 32
                head_kids.append(h)                # 列头排在卡后：列序变化只搅动列头（无需动画）
                y = HDR_H
                for t in [w for w in self.tasks if w["course"] == c]:
                    el = self._cards[t["key"]]
                    el._lx, el._ly = x, y
                    el.left, el.top = x, y
                    el.height = card_height(t, T.COLW)
                    y += el.height + CARD_GAP
            kids = card_kids + head_kids
        else:
            active = sorted([t for t in self.tasks if t["course"] not in self.excluded],
                            key=lambda t: t.get("ts", 9e15))
            sunk = [t for t in self.tasks if t["course"] in self.excluded]
            per = max(1, (len(active) + 1) // 2)
            buckets = [active[:per], active[per:] + sunk]
            for bi, arr in enumerate(buckets):
                x = bi * (T.COLW + T.GAP)
                y = 2
                first_sunk = True
                for t in arr:
                    if t["course"] in self.excluded and first_sunk and bi == 1:
                        self._restore = self._restore_bar   # 常驻实例，只切在/离场与坐标
                        self._restore._lx, self._restore._ly = x, y
                        self._restore.left, self._restore.top = x, y
                        y += SUNK_GAP
                        first_sunk = False
                    el = self._cards[t["key"]]
                    el._lx, el._ly = x, y
                    el.left, el.top = x, y
                    el.height = card_height(t, T.COLW)
                    y += el.height + CARD_GAP
            kids = card_kids + ([self._restore] if self._restore else [])
        self.stack.controls = kids
        # 内容边界
        mx = my = 10
        for k in kids:
            w = getattr(k, "width", 0) or 0
            h = getattr(k, "height", 0) or card_height_of(k)
            mx = max(mx, getattr(k, "_lx", 0) + w)
            my = max(my, getattr(k, "_ly", 0) + h + 10)
        if self.mode == "course":
            my = max(my, self._viewport_h - 14)   # B4i-1：撑到视口高（扣 scroller 上下
            # padding 10 + 4px 安全垫），横向滚动条即贴工作区最底、无纵向溢出
        self.stack.width, self.stack.height = mx, my
        self.stack_holder.width, self.stack_holder.height = mx, my
        # 滚动结构（B3e-5 重做）：外层【单一纵向 scrollable】= vcol，两种模式都开——
        # 旧版科目列 vcol.scroll=None，滚轮命中不可滚的 Stack 无处冒泡 =「滚动条在、滚不动」
        # 的主因。hrow 只在科目列承担横向（不同轴，Flutter 滚轮信号按 hit-test 路径分发给
        # 各轴 Scrollable，互不吞并）。expand 在可滚（无界主轴）下必须关，否则 Expanded
        # 对无限高度报错。stack_holder/scroller 永不可滚（红线，冒烟断言）。
        if self.mode == "course":
            # B4i-1 重制：科目列纵向由 Stack 内部布局承担（列已全高排布），外层不滚；
            # hrow expand=True 撑满视口 → Stack 高度 max(内容,视口) → 横向滚动条
            # 贴工作区最底。滚轮：hit-test 路径上唯一可滚容器=hrow(横轴)=滚轮横滚。
            self.vcol.scroll = None
            self.vcol.horizontal_alignment = ft.CrossAxisAlignment.START
            self.hrow.scroll = ft.ScrollMode.AUTO
            self.hrow.expand = True
        else:
            self.vcol.scroll = ft.ScrollMode.AUTO
            self.vcol.horizontal_alignment = ft.CrossAxisAlignment.CENTER
            self.hrow.scroll = None
            self.hrow.expand = False
        self.hrow.visible = True
        self.placeholder.visible = False
        self.vcol.alignment = ft.MainAxisAlignment.START
        return self


def card_height_of(k):
    return getattr(k, "height", 0) or 90
