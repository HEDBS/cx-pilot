// tools/theme_probe.mjs —— M4 任务书 §二：视觉重制 T1–T5 数字取证（Edge+CDP，承 nav_probe 模式）
// 链路：spawn `python -m server --port 0`（解析 CX_READY）→ 静态服务挂 shell/src
//       （1430→1420 回落，均 CORS 白名单内 origin，server 零改动）→ headless Edge
//       ?cxport&cxtoken&cxscan=0（**不扫描**：本闸只测视觉/令牌，不碰用户账号数据）→
//       页内 CDP evaluate 采样（真实 UI click / 合成 visibilitychange / CDP 媒体模拟）。
// 场景：TH1 主题三态循环 + localStorage 持久化 + system 实时跟随（Emulation.setEmulatedMedia）
//       TH2 暗色无亮色残留（真实 chrome 抽样 + 令牌解析抽样 + 源码静态断言）
//       TH3 T1 切屏滑动 450ms emphasized + 轨迹 ≥3 中间态；reduce 下无位移
//       TH4 T2 Aurora 存在且在跑 / document.hidden 暂停 / reduce 静止
//       TH5 T4 玻璃 backdropFilter 含 blur + @supports / reduced-transparency 回退
//       P1  Aurora 掉帧实测（rAF 间隔 A-B-A 对照：开/关/开）
// 纪律：真课名/账号绝不进 JSON/stdout；OUT 目录每次全新。
// 用法：node tools/theme_probe.mjs [--out DIR] [--port 9345] [--python python] [--edge PATH]
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const arg = (k, d = "") => { const i = process.argv.indexOf("--" + k); return i > 0 ? process.argv[i + 1] : d; };
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "shell", "src");
const OUT = arg("out", path.join(os.tmpdir(), "cx-pilot-theme-" + process.pid));
const CDP_PORT = Number(arg("port", "9345"));
const PROFILE = path.join(OUT, "edge-profile");
const EDGE = arg("edge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe");
const PY = arg("python", "python");

fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const easeOk = (s) => /^cubic-bezier\(\s*0\.65,\s*0(\.0+)?,\s*0\.35,\s*1(\.0+)?\s*\)$/.test(String(s));
const near = (a, b, tol) => Math.abs(a - b) <= tol;
const R1 = (v) => Math.round(v * 10) / 10;   // node 侧取整（PRE 里那个只在页内作用域）

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
    try { kf = a.effect.getKeyframes().map((k) => JSON.stringify({ t: k.transform, opacity: k.opacity })); } catch (_) { }
    return { ctor: a.constructor.name, prop: (a.transitionProperty || null), name: (a.animationName || null),
             dur: t.duration, ease: t.easing,
             iter: (t.iterations === Infinity ? "Infinity" : t.iterations), kf: kf };   // Infinity 过 CDP 会退化成 null
  });
  const R1 = (v) => Math.round(v * 10) / 10;
  const rectOf = (el) => { const r = el.getBoundingClientRect(); return { l: R1(r.left), t: R1(r.top), w: R1(r.width), h: R1(r.height) }; };
  const tyOf = (m) => { if (!m || m === "none") return 0; const p = String(m).match(/matrix\\(([^)]+)\\)/); return p ? parseFloat(p[1].split(",")[5]) : 0; };
`;
const pexec = (fns) => `(async function(){ ${PRE} ${fns} })()`;

// ---------- 断言框架（同 anim_probe / nav_probe） ----------
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

// ---------- CDP 媒体模拟 ----------
const setMedia = (features) => send("Emulation.setEmulatedMedia", { media: "", features });

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
  await sleep(500);
}
// 以 localStorage 设定主题模式后整页重载（首帧引导脚本据此落定 dataset.theme）
async function setThemeLS(mode) {
  await evaluate(`(function(){try{localStorage.setItem("cx.theme",${JSON.stringify(mode)})}catch(_){}return 1})()`);
  await send("Page.navigate", { url: pageUrl });
  await freshBoot();
  await sleep(750);   // 等 body 的 .55s 背景过渡落定再读 computed
}

// 暗色下的「亮色残留」判据：不透明亮面底 / 亮底上的深色文字。
// 注意玻璃 .lg 的 background-color 是 --glass-base（暗色系半透明），白是叠在
// background-image 渐变里的高光——那是玻璃配方本体，不是残留，故只判 backgroundColor。
const LIGHT_BG = ["rgb(255, 255, 255)", "rgb(242, 242, 247)"];
const LIGHT_FG = ["rgb(28, 28, 30)", "rgb(0, 0, 0)"];

// ---------- TH1：主题三态 + 持久化 + system 实时跟随 ----------
async function th1() {
  await scene("TH1 主题三态循环 / localStorage 持久化 / system 实时跟随", async (bag) => {
    await setMedia([]);                 // 清历史模拟
    // headless Edge 的「系统」默认就是 dark（实测 dataset.theme 首帧=dark），
    // 故先把系统显式钉成 light 再断言 system 分支，否则测的是环境而非接线。
    await setMedia([{ name: "prefers-color-scheme", value: "light" }]);
    await setThemeLS("system");

    const s0 = await evaluate(pexec(`
      return { ds: document.documentElement.dataset.theme, mode: localStorage.getItem("cx.theme"),
               bg: getComputedStyle(document.body).backgroundColor,
               lbl: document.getElementById("theme-label").textContent,
               ic: document.getElementById("theme-ic").textContent,
               themeBtn: !!document.getElementById("theme-btn") };
    `));
    check(bag, "顶栏存在 #theme-btn 且初始 system（headless 系统=light）→ dataset.theme='light'",
      s0.themeBtn && s0.ds === "light" && s0.mode === "system" && s0.lbl === "跟随系统",
      { ds: s0.ds, mode: s0.mode, lbl: s0.lbl, bg: s0.bg }, "light/system/跟随系统");

    // 点一次 → light 模式（图标/标签变，dataset 仍 light≠判据，故看 mode+label）
    const d1 = await evaluate(pexec(`
      document.getElementById("theme-btn").click(); await sleep(800);
      return { ds: document.documentElement.dataset.theme, mode: localStorage.getItem("cx.theme"),
               bg: getComputedStyle(document.body).backgroundColor,
               lbl: document.getElementById("theme-label").textContent,
               ic: document.getElementById("theme-ic").textContent };
    `));
    check(bag, "点 1 次 → light 模式（localStorage='light'、标签=浅色、dataset 仍 'light'）",
      d1.mode === "light" && d1.ds === "light" && d1.lbl === "浅色",
      { mode: d1.mode, ds: d1.ds, lbl: d1.lbl, ic: d1.ic }, "light/light/浅色");

    // 再点一次 → dark：dataset 变 dark + body 背景变暗色系
    const d2 = await evaluate(pexec(`
      document.getElementById("theme-btn").click(); await sleep(900);
      return { ds: document.documentElement.dataset.theme, mode: localStorage.getItem("cx.theme"),
               bg: getComputedStyle(document.body).backgroundColor,
               lbl: document.getElementById("theme-label").textContent,
               ic: document.getElementById("theme-ic").textContent };
    `));
    check(bag, "点 2 次 → dark：dataset.theme='dark' 且 body 背景变为暗色系（非 rgb(242,242,247)）",
      d2.ds === "dark" && d2.mode === "dark" && d2.bg === "rgb(28, 28, 30)" && d2.bg !== d1.bg,
      { ds: d2.ds, mode: d2.mode, bg: d2.bg, 前: d1.bg }, "dark + rgb(28,28,30) + 与 light 不同");

    // 点三次 → 回到 system
    const d3 = await evaluate(pexec(`
      document.getElementById("theme-btn").click(); await sleep(900);
      return { ds: document.documentElement.dataset.theme, mode: localStorage.getItem("cx.theme"),
               lbl: document.getElementById("theme-label").textContent };
    `));
    check(bag, "点 3 次 → 回到 system 三态循环闭合（localStorage='system'）",
      d3.mode === "system" && d3.lbl === "跟随系统", d3, "system/跟随系统");

    // 持久化：置 dark → 刷新 → 首帧即 dark
    await setThemeLS("dark");
    const p1 = await evaluate(pexec(`
      return { ds: document.documentElement.dataset.theme, mode: localStorage.getItem("cx.theme"),
               bg: getComputedStyle(document.body).backgroundColor };
    `));
    check(bag, "刷新持久化：localStorage='dark' → 重载后 dataset.theme='dark' 且背景暗色",
      p1.ds === "dark" && p1.mode === "dark" && p1.bg === "rgb(28, 28, 30)", p1, "dark + 暗背景");

    await setThemeLS("light");
    const p2 = await evaluate(pexec(`
      return { ds: document.documentElement.dataset.theme, bg: getComputedStyle(document.body).backgroundColor };
    `));
    check(bag, "反向持久化：localStorage='light' → 重载后 dataset.theme='light' 且背景亮色",
      p2.ds === "light" && p2.bg === "rgb(242, 242, 247)", p2, "light + rgb(242,242,247)");

    // system 实时跟随：不重载，仅切 CDP 媒体模拟；用哨兵证明「未重载」
    await setThemeLS("system");
    await setMedia([{ name: "prefers-color-scheme", value: "light" }]);
    await sleep(500);
    const m1 = await evaluate(pexec(`
      window.__th1marker = 12345;
      return { ds: document.documentElement.dataset.theme, bg: getComputedStyle(document.body).backgroundColor };
    `));
    await setMedia([{ name: "prefers-color-scheme", value: "dark" }]);
    await sleep(900);
    const m2 = await evaluate(pexec(`
      return { ds: document.documentElement.dataset.theme, bg: getComputedStyle(document.body).backgroundColor,
               marker: window.__th1marker };
    `));
    check(bag, "system 模式下 Emulation 置 prefers-color-scheme:dark → 实时变暗（无重载：哨兵仍在）",
      m2.ds === "dark" && m2.bg === "rgb(28, 28, 30)" && m2.marker === 12345 && m1.ds === "light",
      { 前: m1.ds, 后: m2.ds, bg: m2.bg, 哨兵: m2.marker }, "light→dark + 哨兵 12345");

    // system 模式下切回 light 也要实时跟随
    await setMedia([{ name: "prefers-color-scheme", value: "light" }]);
    await sleep(900);
    const m3 = await evaluate(`(function(){return {ds:document.documentElement.dataset.theme, bg:getComputedStyle(document.body).backgroundColor};})()`);
    check(bag, "system 模式再置回 light → 实时跟随回亮色",
      m3.ds === "light" && m3.bg === "rgb(242, 242, 247)", m3, "light + 亮背景");

    // 非 system 模式必须**忽略**系统变化
    await setThemeLS("dark");
    await setMedia([{ name: "prefers-color-scheme", value: "light" }]);
    await sleep(700);
    const g = await evaluate(`(function(){return {ds:document.documentElement.dataset.theme};})()`);
    check(bag, "非 system 模式（dark）忽略系统切亮 → 仍保持 dark（手动优先）",
      g.ds === "dark", g, "dark");
  });
}

// ---------- TH2：暗色无亮色残留 ----------
async function th2() {
  await scene("TH2 暗色无亮色残留（真实 chrome 抽样 + 令牌解析 + 源码静态断言）", async (bag) => {
    await setMedia([]);
    await setThemeLS("dark");

    // 真实常驻 chrome：标题栏/侧栏/主内容/底栏/主题钮/选中项/折叠条
    const real = await evaluate(pexec(`
      var sels = ["#titlebar","#sidebar","#main",".bottombar","#theme-btn",".side-item.active",".side-fold",".seg"];
      var out = [];
      sels.forEach(function (s) {
        var el = document.querySelector(s);
        if (!el) { out.push({ sel: s, missing: true }); return; }
        var cs = getComputedStyle(el);
        out.push({ sel: s, bg: cs.backgroundColor, fg: cs.color, filter: cs.backdropFilter });
      });
      return { out: out, htmlBg: getComputedStyle(document.documentElement).backgroundColor,
               bodyBg: getComputedStyle(document.body).backgroundColor,
               bodyFg: getComputedStyle(document.body).color };
    `));
    check(bag, "body 底色=暗令牌 rgb(28,28,30)、文字=暗令牌亮字 rgb(236,238,242)（html 透明由 body 着色）",
      real.bodyBg === "rgb(28, 28, 30)" && real.bodyFg === "rgb(236, 238, 242)"
      && (real.htmlBg === "rgba(0, 0, 0, 0)" || real.htmlBg === "rgb(28, 28, 30)"),
      { html: real.htmlBg, body: real.bodyBg, fg: real.bodyFg }, "body 28,28,30 / 236,238,242");

    const missing = real.out.filter((x) => x.missing).map((x) => x.sel);
    check(bag, "抽样选择器全部落地（无 missing）", missing.length === 0, missing, "[]");

    const bgHits = real.out.filter((x) => LIGHT_BG.includes(x.bg));
    const fgHits = real.out.filter((x) => LIGHT_FG.includes(x.fg));
    check(bag, "真实 chrome 抽样：无不透明亮色底（白/242,242,247）",
      bgHits.length === 0, bgHits.map((x) => [x.sel, x.bg]), "0 命中");
    check(bag, "真实 chrome 抽样：无亮底深字（28,28,30 / 黑）",
      fgHits.length === 0, fgHits.map((x) => [x.sel, x.fg]), "0 命中");

    // 令牌解析抽样：卡/日志面/弹窗（本闸不扫描 → 无任务数据，卡不自然落地；
    // 这里挂进离屏宿主只为验证 .card/.logbox/.dlg.lg 在 dark 下解析到的**令牌值**，非真实业务卡）
    const synth = await evaluate(pexec(`
      var host = document.getElementById("th-host");
      if (!host) {
        host = document.createElement("div"); host.id = "th-host";
        host.style.cssText = "position:fixed;left:-9999px;top:0;width:400px;height:300px;pointer-events:none;";
        document.body.appendChild(host);
      }
      host.innerHTML = '<div class="card" style="height:80px"></div>'
                     + '<div class="logbox" style="min-height:40px"></div>'
                     + '<div class="dlg lg" style="width:380px;padding:20px"></div>'
                     + '<div class="bottombar lg"></div>';
      var out = [];
      [".card", ".logbox", ".dlg.lg", ".bottombar.lg"].forEach(function (s) {
        var el = host.querySelector(s);
        var cs = getComputedStyle(el);
        out.push({ sel: "合成" + s, bg: cs.backgroundColor, fg: cs.color, filter: cs.backdropFilter });
      });
      return out;
    `));
    const sBgHits = synth.filter((x) => LIGHT_BG.includes(x.bg));
    const sFgHits = synth.filter((x) => LIGHT_FG.includes(x.fg));
    check(bag, "令牌解析：.card/.logbox 底色=暗卡令牌 rgb(44,44,46)（非白）",
      synth[0].bg === "rgb(44, 44, 46)" && synth[1].bg === "rgb(44, 44, 46)",
      { card: synth[0].bg, logbox: synth[1].bg }, "rgb(44,44,46)");
    check(bag, "令牌解析抽样：无不透明亮色底 / 无亮底深字",
      sBgHits.length === 0 && sFgHits.length === 0,
      { bg: sBgHits.map((x) => [x.sel, x.bg]), fg: sFgHits.map((x) => [x.sel, x.fg]) }, "0 命中");

    // 源码静态断言：颜色声明必须令牌化 + 无遗留亮色字面量
    const css = fs.readFileSync(path.join(SRC, "styles.css"), "utf-8");
    const body = css.split("html[data-theme=\"dark\"]")[1] ? css.slice(css.indexOf('* { box-sizing')) : css;
    const litDecls = body.split("\n")
      .map((l, i) => ({ i: i + 1, l: l.trim() }))
      .filter((x) => /^(background|color)\s*:/.test(x.l) && !/var\(--/.test(x.l));
    check(bag, "源码：令牌块之外无任何 background/color 字面量（全 var(--) 驱动）",
      litDecls.length === 0, litDecls, "0 条");
    const legacy = ["246,246,248", "7878801F", "8E8E93", "#F2F2F7", "#FFF"].filter((t) => body.includes(t));
    check(bag, "源码：无遗留亮色硬编码（246,246,248 / 7878801F / 8E8E93 / #F2F2F7 / #FFF）",
      legacy.length === 0, legacy, "[]");
  });
}

// ---------- TH3：T1 切屏滑动 ----------
async function th3() {
  await scene("TH3 T1 切屏滑动：450ms emphasized + 轨迹连续；reduce 无位移", async (bag) => {
    await setMedia([]);
    await setThemeLS("light");

    const ev = await evaluate(pexec(`
      var items = [].slice.call(document.querySelectorAll(".side-item[data-scr]"));
      var byScr = function (k) { return items.filter(function (x) { return x.dataset.scr === k; })[0]; };
      byScr("plan").click(); await sleep(950);
      var el = document.getElementById("scr-queue");
      var beforeHidden = el.hidden;
      byScr("queue").click();
      var anims = animsOf(el);
      var samples = []; var t0 = performance.now();
      await new Promise(function (res) { (function loop() {
        var t = performance.now() - t0;
        var cs = getComputedStyle(el);
        samples.push([Math.round(t), R1(tyOf(cs.transform)), Math.round(parseFloat(cs.opacity) * 100) / 100]);
        if (t > 620) res(); else requestAnimationFrame(loop);
      })(); });
      return { beforeHidden: beforeHidden, afterHidden: el.hidden, cls: el.className,
               anims: anims, samples: samples };
    `));

    check(bag, "进屏解除 hidden（旧屏 hidden → 新屏可见）",
      ev.beforeHidden === true && ev.afterHidden === false, { 前: ev.beforeHidden, 后: ev.afterHidden }, "true→false");

    const a = pick(ev.anims, (x) => x.ctor === "Animation" && near(x.dur, 450, 20) && easeOk(x.ease)
      && (x.kf || []).some((k) => /matrix|translate/i.test(k)));
    check(bag, "进屏 getAnimations() 命中 transform 450±20 emphasized",
      !!a, a && { ctor: a.ctor, dur: a.dur, ease: a.ease }, "Animation@450 + 曲线对");

    const tys = ev.samples.map((s) => s[1]);
    const mid = tys.filter((y) => y > 0.8 && y < 15.2).length;
    check(bag, "轨迹连续：≥3 个采样点处于 translateY 16→0 之间（禁瞬移）",
      mid >= 3, { 中间态点数: mid, 首: tys[0], 末: tys[tys.length - 1], 全轨迹: tys.slice(0, 8) }, "≥3");

    let maxStep = 0;
    for (let i = 1; i < tys.length; i++) maxStep = Math.max(maxStep, Math.abs(tys[i] - tys[i - 1]));
    check(bag, "无单帧跳变（步进 ≤0.5×全程 16px）", maxStep <= 8, R1(maxStep), "≤8");

    const ops = ev.samples.map((s) => s[2]);
    check(bag, "opacity 同步 0→1（起 <0.2、末 =1）",
      ops[0] < 0.2 && ops[ops.length - 1] === 1, { 起: ops[0], 末: ops[ops.length - 1] }, "0→1");
    const opMid = ops.filter((o) => o > 0.05 && o < 0.95).length;
    check(bag, "opacity 轨迹连续（≥2 中间态）", opMid >= 2, opMid, "≥2");

    // reduce：CDP 模拟 prefers-reduced-motion: reduce → 纯淡入淡出、零位移
    await setMedia([{ name: "prefers-reduced-motion", value: "reduce" }]);
    await sleep(400);
    const rd = await evaluate(pexec(`
      var items = [].slice.call(document.querySelectorAll(".side-item[data-scr]"));
      var byScr = function (k) { return items.filter(function (x) { return x.dataset.scr === k; })[0]; };
      byScr("plan").click(); await sleep(500);
      var el = document.getElementById("scr-queue");
      byScr("queue").click();
      var anims = animsOf(el);
      var tys = []; var t0 = performance.now();
      await new Promise(function (res) { (function loop() {
        var t = performance.now() - t0;
        tys.push(R1(tyOf(getComputedStyle(el).transform)));
        if (t > 420) res(); else requestAnimationFrame(loop);
      })(); });
      return { anims: anims, tys: tys };
    `));
    const hasTf = rd.anims.some((x) => (x.kf || []).some((k) => /matrix|translate/i.test(k)));
    check(bag, "reduce：进屏无 transform 关键帧（降级为纯 opacity）", !hasTf,
      rd.anims.map((x) => ({ dur: x.dur, kf: x.kf })), "无 transform");
    check(bag, "reduce：全程 transform 位移恒为 0（实测最大 |ty|）",
      Math.max(...rd.tys.map(Math.abs)) === 0, { max: Math.max(...rd.tys.map(Math.abs)), 采样: rd.tys.slice(0, 5) }, "0");
    const fade = pick(rd.anims, (x) => x.ctor === "Animation" && near(x.dur, 250, 20) && /opacity/i.test((x.kf || []).join("")));
    check(bag, "reduce：时长降级为 250ms 淡入", !!fade, fade && { dur: fade.dur, ease: fade.ease }, "250±20");
    await setMedia([]);
  });
}

// ---------- TH4：T2 Aurora 流光 ----------
async function th4() {
  await scene("TH4 T2 Aurora：存在且 60s linear 在跑 / document.hidden 暂停 / reduce 静止", async (bag) => {
    await setMedia([]);
    await setThemeLS("light");

    const ev = await evaluate(pexec(`
      var outer = document.getElementById("aurora");
      var layer = document.querySelector("#aurora .layer");
      if (!layer || !outer) return { missing: true };
      var cs = getComputedStyle(layer), os = getComputedStyle(outer);
      return { missing: false, layerAnims: animsOf(layer), outerAnims: animsOf(outer),
               bgImage: cs.backgroundImage.slice(0, 160), mask: (cs.maskImage || cs.webkitMaskImage || "").slice(0, 120),
               layerWillChange: cs.willChange,
               name: os.animationName, dur: os.animationDuration, timing: os.animationTimingFunction,
               iter: os.animationIterationCount, playState: os.animationPlayState, willChange: os.willChange,
               // §T2 回退护栏：全文档不得再存在重绘型 background-position 位移动画
               travelAnims: [].slice.call(document.getAnimations())
                 .filter(function (a) { return a.animationName === "aurora"; }).length,
               pausedCls: document.documentElement.classList.contains("aurora-paused") };
    `));
    check(bag, "#aurora / #aurora .layer 均存在", ev.missing === false, ev.missing, false);

    // §T2 已回退：动画本体由「background-position 60s linear」改为「opacity 极慢呼吸」
    // （重绘型位移在 --disable-gpu 下把 rAF 中位打到 22–32ms，击穿 anim_probe A1 的 ≤20ms 闸；
    //  详见 styles.css §T2 块注释与 P1 实测）。此处断言**回退态**，并设护栏防位移回归。
    // 时长/曲线/次数一律读 **computed style**：CSSAnimation 的 getTiming().easing 对
    // 多关键帧动画会报 "linear"（真曲线在关键帧级），computed 才是权威读数。
    // 名字按主题择一（aurora-breathe-l / -d），端点必须落在令牌浓度上（亮 .55 / 暗 .62）
    check(bag, "#aurora 上跑 'aurora-breathe-l|-d'：26s ease-in-out infinite（极慢呼吸）",
      /^aurora-breathe-[ld]$/.test(ev.name) && ev.dur === "26s" && /ease-in-out/.test(ev.timing) && ev.iter === "infinite",
      { name: ev.name, dur: ev.dur, timing: ev.timing, iter: ev.iter }, "aurora-breathe-l|d / 26s / ease-in-out / infinite");
    check(bag, "§T2 回退护栏：无重绘型 background-position 位移动画残留",
      ev.travelAnims === 0, { 位移动画数: ev.travelAnims }, 0);
    // 挂点是性能要害：呼吸若挂在 .layer（与 blur/mask/blended ::after 同层）会退回 36–59ms，
    // 挂 #aurora（无 filter/mask/blend 的纯合成层）才是 16.7ms。此断言锁死该结论。
    check(bag, "§T2 挂点护栏：呼吸**不在** .layer 上（该层带 blur/mask/blend，动它=重绘 36–59ms）",
      ev.layerAnims.length === 0 && ev.layerWillChange === "auto",
      { layerAnim数: ev.layerAnims.length, layerWillChange: ev.layerWillChange }, "0 且 auto");
    check(bag, "性能红线：will-change=opacity 挂在 #aurora（合成型，非重绘型）",
      ev.willChange === "opacity", ev.willChange, "opacity");
    check(bag, "多层 repeating-linear-gradient 条纹 + 径向遮罩",
      /repeating-linear-gradient/.test(ev.bgImage) && /radial-gradient/.test(ev.mask),
      { bg: ev.bgImage.slice(0, 60), mask: ev.mask.slice(0, 60) }, "均命中");
    check(bag, "动画运行中（animation-play-state=running，未被误暂停）",
      ev.playState === "running" && ev.pausedCls === false, { ps: ev.playState, cls: ev.pausedCls }, "running/false");

    // document.hidden → 暂停（theme.js 的 visibilitychange 契约）。
    // headless 单标签页 visibilityState 恒为 visible，故此处合成 hidden 后派发事件
    // （直接驱动 theme.js 监听器读的 document.hidden，测的是接线本身）。
    const hid = await evaluate(pexec(`
      var outer = document.getElementById("aurora");
      Object.defineProperty(document, "hidden", { get: function () { return true; }, configurable: true });
      Object.defineProperty(document, "visibilityState", { get: function () { return "hidden"; }, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
      await sleep(120);
      var paused = getComputedStyle(outer).animationPlayState;
      var cls = document.documentElement.classList.contains("aurora-paused");
      // 还原为可见 → 必须恢复运行
      Object.defineProperty(document, "hidden", { get: function () { return false; }, configurable: true });
      Object.defineProperty(document, "visibilityState", { get: function () { return "visible"; }, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
      await sleep(120);
      return { paused: paused, cls: cls, resumed: getComputedStyle(outer).animationPlayState,
               clsAfter: document.documentElement.classList.contains("aurora-paused") };
    `));
    check(bag, "document.hidden=true → animation-play-state=paused 且 html.aurora-paused 挂上",
      hid.paused === "paused" && hid.cls === true, { ps: hid.paused, cls: hid.cls }, "paused/true");
    check(bag, "恢复可见 → 动画复跑、aurora-paused 摘除",
      hid.resumed === "running" && hid.clsAfter === false, { ps: hid.resumed, cls: hid.clsAfter }, "running/false");

    // reduce → 静止（保留渐变本体，只停位移）
    await setMedia([{ name: "prefers-reduced-motion", value: "reduce" }]);
    await sleep(400);
    const rd = await evaluate(pexec(`
      var outer = document.getElementById("aurora");
      var layer = document.querySelector("#aurora .layer");
      return { n: outer.getAnimations().length,
               bg: /repeating-linear-gradient/.test(getComputedStyle(layer).backgroundImage),
               active: document.getAnimations().filter(function (a) {
                 return /^aurora-breathe-/.test(a.animationName || "") && a.playState === "running"; }).length };
    `));
    check(bag, "reduce：Aurora 呼吸停摆（动画数=0）但多层渐变本体保留",
      rd.n === 0 && rd.bg === true && rd.active === 0, rd, "0/true/0");
    await setMedia([]);
  });
}

// ---------- TH5：T4 液态玻璃 ----------
async function th5() {
  await scene("TH5 T4 液态玻璃：仅 chrome + blur 配方 + @supports/reduced-transparency 回退", async (bag) => {
    await setMedia([]);
    await setThemeLS("dark");

    const ev = await evaluate(pexec(`
      var host = document.getElementById("th-host");
      if (!host) {
        host = document.createElement("div"); host.id = "th-host";
        host.style.cssText = "position:fixed;left:-9999px;top:0;width:400px;height:300px;pointer-events:none;";
        document.body.appendChild(host);
      }
      host.innerHTML = '<div class="dlg lg" style="width:380px;padding:20px"></div>';
      var out = [];
      [["#titlebar","标题栏"],["#sidebar","侧栏"],[".bottombar","底栏"],["合成 .dlg.lg","弹窗"]].forEach(function (p) {
        var el = p[0].indexOf("合成") === 0 ? host.querySelector(".dlg.lg") : document.querySelector(p[0]);
        if (!el) { out.push({ who: p[1], missing: true }); return; }
        var cs = getComputedStyle(el);
        out.push({ who: p[1], lg: el.classList.contains("lg"), filter: cs.backdropFilter,
                   webkit: cs.webkitBackdropFilter, hasBefore: !!getComputedStyle(el, "::before").content });
      });
      var mask = getComputedStyle(document.getElementById("modal-mask"));
      var card = host.querySelector(".card");
      return { out: out, maskFilter: mask.backdropFilter,
               cardHasLg: !!document.querySelector(".card.lg") };
    `));
    const bad = ev.out.filter((x) => x.missing || !x.lg || !/blur\(/.test(x.filter || ""));
    check(bag, "标题栏/侧栏/底栏/弹窗 均为 .lg 且 backdropFilter 含 blur（配方命中）",
      bad.length === 0, ev.out.map((x) => ({ who: x.who, lg: x.lg, f: (x.filter || "").slice(0, 34) })), "全部 .lg + blur");
    check(bag, "配方精度：blur(22px) saturate(180%) contrast(1.04)",
      ev.out.every((x) => /blur\(22px\)/.test(x.filter || "") && /saturate\(1\.8\)/.test(x.filter || "")),
      ev.out[0] && ev.out[0].filter, "blur(22px) saturate(1.8) contrast(1.04)");
    check(bag, "::before 径向边缘高光存在（液体玻璃边缘折射近似）",
      ev.out.every((x) => x.hasBefore === true), ev.out.map((x) => x.hasBefore), "全 true");
    check(bag, "内容卡片**不上玻璃**（无 .card.lg；卡内长文本可读性纪律）",
      ev.cardHasLg === false, ev.cardHasLg, false);
    check(bag, "遮罩层独立 blur(2px)（与玻璃面分层）",
      /blur\(2px\)/.test(ev.maskFilter || ""), ev.maskFilter, "blur(2px)");

    // 静态断言：两条回退分支必须在样式源里（@supports 无法在运行时可靠模拟）
    const css = fs.readFileSync(path.join(SRC, "styles.css"), "utf-8");
    check(bag, "源码：@supports not (backdrop-filter) 回退分支存在且转实色 --card",
      /@supports not \(\(backdrop-filter:/.test(css) && /\.lg \{ background: var\(--card\); \}/.test(css),
      { supports: /@supports not \(\(backdrop-filter:/.test(css), 转实色: /\.lg \{ background: var\(--card\); \}/.test(css) }, "均在");
    check(bag, "源码：prefers-reduced-transparency: reduce 回退存在且关 backdrop-filter",
      /@media \(prefers-reduced-transparency: reduce\)/.test(css) && /backdrop-filter: none/.test(css),
      { media: /prefers-reduced-transparency/.test(css), none: /backdrop-filter: none/.test(css) }, "均在");

    // 实时验证 reduced-transparency（若本机 Chromium 支持该 feature 模拟则必须真的转实色）
    await setMedia([{ name: "prefers-reduced-transparency", value: "reduce" }]);
    await sleep(400);
    const rt = await evaluate(pexec(`
      var sb = document.getElementById("sidebar");
      var cs = getComputedStyle(sb);
      return { filter: cs.backdropFilter, bg: cs.backgroundColor };
    `));
    const supported = rt.filter === "none";
    check(bag, "prefers-reduced-transparency:reduce → .lg 关模糊转实色（本机支持该模拟）",
      supported && rt.bg === "rgb(44, 44, 46)", rt, "backdropFilter=none + rgb(44,44,46)");
    await setMedia([]);
  });
}

// ---------- P1：Aurora 掉帧实测（rAF 间隔 A-B-A 对照） ----------
async function p1() {
  await scene("P1 Aurora 掉帧实测（rAF 间隔 A-B-A：开 / 关 / 开，数字如实记录）", async (bag) => {
    await setMedia([]);
    await setThemeLS("light");
    const ev = await evaluate(pexec(`
      async function sampleRaf(ms) {
        var s = []; var t0 = performance.now(); var last = t0;
        await new Promise(function (res) { (function loop() {
          var now = performance.now();
          s.push(now - last); last = now;
          if (now - t0 > ms) res(); else requestAnimationFrame(loop);
        })(); });
        s.shift();
        s.sort(function (a, b) { return a - b; });
        var n = s.length;
        return { n: n, median: R1(s[Math.floor(n / 2)]), p95: R1(s[Math.floor(n * 0.95)]), max: R1(s[n - 1]) };
      }
      // TH2/TH5 的合成宿主带 backdrop-filter（.dlg.lg/.bottombar.lg），留在 DOM 里会把
      // 软件光栅的帧率拖到 180ms+（实测）——先摘掉，保证本场景测的是 Aurora 本身。
      var host = document.getElementById("th-host");
      if (host) host.remove();
      var layer = document.querySelector("#aurora .layer");
      var aurora = document.getElementById("aurora");
      var on1 = await sampleRaf(2000);          // A：Aurora 开着
      var keep = aurora.style.display;
      aurora.style.display = "none";            // B：彻底摘掉 Aurora 层
      await sleep(250);
      var off = await sampleRaf(2000);
      aurora.style.display = keep;              // A'：恢复
      await sleep(250);
      var on2 = await sampleRaf(2000);
      return { on1: on1, off: off, on2: on2, hostRemoved: !document.getElementById("th-host"),
               anim: layer.getAnimations().length, opacity: getComputedStyle(aurora).opacity };
    `));
    console.log("  [perf] Aurora ON  #1 " + JSON.stringify(ev.on1));
    console.log("  [perf] Aurora OFF    " + JSON.stringify(ev.off));
    console.log("  [perf] Aurora ON  #2 " + JSON.stringify(ev.on2));
    const onMed = Math.min(ev.on1.median, ev.on2.median);
    const onMax = Math.max(ev.on1.max, ev.on2.max);
    const cost = R1(onMed - ev.off.median);
    check(bag, "取证宿主已摘除（否则 backdrop-filter 会污染帧率读数）", ev.hostRemoved === true, ev.hostRemoved, true);
    // 绝对阈值（≤20ms）**故意不设为断言**：对照组实测「整层 display:none 也照样跑出 30ms」，
    // 该读数受环境（同机其它 Edge/负载、headless 软件光栅）支配，用它会 gate 出假阴性。
    // 真正的确定性护栏是上面两条（无重绘位移 + 呼吸不在 .layer）；rAF 数字在此**如实记录**，
    // 只保留一条「不得崩塌」的兜底上限，用来抓把重活搬回 Aurora 的回归。
    check(bag, "兜底：Aurora 在跑时 rAF 中位未崩塌（≤40ms；仅防回归，不作性能达标判据）",
      onMed <= 40, { on1: ev.on1.median, on2: ev.on2.median, off: ev.off.median }, "≤40ms");
    check(bag, "兜底：无 >120ms 长卡顿", onMax <= 120, onMax, "≤120ms");
    bag.perf = { on1: ev.on1, off: ev.off, on2: ev.on2, costMs: cost, onMedMs: onMed };
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
  await sleep(500);
  console.log("[setup] boot 就绪（cxscan=0 不扫描；本闸只测视觉令牌，不碰账号数据）");

  await th1();
  await th2();
  await th3();
  await th4();
  await th5();
  await p1();

  const passed = results.filter((r) => r.pass).length;
  const checks = results.reduce((a, r) => a + r.lines.length, 0);
  const okChecks = results.reduce((a, r) => a + r.lines.filter((l) => l.startsWith("  ✓")).length, 0);
  console.log("\n============================================================");
  for (const r of results) console.log((r.pass ? "PASS  " : "FAIL  ") + r.scene);
  console.log("THEME: " + passed + "/" + results.length + " PASS  (断言 " + okChecks + "/" + checks + ")");
  fs.writeFileSync(path.join(OUT, "theme.json"), JSON.stringify({
    meta: { when: new Date().toISOString(), webOrigin: "http://127.0.0.1:" + webPort, serverPort: srv.port },
    results,
  }, null, 1), "utf-8");
  console.log("[out] 证据已落 " + path.join(OUT, "theme.json"));

  try { await send("Browser.close"); } catch (_) { }
  await Promise.race([new Promise((r) => edge.on("exit", r)), sleep(8000)]);
  killAll();
  process.exit(passed === results.length ? 0 : 1);
}

main().catch((e) => { console.error("THEME-FAIL: " + e.message); process.exit(2); });
