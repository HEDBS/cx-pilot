# TASK-A1 作业审核机制 — 扫描后自动"验卷"，不可作答的置灰隔离

项目：D:\Hermes\cx-pilot（发布线）。用户截图：stat2 临期列表混入「续签合同(5)」这类**非作业任务**，
点它没意义还占版面。需求原话："加一个审核的机制，把扫到的作业读一下看是否是可做的作业再显示，
还能把题目显示出来"。

## 语义定案
每条任务分三类，UI 明确区分：
- **可作答**：领卷成功且题数>0 → 正常显示 + 题数徽章 + **首题预览**（前 2 题题干各截 24 字）
- **不可作答-非作业**：stat2 `event_type` 不是 work（签到/合同/阅读类）→ 灰卡、pill 写「非作业」，
  **复选框禁用**，永远不进解题队列
- **不可作答-读不到**：是 work 但领卷失败（403/门槛/考试锁定）或 0 题 → 灰卡 pill「读不到题/无题」，同样禁选

## 实现要点
1. `core/stat2.py` Task 已有 event_type 字段——norm_stat2() 透传到 task dict（key `etype`）。
2. 新增 `core/audit.py`：`audit_task(client, task) -> dict`（分类+题数+预览）。
   - 缓存 `data/audit_cache.json`：key=`%s:%s` % (courseid, workId)，值含结果+时间戳，
     **TTL 4 小时**（stat2 官方缓存也是 4h 刷新）；缓存命中零请求。
   - 领卷走 questions.fetch_work_questions（直达链已实测稳），异常捕获分类，不抛出。
   - 预览题面若 image_flag：只标「[图]」，**不在审核阶段调视觉 API**（省配额；答题时才读）。
3. 审核触发：刷新 merge 完成后，后台线程**串行**逐条 audit（间隔 1.5s+抖动），每条出结果
   broadcast 更新对应卡片（safe_update 链路）；顶部 prog_text 显示「审核中 n/N」。
   重入/再次刷新时取消旧线程（gen 计数同 scan 模式）。
4. UI（ui/plan_view.py 卡片 + app_v2）：
   - task dict 新键：`audited`(bool)/`solvable`(bool)/`qreal`(int)/`preview`(str)
   - 未审核：pill 后缀「待审」小字；不可作答卡：整卡 opacity≈0.45、复选框 disabled、
     点击卡体不 toggle（只保留眼睛隐藏钮）
   - 时间流/科目列两模式都遵守；解题队列入口 `state["selected"]` 过滤掉 solvable=False
5. `tools/e2e_check.py` 加第 9 环 audit（真环境 1 条：分类正确+缓存命中二访零请求）。
6. smoke_ui 新断言：audit 分类三态渲染、禁选、队列过滤、缓存读写。

## 红线
- submitter 协议与人工确认闸门零改动；不动 core/client 登录。
- 审核**只 GET**（领卷链），绝不 POST 保存/提交。
- %APPDATA%\cx-pilot 现在就有真实 credentials/cookies/api_keys——联调用它，别动别家数据。
- 解释器：C:\Users\lenovo\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe（flet 0.28.3）；
  冒烟+selftest 全绿后 `flet pack app_v2.py -n cx-pilot -D -i assets_app.ico --distpath dist -y`
  重打包并启动 exe。摘要写 docs/claude-a1-log.md，打印 DONE-A1。
