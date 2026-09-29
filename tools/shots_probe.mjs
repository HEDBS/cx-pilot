// tools/shots_probe.mjs —— 出 README 用的界面截图（headless Edge + CDP，承 nav_probe 模式）。
//
// 为什么要单独做：真实软件跑的是**你自己的账号数据**（真课程名、真作业标题、脱敏 key），
// 直接截图会把身份信息带进公开仓。所以这里：
//   1) boot 后把 store.tasks / store.jobs 换成**完全虚构**的数据（通用学科名，非任何真实课程）；
//   2) 把 /settings 打桩成通用后端链，避免露出真实 provider 配置与脱敏 key 片段；
//   3) 逐屏导航 + Page.captureScreenshot 出 PNG。
// 纪律：截图里不得出现真实课程名/账号/手机号/key——出图后仍需人眼复核（vision 看一遍）。
//
// 用法：node tools/shots_probe.mjs [--out DIR] [--port 9346]
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const arg = (k, d = "") => { const i = process.argv.indexOf("--" + k); return i > 0 ? process.argv[i + 1] : d; };
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "shell", "src");
const OUT = arg("out", path.join(os.tmpdir(), "cx-pilot-shots-" + process.pid));
const CDP_PORT = Number(arg("port", "9346"));
const PROFILE = path.join(OUT, "edge-profile");
const EDGE = arg("edge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe");
const PY = arg("python", "python");

fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let ws, mid = 0;
const pending = new Map();
const procs = [];
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++mid;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) {
    const d = r.exceptionDetails.exception && r.exceptionDetails.exception.description;
    throw new Error("eval 异常: " + String(d || JSON.stringify(r.exceptionDetails)).slice(0, 300));
  }
  return r.result.value;
}
async function wait(expr, timeout, step = 300) {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) {
    try { if (await evaluate(`(function(){try{return !!(${expr})}catch(_){return false}})()`)) return; } catch (_) { }
    await sleep(step);
  }
  throw new Error("WAIT-TIMEOUT: " + expr);
}
function killAll() {
  for (const p of procs) { try { spawn("taskkill", ["/T", "/F", "/PID", String(p.pid)], { stdio: "ignore" }); } catch (_) { } }
}
process.on("exit", killAll);

function startServer() {
  return new Promise((resolve, reject) => {
    const p = spawn(PY, ["-m", "server", "--port", "0"], { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
    procs.push(p);
    let buf = "";
    const timer = setTimeout(() => reject(new Error("20s 未见 CX_READY")), 20000);
    p.stdout.on("data", (d) => {
      buf += d.toString("utf8");
      const m = buf.match(/CX_READY port=(\d+) token=(\S+)/);
      if (m) { clearTimeout(timer); resolve({ port: Number(m[1]), token: m[2] }); }
    });
    p.stderr.on("data", (d) => { try { fs.appendFileSync(path.join(OUT, "server.log"), d); } catch (_) { } });
    p.on("exit", (c) => reject(new Error("server 提前退出 exit=" + c)));
  });
}
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
               ".css": "text/css; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon",
               ".svg": "image/svg+xml" };
function startStatic(port) {
  return new Promise((resolve, reject) => {
    const s = http.createServer((req, rsp) => {
      let rel = decodeURIComponent((req.url || "/").split("?")[0]);
      if (rel === "/") rel = "/index.html";
      const f = path.join(SRC, rel);
      if (!f.startsWith(SRC + path.sep) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) {
        rsp.writeHead(404); return rsp.end("404");
      }
      rsp.writeHead(200, { "Content-Type": MIME[path.extname(f)] || "application/octet-stream",
                           "Cache-Control": "no-store" });
      fs.createReadStream(f).pipe(rsp);
    });
    s.once("error", reject);
    s.listen(port, "127.0.0.1", () => resolve(s));
    procs.push({ pid: null, kill: () => s.close() });
  });
}

// ---------- 虚构数据（通用学科名，刻意避开任何真实课程名）----------
const TASKS = [
  { key: "w:9001:5001", course: "数据结构", courseId: "9001", classId: "9101", cpi: "9201",
    workId: "5001", answerId: "0", title: "第 3 章 线性表 练习", sub: "截止 2026-10-08 23:59",
    pill: "剩 1 天", pcls: "urgent", qn: 10, ts: 1791234000, status: "待做", src: "getAllWork",
    url: "", etype: "work", audited: true, solvable: true, qreal: 10,
    preview: "1. 顺序存储结构的特点是？\n2. 单链表插入结点的时间复杂度？" },
  { key: "w:9001:5002", course: "数据结构", courseId: "9001", classId: "9101", cpi: "9201",
    workId: "5002", answerId: "0", title: "第 4 章 栈与队列 随堂练习", sub: "截止 2026-10-15 23:59",
    pill: "剩 8 天", pcls: "warn", qn: 6, ts: 1791838800, status: "待做", src: "getAllWork",
    url: "", etype: "work", audited: true, solvable: true, qreal: 6,
    preview: "1. 栈的特点是什么？\n2. 循环队列如何判满？" },
  { key: "w:9002:5011", course: "计算机网络", courseId: "9002", classId: "9102", cpi: "9202",
    workId: "5011", answerId: "0", title: "第 2 章 物理层 作业", sub: "截止 2026-10-20 23:59",
    pill: "剩 13 天", pcls: "warn", qn: 0, ts: 1792270800, status: "待做", src: "getAllWork",
    url: "", etype: "work", audited: true, solvable: true, qreal: 4,
    preview: "1. 奈氏准则的内容？\n2. 香农公式的适用条件？" },
  { key: "w:9003:5021", course: "计算机组成原理", courseId: "9003", classId: "9103", cpi: "9203",
    workId: "5021", answerId: "0", title: "签到（课堂教学活动）", sub: "截止 2026-10-02 08:00",
    pill: "剩 1 天", pcls: "urgent", qn: 0, ts: 1790726400, status: "待做", src: "stat2",
    url: "", etype: "sign", audited: true, solvable: false, qreal: 0, preview: "",
    reason: "非作业任务（签到/合同类，无题面）" },
  { key: "w:9003:5022", course: "计算机组成原理", courseId: "9003", classId: "9103", cpi: "9203",
    workId: "5022", answerId: "0", title: "第 5 章 指令系统 作业", sub: "截止 2026-10-25 23:59",
    pill: "剩 18 天", pcls: "warn", qn: 0, ts: 1792702800, status: "待做", src: "getAllWork",
    url: "", etype: "work", audited: true, solvable: false, qreal: 0, preview: "",
    reason: "领卷失败：缺班级信息（读不到）" },
];

// 一份「正在解」的 job：前 3 题有结果、第 4 题无结果 → 队列屏会在第 4 题左侧出加载圈
const QS = [
  { qid: "q1", type: "single", stem: "顺序存储结构的特点是？",
    options: { A: "逻辑相邻的元素物理上也相邻", B: "只能随机存储", C: "插入删除不需要移动元素", D: "必须用指针链接" }, image_flag: false },
  { qid: "q2", type: "judge", stem: "栈是先进先出的线性表。", options: {}, image_flag: false },
  { qid: "q3", type: "multi", stem: "以下哪些属于线性结构？",
    options: { A: "数组", B: "链表", C: "二叉树", D: "栈" }, image_flag: false },
  { qid: "q4", type: "blank", stem: "在一个长度为 n 的顺序表中插入一个元素，平均需要移动 （  ） 个元素。",
    options: {}, image_flag: false, blank_count: 1 },
  { qid: "q5", type: "subjective", stem: "简述栈和队列的主要区别。", options: {}, image_flag: false },
];
const RESULTS = {
  q1: { qid: "q1", status: "ok", answer: "A", confidence: 0.8, source: "硅基流动" },
  q2: { qid: "q2", status: "ok", answer: "false", confidence: 0.8, source: "硅基流动" },
  q3: { qid: "q3", status: "ok", answer: "ABD", confidence: 0.8, source: "硅基流动" },
};

const SETTINGS_FAKE = {
  active: ["硅基流动", "OpenRouter"],
  keyed_off: [],
  settings: {
    confidence_threshold: 0.75,
    providers: [
      { kind: "openai", name: "硅基流动", key_ref: "siliconflow", enabled: true,
        base_url: "https://api.siliconflow.cn/v1", model: "Qwen/Qwen2.5-7B-Instruct" },
      { kind: "openai", name: "OpenRouter", key_ref: "openrouter", enabled: true,
        base_url: "https://openrouter.ai/api/v1", model: "qwen/qwen3-8b:free" },
      { kind: "openai", name: "本地 llama.cpp", key_ref: "", enabled: false,
        base_url: "http://127.0.0.1:8080/v1", model: "qwen3-1.7b" },
    ],
    vision_providers: [
      { kind: "openai", name: "硅基流动", key_ref: "siliconflow", enabled: true,
        base_url: "https://api.siliconflow.cn/v1", model: "Qwen/Qwen2.5-VL-7B-Instruct" },
    ],
  },
  keys: {},          // 关键：keys 为空 → 设置屏的脱敏列显示 —，不露任何 key 片段
};

function injectJs() {
  return `(function(){
    var s = window.__m2 && window.__m2.store;
    if (!s) return "no-store";
    // 屏蔽真实 /settings：否则侧栏/设置屏会露出真实后端配置（含脱敏 key 片段）
    window.__origFetchShot = window.fetch;
    window.fetch = function(u, init){
      if (String(u).indexOf("/settings") >= 0 && (!init || String(init.method||"GET").toUpperCase()==="GET")) {
        return Promise.resolve(new Response(JSON.stringify(${JSON.stringify(SETTINGS_FAKE)}),
          { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return window.__origFetchShot.apply(this, arguments);
    };
    s.tasks = ${JSON.stringify(TASKS)};
    s.jobs = {};
    s.jobs["w:9001:5001"] = {
      title: "第 3 章 线性表 练习", course: "数据结构",
      ref: { courseId: "9001", classId: "9101", cpi: "9201", workId: "5001", answerId: "0" },
      questions: ${JSON.stringify(QS)}, results: ${JSON.stringify(RESULTS)},
      state: "running", accepted: new Set(), manual: {}, progress: "3/5"
    };
    s.selected = new Set(["w:9001:5001"]);
    s.busy = "solve";
    s.emit("settings");
    s.emit("tasks");
    s.emit("jobs");
    s.emit("busy");
    // 藏掉开发痕迹：状态条会显示 "sidecar browser-dev · port 5xxxx"（探针态特有，
    // 发行版是 bundled）。README 用图不该出现这个。
    var st = document.createElement("style");
    st.textContent = ".tb-status{display:none !important}";
    document.head.appendChild(st);
    return "ok";
  })()`;
}

async function shot(name) {
  await sleep(700);                      // 让动效落定
  const s = await send("Page.captureScreenshot", { format: "png" });
  const f = path.join(OUT, name + ".png");
  fs.writeFileSync(f, Buffer.from(s.data, "base64"));
  console.log("  拍到 " + name + ".png");
  return f;
}
async function goScreen(scr) {
  await evaluate(`(function(){
    var el = document.querySelector('.side-item[data-scr="${scr}"]');
    if (el) el.click();
    return !!el;
  })()`);
  await sleep(900);                      // 导航动效 450ms + 渲染
}

async function main() {
  console.log("[setup] 出图目录 " + OUT);
  const srv = await startServer();
  console.log("[setup] server 就绪 port=" + srv.port);
  // 端口必须落在 sidecar 的 CORS 白名单里（只放行 1420 / 1430），否则 fetch 全被拦、
  // boot 直接卡死 → __m2 永远不挂（踩过：用了 1440，白等 30 秒）
  let webPort = 1430, webSrv;
  try { webSrv = await startStatic(1430); }
  catch (e) { if (e.code === "EADDRINUSE") { webSrv = await startStatic(1420); webPort = 1420; } else throw e; }
  const url = `http://127.0.0.1:${webPort}/index.html?cxprobe=1&cxport=${srv.port}&cxtoken=${srv.token}&cxscan=0`;

  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
    `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
    "--window-size=1280,820", "--force-device-scale-factor=1",
    "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling", "about:blank",
  ], { stdio: "ignore" });
  procs.push(edge);

  const dl = Date.now() + 20000;
  let wsUrl = "";
  while (Date.now() < dl && !wsUrl) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page && page.webSocketDebuggerUrl) wsUrl = page.webSocketDebuggerUrl;
    } catch (_) { }
    await sleep(400);
  }
  if (!wsUrl) throw new Error("CDP 端点未出现");
  ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (evm) => {
    const m = JSON.parse(evm.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
      m.error ? reject(new Error(m.error.message)) : resolve(m.result);
    }
  };
  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url });
  await wait("window.__cxbootted && document.getElementById('side-fold')", 60000);
  // 关键：__cxbootted 是 boot 一开始就置的，此时 initPlan 可能还没跑完（__m2 还没挂）。
  // 必须显式等钩子就绪，否则注入打空 → 出的是空状态图（踩过）。
  await wait("window.__m2 && window.__m2.store", 30000);
  await sleep(600);
  console.log("[diag] " + await evaluate(`JSON.stringify({
    search: location.search, m2: !!window.__m2, hook: document.body.dataset.m2hook || null,
    tasks: (window.__m2.store.tasks || []).length, jobs: Object.keys(window.__m2.store.jobs || {}).length})`));

  const r = await evaluate(injectJs());
  console.log("[inject] " + r);
  await sleep(900);

  await goScreen("plan");
  await shot("01-plan-columns");
  // 时间流视图（分段控件：找带「时间流」字样的按钮）
  await evaluate(`(function(){
    var b = [].slice.call(document.querySelectorAll("button,.seg,.seg-item,[data-view]"))
      .filter(function(x){ return /时间流/.test(x.textContent); })[0];
    if (b) b.click();
    return !!b;
  })()`);
  await shot("02-plan-timeline");

  await goScreen("queue");
  await shot("03-queue-solving");

  // 审批屏不是"运行中"：清掉占用态，横条不该出现在这张图里
  await goScreen("approve");
  await evaluate(`(function(){var s=window.__m2.store; s.busy=null; s.emit("busy"); return 1;})()`);
  await shot("04-approve");

  await goScreen("settings");
  await shot("05-settings");

  console.log("[done] 出图完毕");
  try { await send("Browser.close"); } catch (_) { }
  await Promise.race([new Promise((r2) => edge.on("exit", r2)), sleep(8000)]);
  killAll();
  process.exit(0);
}

main().catch((e) => { console.error("SHOTS-FAIL: " + e.message); process.exit(2); });
