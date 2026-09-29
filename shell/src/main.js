// main.js —— 启动流程 + 路由 + 侧栏（M1 任务书 §1/§2）
// 握手(cx_info) → /version 角标 → /tasks 渲染 → 空表自动扫描（承 GUI 启动 do_refresh 行为）
// → 扫描后自动验卷（plan.runScan 内置，§3 三态沉底）。401/428 由 api 层广播、modal 层接。
import { net } from "./api.js";
import { store, refreshTasks, lsGet, lsSet } from "./store.js";
import { initPlan, runScan, layout, layoutInstant, solveSelected } from "./plan.js";
import { initQueue } from "./queue.js";
import { initApprove } from "./approve.js";
import { initLogs } from "./logs.js";
import { initSettings } from "./settings.js";
import { EASE, DUR_MOVE, DUR_FADE, reduced } from "./flip.js";
import { initTheme } from "./theme.js";
import { registerNav } from "./nav.js";

// ---------- M4 T1：屏级容器上下滑动（TASK-M4 §T1）----------
// 只动 .scr 容器本体（transform/opacity，WAAPI），不进卡片层、不触发布局属性——
// A1–A9 的 FLIP 采样在同帧取 before/after 矩形，容器在飞偏置等量抵消，互不干扰。
// 进场 translateY(+16→0)+opacity(0→1) 450ms emphasized；离场=旧屏加 .scr-leave 绝对浮层
// （立刻让位宽度给进屏，无双栏挤压）translateY(0→-16) 同曲线淡出；离屏在动画结束回调里
// 才真 hidden。可打断接续：任何起点先 captureVis（getComputedStyle 含在飞动画值）再 cancel，
// 新动画从当前视觉位起，不跳回。reduce → 去位移，纯 opacity 淡入淡出 250ms linear。
function captureVis(el) {
  const cs = getComputedStyle(el);
  return { tf: cs.transform === "none" ? null : cs.transform,
           op: Math.min(1, Math.max(0, parseFloat(cs.opacity) || 0)) };
}
function stopScrAnim(el) {
  if (el._scrA) { el._scrA.cancel(); el._scrA = null; }
}
function leaveScreen(el) {
  const vis = captureVis(el);
  stopScrAnim(el);
  el.classList.add("scr-leave");
  const rd = reduced();
  const a = el.animate(
    rd ? [{ opacity: vis.op }, { opacity: 0 }]
       : [{ transform: vis.tf || "none", opacity: vis.op },
          { transform: "translateY(-16px)", opacity: 0 }],
    { duration: rd ? DUR_FADE : DUR_MOVE, easing: rd ? "linear" : EASE });
  el._scrA = a;
  a.finished.then(() => {
    if (el._scrA === a) {          // 期间被再进场打断过（cancel）则不动：由进方接管
      el._scrA = null;
      el.classList.remove("scr-leave");
      el.hidden = true;
    }
  }).catch(() => {});
}
function enterScreen(el) {
  const fresh = el.hidden && !el.classList.contains("scr-leave");   // 常规切屏：从未显示进
  const vis = fresh ? null : captureVis(el);                         // 打断/回流：抓在飞视觉位
  stopScrAnim(el);
  el.classList.remove("scr-leave");
  el.hidden = false;
  const rd = reduced();
  const fromT = rd ? undefined : (fresh ? "translateY(16px)" : (vis.tf || "translateY(16px)"));
  const fromO = fresh ? 0 : vis.op;
  const frames = rd ? [{ opacity: fromO }, { opacity: 1 }]
                    : [{ transform: fromT, opacity: fromO }, { transform: "none", opacity: 1 }];
  const a = el.animate(frames, { duration: rd ? DUR_FADE : DUR_MOVE, easing: rd ? "linear" : EASE });
  el._scrA = a;
  a.finished.then(() => { if (el._scrA === a) el._scrA = null; }).catch(() => {});
}

const SCREENS = {
  plan: { el: "#scr-plan", name: "作业计划", init: initPlan },
  queue: { el: "#scr-queue", name: "解题队列", init: initQueue },
  approve: { el: "#scr-approve", name: "提交审批", init: initApprove },
  logs: { el: "#scr-logs", name: "运行日志", init: initLogs },
  settings: { el: "#scr-settings", name: "设置", init: initSettings },
};
const inited = new Set();
let cur = "";
let navDone = false;   // 首次路由=瞬置（boot 可能直接落在 #settings，不该从计划位滑行入场）

// ---------- M3 I3：侧栏选中指示器（常驻单元素，translateY 平移，不重建 DOM） ----------
let sidebarEl, foldBtn, pill, pillY = -1, pillA = null;
function placePill(animate) {
  if (!pill || !sidebarEl) return;
  const act = document.querySelector("#sidebar .side-item.active");
  if (!act) return;
  const y = act.offsetTop, h = act.offsetHeight;
  if (pillA) { pillA.cancel(); pillA = null; }
  // 起点=当前视觉位（含在飞 transform）→ 打断立即接续，不跳回
  const prev = pill.getBoundingClientRect().top - sidebarEl.getBoundingClientRect().top;
  const from = pillY < 0 ? y : prev;
  pill.style.height = h + "px";
  pill.style.transform = "translateY(" + y + "px)";
  if (animate && !reduced() && Math.abs(y - from) > 0.5) {
    const a = pill.animate(
      [{ transform: "translateY(" + from + "px)" }, { transform: "translateY(" + y + "px)" }],
      { duration: DUR_MOVE, easing: EASE });
    pillA = a;
    a.finished.then(() => { if (pillA === a) pillA = null; }).catch(() => {});
  }
  pillY = y;
}

// ---------- M3 I2：侧栏折叠（width 过渡=§0 声明例外；折叠态 localStorage 持久化） ----------
const FOLD_KEY = "cx.sidefold";
function setFold(collapsed, persist = true) {
  if (!sidebarEl) return;
  sidebarEl.classList.toggle("collapsed", collapsed);
  if (foldBtn) {
    foldBtn.textContent = collapsed ? "»" : "«";
    foldBtn.title = collapsed ? "展开侧栏" : "折叠侧栏";
  }
  if (persist) lsSet(FOLD_KEY, collapsed ? "1" : "0");
  placePill(false);   // 指示器 left/right 内缩自动随宽，无需位移动画
}

function go(id) {
  if (!SCREENS[id]) id = "plan";
  const prevEl = cur && SCREENS[cur] ? document.querySelector(SCREENS[cur].el) : null;
  const nextEl = document.querySelector(SCREENS[id].el);
  const swap = navDone && prevEl && prevEl !== nextEl;   // T1：首航瞬置（boot 直接落某屏不该滑行入场）
  cur = id;
  for (const [k, s] of Object.entries(SCREENS)) {
    const el = document.querySelector(s.el);
    if (el === prevEl && swap) continue;   // 离屏由 leaveScreen 淡出后自行 hidden
    if (el === nextEl && navDone) continue; // 进屏由 enterScreen 解除 hidden
    if (el._scrA && el.classList.contains("scr-leave")) continue;  // 更早批次的离场淡出在飞：等它自己的收尾回调，勿硬切
    el.hidden = k !== id;
  }
  if (swap) leaveScreen(prevEl);
  if (navDone) enterScreen(nextEl);
  document.querySelectorAll(".side-item").forEach((x) =>
    x.classList.toggle("active", x.dataset.scr === id));
  placePill(navDone);   // I3：指示器平滑滑到目标项（450ms emphasized）；首航瞬置
  navDone = true;
  if (id === "settings") initSettings(document.querySelector("#scr-settings"));  // 每次进入重读脱敏态
  else if (!inited.has(id)) { SCREENS[id].init(document.querySelector(SCREENS[id].el)); inited.add(id); }
  if (id === "plan") layout();
  document.getElementById("tb-screen").textContent = SCREENS[id].name;
  history.replaceState(null, "", "#" + id);
}

// 把真实路由注册给 nav.js，供 plan/queue 等模块 go(screen) 使用
registerNav(go);

// ---------- 顶部进行中横条（任务占线程时的即时反馈；用户实测反馈新增）----------
// 只在"占线程且没有自己进度 UI"的任务上出现：扫描自带进度条与按钮文案，不重复提示。
// 文案说人话，不暴露 scan/solve 这类内部代号。
const BUSY_TEXT = {
  audit: "正在验卷（逐条核验作业能否作答）…",
  solve: "正在领卷并逐题解答…",
  submit: "正在提交到学习通…",
  "submit-dry": "正在预检提交表单…",
  "approve-manual": "正在写回人工答案…",
};
function paintBusyBar() {
  const el = document.getElementById("busy-bar");
  if (!el) return;
  const m = store.busy;
  const text = m && BUSY_TEXT[m];
  if (!text) {                      // 空闲 / 扫描（有自己的进度 UI）/ 未知代号 → 不显示
    el.hidden = true;
    return;
  }
  el.innerHTML = `<span class="spin"></span><span class="bb-txt"></span>`;
  el.querySelector(".bb-txt").textContent = text;
  el.hidden = false;
}

function updateBadges() {
  const n = store.tasks.length;
  document.querySelector('[data-scr="plan"] .side-badge').textContent = n;
  document.querySelector('[data-scr="plan"] .side-badge').style.display = n ? "" : "none";
  const jobs = Object.keys(store.jobs).length;
  document.querySelector('[data-scr="queue"] .side-badge').textContent = jobs;
  document.querySelector('[data-scr="queue"] .side-badge').style.display = jobs ? "" : "none";
  document.querySelector('[data-scr="approve"] .side-badge').textContent = jobs;
  document.querySelector('[data-scr="approve"] .side-badge').style.display = jobs ? "" : "none";
}

function setTbStatus(text, cls) {
  const el = document.getElementById("tb-status");
  el.textContent = text; el.className = "tb-status " + (cls || "");
}

async function loadSettingsIntoSidebar() {
  try {
    const d = await net.api("/settings");
    store.settings = d;
    const box = document.getElementById("backend-list");
    box.innerHTML = "";
    (d.active || []).forEach((name) => {
      const el = document.createElement("div");
      el.className = "side-item";
      el.innerHTML = `<span class="ic" style="background:var(--accent)">●</span>
        <span class="bn"></span><span class="conn">已连通(在链)</span>`;
      el.querySelector(".bn").textContent = name;
      box.appendChild(el);
    });
    if (!d.active || !d.active.length) {
      box.innerHTML = `<div class="side-item" style="color:var(--red)"><span class="lbl">后端链为空——去设置启用 provider</span></div>`;
    }
  } catch (e) { /* 401/428 已由 api 层广播登录框 */ }
}

const q = new URLSearchParams(location.search);

async function boot() {
  window.__cxbootted = true;
  initTheme();   // M4 T3：主题按钮接线 + visibilitychange 暂停 Aurora（首帧主题已由内联引导落定）
  for (const [k, s] of Object.entries(SCREENS)) {
    document.querySelector(s.el).dataset.scr = k;
  }
  document.querySelectorAll(".side-item[data-scr]").forEach((x) => { x.onclick = () => go(x.dataset.scr); });

  // M3 I2/I3：折叠按钮接线 + 持久折叠态无动画恢复（noanim 防首帧从 212px 收过来）
  sidebarEl = document.getElementById("sidebar");
  foldBtn = document.getElementById("side-fold");
  pill = document.getElementById("side-pill");
  foldBtn.onclick = () => setFold(!sidebarEl.classList.contains("collapsed"));
  if (lsGet(FOLD_KEY) === "1") {
    sidebarEl.classList.add("noanim");
    setFold(true, false);
    void sidebarEl.offsetWidth;              // 强制 reflow 落定无过渡态
    sidebarEl.classList.remove("noanim");
  }
  placePill(false);                          // 握手可能耗时，指示器先就位免得空条

  const info = await net.handshake();
  if (!info) { setTbStatus("sidecar 未就绪（30s 无握手）", "err"); return; }
  setTbStatus(`sidecar ${info.mode} · port ${info.port}`, "ok");
  store.retryLast = bootScan;
  store.hydrateJobs();   // 队列/审批屏跨页面刷新不空（见 store.js 注释）
  store.hydrateLog();

  try {
    const v = await net.api("/version");
    document.getElementById("verbadge").textContent = "v" + v.version;
    document.getElementById("enginebadge").textContent = "engine " + v.engine;
  } catch (e) { setTbStatus("version 失败：" + e, "err"); return; }

  // M3b R1：幽灵任务自愈——重启后 localStorage 里的 running 卡必须先对账再渲染/扫描。
  // 必须 await：R5 要求 bootScan 发起前幽灵已标 interrupted，避免「扫描中」与「旧 running」混淆。
  // 对账失败（无凭据/离线/401/428）保持原样只记日志——绝不误判（语义见 store.reconcileJobs）。
  await store.reconcileJobs();

  initPlan(document.querySelector("#scr-plan"));   // 扫描进度条/布局常驻，与当前屏无关
  inited.add("plan");
  updateBadges();
  go((location.hash || "#plan").slice(1));
  window.addEventListener("hashchange", () => go((location.hash || "#plan").slice(1)));
  store.on((what) => {
    if (what === "tasks") { updateBadges(); if (inited.has("plan")) layout(); }
    else if (what === "jobs") updateBadges();
    else if (what === "busy") paintBusyBar();
  });
  paintBusyBar();
  // M2：resize 重排直接落位（layoutInstant），不补间——连续 resize 事件下 FLIP 会逐帧起新动画，抖
  // M3 I3：指示器同步重定位（文字换行可能改行高→offsetTop 变），同样瞬置
  window.addEventListener("resize", () => { if (cur === "plan") layoutInstant(); placePill(false); });

  await loadSettingsIntoSidebar();

  let d;
  try { d = await refreshTasks(); } catch (e) { d = { count: 0 }; }
  store.addLog(`启动：任务表 ${d.count} 条 · mode=${info.mode}`);
  if (!d.count && q.get("cxscan") !== "0") await bootScan();
}

async function bootScan() {
  await runScan();
  // headless 验收钩子（仅浏览器 dev 回退态有 URL 参数；Tauri 壳无 location.search 参数）
  if (q.get("cxsolve") === "1") {
    const t = store.tasks.find((x) => x.etype === "work" && x.solvable !== false && x.classId);
    if (t) { store.selected.add(t.key); go("queue"); await solveSelected(false); }
  }
  // CDP 验收探针：管线（扫描→验卷→领卷）全部落定后置位，供 e2e_ui.mjs 轮询
  document.body.dataset.m1pipeline = JSON.stringify({
    ok: true, tasks: store.tasks.length, jobs: Object.keys(store.jobs).length,
  });
}

boot();
