// tools/jobs_probe.mjs —— M3b 任务书 §三 取证（Edge+CDP，承 nav_probe/anim_probe 链路）
// 场景（逐条对齐任务书验收）：
//   J1 幽灵任务自愈：boot 建探针 job（solve solve=false 假题，不触模型/不触提交协议）→
//      标 running → kill server（探针自家子进程；纪律的「不 kill」指用户 shell.exe）→
//      重启 → 卡片=已中断（重启前未完成），且 /jobs 里不存在它。
//   J1b 对账失败不误判：页内 stub net.api 抛 428（b1b 决定论同法）驱动 reconcileJobs catch
//      → 卡片保持 running + 日志「对账跳过」+ localStorage 不改写 + 卡片不丢。
//   J2 真取消：node 起 /scan → 确认真进入逐课（[2/N] log）后 POST /cancel →
//      流收 {"type":"error","msg":"cancelled"} 且无 done → 立刻再 POST /scan=200
//      （单锁已释放，非 409）→ 再 cancel 收尾；上游秒回抖动重试 ≤4 轮并如实记录。
//   J3 幂等：空闲 POST /cancel → 200 {ok:false,reason:"not_running"}；GET /jobs 形状。
//   J4 删除：running 卡 ×→确认 → 真 POST /cancel（fetch 计数）→ 卡消失/无 localStorage/徽标-1；
//      interrupted 卡 ×→确认 → 不发 /cancel 直接删；空态文案回来；全程 #jserr 不亮。
//   J5 R5 时序：不带 cxscan=0 真重启 boot → 日志序「任务对账」<「开始全量扫描」，
//      扫描进行中幽灵卡=已中断（不与「扫描中」混淆）；收尾 /cancel 该扫描。
// 纪律：真课名/账号/口令绝不进 JSON/stdout；证据落 os.tmpdir()（--out 可覆盖）；不动禁改面。
// 用法：node tools/jobs_probe.mjs [--out DIR] [--port 9345] [--python python] [--edge PATH]
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const arg = (k, d = "") => { const i = process.argv.indexOf("--" + k); return i > 0 ? process.argv[i + 1] : d; };
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "shell", "src");
const OUT = arg("out", path.join(os.tmpdir(), "cx-pilot-jobs-" + process.pid));
const CDP_PORT = Number(arg("port", "9345"));
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

// ---------- server 子进程（探针自管自杀；不触碰用户 shell.exe） ----------
function startServer(extraEnv) {
  return new Promise((resolve, reject) => {
    const env = { ...process.env, ...(extraEnv || {}) };
    const p = spawn(PY, ["-m", "server", "--port", "0"], { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
    procs.push(p);
    let buf = "";
    const timer = setTimeout(() => reject(new Error("20s 未见 CX_READY")), 20000);
    p.stdout.on("data", async (d) => {
      buf += d.toString("utf8");
      const m = buf.match(/CX_READY port=(\d+) token=(\S+)/);
      if (!m) return;
      clearTimeout(timer);
      // 就绪等待：CX_READY 在 uvicorn.run 绑口之前打印（__main__.py 既有顺序），
      // 立刻请求会撞 connection-refused（本探针踩实：第二次 goto fetch 失败）
      const port = Number(m[1]);
      const dl = Date.now() + 15000;
      for (;;) {
        try { if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) break; } catch (_) { }
        if (Date.now() > dl) { reject(new Error("15s /health 未通（port=" + port + "）")); return; }
        await sleep(200);
      }
      resolve({ port, token: m[2], proc: p });
    });
    p.stderr.on("data", (d) => { try { fs.appendFileSync(path.join(OUT, "server.log"), d); } catch (_) { } });
    p.on("exit", (c) => reject(new Error("server 提前退出 exit=" + c + "（见 server.log）")));
  });
}
function killServer(s) {
  return new Promise((res) => {
    try { spawn("taskkill", ["/T", "/F", "/PID", String(s.proc.pid)], { stdio: "ignore" }); } catch (_) { }
    setTimeout(res, 1200);
  });
}
let srv = { port: 0, token: "" };
async function httpApi(s, p, body) {
  const init = body
    ? { method: "POST", headers: { "Content-Type": "application/json", "X-CX-Token": s.token }, body: JSON.stringify(body) }
    : { headers: { "X-CX-Token": s.token } };
  const r = await fetch(`http://127.0.0.1:${s.port}${p}`, init);
  const j = await r.json().catch(() => null);
  return { status: r.status, json: j };
}

// ---------- 静态服务（挂 shell/src；仅回环，1430→1420 回落=dev origin 白名单内） ----------
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon", ".svg": "image/svg+xml" };
function startStatic(port) {
  return new Promise((resolve, reject) => {
    const s = http.createServer((req, rsp) => {
      let rel = decodeURIComponent((req.url || "/").split("?")[0]);
      if (rel === "/") rel = "/index.html";
      const f = path.join(SRC, rel);
      if (!f.startsWith(SRC + path.sep) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) {
        rsp.writeHead(404); return rsp.end("404");
      }
      rsp.writeHead(200, { "Content-Type": MIME[path.extname(f)] || "application/octet-stream", "Cache-Control": "no-store" });
      fs.createReadStream(f).pipe(rsp);
    });
    s.once("error", reject);
    s.listen(port, "127.0.0.1", () => resolve(s));
    procs.push({ pid: null, kill: () => s.close() });
  });
}

// ---------- 断言框架（同 nav_probe） ----------
const results = [];
function mkBag() { return { ok: 0, fail: 0, lines: [] }; }
function check(bag, name, ok, actual, expected) {
  bag.lines.push((ok ? "  ✓ " : "  ✗ ") + name + "  实测=" + JSON.stringify(actual) + "  期望=" + expected);
  if (ok) bag.ok++; else bag.fail++;
}
async function scene(name, fn) {
  const bag = mkBag();
  console.log("\n### 场景 " + name);
  try { await fn(bag); } catch (e) {
    check(bag, "场景执行", false, String(e.message).slice(0, 200), "无异常");
  }
  results.push({ scene: name, pass: bag.fail === 0, lines: bag.lines });
  console.log(bag.lines.join("\n"));
  console.log((bag.fail === 0 ? "[PASS] " : "[FAIL] ") + name);
}

let pageUrl = "";
// boot 竞态护栏（nav_probe 同款）：先等旧文档全局消失，再等新 boot
async function freshBoot() {
  const dl = Date.now() + 15000;
  while (Date.now() < dl) {
    let booted = false;
    try { booted = !!(await evaluate("!!window.__cxbootted")); } catch (_) { booted = false; }
    if (!booted) break;
    await sleep(150);
  }
  await wait("window.__cxbootted && window.__m3b", 60000);
  await sleep(400);   // boot 尾部落定（reconcile/刷新任务表）
}
function navUrl(s) {
  return `http://127.0.0.1:${webPort}/index.html?cxprobe=1&cxscan=0&cxport=${s.port}&cxtoken=${s.token}`;
}
async function goto(srvNew) {
  await send("Page.navigate", { url: navUrl(srvNew) });
  await freshBoot();
  // boot 后默认在计划屏（URL 无 hash）；卡片在队列屏——统一切过去再断言
  await evaluate(`(function(){document.querySelector('.side-item[data-scr="queue"]').click(); return 1;})()`);
  await sleep(250);
}

let webPort = 1430;

// ---------- J1：幽灵任务自愈（造 running → kill → 重启 → 已中断） ----------
async function j1() {
  await scene("J1 幽灵任务：running 卡跨 server 重启 → 已中断（重启前未完成）", async (bag) => {
    await goto(srv);
    // 1) 真 /solve 假题流建卡（solve=false 只领卷不触模型；自造题 ref=probe）
    await evaluate(`window.__m3b.solveFetchOnly("probe:g1")`);
    await wait(`Object.keys(window.__m2.store.jobs).includes("probe:g1")`, 20000);
    const mk = await evaluate(`(function(){
      var s=window.__m2.store; s.jobs["probe:g1"].state="running"; s.emit("jobs");
      document.querySelector('.side-item[data-scr="queue"]').click();
      return Object.keys(s.jobs);
    })()`);
    check(bag, "探针 job 建卡且标 running（模拟重启前状态）", mk.includes("probe:g1"), mk, '含 "probe:g1"');
    const before = await evaluate(`(function(){
      var c=[].slice.call(document.querySelectorAll(".job-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g1")===0;});
      return c? {pill:c.querySelector(".st").textContent, cls:c.querySelector(".st").className,
                 ls:JSON.parse(localStorage.getItem("cx.jobs")||"{}")["probe:g1"].state} : null;
    })()`);
    check(bag, "重启前外观：pill=running + .run 类 + localStorage=running",
      before && before.pill === "running" && /\brun\b/.test(before.cls) && before.ls === "running",
      before, "running/run/running");
    // 2) kill 探针 server（模拟 Tauri 重启的进程换代）→ 3) 重启
    await killServer(srv);
    srv = await startServer();
    await goto(srv);
    const jobs = await httpApi(srv, "/jobs");
    const gone = !(jobs.json.jobs || []).some((x) => x.job_id === "probe:g1");
    check(bag, "新 server GET /jobs 里不存在 probe:g1", jobs.status === 200 && gone,
      jobs.json.jobs.map((x) => x.job_id), "[]（无 probe:g1）");
    const after = await evaluate(`(function(){
      var c=[].slice.call(document.querySelectorAll(".job-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g1")===0;});
      if(!c) return {card:false};
      return {card:true, pill:c.querySelector(".st").textContent, cls:c.querySelector(".st").className,
              note:c.querySelector(".jnote").textContent, noteShown:c.querySelector(".jnote").style.display!=="none",
              hasDel:!!c.querySelector(".card-del"),
              ls:JSON.parse(localStorage.getItem("cx.jobs")||"{}")["probe:g1"].state,
              log:(window.__m2.store.log.map(function(x){return x.msg;})).filter(function(m){return m.indexOf("对账")>=0;}).slice(-1)[0]};
    })()`);
    check(bag, "自愈断言：pill=已中断、无 .run 运行外观",
      after && after.card && after.pill === "已中断" && !/\brun\b/.test(after.cls),
      after && { pill: after.pill, cls: after.cls }, "已中断 / 非 run");
    check(bag, "文案「已中断（重启前未完成）」可见",
      after && after.noteShown && after.note === "已中断（重启前未完成）",
      after && { note: after.note, shown: after.noteShown }, "已中断（重启前未完成）");
    check(bag, "删除入口在位（.card-del）", after && after.hasDel === true, after && after.hasDel, "true");
    check(bag, "interrupted 态已落 localStorage（重启不反复横跳）", after && after.ls === "interrupted", after && after.ls, "interrupted");
    check(bag, "对账日志存在（「任务对账：…标记已中断」）", after && /标记已中断/.test(after.log || ""), after && after.log, "含「标记已中断」");
  });
}

// ---------- J1b：对账失败不误判（R1 第2条：网络/401/428 → 保持原样） ----------
// 说明：/jobs 是纯内存端点、无凭据也返回 200 空表——「新 server 确实没有该 job」被标
// interrupted 是 J1 的正确行为，空 DATA 服务器构造不出对账失败态。失败分支用页内
// 动态 import 同一 api.js 模块实例、临时替换 net.api 抛 status=428（b1b 决定论同法），
// 直接驱动 store.reconcileJobs 的 catch 路径；跑完即还原。
async function j1b() {
  await scene("J1b 对账失败不误判：net 428 → 卡片保持 running + 日志「对账跳过」+ 不清空", async (bag) => {
    // 再造一张 running 卡 probe:g1b（g1 此时已是 interrupted，两卡分开断言互不污染）
    await evaluate(`window.__m3b.solveFetchOnly("probe:g1b")`);
    await wait(`(function(){var j=window.__m2.store.jobs["probe:g1b"]; if(!j) return false; j.state="running"; window.__m2.store.emit("jobs"); return document.querySelectorAll(".job-card").length===2;})()`, 20000);
    const res = await evaluate(`(async function(){
      var {net} = await import("./api.js");     // ESM 缓存=store 用的同一实例
      var orig = net.api;
      net.api = async function(p){ if(p==="/jobs"){ var e=new Error("simulated"); e.status=428; e.detail="no_credentials"; throw e; }
        return orig.apply(net, arguments); };
      var s=window.__m2.store;
      var r = await s.reconcileJobs();
      var c=[].slice.call(document.querySelectorAll(".job-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g1b")===0;});
      var lg=s.log.map(function(x){return x.msg;}).filter(function(m){return m.indexOf("对账跳过")>=0;});
      var out={rc:r, state:s.jobs["probe:g1b"] && s.jobs["probe:g1b"].state,
              pill:c? c.querySelector(".st").textContent : null,
              runCls:c? /\\brun\\b/.test(c.querySelector(".st").className): false,
              ls:(JSON.parse(localStorage.getItem("cx.jobs")||"{}")["probe:g1b"]||{}).state,
              skipLog:lg.slice(-1)[0]||"", jobsLeft:Object.keys(s.jobs).length};
      net.api = orig;                            // 还原——后续场景走真实链路
      return out;
    })()`);
    check(bag, "reconcileJobs 返回 {ok:false, kept}（未做任何标记）",
      res.rc && res.rc.ok === false && res.rc.kept >= 2, res.rc, "ok:false/kept≥2");
    check(bag, "状态未被误标：state=running / pill=running / .run 外观在",
      res.state === "running" && res.pill === "running" && res.runCls,
      { state: res.state, pill: res.pill, runCls: res.runCls }, "running/running/true");
    check(bag, "localStorage 未被改写（仍 running；不清空不伪造）", res.ls === "running", res.ls, "running");
    check(bag, "记一条「任务对账跳过：无凭据——N 个 job 保持原状态」日志",
      /对账跳过/.test(res.skipLog) && /无凭据/.test(res.skipLog) && /保持原状态/.test(res.skipLog),
      res.skipLog, "含「对账跳过·无凭据·保持原状态」");
    check(bag, "卡片一个没丢（jobsLeft=2）", res.jobsLeft === 2, res.jobsLeft, "2");
  });
}

// ---------- J2：真取消（/scan 逐课进行中 cancel → 流 cancelled → 立刻再 /scan=200） ----------
// 抗抖动：上游课程列表瞬时为空时 /scan 会 <1s 秒回 done（本轮实测 doneData.works=0，
// 疑为今日多次全量扫描后的风控）——取消窗口不存在≠机制回归：
// 必须先确认真进入逐课迭代（出现 [2/N] 课程 log）再 cancel；秒回则重试至多 4 轮，
// 全部秒回如实 FAIL 并归因上游（同记忆 cx-e2e-env-drift 类，不伪造通过）。
async function j2() {
  await scene("J2 真取消：/scan 逐课中 /cancel → 流收 cancelled → 立即重扫 200（锁已释放）", async (bag) => {
    await killServer(srv);   // 换新干净 server 跑扫描（J1b 不改 server）
    srv = await startServer();
    let trace = null, events = null, attempts = 0, fastDone = null;
    for (let k = 0; k < 4; k++) {
      attempts = k + 1;
      events = [];
      const t0 = Date.now();
      const resp = await fetch(`http://127.0.0.1:${srv.port}/scan`, {
        method: "POST", headers: { "X-CX-Token": srv.token },
      });
      if (k === 0) {
        check(bag, "/scan 起手 200 + event-stream",
          resp.status === 200 && /event-stream/.test(resp.headers.get("content-type") || ""),
          { status: resp.status, ctype: resp.headers.get("content-type") }, "200/event-stream");
      }
      const rd = resp.body.getReader(); const dec = new TextDecoder("utf-8");
      let buf = "";
      (async () => {
        for (;;) {
          const { done: eof, value } = await rd.read();
          if (eof) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, i); buf = buf.slice(i + 2);
            for (const ln of frame.split("\n")) {
              if (!ln.startsWith("data: ")) continue;
              try { events.push({ t: Date.now() - t0, ...JSON.parse(ln.slice(6)) }); } catch (_) { }
            }
          }
        }
      })().catch(() => { });
      // 真进入逐课（第 2 门 log）才造取消窗口；≤10s 内直接 done/error → 重试
      const dl = Date.now() + 10000;
      let inScan = false, term = null;
      while (Date.now() < dl) {
        if (events.some((e) => e.type === "log" && /\[2\/\d+\]/.test(String(e.msg)))) { inScan = true; break; }
        term = events.find((e) => e.type === "done" || e.type === "error");
        if (term) break;
        await sleep(200);
      }
      if (!inScan) { fastDone = term || "timeout"; await sleep(1500); continue; }
      await sleep(1500);   // 逐课进行中再等一拍（任务书「3s 后」量级）
      const c = await httpApi(srv, "/cancel", { mode: "scan" });
      check(bag, `POST /cancel {mode:scan} → 200 ok:true cancelled=scan（第${attempts}轮）`,
        c.status === 200 && c.json && c.json.ok === true && c.json.cancelled === "scan",
        c.json, "ok:true");
      if (!(c.json && c.json.ok === true)) { await sleep(1500); continue; }
      const dl2 = Date.now() + 8000;
      while (Date.now() < dl2 && !events.some((e) => e.type === "error")) await sleep(150);
      const err = events.find((e) => e.type === "error");
      check(bag, "流内收到 {\"type\":\"error\",\"msg\":\"cancelled\"}",
        !!err && err.msg === "cancelled",
        err && { 距cancel毫秒: err.t, msg: err.msg }, "cancelled（≤8s）");
      const done = events.find((e) => e.type === "done");
      trace = events.map((e) => `${e.t}ms:${e.type}:${String(e.msg || (e.data && JSON.stringify(e.data).slice(0, 80)) || "")}`);
      check(bag, "无 done 事件（非「跑完再退」——是真中止）", !done,
        { done: !!done, doneData: done && done.data, 事件序列: trace.slice(0, 14), 总事件: events.length }, "false");
      break;
    }
    if (!trace) {   // 4 轮全秒回/超时：如实 FAIL，归因上游
      check(bag, "J2 判定", false,
        { 结论: `连续 ${attempts} 轮 /scan 未进入逐课（末轮 ${String(fastDone).slice(0, 120)}）——上游课程列表瞬时为空，取消窗口不存在，非取消机制回归` },
        "至少一轮进入逐课再取消");
      return;
    }
    // 立刻再 /scan：单锁已释放 → 200 非 409
    const r2 = await fetch(`http://127.0.0.1:${srv.port}/scan`, { method: "POST", headers: { "X-CX-Token": srv.token } });
    check(bag, "取消后立刻 POST /scan → 200（单锁已释放，非 409）",
      r2.status === 200 && /event-stream/.test(r2.headers.get("content-type") || ""),
      { status: r2.status }, "200");
    if (r2.body) { try { r2.body.cancel(); } catch (_) { } }
    await httpApi(srv, "/cancel", { mode: "scan" });   // 收尾第二个扫描（其 worker 到下一 emit 中止）
    check(bag, "收尾：第二个扫描亦被取消，busy 回 null（waitIdle ≤8s）", await waitIdle(srv, 8000), "true");
  });
}
const wait2 = async (pred, timeout) => {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) { if (pred()) return; await sleep(200); }
};
// server 空闲（busy=null）确认——幂等/形状断言前置，消除取消收尾与断言的竞态
async function waitIdle(s, timeout = 8000) {
  const dl = Date.now() + timeout;
  while (Date.now() < dl) {
    const j = await httpApi(s, "/jobs");
    if (j.json && j.json.busy === null) return true;
    await sleep(250);
  }
  return false;
}

// ---------- J3：幂等 + /jobs 形状 ----------
async function j3() {
  await scene("J3 幂等：空闲 /cancel → not_running；GET /jobs 形状齐", async (bag) => {
    const c = await httpApi(srv, "/cancel", { mode: "scan" });
    check(bag, "无任务在跑 POST /cancel scan → 200 {ok:false, reason:not_running}",
      c.status === 200 && c.json && c.json.ok === false && c.json.reason === "not_running",
      c.json, "not_running（不报错）");
    // 形状断言用真实存在的 job：node 端走同一 /solve 假题流建一个（不触模型/不触提交协议）
    const sv = await fetch(`http://127.0.0.1:${srv.port}/solve`, {
      method: "POST", headers: { "X-CX-Token": srv.token, "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: "probe:g3", solve: false,
        questions: [{ qid: "gq9", type: "single", stem: "2+2 等于几？（探针自造题）",
                      options: { A: "3", B: "4", C: "5", D: "6" }, image_flag: false }],
        ref: { course: "PROBE", title: "探针假作业3", courseId: "probe", classId: "probe",
               cpi: "0", workId: "g3", answerId: "0" } }),
    });
    const svTxt = await sv.text();   // 读尽整条流（done+哨兵后服务端释放）
    check(bag, "node 端 /solve 假题流收束（含 done）", sv.status === 200 && /"done"/.test(svTxt),
      { status: sv.status, 尾: svTxt.slice(-90) }, "200/done");
    const j = await httpApi(srv, "/jobs");
    const g3 = (j.json.jobs || []).find((x) => x.job_id === "probe:g3");
    const shapeOk = (j.json.jobs || []).every((x) =>
      ["job_id", "mode", "state", "progress", "started", "low"].every((k) => k in x));
    check(bag, "GET /jobs 含 probe:g3 且每条六字段齐（job_id/mode/state/progress/started/low）",
      j.status === 200 && !!g3 && shapeOk, g3, "六字段齐");
    check(bag, "probe:g3 mode=solve / state=init（领而未解的真实态）",
      g3 && g3.mode === "solve" && g3.state === "init", g3 && { mode: g3.mode, state: g3.state }, "solve/init");
    check(bag, "GET /jobs busy=null（空闲）", j.json.busy === null, j.json.busy, "null");
  });
}

// ---------- J4：删除按钮（×→确认→先取消/直删→同步清理） ----------
async function j4() {
  await scene("J4 删除：running 卡先 /cancel 再删；interrupted 卡直删；localStorage/徽标同步", async (bag) => {
    await goto(srv);   // 重新 boot：probe:g1b 在 server 上已失（J2 重启后）→ 对账回 interrupted；
                       // probe:g1 同理。再造一张 running 卡 probe:g2（服务器有 job→直删路径反例由 g2 的 /cancel 计数验证）
    await evaluate(`window.__m3b.solveFetchOnly("probe:g2")`);
    await wait(`Object.keys(window.__m2.store.jobs).includes("probe:g2")`, 20000);
    await evaluate(`(function(){var s=window.__m2.store; s.jobs["probe:g2"].state="running"; s.emit("jobs");
      window.__cancelCalls=[]; var of=window.fetch;
      window.fetch=function(u,init){ try{ if(String(u).indexOf("/cancel")>=0) window.__cancelCalls.push((init&&init.body)||"?"); }catch(_){}; return of.apply(this,arguments); };
      document.querySelector('.side-item[data-scr="queue"]').click(); return 1;})()`);
    await sleep(300);
    const badge0 = await evaluate(`document.querySelector('[data-scr="queue"] .side-badge').textContent`);
    const n0 = await evaluate(`document.querySelectorAll(".job-card").length`);
    check(bag, "删前：3 卡（g1/g1b interrupted + g2 running），徽标=3",
      Number(badge0) === 3 && n0 === 3, { badge: badge0, cards: n0 }, "3/3");
    // —— running 卡 g2：×→确认
    await evaluate(`(function(){var c=[].slice.call(document.querySelectorAll(".job-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g2")===0;}); c.querySelector(".card-del").click(); return 1;})()`);
    await wait(`!document.getElementById("modal-mask").hidden && /删除任务卡/.test(document.querySelector(".dlg-title").textContent)`, 8000);
    const dlg = await evaluate(`(function(){var b=document.getElementById("modal-box");
      return {title:b.querySelector(".dlg-title").textContent, yes:b.querySelector("[data-x=yes]").textContent,
              body:b.querySelector(".dlg-body").textContent};})()`);
    check(bag, "二次确认弹窗（同 M3 §I 风格：标题=删除任务卡，按钮=确认删除）",
      dlg.title === "删除任务卡" && dlg.yes === "确认删除" && /POST \/cancel/.test(dlg.body),
      { title: dlg.title, yes: dlg.yes, 提示含取消: /POST \/cancel/.test(dlg.body) }, "删除任务卡/确认删除/true");
    await evaluate(`(function(){document.querySelector("#modal-box [data-x=yes]").click(); return 1;})()`);
    await wait2(() => false, 900);   // 等删除流程（cancel→remove→渲染）
    const g2 = await evaluate(`(function(){
      var s=window.__m2.store;
      return {card:!![].slice.call(document.querySelectorAll(".job-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g2")===0;}),
              mem:!!s.jobs["probe:g2"],
              ls:!!(JSON.parse(localStorage.getItem("cx.jobs")||"{}")["probe:g2"]),
              cancelCalls:window.__cancelCalls.length, cancelBody:window.__cancelCalls[0]||"",
              badge:document.querySelector('[data-scr="queue"] .side-badge').textContent,
              cards:document.querySelectorAll(".job-card").length};
    })()`);
    check(bag, "running 卡删除前真发了 POST /cancel {mode:solve,job_id:probe:g2}",
      g2.cancelCalls === 1 && /"mode":"solve"/.test(g2.cancelBody) && /probe:g2/.test(g2.cancelBody),
      { 次数: g2.cancelCalls, body: String(g2.cancelBody).slice(0, 90) }, "1 次/mode=solve");
    check(bag, "g2 删除生效：DOM 无卡、内存无、localStorage 无",
      g2.card === false && g2.mem === false && g2.ls === false,
      { DOM: g2.card, mem: g2.mem, ls: g2.ls }, "全 false");
    check(bag, "徽标同步 -1（3→2）", Number(g2.badge) === 2 && g2.cards === 2, { badge: g2.badge, cards: g2.cards }, "2/2");
    // —— interrupted 卡 g1：×→确认（不得发 /cancel，直接删）
    await evaluate(`(function(){var c=[].slice.call(document.querySelectorAll(".job-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g1")===0;}); c.querySelector(".card-del").click(); return 1;})()`);
    await wait(`!document.getElementById("modal-mask").hidden && /删除任务卡/.test(document.querySelector(".dlg-title").textContent)`, 8000);
    await evaluate(`(function(){document.querySelector("#modal-box [data-x=yes]").click(); return 1;})()`);
    await wait2(() => false, 900);
    const g1 = await evaluate(`(function(){
      var s=window.__m2.store;
      return {gone:!s.jobs["probe:g1"],
              ls:!(JSON.parse(localStorage.getItem("cx.jobs")||"{}")["probe:g1"]),
              cancelCalls:window.__cancelCalls.length,
              badge:document.querySelector('[data-scr="queue"] .side-badge').textContent};
    })()`);
    check(bag, "interrupted 卡直删（无 /cancel：计数仍=1）且 localStorage 无",
      g1.gone && g1.ls && g1.cancelCalls === 1, g1, "true/true/1");
    check(bag, "徽标 2→1", Number(g1.badge) === 1, g1.badge, "1");
    // —— 审批屏同样有 ×（同一 deleteJobUI）：最后清空
    await evaluate(`(function(){document.querySelector('.side-item[data-scr="approve"]').click(); return 1;})()`);
    await sleep(300);
    const ap = await evaluate(`(function(){
      var c=[].slice.call(document.querySelectorAll(".ap-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g1b")===0;});
      if(!c) return {found:false};
      var st=c.querySelector(".ap-st");
      return {found:true, pill:st? st.textContent:"", del:!!c.querySelector(".card-del")};
    })()`);
    check(bag, "审批屏卡片：已中断灰 pill + × 入口在位",
      ap.found && ap.pill === "已中断" && ap.del, ap, "已中断/true");
    await evaluate(`(function(){var c=[].slice.call(document.querySelectorAll(".ap-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g1b")===0;}); c.querySelector(".card-del").click(); return 1;})()`);
    await wait(`!document.getElementById("modal-mask").hidden && /删除任务卡/.test(document.querySelector(".dlg-title").textContent)`, 8000);
    await evaluate(`(function(){document.querySelector("#modal-box [data-x=yes]").click(); return 1;})()`);
    await wait2(() => false, 900);
    const fin = await evaluate(`(function(){var s=window.__m2.store;
      document.querySelector('.side-item[data-scr="queue"]').click();
      return {jobs:Object.keys(s.jobs).length, ls:Object.keys(JSON.parse(localStorage.getItem("cx.jobs")||"{}")).length,
              badgeTxt:document.querySelector('[data-scr="queue"] .side-badge').style.display,
              empty:/暂时为空/.test(document.getElementById("queue-list")?document.getElementById("queue-list").textContent:"")};
    })()`);
    check(bag, "全删空：内存0/localStorage0/徽标隐藏/队列空态文案",
      fin.jobs === 0 && fin.ls === 0 && fin.badgeTxt === "none" && fin.empty, fin, "0/0/none/true");
    const jserr = await evaluate(`document.getElementById("jserr").hidden !== false ? document.getElementById("jserr").textContent : ""`);
    check(bag, "全程无页面 JS 错误（#jserr 未点亮）", jserr === "", jserr, "空");
  });
}

// ---------- J5（R5）：重启自动扫描与自愈并存——扫描发起前幽灵已标 interrupted ----------
async function j5() {
  await scene("J5 R5 时序：boot→对账(interrupted)→才自动扫描（日志序+并存在证）", async (bag) => {
    // J4 已清空；造一张 running 幽灵卡 probe:g5（server 上该 job 流已结束但 busy 空闲且 finished 已置→
    // /jobs 报 init——为制造「server 无此 job」的重启态，kill+restart 换新进程）
    await evaluate(`window.__m3b.solveFetchOnly("probe:g5")`);
    await wait(`(function(){var j=window.__m2.store.jobs["probe:g5"]; if(!j) return false; j.state="running"; window.__m2.store.emit("jobs"); return true;})()`, 20000);
    await killServer(srv);
    srv = await startServer();
    // 清空历史日志（内存+localStorage）——否则 boot 的 hydrateLog 回放旧行，日志序断言失真
    await evaluate(`(function(){try{localStorage.removeItem("cx.log");}catch(_){ } window.__m2.store.log.length=0; return 1;})()`);
    // 不带 cxscan=0 → boot 尾分支 `if (!d.count && q.get("cxscan") !== "0") await bootScan()` 真触发
    await send("Page.navigate", { url:
      `http://127.0.0.1:${webPort}/index.html?cxprobe=1&cxport=${srv.port}&cxtoken=${srv.token}` });
    await freshBoot();
    // boot 尾分支 refreshTasks→bootScan 异步启动：等「开始全量扫描」进日志再取证（防时序误采）
    try { await wait("(function(){var lg=window.__m2.store.log; for(var i=0;i<lg.length;i++){if(/开始全量扫描/.test(lg[i].msg)) return true;} return false;})()", 20000); } catch (_) { }
    const st = await evaluate(`(function(){
      var s=window.__m2.store;
      var lg=s.log.map(function(x){return x.msg;});
      var iRec=-1, iScan=-1;
      for(var i=0;i<lg.length;i++){
        if(iRec<0 && /任务对账/.test(lg[i])) iRec=i;
        if(/开始全量扫描/.test(lg[i])) { iScan=i; break; }
      }
      document.querySelector('.side-item[data-scr="queue"]').click();
      var c=[].slice.call(document.querySelectorAll(".job-card")).find(function(x){return x.querySelector(".jt").textContent.indexOf("probe:g5")===0;});
      return {iRec:iRec, iScan:iScan, recLog:lg[iRec]||"",
              pill:c? c.querySelector(".st").textContent : null,
              runCls:c? /\\brun\\b/.test(c.querySelector(".st").className) : false};
    })()`);
    check(bag, "自动扫描确实触发（日志「开始全量扫描」在位）", st.iScan >= 0, { iScan: st.iScan }, "≥0");
    check(bag, "R5 时序：对账日志先于扫描发起（idx 对账<idx 扫描）",
      st.iRec >= 0 && st.iScan >= 0 && st.iRec < st.iScan, { 对账idx: st.iRec, 扫描idx: st.iScan }, "对账<扫描");
    check(bag, "扫描进行中幽灵卡非运行外观：pill=已中断（且与扫描并存不混淆）",
      st.pill === "已中断" && st.runCls === false, { pill: st.pill, runCls: st.runCls }, "已中断/false");
    // 清理：取消这场扫描；上游秒回场景（课程列表瞬时为空）扫描已自然完成→
    // not_running 也算收尾干净（以日志「扫描完成」为证，不伪造取消结果）
    const c = await httpApi(srv, "/cancel", { mode: "scan" });
    const finLog = await evaluate(`(function(){var lg=window.__m2.store.log.map(function(x){return x.msg;});
      for(var i=lg.length-1;i>=0;i--){if(/扫描完成|扫描已取消/.test(lg[i])) return lg[i];} return "";})()`);
    check(bag, "收尾干净：/cancel ok:true 或扫描已自然完成（日志在证）",
      (c.json && c.json.ok === true) || /扫描完成/.test(finLog),
      { cancel: c.json && { ok: c.json.ok, reason: c.json.reason }, 收场日志: finLog }, "true 或 扫描完成");
    check(bag, "收尾 busy 回 null", await waitIdle(srv, 8000), "true");
  });
}

// ---------- 主流程 ----------
async function main() {
  console.log("[setup] 出证目录 " + OUT);
  srv = await startServer();
  console.log("[setup] server 就绪 port=" + srv.port);
  let webSrv;
  try { webSrv = await startStatic(1430); }
  catch (e) { if (e.code === "EADDRINUSE") { webSrv = await startStatic(1420); webPort = 1420; } else throw e; }
  console.log("[setup] 静态服务 http://127.0.0.1:" + webPort);

  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run",
    `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
    "--window-size=1280,860", "--force-device-scale-factor=1",
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
  if (!wsUrl) throw new Error("CDP 端点未出现（edge 启动失败？）");
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

  await j1();
  await j1b();
  await j2();
  await j3();
  await j4();
  await j5();

  const passed = results.filter((r) => r.pass).length;
  console.log("\n============================================================");
  for (const r of results) console.log((r.pass ? "PASS  " : "FAIL  ") + r.scene);
  console.log("JOBS: " + passed + "/" + results.length + " PASS");
  fs.writeFileSync(path.join(OUT, "jobs.json"), JSON.stringify({
    meta: { when: new Date().toISOString(), webOrigin: "http://127.0.0.1:" + webPort },
    results,
  }, null, 1), "utf-8");
  console.log("[out] 证据已落 " + path.join(OUT, "jobs.json"));

  try { await send("Browser.close"); } catch (_) { }
  await Promise.race([new Promise((r) => edge.on("exit", r)), sleep(8000)]);
  killAll();
  process.exit(passed === results.length ? 0 : 1);
}

main().catch((e) => { console.error("JOBS-FAIL: " + e.message); process.exit(2); });
