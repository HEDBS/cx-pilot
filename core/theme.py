# -*- coding: utf-8 -*-
"""cx-pilot 设计令牌（DESIGN.md 的代码化）。颜色/字阶/间距一律从这里取，禁止散落硬编码。
色值格式统一 #RRGGBBAA（Flet 0.28 客户端按此解析；曾有 9 位 hex 非法值导致整页 diff 渲染失败）。
权威来源：design/mock-3modes.html / design/mock-flow.html 的 :root 与组件样式。"""
import flet as ft

ACCENT = "#0A84FF"
ACCENT_SOFT = "#0A84FF1F"      # rgba(10,132,255,.12) 样张 --blue-soft
INK = "#1C1C1E"
INK2 = "#6E6E73"
INK3 = "#AEAEB2"
BG = "#F2F2F7"
CARD = "#FFFFFF"
WHITE = "#FFFFFF"
SEP = "#3C3C431F"              # rgba(60,60,67,.12) 发丝线
RED = "#FF3B30"
ORANGE = "#FF9F0A"
GREEN = "#34C759"
PURPLE = "#5E5CE6"
MAUVE = "#AF52DE"
GRAY = "#8E8E93"
DARK = "#1C1C1E"

# 语义前景色（样张 pill.warn/pill.ok/log.ok/log.warn 用色）
WARN_FG = "#C77700"
OK_FG = "#1D8F3E"
GRAY_SOFT = "#7878801F"        # rgba(120,120,128,.12) 灰胶囊底/已隐藏标记/分段容器底
PROG_TRACK = "#78788029"       # rgba(120,120,128,.16) 进度条底槽
SIDEBAR = "#EEF0F4B8"          # rgba(238,240,244,.72) 侧栏毛玻璃
BOTTOMBAR = "#F6F6F8D9"        # 底栏毛玻璃白
WARN_STRIP_BG = "#FF9F0A1A"    # rgba(255,159,10,.10) 审批屏警示条
WARN_STRIP_FG = "#8A5A00"

CURSOR_COLOR = ACCENT

# 课程色环（同科恒同色：按 courseid 哈希取模）
COURSE_COLORS = [RED, ORANGE, "#FFCC00", GREEN, "#00C7BE", ACCENT, PURPLE, MAUVE, GRAY]

RADIUS = 14                    # 卡片圆角（样张 --radius）
RADIUS_BTN = 9                 # 按钮/分段控件圆角（样张 .btn/.seg）
COLW = 330
GAP = 16

# 样张双层卡片阴影 0 1 2 rgba(0,0,0,.04), 0 6 20 rgba(0,0,0,.06)
SHADOW_CARD = [ft.BoxShadow(blur_radius=2, spread_radius=0, offset=ft.Offset(0, 1),
                            color="#0000000A"),
               ft.BoxShadow(blur_radius=20, spread_radius=0, offset=ft.Offset(0, 6),
                            color="#0000000F")]
# 分段控件选中段阴影 0 1 4 rgba(0,0,0,.12)
SHADOW_SEG = ft.BoxShadow(blur_radius=4, offset=ft.Offset(0, 1), color="#0000001F")


def course_color(courseid: str) -> str:
    try:
        n = int(courseid)
    except (TypeError, ValueError):
        n = abs(hash(str(courseid)))
    return COURSE_COLORS[n % len(COURSE_COLORS)]


def pill_style(pcls: str):
    """(bg, fg) 紧急度胶囊配色。urgent/warn/ok/grey/run —— 透明度按样张逐 token。"""
    return {
        "urgent": ("#FF3B3021", RED),       # rgba(255,59,48,.13)
        "warn": ("#FF9F0A26", WARN_FG),     # rgba(255,159,10,.15)
        "ok": ("#34C75924", OK_FG),         # rgba(52,199,89,.14)
        "grey": (GRAY_SOFT, INK2),          # rgba(120,120,128,.12)
        "run": (ACCENT_SOFT, ACCENT),       # rgba(10,132,255,.12)
    }.get(pcls, (GRAY_SOFT, INK2))


def urgency_pill(due_ts=None, remain_str="") -> str:
    import time as _t
    if due_ts:
        left_h = (due_ts / 1000 - _t.time()) / 3600
        if left_h < 0:
            return "grey"
        if left_h <= 24:
            return "urgent"
        if left_h <= 96:
            return "warn"
        return "ok"
    return "grey"
