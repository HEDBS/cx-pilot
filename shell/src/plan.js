// plan.js —— 屏1 作业计划（M1 任务书 §2.1 + M2 动效任务书 §3.1）
// 科目列⇄时间流两模式 + 列头色点 + 隐藏/恢复 + 扫描进度条。
// 结构承 mock-3modes：常驻元素池 + JS 绝对定位改坐标——DESIGN 交互规则4「禁止切模式
// 重建 DOM」是位移动效的前提。M2 接线：所有会移动画布的触发点走 flip.js 的
// animateLayout(canvas, applyLayout)（A1 切换/A2 隐藏/A3 恢复/A4 沉底/A9 落定），
// 只动 transform/opacity；resize 等重排不补间（layoutInstant）。
import { net } from "./api.js";
import { store, courseColor, errText, withBusy, refreshTasks } from "./store.js";
import { EASE, animateLayout, reduced } from "./flip.js";
import { confirmDlg } from "./modal.js";
import { go } from "./nav.js";

const COLW = 330, GAP = 16, HDR = 44, CARD_GAP = 10;
// 取证钩子开关：仅 headless 探针带 ?cxprobe=1 时挂 window.__m2（生产壳 URL 无此参数=零暴露）
const PROBE = new URLSearchParams(location.search).get("cxprobe") === "1";

let stage, canvas, scanbar, scanfill, scantext, bottom, seg, btnScan;
let segThumb, segSpans = [];
let mode = "course";
const cards = {}, heads = {};   // 常驻元素池：key -> el
let scanStart = 0, scanTimer = null;

export function initPlan(root) {
  root.innerHTML = `
    <div class="toolbar">
      <div class="h1">作业计划</div>
      <div class="seg" id="plan-seg">
        <span data-v="course" class="on">科目列</span>
        <span data-v="time">时间流</span>
      </div>
      <div class="spacer"></div>
      <button class="btn grey" id="btn-audit">验卷</button>
      <button class="btn blue" id="btn-scan">⟳ 扫描</button>
    </div>
    <div class="scanbar" id="scanbar" hidden>
      <div class="prog"><i id="scanfill"></i></div>
      <span class="scan-text" id="scantext">扫描 0/0</span>
    </div>
    <div class="stage plan-stage" id="plan-stage"><div class="canvas" id="plan-canvas"></div></div>
    <div class="bottombar lg" id="plan-bottom">
      <span>已选 <b id="sel-n">0</b> 份 · <b id="sel-q">0</b> 题</span>
      <span id="plan-elapsed"></span>
      <span class="spacer"></span>
      <span>提交方式：<b>人工确认</b></span>
      <button class="btn dark" id="btn-solve">开始解题 ▸</button>
    </div>`;
  stage = root.querySelector("#plan-stage");
  canvas = root.querySelector("#plan-canvas");
  scanbar = root.querySelector("#scanbar");
  scanfill = root.querySelector("#scanfill");
  scantext = root.querySelector("#scantext");
  seg = root.querySelector("#plan-seg");
  bottom = root.querySelector("#plan-bottom");
  btnScan = root.querySelector("#btn-scan");

  // A5：分段控件白底胶囊——独立常驻元素，选中切换=translateX 滑移 200ms emphasized。
  // 必须用 div 而非 span：`.seg span`（文字段规则）会把胶囊拽回 position:relative 流内。
  segThumb = document.createElement("div"); segThumb.className = "seg-thumb";
  seg.appendChild(segThumb);
  segSpans = [...seg.querySelectorAll("span[data-v]")];
  placeThumb(false);

  seg.onclick = (e) => {
    const b = e.target.closest("span[data-v]"); if (!b) return;
    segSpans.forEach((x) => x.classList.toggle("on", x === b));
    mode = b.dataset.v;
    placeThumb(true);
    layout();
    stage.scrollLeft = 0; stage.scrollTop = 0;   // 交互规则3：滚动双向强制归零
  };
  root.querySelector("#btn-scan").onclick = runScan;
  root.querySelector("#btn-audit").onclick = () => runAudit({ all: true });
  root.querySelector("#btn-solve").onclick = () => startSolve(true);

  if (PROBE) {
    // M2 任务书取证钩子：探针复用真实代码路径（applyAuditItem=审核落定同一处理函数），
    // 不新增生产分支；引擎侧真实审核语义由 M0 环9 背书。
    window.__m2 = {
      store, applyAuditItem, layout, layoutRaw: applyLayout,
      auditKeys: (keys) => runAudit({ keys }),
      showConfirm: () => confirmDlg("动效取证 A8",
        ["M2 任务书 §2 A8：弹窗 scale 0.95→1 + opacity 200ms，backdrop 渐暗。"], "确认"),
    };
    // M3b 取证钩子（jobs_probe）：假题领卷建 job 走真实 /solve 流+真实事件处理；
    // 扫描/取消出口供 R2 场景驱动。自造题不触模型（solve:false）、不触提交协议。
    window.__m3b = {
      solveFetchOnly: (jobId) => net.sse("/solve", {
        job_id: jobId,
        questions: [{ qid: "gq1", type: "single", stem: "1+1 等于几？（探针自造题）",
                      options: { A: "1", B: "2", C: "3", D: "4" }, image_flag: false }],
        ref: { course: "PROBE-COURSE", title: "探针假作业", courseId: "probe",
               classId: "probe", cpi: "0", workId: jobId, answerId: "0" },
        solve: false,
      }, applySolveEvent),
      scan: () => runScan(),
      cancelScan, cancelSolve,
    };
    document.body.dataset.m2hook = "1";
  }
}

// A5 胶囊落位：终态写行内 transform（打断时从当前视觉位置起新动画，200ms）。
// reduce 降级：瞬移（无位移），选中态文字色/字重本就瞬时变化。
function placeThumb(animate) {
  if (!segThumb || !segSpans.length) return;
  const on = seg.querySelector("span.on") || segSpans[0];
  const x = on.offsetLeft, w = on.offsetWidth;
  const prev = segThumb._x === undefined ? x : segThumb._x;
  segThumb.style.width = w + "px";
  segThumb.style.height = on.offsetHeight + "px";
  segThumb.style.top = on.offsetTop + "px";
  segThumb.style.transform = "translateX(" + x + "px)";
  if (segThumb._a) { segThumb._a.cancel(); segThumb._a = null; }
  if (animate && !reduced() && Math.abs(x - prev) > 0.5) {
    const cur = segThumb.getBoundingClientRect().left - seg.getBoundingClientRect().left;
    const a = segThumb.animate(
      [{ transform: "translateX(" + cur + "px)" }, { transform: "translateX(" + x + "px)" }],
      { duration: 200, easing: EASE });
    segThumb._a = a;
    a.finished.then(() => { if (segThumb._a === a) segThumb._a = null; }).catch(() => {});
  }
  segThumb._x = x;
}

// 验收/自动化钩子：外部（main.js headless 分支）触发选中任务领卷
export function solveSelected(solveFlag) { return startSolve(!!solveFlag); }

// M3b R2：真取消统一走 server——POST /cancel 置位 mode 事件，worker 侧 emit 检查点
// 抛 CancelledError（BaseException 穿透引擎一切 except Exception，含 refresh_all 对
// progress 回调的吞异常——实测）→ 既有 finally 释放单锁 → 本 open 流收到 cancelled 事件。
// core/ 零改动。探针/删卡共用这两个出口：
export function cancelScan() { return net.api("/cancel", { method: "POST", body: { mode: "scan" } }); }
export function cancelSolve(jobId) {
  return net.api("/cancel", { method: "POST", body: { mode: "solve", job_id: jobId } });
}

// /solve SSE 事件处理（startSolve 与探针共用同一条真实代码路径，M2 __m2 先例）
export function applySolveEvent(ev) {
  if (ev.type === "log") store.addLog(ev.msg);
  else if (ev.type === "error") {
    if (ev.msg === "cancelled") {
      // M3b R2：取消收尾——把仍挂在 running/init 的卡标为已中断（引擎态已中止、锁已放）
      let n = 0;
      for (const j of Object.values(store.jobs)) {
        if (j.state === "running" || j.state === "init") {
          j.state = "interrupted"; j.interruptNote = "已中断（已取消）"; n++;
        }
      }
      if (n) store.emit("jobs");
      store.addLog("解题已取消（单锁已释放）", "warn");
    } else if (/9010|风控/.test(String(ev.msg))) {
      // 风控不是掉线：重登/冷却都试过仍失败时才走到这（server 侧已做两级自愈）。
      // 给用户可操作的两步，而不是甩一句【9010】让他自己猜。
      store.addLog("!! 被超星风控拦截（要求图片验证码）——自动重登重试后仍未通过", "err");
      store.addLog("   ① 等 1-2 分钟再点一次（风控跟请求频率走，冷却后通常自动放行）", "err");
      store.addLog("   ② 若反复出现：用手机学习通 App 正常登录一次，再回来重试", "err");
    } else store.addLog("!! 解题失败：" + String(ev.msg).slice(0, 120), "err");
  }
  else if (ev.type === "item") upsertJobEvent(ev.data);
  else if (ev.type === "progress") { /* 队列屏逐题进度由 item 驱动 */ }
  else if (ev.type === "done") {
    (ev.data.jobs || []).forEach((m) => {
      const j = store.jobs[m.job_id];
      if (j) { j.state = m.state; j.low = m.low || []; }
      store.addLog(`Job ${shortId(m.job_id)} ${m.state} · 进度 ${m.progress}`,
                   m.state === "done" ? "ok" : "warn");
    });
    store.emit("jobs");
  }
}


// ---------- 扫描（§2.5 SSE progress n/total；§3 进度条平滑+文字不抖） ----------
export async function runScan() {
  const ok = await withBusy("scan", async () => {
    scanStart = Date.now();
    scanbar.hidden = false;
    scanfill.style.width = "0%";
    scantext.textContent = "扫描 0/…";
    store.addLog("开始全量扫描（stat2 临期 + works 逐课）");
    scanTimer = setInterval(showElapsed, 1000);
    const consume = async (ev) => {
      if (ev.type === "progress") {
        store.scanTotal = ev.total;
        scanfill.style.width = (100 * ev.n / (ev.total || 1)).toFixed(1) + "%";
        scantext.textContent = `扫描 ${ev.n}/${ev.total}`;
      } else if (ev.type === "tasks") {
        // 逐份入库：每到一批就把新卡片冒出来（新卡入场动效由 flip.js animateLayout 负责），
        // 不用等整轮 36 门扫完（用户实测反馈）。
        await refreshTasks();
        layout();
      } else if (ev.type === "log") store.addLog(ev.msg);
      else if (ev.type === "error") {
        // M3b R2：server 端 /cancel 生效→本 open 流收到 cancelled，随后哨兵收流；
        // 不 abort 本地读取，让 withBusy 正常收束清 busy（徽标/按钮禁用态随之落定）。
        if (ev.msg === "cancelled") {
          scanbar.hidden = true;
          store.addLog("扫描已取消（单锁已释放，可重新扫描）", "warn");
        } else store.addLog("!! 扫描失败：" + String(ev.msg).slice(0, 120), "err");
      }
      else if (ev.type === "done") {
        const d = ev.data;
        store.addLog(`扫描完成：临期 ${d.near} · works ${d.works}（待做 ${d.works_todo}）· 任务表 ${d.tasks} 条`, "ok");
        setTimeout(() => { scanbar.hidden = true; }, 500);   // B3d：完成 0.5s 后隐藏
      }
    };
    await net.sse("/scan", {}, consume);
    await refreshTasks();
  });
  clearInterval(scanTimer); showElapsed();
  if (ok) await runAudit({ all: true });   // 扫描后自动「验卷」——承 app_v2 A1 既有行为，沉底逻辑靠它
  if (PROBE) document.body.dataset.m2scan = "1";   // 探针：扫描+自动验卷全链落定标志
}

function showElapsed() {
  if (!scanStart) return;
  const s = Math.round((Date.now() - scanStart) / 1000);
  bottom.querySelector("#plan-elapsed").textContent =
    s ? `用时 ${Math.floor(s / 60)} 分 ${String(s % 60).padStart(2, "0")} 秒` : "";
  if (store.busy === "scan") btnScan.textContent = `扫描中 ${scantext.textContent.slice(2)}…`;
  else btnScan.textContent = "⟳ 扫描";
}

// ---------- 验卷（/audit SSE 逐条落定；不可作答置灰禁选沉底=§3 继承） ----------
// A4/A9：单条落定的处理体抽出——SSE item 与探针钩子共用同一真实路径（layout→animateLayout，
// 卡片滑行沉底 + pill 原地换字 + 灰化 250ms；禁瞬移=§2 A4）。
function applyAuditItem(d) {
  const t = store.tasks.find((x) => x.key === d.key);
  if (!t) return false;
  t.audited = true; t.solvable = !!d.solvable;
  t.qreal = d.qreal || 0; t.preview = d.preview || "";
  if (t.solvable) { if (t.qreal) t.qn = t.qreal; }
  else {
    t.pill = { "非作业": "非作业", "无题": "无题", "缺班级信息": "读不到题" }[d.reason] || "读不到题";
    t.pcls = "grey";
    if (store.selected.delete(t.key)) { /* 不可作答撤销勾选 */ }
  }
  layout();
  return true;
}

async function runAudit(body) {
  await withBusy("audit", async () => {
    store.addLog(`开始验卷（${body.all ? "全部未审条目" : body.keys.length + " 条"}，只 GET）`);
    await net.sse("/audit", body, async (ev) => {
      if (ev.type === "log") store.addLog(ev.msg);
      else if (ev.type === "error") {
        if (ev.msg === "cancelled") store.addLog("验卷已取消（单锁已释放）", "warn");
        else store.addLog("!! 验卷失败：" + String(ev.msg).slice(0, 120), "err");
      }
      else if (ev.type === "item") applyAuditItem(ev.data);
      else if (ev.type === "done") {
        store.addLog(`验卷完成：审 ${ev.data.audited} · 可作答 ${ev.data.solvable} · 缓存 ${ev.data.from_cache} · 请求 ${ev.data.requests}`, "ok");
        await refreshTasks();
      }
    });
  });
}

// ---------- 开始解题（选中任务 → /solve keys 流；建 job 供队列/审批屏） ----------
// solveFlag=false：只领卷建 job 不调模型（headless 验收走这条，避开 OpenRouter 配额日）
// 事件处理抽至 applySolveEvent（探针 __m3b.solveFetchOnly 复用同一真实路径，M2 __m2 先例）
async function startSolve(solveFlag = true) {
  const keys = [...store.selected];
  if (!keys.length) { store.addLog("⚠ 未勾选任何任务，先在作业计划勾选", "warn"); return; }
  // 用户实测反馈：点了「开始解题」却停在计划屏，不知道有没有生效。
  // 只在真能开跑时跳屏（占用中交给 withBusy 出提示，别把人空跳过去）。
  if (!store.busy) go("queue");
  await withBusy("solve", async () => {
    store.addLog(`开始解题：${keys.length} 份（领卷${solveFlag ? "→逐题" : "（只领卷）"}，SSE）`);
    await net.sse("/solve", { keys, solve: solveFlag }, applySolveEvent);
  });
}

export function shortId(id) { return String(id).replace(/^w:/, "").slice(-10); }

// /solve 事件流两类 item：领卷播报（无 status）/ 解题结果（带 status）——语义对齐 e2e 环5 修复
function upsertJobEvent(d) {
  if (!d || !d.job_id) return;
  const t = store.tasks.find((x) => x.key === d.job_id);
  let j = store.jobs[d.job_id];
  if (!j) {
    j = store.jobs[d.job_id] = {
      title: t ? t.title : d.job_id, course: t ? t.course : "",
      ref: { courseId: t && t.courseId, classId: t && t.classId, cpi: t && t.cpi,
             workId: t && t.workId, answerId: t && (t.answerId || "0") },
      questions: [], results: {}, state: "running", accepted: new Set(), manual: {},
    };
    store.emit("jobs");
  }
  if (d.status === undefined) {
    if (!j.questions.find((q) => q.qid === d.qid)) {
      j.questions.push({ qid: d.qid, type: d.type, stem: d.stem, options: d.options,
                         n_options: d.n_options, image_flag: d.image_flag, blank_count: d.blank_count });
    }
  } else {
    j.results[d.qid] = d;
    j.progress = Object.keys(j.results).length + "/" + j.questions.length;
  }
  store.emit("jobs");
}

// ---------- 黑名单（交互规则1/2：置灰沉底仍可勾选；恢复=点列头/全部恢复条；44px 空档） ----------
function toggleEx(course) {
  store.excluded.has(course) ? store.excluded.delete(course) : store.excluded.add(course);
  store.persistExcluded();
  layout();
}

// ---------- 布局引擎（承 mock-3modes 算法，数据换成真任务表） ----------
function groups() {
  const byCourse = new Map();
  store.tasks.forEach((t) => {
    if (!byCourse.has(t.course)) byCourse.set(t.course, []);
    byCourse.get(t.course).push(t);
  });
  return byCourse;
}

function sinkable(t) { return t.solvable === false; }   // §3：不可作答沉底
const due = (t) => t.ts || Infinity;

// M2：会移动画布的变更一律走 animateLayout（FLIP：translate→none，450ms emphasized，可打断）；
// resize 等重排用 layoutInstant（不补间，直接落位）。
export function layout() { animateLayout(canvas, applyLayout); }
export function layoutInstant() { applyLayout(); }

function applyLayout() {
  const gc = groups();
  const courses = [...gc.keys()].sort(
    (a, b) => (store.excluded.has(a) - store.excluded.has(b)) ||
              gc.get(b).length - gc.get(a).length);

  // 元素池对账：新增建、消失隐（常驻不重建=规则4）
  for (const t of store.tasks) if (!cards[t.key]) cards[t.key] = buildCard(t);
  for (const c of courses) if (!heads[c]) heads[c] = buildHead(c);
  Object.values(heads).forEach((h) => { h.style.display = courses.includes(h.dataset.course) ? "" : "none"; });
  Object.entries(cards).forEach(([k, el]) => {
    if (!store.tasks.find((t) => t.key === k)) el.style.display = "none";
  });

  const innerW = stage.clientWidth - 40;
  if (mode === "course") layoutCourses(courses, gc, innerW);
  else layoutTimeline(courses, gc, innerW);
  updateBottom();
}

function layoutCourses(courses, gc, innerW) {
  const totalW = courses.length * COLW + Math.max(0, courses.length - 1) * GAP;
  canvas.style.width = Math.max(totalW, innerW) + "px";
  let maxH = 0;
  courses.forEach((c, i) => {
    const x = i * (COLW + GAP);
    const h = heads[c]; h.style.display = ""; h.classList.remove("dim");
    h.style.left = x + "px"; h.style.top = "2px"; h.style.width = COLW + "px";
    const ex = store.excluded.has(c);
    h.querySelector(".eye").textContent = ex ? "已隐藏·点恢复" : "👁";
    let rows = gc.get(c).slice();
    rows.sort((a, b) => (sinkable(a) - sinkable(b)) || due(a) - due(b));   // 不可作答沉底（列内）
    h.querySelector(".col-n").textContent = rows.length;
    let y = HDR;
    rows.forEach((t) => {
      const el = cards[t.key]; el.style.display = "";
      el.style.width = COLW + "px";
      const mh = el.offsetHeight;
      el.style.left = x + "px"; el.style.top = y + "px";
      y += mh + CARD_GAP;
    });
    maxH = Math.max(maxH, y);
  });
  canvas.style.height = maxH + 8 + "px";
  stage.style.overflowX = "auto"; stage.style.overflowY = "hidden";
  canvas.querySelector(".restorebar")?.classList.remove("vis");
}

function layoutTimeline(courses, gc, innerW) {
  Object.values(heads).forEach((h) => h.classList.add("dim"));
  const all = store.tasks.slice();
  const isSunk = (t) => store.excluded.has(t.course) || sinkable(t);
  const active = all.filter((t) => !isSunk(t)).sort((a, b) => due(a) - due(b));
  const sunk = all.filter(isSunk).sort((a, b) =>
    (sinkable(a) - sinkable(b)) || (due(a) - due(b)));   // 沉底区：读不到在最底
  const colW = COLW, colN = 2;
  const totalW = colN * colW + GAP;
  const offX = Math.max(0, (innerW - totalW) / 2);       // 两列居中（DESIGN 时间流）
  const per = Math.ceil(active.length / colN);
  const buckets = [active.slice(0, per), active.slice(per).concat(sunk)];
  canvas.style.width = Math.max(innerW, totalW) + "px";
  const heights = [];
  let restoreY = -1;
  buckets.forEach((arr, bi) => {
    const x0 = offX + bi * (colW + GAP);
    let y = 2;
    arr.forEach((t, idx) => {
      if (bi === colN - 1 && store.excluded.has(t.course) &&
          !arr.slice(0, idx).some((p) => store.excluded.has(p.course)) && restoreY < 0) {
        restoreY = y; y += 44;                          // 恢复条与卡片间 44px 空档（交互规则2）
      }
      const el = cards[t.key]; el.style.display = "";
      el.style.width = colW + "px";
      el.style.left = x0 + "px"; el.style.top = y + "px";
      y += el.offsetHeight + CARD_GAP;
    });
    heights.push(y);
  });
  let bar = canvas.querySelector(".restorebar");
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "restorebar";
    bar.innerHTML = `<span class="ex-flag">以下科目已隐藏 · 点我全部恢复</span>`;
    bar.onclick = () => { store.excluded.clear(); store.persistExcluded(); layout(); };
    canvas.appendChild(bar);
  }
  if (restoreY >= 0) {
    bar.classList.add("vis");
    bar.style.left = offX + (colN - 1) * (colW + GAP) + "px";
    bar.style.top = restoreY + "px"; bar.style.width = colW + "px";
  } else bar.classList.remove("vis");
  canvas.style.height = Math.max(...heights, 40) + 8 + "px";
  stage.style.overflowX = "hidden"; stage.style.overflowY = "auto";
}

function buildHead(course) {
  const el = document.createElement("div");
  el.className = "col-h"; el.dataset.course = course;
  el.innerHTML = `<span class="dot" style="background:${courseColor(course)}"></span>
    <span class="col-t"></span><span class="col-n"></span><span class="eye">👁</span>`;
  el.querySelector(".col-t").textContent = course;      // textContent 防注入
  el.onclick = () => toggleEx(course);
  canvas.appendChild(el);
  return el;
}

function buildCard(t) {
  const el = document.createElement("div");
  el.className = "card task"; el.dataset.key = t.key;
  el.innerHTML = `
    <div class="row">
      <div class="chk"></div>
      <div class="body">
        <div class="r-title"></div>
        <div class="r-sub"></div>
      </div>
      <div class="r-meta"><span class="qn"></span><span class="pill"></span></div>
    </div>
    <div class="preview"></div>`;
  el.querySelector(".chk").onclick = () => {
    // refreshTasks 会整体替换 store.tasks 对象——点击时按 key 现查，不吃闭包旧引用
    const cur = store.tasks.find((x) => x.key === t.key) || t;
    if (cur.solvable === false) return;                // 置灰+禁选（HANDOFF §3 三态）
    store.selected.has(t.key) ? store.selected.delete(t.key) : store.selected.add(t.key);
    syncCard(cur, el); updateBottom();
  };
  canvas.appendChild(el);
  syncCard(t, el);
  return el;
}

function syncCard(t, el) {
  el = el || cards[t.key];
  if (!el) return;
  el.querySelector(".r-title").textContent = t.title;
  el.querySelector(".r-sub").textContent =
    `${t.course} · ${t.sub || ""} · ${t.status || "待做"}${t.audited ? (t.solvable ? ` · 可作答 ${t.qreal} 题` : " · " + (t.pill || "读不到题")) : " · 未验"}`;
  el.querySelector(".qn").textContent = t.qn ? `${t.qn} 题` : "";
  const pill = el.querySelector(".pill");
  pill.textContent = t.pill || "";
  pill.className = "pill " + (t.pcls || "grey");
  pill.style.visibility = t.pill ? "visible" : "hidden";
  el.classList.toggle("sel", store.selected.has(t.key));
  el.querySelector(".chk").classList.toggle("on", store.selected.has(t.key));   // 勾选蓝底✓上屏（M1 漏接线：只切了 .card.sel）
  el.classList.toggle("excluded", store.excluded.has(t.course));
  el.classList.toggle("unsolvable", t.solvable === false);
  const pv = el.querySelector(".preview");
  pv.textContent = t.audited && t.solvable && t.preview ? "预览：" + t.preview : "";
  pv.style.display = pv.textContent ? "" : "none";
}

function updateBottom() {
  store.tasks.forEach((t) => { if (cards[t.key]) syncCard(t); });
  const sel = store.tasks.filter((t) => store.selected.has(t.key));
  bottom.querySelector("#sel-n").textContent = sel.length;
  bottom.querySelector("#sel-q").textContent = sel.reduce((a, t) => a + (t.qn || 0), 0);
  store.emit("sel");
}

// ---------- M3b R4：删除任务卡（队列/审批屏共用） ----------
// 二次确认（同 M3 弹窗风格）；运行中卡片先 POST /cancel 且确认成功才删（R2）；
// server 报 not_running=幂等空操作，可直接删；timeout/网络失败则保留卡片（宁缺勿误删）。
export async function deleteJobUI(jid) {
  const j = store.jobs[jid];
  if (!j) return;
  const running = j.state === "running" || j.state === "init";
  const yes = await confirmDlg("删除任务卡",
    [`将删除任务卡《${j.title || shortId(jid)}》${j.course ? `（${j.course}）` : ""}。`,
     running ? "卡面显示运行中：会先向本地 server 发 POST /cancel 真取消，确认锁已释放后再删。"
             : "仅删除本机镜像与 server 内存 job，不会向学习通发送任何请求。",
     "删除后 localStorage 记录、徽标计数、审批勾选态一并清理；此操作不可撤销。"],
    "确认删除");
  if (!yes) return;
  if (running) {
    let d;
    try {
      d = await net.api("/cancel", { method: "POST", body: { mode: "solve", job_id: jid } });
    } catch (e) {
      store.addLog("!! " + errText("取消任务", "", e, "稍后重试删除"), "err");
      return;   // 取消失败=可能还在跑，不删
    }
    if (d.ok === true) store.addLog(`已取消运行中的任务（${shortId(jid)}），删除继续`, "warn");
    else if (d.reason === "not_running") store.addLog(`删除 ${shortId(jid)}：server 无运行任务（幂等，直接删）`);
    else { store.addLog(`!! 取消未确认（${d.reason || "?"}），为防误删保留卡片`, "err"); return; }
  }
  store.removeJob(jid);
  store.addLog(`已删除任务卡：${shortId(jid)}`);
}
