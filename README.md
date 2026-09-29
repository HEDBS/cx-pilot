# CX Pilot · 学习通作业助手

Windows 桌面小工具：把散落在几十门课程里的学习通作业聚合成一张计划表，
用本地 OCR + 视觉大模型读懂题面，AI 生成答案，**你逐项确认后**才替你点提交。

> 仅供个人学习管理与自动化技术研究。默认带人工确认闸门，请保留它。

**最新版 v0.4.0** — [下载免安装绿色包](../../releases/latest)（解压即用，**不需要装 Python**）
· Windows 10/11 · 数据全在本机 `%APPDATA%\cx-pilot\`

## 它能做什么

- 📋 **作业聚合** — 走学习通官方"学习规划"接口 + 逐课程扫描双通道，临期任务、
  截止时间、科目一目了然；支持"科目列 / 时间流"两种视图和科目黑名单
- 🔎 **自动审核（v0.3 新）** — 扫到的每条任务自动"读卷验真"（只读不写）：可作答的显示
  真实题数与前两题预览；签到/合同类非作业任务、读不到题的被置灰禁用，永不混进解题队列
- 🔍 **进度透明** — 全课程扫描带实时进度条（n/36），掉线自动重登
- 🖼️ **三层识图** — 题目是图片？本地 OCR（免费离线，源码模式）先读；公式类自动转
  视觉 API 精读；都不行就在审批界面直接看原图手填——不猜题
- 🤖 **可插拔后端** — 解题/视觉模型全部是 OpenAI 兼容接口：硅基流动、
  OpenRouter 免费池、Moonshot、智谱、Groq、自建端点……设置里下拉选供应商、
  粘贴 key 即用，互不影响；侧栏实时显示当前**后端链**与优先级（拖动即调序）
- 👁️ **风控验证码在软件内过（v0.4 新）** — 撞上超星的图片验证码时，软件弹窗把验证码图
  取回来给你看，输 4 位即可继续刚才的任务，不用切手机 App。**不自动识别验证码**
  （那属于绕过人机验证）；验证通过与否以回验为准，不信响应文案
- 📈 **阶段进度清晰（v0.4 新）** — 顶部横条区分「正在领卷…」与「正在解题 3/10…」；
  解题队列里**正在解答的那道题左侧转圈**，结果落定圈自动挪到下一道
- ✍️ **答案像学生写的（v0.4 新）** — 简答/填空按「简洁、平实、去 AI 味」约束生成：
  不出现「首先/其次/综上所述」，不带 markdown 加粗与分点罗列，能手写成纯文本
- ✅ **人工确认闸门** — 所有答案先进审批屏：逐题预览、修改、跳过，点"提交"前
  必须二次确认。**软件从不静默交卷**
- 🧊 **格式朴素** — 答案按人工手写风格提交（纯文本、矩阵逐行、分数写 `9/2`），
  适配学习通 AI 评分习惯

## 下载与安装（用户）

1. 到 [Releases](../../releases) 下载 `cx-pilot-vX.Y.Z.zip`
   > 首次启动：应用会弹出「登录学习通」对话框，输入手机号+密码即可（凭据仅存本机）
2. **解压到任意目录**（不要只把 exe 拖出来——`sidecar/` 目录是内置的 Python 运行时，
   免安装、**不需要你自己装 Python**）
   > 注：exe 发行版未捆绑本地 OCR（体积原因），图片题自动走视觉 API → 人工两级；
   > 源码模式 `pip install rapidocr-onnxruntime` 即获完整三层链路
3. 双击 `cx-pilot.exe`。需要 Windows 10/11 自带的 WebView2（绝大多数机器已预装）
4. 数据/配置存于 `%APPDATA%\cx-pilot\`，删除即彻底清除；卸载 = 删文件夹

## 首次配置教程

### 1. 学习通登录
应用内输入手机号+密码登录一次即可，会话自动保持与续期（凭据仅存本机）。

### 2. 解题后端（文本模型，答普通题）
设置 →「解题后端」→ 选一个供应商注册领免费 key → 粘贴保存 → 点「测试全部连通」：

| 供应商 | 注册地址 | 特点 |
|---|---|---|
| OpenRouter | openrouter.ai | 一个 key 用几百个模型，`:free` 免费池有日限额 |
| Groq | console.groq.com | 速度最快，国内可能需要代理（设置里可填） |
| 智谱 / Moonshot / SiliconFlow | 各家控制台 | 国内直连，注册送额度 |

### 3. 识图后端（视觉模型，读图片题——推荐配）
设置 →「识图后端」→「＋ 新增后端」→ **供应商下拉直接选**（base_url 和模型清单自动填好）→
粘贴 key → 保存。不配也能用：图片题会落在审批屏让你看原图手填。

- 追求速度/质量：SiliconFlow 的 `Qwen2.5-VL-7B`（约 1 秒/张）
- 零成本：OpenRouter `…:free` 视觉模型（慢、限流，能跑）
- 永久免费档：智谱 `glm-4v-flash`（新账号可能触发风控，过实名后用）

### 4. 用一遍
作业计划 →「刷新」→ 全课程扫描 → 勾选作业 →「开始解题」→ 审批屏逐题过目 →
「确认提交」。低置信度题目默认留空等你填。

> 扫描时如果撞上超星风控（提示里有 `【9010】`），软件会自动重登重试；仍不行会弹一个
> 验证码窗口，你看着图输 4 位就能继续，不用切手机 App。

## 常见问题

**Q：扫描提示「有 N 门课被风控跳过」怎么办？**
风控跟**请求频率**走，不是账号被封。等 1-2 分钟再点一次「刷新」就能补齐；被跳过的那几门
不是没作业。反复出现就用软件弹出的验证码窗口过一下（见上）。

**Q：哪来的模型 key？有免费的吗？**
解题后端有几个能白嫖的：智谱 `glm-4-flash`（长期免费）、硅基流动（注册送额度，国内直连）、
魔搭（每日额度）、OpenRouter `:free` 池（日限 50 次）。识图同理。都不配也能用，只是图片题
会落到审批屏让你手填。

**Q：答案会不会不经过我就提交？**
不会。**所有答案先进审批屏**，你逐题看过、改过、跳过，最后点「确认提交」并二次确认才真的交。
这条是设计红线，不是可选项——请保留它。

**Q：换 key / 换后端要重装吗？**
不用。设置页改完保存即生效；侧栏「后端链」按顺序尝试，前一个挂了自动落到下一个。
拖动行可以调优先级（拖动顺序 = 调用顺序）。

**Q：日志在哪？出问题怎么反馈？**
界面「运行日志」屏可看全过程；数据目录 `%APPDATA%\cx-pilot\` 下有 cookie、缓存与配置。
提 Issue 时带上：日志屏的报错行 + 卡在哪一步 + 你的后端链顺序。**不要贴 cookie 或 key。**

## 项目结构

```
core/         协议引擎（登录/聚合/领卷/识图/解题/提交）——与界面完全解耦
server/       FastAPI sidecar：把 core 薄封装成 REST + SSE（仅绑 127.0.0.1）
shell/        Tauri 2 桌面壳（Rust）+ shell/src 前端（原生 WebView，无框架）
tools/        回归门与工具：smoke_ui / solve_probe / risk_probe / scan_leak / make_release …
sidecar.spec  PyInstaller 冻结 server（发行包里 sidecar/ 的来源）
app_v2.py     历史 Flet 桌面壳（已由 Tauri 壳取代，保留供参考）
```

## 开发

```bash
# 引擎侧（纯 Python，无 GUI 依赖）
pip install -r requirements-server.txt
python -m server --port 8765        # 起 sidecar（FastAPI，仅绑 127.0.0.1）
python tools/smoke_ui.py            # 36 项 UI/接口冒烟（离线）
python tools/solve_probe.py         # 25 项解题层回归（离线，打桩 chat）
python tools/risk_probe.py          # 10 项会话/风控自愈回归（离线）
python tools/e2e_http.py            # 端到端 HTTP 语义 8 环
python tools/e2e_check.py           # 端到端 8 环（需自己的学习通账号）

# 桌面壳（Tauri 2 + 原生 WebView，前端在 shell/src）
cd shell/src-tauri && cargo run     # dev：自动找 PATH 上的 python 拉起 server
cd shell && npm run tauri dev       # 同上（走 tauri CLI）
```

架构：`core/` 协议引擎（登录/聚合/领卷/识图/解题/提交）与界面完全解耦——
换任何壳不动引擎。桌面壳为 **Tauri 2**（Rust）+ FastAPI sidecar：壳负责窗口/托盘/
单实例与 sidecar 生命周期，界面是原生 WebView 里的 `shell/src`（无框架，零构建）。
sidecar 只绑 `127.0.0.1:<随机端口>`，启动生成随机 token，所有请求带 `X-CX-Token`
——本机其它进程不该能操作用户的学习通。

### 打包发布（维护者）

产物 = **免安装绿色包**：`sidecar/` 里是 PyInstaller 冻结的 Python 运行时，
所以用户机器不需要装 Python。步骤：

```bash
# 1) 冻结 sidecar（产物 dist/cx-sidecar/）
python -m PyInstaller --noconfirm --clean sidecar.spec

# 2) 编 release 壳（产物 target/release/shell.exe）
#    ⚠ 必须带 --remap-path-prefix：Rust 第三方 crate 的 file!() 会把编译时的
#    CARGO_HOME 全路径写进 panic 消息，release exe 里就烘进了打包机路径
#    （形如 <CARGO_HOME>/registry/src/<镜像名>/<crate>/src/*.rs）。编译期改写掉，
#    别等到发布扫描才发现。
export RUSTFLAGS="--remap-path-prefix=$CARGO_HOME=/cargo \
--remap-path-prefix=$CARGO_TARGET_DIR=/target \
--remap-path-prefix=<你的仓库绝对路径>/shell=/src"
cd shell/src-tauri && cargo build --release

# 3) 组装 + 泄漏扫描 + 打 zip（脚本在 tools/make_release.py）
python tools/make_release.py --version 0.4.0 --shell-exe <release 壳路径>

# 4) 打 tag 并发 release（把 zip 与构建产物之外的说明一起发）
git tag -a v0.4.0 -m "v0.4.0" && git push origin v0.4.0
#    有 gh 就直接 gh release create v0.4.0 <zip> --notes-file <notes>；
#    没有就用 REST API（POST /repos/<owner>/<repo>/releases + uploads.github.com 传资产）
```

发布前必过：`python tools/scan_leak.py`（仓库）**与** `python tools/scan_leak.py <组装目录>`
（产物，含 exe/pyc 的 raw 字节层）。注意 zip 是压缩的，扫不出明文——**扫描必须在打包前做**，
`make_release.py` 已按这个顺序执行（扫描不过就不出包）。
公开仓禁入：`docs/TASK-*.md`、`docs/claude-*.md`、`docs/HANDOFF*`（已 .gitignore）。

## 边界与免责

- 依赖学习通**非公开接口**，平台风控调整随时可能让某个环节失效。提 Issue 时带上运行日志
  的报错行 + 卡在哪一步，能大幅加速定位
- **不自动识别图片验证码**（那属于绕过人机验证）：撞上风控时软件把验证码图取回来给你看，
  由你本人输入；不做考试，仅限平时作业场景
- 本仓库不含任何账号、密钥、cookie；所有凭据只写进你本机 `%APPDATA%`，删目录即彻底清除
- 答案由 AI 生成，不保证正确，**确认闸门的存在就是让你看一遍**——请保留它，别关
- 仅供个人学习管理与自动化技术研究；请遵守你所在学校的规定
- MIT License

## 更新日志
- **v0.4.0** — 风控验证码可在软件内过（弹窗取图给你看，输 4 位继续，不自动识别）；
  顶部横条区分「正在领卷 / 正在解题 n/N」+ 正在解答的题左侧转圈；侧栏显示后端链（拖动调序）；
  修复多选题被当简答题答（提交按选项字母发，原来发的是整句话）、点「重试」时不出加载圈
  且卡片留着上一轮旧答案、领卷撞风控 2 秒即失败（自愈级联没跑到的路径补齐）、
  配了 key 侧栏却不显示（key 存了但没勾启用）；简答/填空答案去 AI 味（同题 524 字 → 70 字）
- **v0.3.1** — 随堂练习不再入口误杀（交审核验真）；不可作答任务两视图自动沉底；
  活动类任务缺班级信息归"读不到"不再打无效请求；GUI 首登流程实测修订
- **v0.3.0** — 新增作业审核三态（可作答/非作业/读不到，自动验卷+题数+预览）；
  新增首次启动 GUI 登录对话框；识图后端供应商下拉预设；API Key 配置框加宽防误改
- **v0.2.0** — 首个公开版

## Roadmap
- [x] Tauri 2 壳重制（v0.4.0 完成：动效/体积/观感，core 引擎原样保留）
- [ ] 批阅结果回读与格式规范收敛
- [ ] 定时批处理 + 推送通知
- [ ] 安装器（NSIS / Inno Setup），免解压
- [ ] macOS / Linux 构建（引擎已跨平台，壳顺路）
