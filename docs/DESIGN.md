# cx-pilot DESIGN.md — 设计令牌与交互规则（2026-09-27 用户定稿）

模式：Operate（任务型工具 UI）。视觉世界：macOS/Cupertino 原生质感，
Flutter 实现（Flet 28 个 Cupertino 组件 + Blur/SegmentedButton/Dismissible）。

## 色彩
| 令牌 | 值 | 用途 |
|---|---|---|
| accent | #0A84FF | 主动作/选中态/进度（全局唯一强调色） |
| ink / ink2 / ink3 | #1C1C1E / #6E6E73 / #AEAEB2 | 三级文字阶 |
| bg / card | #F2F2F7 / #FFF | 面/卡 |
| sep | rgba(60,60,67,.12)（分隔线 0.5px） | 发丝线 |
| 语义 | #FF3B30 紧急 / #FF9F0A 临近 / #34C759 宽裕·成功 / #5E5CE6 辅助 | 时间胶囊与状态 |
| 课程色点 | 按课程哈希取 [红橙黄绿青蓝紫] 环 | 列头标识，同科恒同色 |

## 字阶（PingFang SC / 系统默认）
- 屏标题 20/700 · 列头 13.5/700 · 卡标题 14.5/600（行高1.45，**完整显示不截断**）
- 副题 12.5/ink2 · 徽标 11~12

## 卡片与布局
- 圆角 14 · 阴影 `0 1 2 rgba(0,0,0,.04), 0 6 20 rgba(0,0,0,.06)` · 卡高随内容撑开
- 列宽固定 330px（两模式同款卡宽），列距 16
- **科目列**：横向滚动唯一方向（overflow-x:auto，纵向隐藏）
- **时间流**：竖向滚动唯一方向（overflow-x:hidden）；两列固定宽**居中**，禁右侧大面积空白
- 底栏：毛玻璃(blur16, 92%白)常驻——已选数/用时/提交模式/主按钮

## 交互规则（用户逐条确认）
1. 黑名单科目：点列头 👁 → 变灰(opacity .35)+沉底（列模式=最右列；时间流=右列底部区），**仍可勾选**
2. 恢复：点已隐藏列头/沉底区「全部恢复」条；恢复条与卡片间必须留 44px 空档（重叠事故已修）
3. 模式切换：卡片位移动画 `cubic-bezier(.3,.8,.3,1) 450ms`，列标题淡出；**滚动双向强制归零**
4. 常驻元素原则：动画靠同批元素改坐标实现，禁止切模式重建 DOM（会杀动画——踩过）
5. 防误触：👁 悬停才显示；提交永远走审批屏勾选+确认弹窗

## 文案规范
用词书面（「作业计划/解题队列/提交审批/运行日志」），错误提示句式：`<对象> <动作>失败：<原因(截120字)>，可<补救>`。

## 深色模式（B3 后期）：bg #1C1C1E / card #2C2C2E / ink 反转，accent 不变。

## B3 修订（2026-09-27，按 design/mock-*.html 逐 token 补齐，实现见 core/theme.py）
- 色值格式统一 **#RRGGBBAA**（Flet 客户端 parseColor 对 9 位 hex 直接抛异常 → 整页 diff 报废，
  即「刷新后卡片空白」事故根因；theme.SHADOW_CARD 取代旧单阴影写法）。
- 双层卡影 `0 1 2 #0000000A, 0 6 20 #0000000F`；卡 padding 14/16，列头高 32、HDR 距 44。
- 胶囊透明度对样张：urgent .13 / warn .15 / ok .14 / grey、run .12；pill 前景 #C77700/#1D8F3E 入令牌。
- 分段控件弃 Material SegmentedButton（紫色选中），自制：容器 #7878801F radius9 pad2，
  选中段白底 radius7 + 阴影 `0 1 4 #0000001F`，文字 12.5（选中 W600/ink）。
- 时间流两列**居中**（Column 水平居中，样张 offX 算法等效）；卡右侧 meta 改横排「N 题 + 胶囊」。
- 侧栏 #EEF0F4B8、底栏 #F6F6F8D9、徽标数字胶囊（红 17×H radius9，10.5/700 白字，完成转绿）。
- 👁 悬停才显示（Container.on_hover 改 opacity）；进度条 130×6 底槽 #78788029。
- 日志 Consolas 等宽 + 语义色（err #FF3B30 / warn #C77700 / ok #1D8F3E / ts ink3）。
- 已知无法对齐项：letter-spacing（Flet Text 不暴露），徽标最小宽用 padding 近似 min-width。

## B3d 修订（2026-09-28，桌面 exe 实拍反馈，规则 5 的 👁 条目被本修订覆盖）
- **👁 常显**：列头右侧固定小号 👁（INK3，悬停仅变 ACCENT 色），点击=隐藏；桌面窗口 hover
  不可靠，「悬停才显示」仅适用 web，废止。已隐藏列的「已隐藏·点恢复」胶囊保留。
- **点击即切**：分段/黑名单等一切事件 handler 内必须同步 layout + 广播 safe_update——
  Flet 0.28 事件后**不会**自动 page.update（core/page.py on_event_async 只调 handler）。
- **位移动画的前提是 diff 稳定**：stack.children 卡片恒按数据原序排列、列头/恢复条只追加
  尾部；Flet 按对象 id SequenceMatcher 匹配 children，顺序一变即 remove+add 重建、动画灭。
  卡片 animate=Animation(450ms, EASE_IN_OUT_CUBIC_EMPHASIZED)，置灰走 animate_opacity。
- **忙碌文案写 `button.text`**——FilledButton **没有** value 属性，btn.value 是死属性永不上屏。
- **扫描进度条**：卡片区顶部横向 ProgressBar（宽=内容区，ACCENT/PROG_TRACK，圆角随令牌），
  随 progress n/total 推进，完成 0.5s 后隐藏；按钮实时「扫描中 n/36…」。

## M4 视觉重制（2026-09-29 · Tauri/webview 壳）

> 适用范围：`shell/src/styles.css`（webview 壳）。上文 B3/B3d 记录的 Flet 面（`core/theme.py`）
> 保持原样；两套并存，**结构/布局/尺寸零改动**，只改颜色、形状与动效表现。风格：高级简约。
> 实现与逐条实测见 `docs/TASK-M4-visual.md` §四。

### 双主题令牌表
`:root` = 亮（既有定稿，零视觉回归）；`html[data-theme="dark"]` = 暗（对齐 §深色模式草案，
灰阶按预览 v3 微调）。由 `theme.js` 置 `documentElement.dataset.theme`，
`index.html` 内联 pre-paint 引导脚本先行落定（防首帧闪色）。模式 `system`（默认）/`light`/`dark`，
持久化 `localStorage["cx.theme"]`，顶栏 `#theme-btn` 三态循环。

| 令牌 | 亮 | 暗 |
|---|---|---|
| `--ink` / `--ink2` / `--ink3` | `#1C1C1E` / `#6E6E73` / `#AEAEB2` | `#ECEEF2` / `#A3A9B8` / `#6E7484` |
| `--bg` / `--card` | `#F2F2F7` / `#FFF` | `#1C1C1E` / `#2C2C2E` |
| `--accent` / `--accent-soft` | `#0A84FF` / `rgba(10,132,255,.12)` | 同左 / `rgba(10,132,255,.20)` |
| `--sep` | `rgba(60,60,67,.12)` | `rgba(255,255,255,.12)` |
| `--chip` / `--chip-hi` / `--track` | `rgba(120,120,128,.12/.10/.16)` | `.28/.22/.32` |
| `--mask` | `rgba(0,0,0,.32)` | `rgba(0,0,0,.55)` |
| `--orange` / `--green` / `--violet` | `#FF9F0A` / `#34C759` / `#5E5CE6` | `#FFB340` / `#4CD263` / `#8B88F0` |
| `--warn-fg` / `--ok-fg` | `#C77700` / `#1D8F3E` | `#F0A848` / `#57D47C` |
| `--shadow` (soft → lift) | 黑系 `rgba(0,0,0,.04~.16)` | 深黑系 `rgba(0,0,0,.35~.60)` |
| `--radius` / `--radius-card` | `14px` / **`16px`**（M4：卡片 14→16） | 同左 |

**纪律**：令牌块之外**不得**出现任何 `background`/`color` 字面量（闸=`tools/theme_probe.mjs`
TH2 静态断言）；暗色下不得残留亮色硬编码。`color-scheme` 随主题切换（原生控件/滚动条跟随）。

### 液态玻璃配方（仅 chrome）
**Apple Liquid Glass 的 Web 诚实近似**（taste-skill 附录 C），**非 Apple 官方材质**。
取舍（预览 v3 已验收）：玻璃**只上**标题栏 / 侧栏 / 底栏 / 弹窗 / 按钮；
**内容卡片不上玻璃**——卡内是长文本，模糊毁可读性，改用近不透明 `--card` + 细描边 + 软阴影。

```
.lg {
  border: 1px solid var(--glass-border);
  background: linear-gradient(135deg, var(--glass-fill-a), var(--glass-fill-b)), var(--glass-base);
  backdrop-filter: blur(22px) saturate(180%) contrast(1.04);
  box-shadow: inset 0 1px 0 var(--glass-inset), var(--glass-shadow);
}
.lg::before {  /* 径向边缘折射高光：circle at 20% 0% */
  background: radial-gradient(circle at 20% 0%, var(--glass-hi), transparent 36%);
}
```

亮/暗**两套完整定义**（`--glass-*` 令牌各有亮暗值）。两条强制回退：
`@media (prefers-reduced-transparency: reduce)` → 转实色 `--card` 且关 `backdrop-filter`；
`@supports not ((backdrop-filter: …) or (-webkit-backdrop-filter: …))` → 转实色。
弹窗遮罩另起一层 `blur(2px)`，与玻璃面分层。

### 流光规格（Aurora Background · 已按性能红线降级）
来源：Aceternity UI / React Bits「Aurora Background」。三层构成：
① `repeating-linear-gradient(100deg, …)` 多层彩色条纹（白条 + `--a1/--a2/--a3` 三色相间）
＋`background-size: 300%,200%`；② `::after` 同款渐变 + `mix-blend-mode: difference` 叠纵深；
③ `mask-image: radial-gradient(ellipse at 100% 0%, …)` 收束辉光到右上 + `filter: blur(11px)`。
三色令牌：亮 `#2F6BFF/#8AA0FF/#35C7C0`、暗 `#2E5BFF/#7C6BFF/#17A79B`；
浓度 `--aurora-opacity`（亮 `.55` / 暗 `.62`），内容区另叠 `--scrim` 遮罩保证可读性。

**降级记录（重要）**：原规格为 `background-position 50%→350% / 60s linear infinite` 的**匀速位移**。
实测该位移属**重绘**而非合成，在 `--disable-gpu`（验收闸同条件）下 rAF 中位由 16.6ms 恶化到
22.4~31.9ms，击穿 `anim_probe` A1「帧率中位≤20ms」闸（M4 前为 16ms）。故按 §T2 红线改为
**静态多层渐变 + 极慢 opacity 呼吸**：`#aurora` 挂 `aurora-breathe-l|-d`
（26s `ease-in-out alternate`，端点写**数值字面量**——写 `calc(var(…))` 会让动画掉出合成器，
实测在 16.8↔26.8ms 摇摆）。渐变本体、blur、difference、径向遮罩、三色令牌全部保留。
挂点必须在**外层 `#aurora`**（该层无 filter/mask/blend）：挂内层 `.layer` 实测 36.3~59.2ms。
GPU 下各分支均 6.1ms（流光几乎免费）；若需完全确定性的帧预算，可删 `animation-name` 一行
退回纯静态（实测恒 16.6ms）。`document.hidden` → `html.aurora-paused` 暂停；
`prefers-reduced-motion: reduce` → 停呼吸、保留渐变本体。

### 动效（与 M2 一致，未改判据）
统一曲线 `--ease = cubic-bezier(0.65,0,0.35,1)`（Apple emphasized）＝ `flip.js` 的 `EASE`。
新增屏级切换：`#main` 内 `.scr` 进出 `translateY(±16px)+opacity` 450ms（WAAPI，可打断接续），
只动屏级容器、不进卡片层；`reduce` → 纯淡入淡出 250ms 无位移。A1–A9 既有动效全部保留。
