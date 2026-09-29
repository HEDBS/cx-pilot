// tools/nav_probe.mjs —— M3 任务书 §五：I2/I3 侧栏断言 + B2/B3/I1 设置面取证（Edge+CDP，承 anim_probe 模式）
// 链路：spawn `python -m server --port 0`（解析 CX_READY）→ 静态服务挂 shell/src（1430→1420 回落，
//       均 CORS 白名单内 origin，server 零改动）→ headless Edge ?cxport&cxtoken&cxscan=0 →
//       页内 CDP evaluate 采样（真实 UI click/合成 pointer 驱动，不绕代码路径）。
// 场景：N1 I3 指示器 translateY 450ms emphasized + 对齐选中项；
//       N2 I2 折叠 width 450ms + 「文字先淡出再收宽」 + #main 同步补位；
//       N2p 刷新持久化恢复（无动画）+ 展开（文字后淡入）；
//       N2r prefers-reduced-motion 直接切换无过渡；
//       N3 设置屏 B3 提示位置 / B2 新增+删除识图后端 / I1 拖动排序 → 真 POST /settings
//          → node 侧 GET 复核写回顺序与新增项 → 恢复原始盘态（不留测试数据）。
// 纪律：真课名/账号绝不进 JSON/stdout；OUT 目录每次全新。
// 用法：node tools/nav_probe.mjs [--out DIR] [--port 9344] [--python python] [--edge PATH]
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const arg = (k, d = "") => { const i = process.argv.indexOf("--" + k); return i > 0 ? process.argv[i + 1] : d; };
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "shell", "src");
const OUT = arg("out", path.join(os.tmpdir(), "cx-pilot-nav-" + process.pid));
const CDP_PORT = Number(arg("port", "9344"));
const PROFILE = path.join(OUT, "edge-profile");
const EDGE = arg("edge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe");
const PY = arg("python", "python");

fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const easeOk = (s) => /^cubic-bezier\(\s*0\.65,\s*0(\.0+)?,\s*0\.35,\s*1(\.0+)?\s*\)$/.test(String(s));
const near = (a, b, tol) => Math.abs(a - b) <= tol;

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
async function wait(expr, timeout, step = 400) {
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

// ---------- server 子进程 ----------
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
    p.on("exit", (c) => reject(new Error("server 提前退出 exit=" + c + "（见 server.log）")));
  });
}
let srv = { port: 0, token: "" };
async function httpApi(p, body) {
  const init = body
    ? { method: "POST", headers: { "Content-Type": "application/json", "X-CX-Token": srv.token }, body: JSON.stringify(body) }
    : { headers: { "X-CX-Token": srv.token } };
  const r = await fetch(`http://127.0.0.1:${srv.port}${p}`, init);
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error(p + " HTTP " + r.status + " " + JSON.stringify(j).slice(0, 160));
  return j;
}

// ---------- 静态服务（挂 shell/src；仅回环，1430→1420 回落） ----------
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

// ---------- 页内公共前导（sleep + 动画取证器） ----------
const PRE = `
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const animsOf = (el) => el.getAnimations().map((a) => {
    const t = a.effect.getTiming();
    let kf = null;
    try { kf = a.effect.getKeyframes().map((k) => String(k.transform || k.computedStyle || "")); } catch (_) { }
    return { ctor: a.constructor.name, prop: (a.transitionProperty || null), dur: t.duration, ease: t.easing, kf: kf };
  });
  const R1 = (v) => Math.round(v * 10) / 10;
  const rectOf = (el) => { const r = el.getBoundingClientRect(); return { l: R1(r.left), t: R1(r.top), w: R1(r.width), h: R1(r.height) }; };
`;
const pexec = (fns) => `(async function(){ ${PRE} ${fns} })()`;

// ---------- 断言框架（同 anim_probe） ----------
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
const pick = (arr, pred) => arr.find(pred) || null;
const at = (samples, t) => samples.reduce((a, b) => (Math.abs(b[0] - t) < Math.abs(a[0] - t) ? b : a));

// ---------- N1：I3 指示器滑移 ----------
async function n1() {
  await scene("N1 指示器 translateY 450ms emphasized（点「运行日志」）", async (bag) => {
    const ev = await evaluate(pexec(`
      var pill=document.getElementById("side-pill");
      var items=[].slice.call(document.querySelectorAll(".side-item[data-scr]"));
      var y0=R1(pill.getBoundingClientRect().top);
      items[3].click();
      var anims=animsOf(pill);
      var samples=[]; var t0=performance.now();
      await new Promise((res)=>{var iv=setInterval(function(){
        samples.push([Math.round(performance.now()-t0), R1(pill.getBoundingClientRect().top)]);
        if (performance.now()-t0>650){clearInterval(iv);res();}}, 40)});
      return {y0:y0, anims:anims, samples:samples, pill:rectOf(pill), item:rectOf(document.querySelector(".side-item.active"))};
    `));
    const a = pick(ev.anims, (x) => x.ctor === "Animation" && near(x.dur, 450, 20) && easeOk(x.ease)
      && (x.kf || []).some((k) => /matrix|translate/i.test(k)));
    check(bag, "getAnimations() 命中 transform 动画 450±20 emphasized", !!a,
      a && { ctor: a.ctor, dur: a.dur, ease: a.ease }, "Animation@450 + 曲线对");
    const ys = ev.samples.map((s) => s[1]);
    const lo = Math.min(ev.y0, ys[ys.length - 1]), hi = Math.max(ev.y0, ys[ys.length - 1]);
    const moved = hi - lo;
    check(bag, "确有滑移（起→终 ≥20px，非原地）", moved >= 20, { y0: ev.y0, yEnd: ys[ys.length - 1] }, "≥20");
    const inside = ys.filter((y) => y > lo + 3 && y < hi - 3).length;
    check(bag, "轨迹连续：≥3 个采样点处于起终之间（禁瞬移）", inside >= 3, { 中间态点数: inside, 全轨迹: [ev.y0, ...ys.slice(0, 6), "…", ys[ys.length - 1]] }, "≥3");
    let maxStep = 0;
    for (let i = 1; i < ys.length; i++) maxStep = Math.max(maxStep, Math.abs(ys[i] - ys[i - 1]));
    check(bag, "无单帧跳变（步进 ≤0.4×全程）", maxStep <= 0.4 * moved + 1.5, maxStep, "≤" + (0.4 * moved + 1.5).toFixed(1));
    check(bag, "终态贴合选中项（top/left/width ±1.5px）",
      near(ev.pill.t, ev.item.t, 1.5) && near(ev.pill.l, ev.item.l, 1.5) && near(ev.pill.w, ev.item.w, 1.5),
      { pill: ev.pill, item: ev.item }, "三值全贴合");
    const act = await evaluate(`getComputedStyle(document.querySelector(".side-item.active")).backgroundColor`);
    check(bag, "选中底色=指示器提供（.active 本体背景透明，非逐项切背景）", /rgba\(0, 0, 0, 0\)|transparent/.test(act), act, "透明");
  });
}

// ---------- N2：I2 折叠 ----------
async function n2() {
  await scene("N2 折叠：width 450ms + 文字先淡出再收宽 + #main 同步补位", async (bag) => {
    const ev = await evaluate(pexec(`
      var sb=document.getElementById("sidebar"); var fold=document.getElementById("side-fold");
      var w0=R1(sb.getBoundingClientRect().width);
      var lbl=document.querySelector('.side-item[data-scr="plan"] .lbl');
      var itemsBefore=[].slice.call(document.querySelectorAll(".side-item[data-scr]"))
        .map(function(it){return [it.dataset.scr, R1(it.getBoundingClientRect().top)];});
      fold.click();
      var anims=animsOf(sb);
      var samples=[]; var t0=performance.now();
      await new Promise((res)=>{var iv=setInterval(function(){
        samples.push([Math.round(performance.now()-t0), R1(sb.getBoundingClientRect().width),
                      R1(document.getElementById("main").getBoundingClientRect().left),
                      getComputedStyle(lbl).opacity]);
        if (performance.now()-t0>850){clearInterval(iv);res();}}, 45)});
      var itemsAfter=[].slice.call(document.querySelectorAll(".side-item[data-scr]"))
        .map(function(it){return [it.dataset.scr, R1(it.getBoundingClientRect().top)];});
      return {w0:w0, anims:anims, samples:samples, collapsed:sb.classList.contains("collapsed"),
              foldTxt:fold.textContent, itemsBefore:itemsBefore, itemsAfter:itemsAfter};
    `));
    const tw = pick(ev.anims, (x) => x.prop === "width" && near(x.dur, 450, 20) && easeOk(x.ease));
    check(bag, "#sidebar CSSTransition(width) 450±20 emphasized（§0 例外：容器收放允许 width）", !!tw,
      tw && { prop: tw.prop, dur: tw.dur, ease: tw.ease }, "width@450 曲线对");
    const last = ev.samples[ev.samples.length - 1];
    check(bag, "终宽 66±1.5 + collapsed 类 + 按钮变 »", near(last[1], 66, 1.5) && ev.collapsed === true && ev.foldTxt === "»",
      { w: last[1], collapsed: ev.collapsed, txt: ev.foldTxt }, "66/true/»");
    const s150 = at(ev.samples, 150);
    check(bag, "文字先淡出：t≈150ms 时 lbl.opacity<0.6 且宽度未收（>208）", Number(s150[3]) < 0.6 && s150[1] > ev.w0 - 4,
      { t: s150[0], w: s150[1], op: s150[3] }, "op<0.6 且 w≈212");
    check(bag, "终态文字 opacity=0", Number(last[3]) === 0, last[3], "0");
    const sync = ev.samples.every((s) => near(s[2], s[1], 1.5));
    const mono = ev.samples.every((s, i) => i === 0 || s[1] <= ev.samples[i - 1][1] + 0.6);
    check(bag, "#main 同步补位（每采样 left==侧栏宽 ±1.5）且单调收", sync && mono,
      { 失同步点数: ev.samples.filter((s) => !near(s[2], s[1], 1.5)).length, 单调: mono }, "0/true");
    // 图标位置不变（用户反馈修复的永久护栏）：折叠后每个 .side-item 的 top 必须与折叠前一致。
    // 起因：.side-sec「系统/后端」在 66px 宽下换行成两行，把下方项顶下 ~15px → 图标位移+#side-pill 偏移。
    const drift = ev.itemsBefore.map((b, i) => ({ scr: b[0], dy: Math.round(((ev.itemsAfter[i] || [null, NaN])[1] - b[1]) * 10) / 10 }));
    const maxDrift = Math.max(...drift.map((d) => Math.abs(d.dy)));
    check(bag, "折叠后图标位置不变：每个 .side-item top 位移 ≤0.5px（用户反馈 bug 护栏）",
      maxDrift <= 0.5, { 各项dy: drift }, "全部 ≤0.5");
  });
}

// ---------- N2p：持久化 + 展开 ----------
let pageUrl = "";
// 竞态护栏：Page.navigate 返回时旧文档可能仍存活（其全局 __cxbootted 仍为 true），
// 直接轮询真值=测旧文档假过。先等旧全局消失（换文档），再等新 boot。
async function freshBoot() {
  const dl = Date.now() + 15000;
  while (Date.now() < dl) {
    let booted = false;
    try { booted = !!(await evaluate("!!window.__cxbootted")); } catch (_) { booted = false; }
    if (!booted) break;
    await sleep(150);
  }
  await wait("window.__cxbootted && document.getElementById('side-fold')", 60000);
}
async function n2p() {
  await scene("N2p 刷新持久化（无动画恢复）+ 展开（文字后淡入）", async (bag) => {
    await send("Page.navigate", { url: pageUrl });
    await freshBoot();
    await sleep(350);
    const restored = await evaluate(pexec(`
      var sb=document.getElementById("sidebar");
      return {w:R1(sb.getBoundingClientRect().width), collapsed:sb.classList.contains("collapsed"),
              running:sb.getAnimations().length, ls:localStorage.getItem("cx.sidefold")};
    `));
    check(bag, "刷新后直接呈折叠态 66±1.5、无在跑过渡、localStorage=1",
      near(restored.w, 66, 1.5) && restored.collapsed && restored.running === 0 && restored.ls === "1",
      restored, "66/true/0/'1'");
    const ev = await evaluate(pexec(`
      var sb=document.getElementById("sidebar"); var fold=document.getElementById("side-fold");
      var lbl=document.querySelector('.side-item[data-scr="plan"] .lbl');
      fold.click();
      var anims=animsOf(sb);
      var samples=[]; var t0=performance.now();
      await new Promise((res)=>{var iv=setInterval(function(){
        samples.push([Math.round(performance.now()-t0), R1(sb.getBoundingClientRect().width), getComputedStyle(lbl).opacity]);
        if (performance.now()-t0>850){clearInterval(iv);res();}}, 45)});
      return {anims:anims, samples:samples, ls:localStorage.getItem("cx.sidefold"), foldTxt:fold.textContent};
    `));
    const tw = pick(ev.anims, (x) => x.prop === "width" && near(x.dur, 450, 20) && easeOk(x.ease));
    check(bag, "展开 CSSTransition(width) 450±20 emphasized", !!tw, tw && { dur: tw.dur, ease: tw.ease }, "450 曲线对");
    const last = ev.samples[ev.samples.length - 1];
    check(bag, "回宽 212±2 + ls=0 + 按钮 «", near(last[1], 212, 2) && ev.ls === "0" && ev.foldTxt === "«",
      { w: last[1], ls: ev.ls, txt: ev.foldTxt }, "212/'0'/«");
    const s150 = at(ev.samples, 150);
    check(bag, "宽先动、文字后淡入（t≈150ms opacity<0.3；终态=1）", Number(s150[2]) < 0.3 && Number(last[2]) === 1,
      { t150: s150[2], end: last[2] }, "<0.3 / 1");
  });
}

// ---------- N2r：reduced-motion ----------
async function n2r() {
  await scene("N2r prefers-reduced-motion：直接切换无过渡（含指示器瞬置）", async (bag) => {
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
    await sleep(120);
    const ev = await evaluate(pexec(`
      var sb=document.getElementById("sidebar"); var fold=document.getElementById("side-fold");
      var pill=document.getElementById("side-pill");
      fold.click(); await sleep(130);
      var wA=R1(sb.getBoundingClientRect().width), runA=sb.getAnimations().length;
      document.querySelector('.side-item[data-scr="queue"]').click(); await sleep(60);
      var pillQueue=near2(pill.getBoundingClientRect().top, document.querySelector('.side-item.active').getBoundingClientRect().top, 1.5);
      var runP=pill.getAnimations().filter(function(a){return a.playState==="running";}).length;
      fold.click(); await sleep(130);
      return {wA:wA, runA:runA, wB:R1(sb.getBoundingClientRect().width), tdur:getComputedStyle(sb).transitionDuration, pillQueue:pillQueue, runP:runP};
      function near2(a,b,t){return Math.abs(a-b)<=t;}
    `));
    check(bag, "折叠 130ms 内即到 66±1.5、无在飞过渡", near(ev.wA, 66, 1.5) && ev.runA === 0,
      { w: ev.wA, running: ev.runA }, "66 / 0");
    check(bag, "再点到 212±1.5 且 computed transition-duration=0s",
      near(ev.wB, 212, 1.5) && /0s/.test(String(ev.tdur)) && !/\d+m/.test(String(ev.tdur).replace(/0s/g, "")),
      { w: ev.wB, tdur: ev.tdur }, "212 / '0s'");
    check(bag, "reduce 下指示器瞬置（贴合 active、无 running 动画）", ev.pillQueue === true && ev.runP === 0,
      { 贴合: ev.pillQueue, running: ev.runP }, "true / 0");
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });
    await evaluate(pexec(`document.querySelector('.side-item[data-scr="plan"]').click(); return 1;`));
    await sleep(300);
  });
}

// ---------- N3：设置屏 B3 / B2 / I1 + 写回复核 + 恢复盘态 ----------
async function n3() {
  await scene("N3 设置屏 B3 提示 / B2 新增+删除识图后端 / I1 拖动排序（含 /settings 写回与盘态恢复）", async (bag) => {
    const orig = await httpApi("/settings");   // 盘态快照（最后据此恢复）
    const minCfg = (c) => {
      const o = { kind: c.kind, name: c.name, key_ref: c.key_ref || "", enabled: !!c.enabled, model: c.model || "" };
      if (c.base_url !== undefined) o.base_url = c.base_url;
      return o;
    };
    const origP = (orig.settings.providers || []).map(minCfg);
    const origV = (orig.settings.vision_providers || []).map(minCfg);

    await evaluate(pexec(`document.querySelector('.side-item[data-scr="settings"]').click(); await sleep(700); return 1;`));

    // B3：placeholder 只留脱敏值；提示走独立 .hint-inline
    const b3 = await evaluate(pexec(`
      var rows=[].slice.call(document.querySelectorAll('.set-group[data-grp="providers"] .set-row'));
      var withKey=null, noKey=null;
      rows.forEach(function(r){
        var masked=r.querySelector(".masked").textContent; var key=r.querySelector(".key");
        var hint=r.querySelector(".hint-inline");
        if (masked!=="—" && !withKey) withKey={masked:masked, ph:key.placeholder, val:key.value,
          hint: hint? {t:hint.textContent, hidden:hint.hasAttribute("hidden")} : null};
        if (masked==="—" && !noKey) noKey={ph:key.placeholder, hint: hint? {t:hint.textContent, hidden:hint.hasAttribute("hidden")} : null};
      });
      return {count:rows.length, withKey:withKey, noKey:noKey};
    `));
    check(bag, "B3 有脱敏值行：placeholder=脱敏值本体（不含「留空=」字样）",
      b3.withKey && b3.withKey.ph === b3.withKey.masked && !/留空/.test(b3.withKey.ph),
      b3.withKey && { ph: b3.withKey.ph, masked: b3.withKey.masked }, "ph===masked 且无「留空」");
    check(bag, "B3 独立小字提示存在且可见：span.hint-inline=「留空=不修改」",
      b3.withKey && b3.withKey.hint && b3.withKey.hint.t === "留空=不修改" && b3.withKey.hint.hidden === false,
      b3.withKey && b3.withKey.hint, "文本对 + 未 hidden");
    check(bag, "B3 无 key 行：placeholder=粘贴新 key，提示隐藏（脱敏值绝不回传：input.value 恒空）",
      b3.noKey && b3.noKey.ph === "粘贴新 key" && b3.noKey.hint && b3.noKey.hint.hidden === true && b3.withKey.val === "",
      b3.noKey && { ph: b3.noKey.ph, hintHidden: b3.noKey.hint.hidden, val: b3.withKey.val }, "'粘贴新 key'/true/''");

    // B2：空列表 → 提示 + 「+ 新增后端」预设模板（不含 key）；可编辑/删除
    const b2 = await evaluate(pexec(`
      var box=document.querySelector('.set-group[data-grp="vision_providers"]');
      var before=box.querySelectorAll('.set-row').length;
      var emptyShown=!!box.querySelector('.vp-empty');
      box.querySelector('.vp-add').click(); await sleep(60);
      var menu=[].slice.call(box.querySelectorAll('.pm-item'));
      var menuTxt=menu.map(function(b){return b.dataset.p;});
      menu.find(function(b){return b.dataset.p==="siliconflow";}).click(); await sleep(60);
      var rows=[].slice.call(box.querySelectorAll('.set-row'));
      var added=null;
      if (rows.length===before+1){ var r=rows[before];
        added={nm:r.querySelector('.nm').textContent, kind:r.querySelector('.kind').textContent,
               burl:r.querySelector('.burl').value, model:r.querySelector('.model').value, key:r.querySelector('.key').value};
      }
      box.querySelector('.vp-add').click(); await sleep(40);
      var rows2=[].slice.call(box.querySelectorAll('.set-row'));
      var orRow=null; if (rows2.length===before+1){ box.querySelector('.pm-item[data-p="openrouter"]').click(); await sleep(40);
        rows2=[].slice.call(box.querySelectorAll('.set-row')); orRow=rows2[rows2.length-1].querySelector('.nm').textContent;
        rows2[rows2.length-1].querySelector('.del').click(); await sleep(40); }
      return {before:before, emptyShown:emptyShown, menuCount:menu.length, menuTxt:menuTxt, added:added,
              orRow:orRow, afterDel:box.querySelectorAll('.set-row').length};
    `));
    check(bag, "B2 空列表可见提示（不再是空白无入口）", b2.emptyShown || b2.before > 0, { emptyShown: b2.emptyShown, before: b2.before }, "提示或既有项在场");
    check(bag, "B2 预设菜单 ≥4 项（硅基流动/智谱/OpenRouter/自定义）",
      b2.menuCount >= 4 && ["siliconflow", "zhipu", "openrouter", "custom"].every((k) => b2.menuTxt.includes(k)),
      b2.menuTxt, "四键齐");
    check(bag, "B2 新增成功：kind=openai_compat / base_url=硅基流动 / key 留空（模板不含 key）",
      b2.added && /openai_compat/.test(b2.added.kind) && b2.added.burl === "https://api.siliconflow.cn/v1" && b2.added.key === "" && b2.added.model !== "",
      b2.added, "字段齐、key=''");
    check(bag, "B2 删除生效：加 OpenRouter 项→删除→回到新增后计数",
      b2.orRow === "OpenRouter" && b2.afterDel === b2.before + 1, { orRow: b2.orRow, afterDel: b2.afterDel }, `OpenRouter/${b2.before + 1}`);

    // I1：合成 pointer 拖动第 1 行到第 2 行之后（真实 bindDrag 路径），落位动画 450
    const dr = await evaluate(pexec(`
      var box=document.querySelector('.set-group[data-grp="providers"]');
      var names=function(){return [].slice.call(box.querySelectorAll('.set-row')).map(function(r){return r._cfg.name;});};
      var before=names();
      var rows=[].slice.call(box.querySelectorAll('.set-row'));
      var rA=rows[0], rB=rows[1], rC=rows[2];
      var grip=rA.querySelector('.grip'); var gb=grip.getBoundingClientRect();
      var cx=gb.left+gb.width/2, cy=gb.top+gb.height/2;
      var ra=rA.getBoundingClientRect();
      var cb=rB.getBoundingClientRect().top+rB.getBoundingClientRect().height/2;
      var cc=rC.getBoundingClientRect().top+rC.getBoundingClientRect().height/2;
      var dy=(cb+cc)/2-(ra.top+ra.height/2);
      grip.dispatchEvent(new PointerEvent('pointerdown',{pointerId:7,button:0,bubbles:true,cancelable:true,clientX:cx,clientY:cy}));
      for (var y=0;y<dy;y+=12){ grip.dispatchEvent(new PointerEvent('pointermove',{pointerId:7,bubbles:true,clientX:cx,clientY:cy+y})); await sleep(16); }
      grip.dispatchEvent(new PointerEvent('pointermove',{pointerId:7,bubbles:true,clientX:cx,clientY:cy+dy}));
      var followTop=R1(rA.getBoundingClientRect().top);
      var animFollow=[rA].map(function(){return getComputedStyle(rA).transform;});
      grip.dispatchEvent(new PointerEvent('pointerup',{pointerId:7,bubbles:true,cancelable:true,clientX:cx,clientY:cy+dy}));
      var anims=animsOf(rA);
      var after=names();
      await sleep(700);
      return {before:before, after:after, dy:R1(dy), startY:R1(ra.top), followTop:followTop,
              followCss:animFollow[0], anims:anims,
              kindAfter:rA.querySelector('.kind').textContent, nmAfter:rA.querySelector('.nm').textContent};
    `));
    const swapped = dr.after[0] === dr.before[1] && dr.after[1] === dr.before[0]
      && dr.after.slice(2).join() === dr.before.slice(2).join();
    check(bag, "I1 拖动改序：行1 与 行2 互换、其余不动（DOM 序=collect 序）", swapped,
      { before: dr.before.slice(0, 3), after: dr.after.slice(0, 3) }, "前两行互换");
    check(bag, "I1 拖动中跟手：transform 位移≈dy（实测视觉 top ≈ start+dy ±3）",
      near(dr.followTop, dr.startY + dr.dy, 3) && /matrix/.test(dr.followCss || ""),
      { startY: dr.startY, followTop: dr.followTop, dy: dr.dy, css: dr.followCss }, "±3 + matrix");
    const drop = pick(dr.anims, (x) => near(x.dur, 450, 20) && easeOk(x.ease) && (x.kf || []).some((k) => /matrix|translate/i.test(k)));
    check(bag, "I1 落位动画 450±20 emphasized（animateLayout FLIP）", !!drop, drop && { dur: drop.dur, ease: drop.ease }, "450 曲线对");
    check(bag, "I1 行 _cfg 随行保：拖后首行仍=原第二行内容（kind/name 不丢）",
      dr.nmAfter === dr.before[0] && dr.after[1] === dr.nmAfter,
      { nmAfter: dr.nmAfter, afterIdx1: dr.after[1] }, "一致");

    // 保存写回 → node 侧 GET 复核（B2 落盘 / I1 顺序=优先级）
    await evaluate(pexec(`document.getElementById("set-save").click(); await sleep(700); return 1;`));
    const after = await httpApi("/settings");
    const pNames = (after.settings.providers || []).map((c) => c.name);
    check(bag, "写回复核：GET /settings providers 顺序=拖动后 DOM 顺序",
      pNames[0] === dr.after[0] && pNames[1] === dr.after[1], pNames.slice(0, 3), dr.after.slice(0, 3).join(" > "));
    const vEnt = (after.settings.vision_providers || []).find((c) => c.name === "硅基流动");
    check(bag, "写回复核：GET /settings 回显新增 vision_providers 项（无 api_key 字段=没写死 key）",
      !!vEnt && vEnt.base_url === "https://api.siliconflow.cn/v1" && !vEnt.api_key,
      vEnt || "(缺项)", "该项存在且无 key");
    // 拖入链首的是禁用的 Groq（无 key 不在链）——链内容仍 Pollinations > OpenRouter：
    // 该项为真实序断言的护栏（链=数组内「enabled+有key」项的相对顺序）
    const inChainHint = await evaluate(`(function(){var h=document.querySelector("#scr-settings .toolbar .hint");return h?h.textContent:"";})()`);
    check(bag, "在链提示=数组序过滤（Groq 虽在首位但禁用不入链；Pollinations 先于 OpenRouter）",
      inChainHint.includes("在链后端") && inChainHint.indexOf("Pollinations") < inChainHint.indexOf("OpenRouter") && !inChainHint.includes("Groq"),
      inChainHint, "P…OR，无 Groq");

    // 恢复原始盘态（测试不留数据）
    await httpApi("/settings", { settings: { providers: origP, vision_providers: origV } });
    const fin = await httpApi("/settings");
    const finP = (fin.settings.providers || []).map((c) => c.name);
    check(bag, "盘态恢复：providers 序与 vision 列表回到测试前",
      finP.join() === origP.map((c) => c.name).join()
      && (fin.settings.vision_providers || []).length === origV.length,
      { order: finP.slice(0, 2), vlen: (fin.settings.vision_providers || []).length },
      origP.map((c) => c.name).slice(0, 2).join() + " / " + origV.length);
    const jserr = await evaluate(`document.getElementById("jserr").hidden !== false ? document.getElementById("jserr").textContent : ""`);
    check(bag, "全程无页面 JS 错误（#jserr 未点亮）", jserr === "", jserr, "空");
  });
}

// ---------- 主流程 ----------
async function main() {
  console.log("[setup] 出证目录 " + OUT);
  srv = await startServer();
  console.log("[setup] server 就绪 port=" + srv.port + " token=***");
  let webPort = 1430, webSrv;
  try { webSrv = await startStatic(1430); }
  catch (e) { if (e.code === "EADDRINUSE") { webSrv = await startStatic(1420); webPort = 1420; } else throw e; }
  console.log("[setup] 静态服务 http://127.0.0.1:" + webPort);
  pageUrl = `http://127.0.0.1:${webPort}/index.html?cxport=${srv.port}&cxtoken=${srv.token}&cxscan=0`;

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
  await send("Page.navigate", { url: pageUrl });
  await wait("window.__cxbootted && document.getElementById('side-fold')", 60000);
  await sleep(500);   // 握手/version 落定
  console.log("[setup] boot 就绪（cxscan=0 不扫描）");

  await n1();
  await n2();
  await n2p();
  await n2r();
  await n3();

  const passed = results.filter((r) => r.pass).length;
  console.log("\n============================================================");
  for (const r of results) console.log((r.pass ? "PASS  " : "FAIL  ") + r.scene);
  console.log("NAV: " + passed + "/" + results.length + " PASS");
  fs.writeFileSync(path.join(OUT, "nav.json"), JSON.stringify({
    meta: { when: new Date().toISOString(), webOrigin: "http://127.0.0.1:" + webPort, serverPort: srv.port },
    results,
  }, null, 1), "utf-8");
  console.log("[out] 证据已落 " + path.join(OUT, "nav.json"));

  try { await send("Browser.close"); } catch (_) { }
  await Promise.race([new Promise((r) => edge.on("exit", r)), sleep(8000)]);
  killAll();
  process.exit(passed === results.length ? 0 : 1);
}

main().catch((e) => { console.error("NAV-FAIL: " + e.message); process.exit(2); });
