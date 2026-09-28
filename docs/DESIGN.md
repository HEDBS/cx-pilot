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
