// tools/anim_probe.mjs —— M2 任务书 §1 动效取证器（Node>=22 + CDP over headless Edge，思路承 M1 tools/e2e_ui.mjs）
// 链路：spawn `python -m server --port 0`（解析 CX_READY port/token）→ 本机静态服务挂 shell/src
//       （origin 127.0.0.1:1430，被占则 1420——两者均已在 server CORS 白名单内，**server 零改动**）
//       → headless Edge 带 ?cxport&cxtoken&cxprobe=1&cxscan=0 → 注入页内采样器 → 九场景：
//       begin(真实 UI 事件触发) → t=0/150/300/450ms 各 1 张 PNG（留档，非判据）→ collect(数字证据)
//       → 场景判据断言 → 末尾 `ANIM: x/9 PASS`，FAIL 逐条打 实测 vs 期望。
// 证据落盘：--out DIR（缺省=系统临时目录下 cx-pilot-anim-m2，不依赖任何机器路径）。
//   真课名/账号绝不进 JSON/stdout：场景只回传坐标/时长/属性名/数字任务ID尾段（dataset.key）。
// 用法：node tools/anim_probe.mjs [--out DIR] [--port 9343] [--python python] [--edge PATH]
//       [--skip A7,...]（--skip 仅调试用；正式验收必须全跑） [--scan-timeout 240000]
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const arg = (k, d = "") => { const i = process.argv.indexOf("--" + k); return i > 0 ? process.argv[i + 1] : d; };
const argList = (k) => (arg(k) || "").split(",").map((x) => x.trim()).filter(Boolean);

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "shell", "src");
const OUT = arg("out", path.join(os.tmpdir(), "cx-pilot-anim-m2"));
const CDP_PORT = Number(arg("port", "9343"));
const PROFILE = path.join(OUT, "edge-profile-" + process.pid);   // 每次全新：避免残留黑名单/日志 localStorage 污染 A2/A3
const EDGE = arg("edge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe");
const PY = arg("python", "python");
const SKIP = new Set(argList("skip"));
const SCAN_TIMEOUT = Number(arg("scan-timeout", "240000"));

fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const round = (v, p = 2) => { const m = 10 ** p; return Math.round(v * m) / m; };
const near = (a, b, tol) => Math.abs(a - b) <= tol;
const EASE_RE = /^cubic-bezier\(\s*0\.65,\s*0(\.0+)?,\s*0\.35,\s*1(\.0+)?\s*\)$/;
const easeOk = (s) => EASE_RE.test(String(s));
// §0 硬约束的逐场景放行属性集（§1 表「只动 transform/opacity」行 + §2 规格内明写的例外）
const ALLOW = {
  A1: ["transform", "opacity"], A1i: ["transform", "opacity"], A1r: ["opacity"],
  A2: ["transform", "opacity"], A3: ["transform", "opacity"], A4: ["transform", "opacity"],
  A5: ["transform"],
  A6: ["box-shadow", "background-color", "border-top-color", "border-right-color", "border-bottom-color", "border-left-color"],
  A7: ["width"], A8: ["transform", "opacity"],
  // A9 与 A4 同属一次落定事件：滑行 transform + 卡片 opacity + pill 上色，全在 §2 规格内
  A9: ["transform", "opacity", "background-color", "color"],
};

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
async function evaluate(expression, awaitPromise = true) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) {
    const d = r.exceptionDetails.exception && r.exceptionDetails.exception.description;
    throw new Error("eval 异常: " + String(d || JSON.stringify(r.exceptionDetails)).slice(0, 300));
  }
  return r.result.value;
}
async function wait(expr, timeout, step = 500) {
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

// ---------- 静态服务（挂 shell/src；仅回环，1430→1420 回落，均为 CORS 白名单内 origin）----------
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
  });
}

// ---------- 页内注入：采样器 + 场景 begin/collect（全部走真实 UI 事件路径）----------
// 注入体为纯 ES5 风格字符串（无模板字面量/无 backtick），经 Runtime.evaluate 执行。
const HELPER = String.raw`
(function () {
  var round = function (v) { return Math.round(v * 100) / 100; };
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var P = { errs: [], cur: null, curS: null, a7: null, a8: null, course2: "" };
  window.__P = P;
  window.addEventListener("error", function (e) { P.errs.push(String(e.message) + " @" + (e.filename || "") + ":" + (e.lineno || 0)); });

  var geom = function (el) {
    var r = el.getBoundingClientRect(), c = getComputedStyle(el);
    return { left: round(r.left), top: round(r.top), w: round(r.width), h: round(r.height),
             ctop: c.top, cleft: c.left, cw: c.width, ch: c.height,
             cm: c.marginTop + "/" + c.marginLeft, shadow: c.boxShadow, op: c.opacity,
             color: c.color, bg: c.backgroundColor };
  };
  function propsOf(ef) {
    // 本 Edge 内核 KeyframeEffect 无 getPropertyNames()（debug1 T1 实锤）——
    // 从关键帧字段推导属性名；camel→kebab 统一（border 简写在本内核拆成 per-side CSSTransition：
    // borderBottomColor → border-bottom-color，run4 实锤）。
    if (ef.getPropertyNames) { try { var l = ef.getPropertyNames(); if (l && l.length) return Array.from(l).join(","); } catch (e) { } }
    var seen = {};
    if (ef.getKeyframes) {
      ef.getKeyframes().forEach(function (k) {
        for (var p in k) {
          if (p === "offset" || p === "computedOffset" || p === "easing" || p === "composite" ||
              p === "computedEasing" || p === "property" || p === "transitionProperty") continue;
          seen[p.replace(/[A-Z]/g, function (m) { return "-" + m.toLowerCase(); })] = 1;
        }
      });
    }
    return Object.keys(seen).join(",");
  }
  var anims = function (pred) {
    var out = [];
    document.getAnimations().forEach(function (a) {
      try {
        var ef = a.effect; if (!ef || !ef.target || !pred(ef.target)) return;
        var t = ef.getTiming();
        var kf = "";
        if (ef.getKeyframes) {
          kf = ef.getKeyframes().map(function (k) {
            return (k.transform === undefined ? "" : k.transform) + "@" + (k.opacity === undefined ? "" : k.opacity);
          }).join(">");
        }
        out.push({ cls: a.constructor.name,
                   target: (ef.target.id || (ef.target.className && String(ef.target.className).slice(0, 40)) || ef.target.tagName),
                   key: ef.target.dataset ? (ef.target.dataset.key || "") : "",
                   props: propsOf(ef),
                   dur: t.duration, ease: t.easing, ps: a.playState, kf: kf });
      } catch (e) { }
    });
    return out;
  };

  function begin(els, opts) {
    opts = opts || {};
    var s = { t0: performance.now(), frames: [], anims: [], removed: 0, raf: 0, mo: null, els: [], ids: [], before: [], snapAt: opts.snapAt || [], pred: opts.pred || function () { return false; } };
    for (var i = 0; i < els.length; i++) {
      s.els.push(els[i]);
      s.ids.push(els[i].dataset.key || (i + ":" + els[i].className));
      s.before.push(geom(els[i]));
    }
    var canvas = document.querySelector("#plan-canvas");
    if (canvas && opts.mo !== false) {
      s.mo = new MutationObserver(function (ms) {
        for (var j = 0; j < ms.length; j++) {
          var rn = ms[j].removedNodes;
          for (var k = 0; k < rn.length; k++) {
            var n = rn[k];
            if (n.nodeType === 1 && n.classList && n.classList.contains("task")) s.removed++;
          }
        }
      });
      s.mo.observe(canvas, { childList: true, subtree: true });
    }
    var tick = function () {
      var now = performance.now() - s.t0;
      var g = [];
      for (var i = 0; i < s.els.length; i++) g.push(s.els[i].isConnected ? geom(s.els[i]) : null);
      s.frames.push({ t: Math.round(now), g: g });
      for (var q = 0; q < s.snapAt.length; q++) {
        if (!s["got" + s.snapAt[q]] && now >= s.snapAt[q]) {
          s["got" + s.snapAt[q]] = 1;
          s.anims.push({ at: Math.round(now), list: anims(s.pred) });
        }
      }
      s.raf = requestAnimationFrame(tick);
    };
    s.raf = requestAnimationFrame(tick);
    P.cur = s;
    return s;
  }
  async function end(minWaitMs, extraPred) {
    var s = P.cur;
    await sleep(Math.max(0, (minWaitMs || 650) - (performance.now() - s.t0)) + 30);
    if (extraPred) s.anims.push({ at: Math.round(performance.now() - s.t0), list: anims(extraPred) });
    cancelAnimationFrame(s.raf);
    if (s.mo) s.mo.disconnect();
    P.cur = null;
    s.final = [];
    for (var i = 0; i < s.els.length; i++) s.final.push(s.els[i].isConnected ? geom(s.els[i]) : null);
    s.identity = [];
    for (var j = 0; j < s.els.length; j++) {
      var e2 = s.els[j];
      s.identity.push({ id: s.ids[j], connected: e2.isConnected,
        same: e2.dataset.key ? document.querySelector("#plan-canvas [data-key='" + e2.dataset.key + "']") === e2 : e2.isConnected });
    }
    return s;
  }
  function dist(a, b) { return Math.hypot(a.left - b.left, a.top - b.top); }
  function perEl(s) {
    return s.els.map(function (el, i) {
      var bs = s.before[i], fs = s.final[i] || bs;
      var series = [];
      for (var k = 0; k < s.frames.length; k++) { var g = s.frames[k].g[i]; if (g) series.push({ t: s.frames[k].t, left: g.left, top: g.top }); }
      var db = [], df = [], dl = [];
      for (var a = 0; a < series.length; a++) { db.push(round(dist(series[a], bs))); df.push(round(dist(series[a], fs))); }
      for (var b = 1; b < series.length; b++) dl.push(round(dist(series[b], series[b - 1])));
      var uniq = function (key) {
        var m = {};
        for (var f = 0; f < s.frames.length; f++) { var g2 = s.frames[f].g[i]; if (g2) m[g2[key]] = 1; }
        return Object.keys(m).length;
      };
      return { id: s.ids[i], movedPx: round(dist(bs, fs)), peakPx: db.length ? Math.max.apply(null, db) : 0,
               dBefore: db, dFinal: df, deltas: dl, frames: series.length,
               geomConst: { top: uniq("ctop") <= 1, left: uniq("cleft") <= 1, width: uniq("cw") <= 1, height: uniq("ch") <= 1, margin: uniq("cm") <= 1 } };
    });
  }
  function fps(s) {
    var iv = [];
    for (var k = 1; k < s.frames.length; k++) iv.push(s.frames[k].t - s.frames[k - 1].t);
    var st = iv.slice().sort(function (a, b) { return a - b; });
    return { n: iv.length, median: st.length ? st[Math.floor(st.length / 2)] : null, max: st.length ? st[st.length - 1] : null };
  }
  function midAnims(s, ats) {
    var now = performance.now() - s.t0;
    for (var i = 0; i < ats.length; i++) {
      if (!s["got" + ats[i]] && now >= ats[i]) { s["got" + ats[i]] = 1; s.anims.push({ at: Math.round(now), list: anims(s.pred) }); }
    }
  }
  function visibleCards() {
    return Array.prototype.slice.call(document.querySelectorAll("#plan-canvas .card.task"))
      .filter(function (c) { var r = c.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  }
  function segSpans() { return Array.prototype.slice.call(document.querySelectorAll("#plan-seg span[data-v]")); }
  var CARD_PRED = function (t) { return t.classList && t.classList.contains("card") && t.classList.contains("task"); };

  // ---- A7：真实 /scan + 自动验卷全程（进度条 width 过渡 200ms + 「扫描 n/36」宽度不抖）----
  P.A7b = function () {
    var fill = document.getElementById("scanfill"), txt = document.getElementById("scantext");
    var s = { t0: performance.now(), fillRuns: [], samples: [], anims: [], timer: 0 };
    P.a7 = s;
    fill.addEventListener("transitionrun", function (e) {
      if (e.propertyName === "width") {
        var c = getComputedStyle(fill);
        s.fillRuns.push({ t: Math.round(performance.now() - s.t0), dur: c.transitionDuration, ease: c.transitionTimingFunction });
      }
    });
    s.timer = setInterval(function () {
      s.samples.push({ n: txt.textContent, w: Math.round(txt.getBoundingClientRect().width), fw: getComputedStyle(fill).width });
      document.getAnimations().forEach(function (a) {
        try {
          if (a.effect && a.effect.target === fill) {
            var t = a.effect.getTiming();
            s.anims.push({ props: propsOf(a.effect), dur: t.duration, ease: t.easing });
          }
        } catch (e) { }
      });
    }, 150);
    document.getElementById("btn-scan").click();
    return "A7-scanning";
  };
  P.A7c = function () {
    var s = P.a7;
    clearInterval(s.timer);
    var wSet = {}, nSet = {};
    // 进度条隐藏后（done+0.5s）采样盒宽归 0——「不抖」只对可见期采样判
    s.samples.forEach(function (x) { if (x.w > 0) wSet[x.w] = 1; nSet[x.n] = 1; });
    return { fillRuns: s.fillRuns, anims: s.anims, textW: Object.keys(wSet), nVariants: Object.keys(nSet).length, samples: s.samples.length };
  };

  // ---- A5：分段控件胶囊 200ms 滑移（起止 x 差=段宽）----
  P.A5b = function () {
    var spans = segSpans();
    var curOn = spans.find(function (x) { return x.classList.contains("on"); });
    var other = spans.find(function (x) { return x !== curOn; });
    var thumb = document.querySelector(".seg-thumb");
    if (!curOn || !other || !thumb) return "NO-SEG";
    var s = begin([thumb], { snapAt: [40, 120, 260], mo: false, pred: function (t) { return t.classList && t.classList.contains("seg-thumb"); } });
    s.meta = { fromW: curOn.offsetWidth, fromX: curOn.offsetLeft, toX: other.offsetLeft, beforeL: thumb.getBoundingClientRect().left };
    P.curS = s;
    other.click();
    return "A5b";
  };
  P.A5c = async function () {
    var s = P.curS;
    await end(500);
    var moved = round(s.els[0].getBoundingClientRect().left - s.meta.beforeL);
    var spans = segSpans();
    var curOn = spans.find(function (x) { return x.classList.contains("on"); });
    spans.find(function (x) { return x !== curOn; }).click();   // 复位回科目列
    await sleep(500);
    return { anims: s.anims, fps: fps(s), meta: s.meta, moved: moved,
             ok: Math.abs(moved - s.meta.fromW) <= 1, geom: perEl(s) };
  };

  // ---- A1a：科目列⇄时间流 单程切换（450ms 连续位移 + 不重建）----
  P.A1b = function () {
    var cards = visibleCards();
    var other = segSpans().find(function (x) { return !x.classList.contains("on"); });
    if (!cards.length || !other) return "NO-DATA";
    var s = begin(cards, { snapAt: [40, 120, 260, 420], pred: CARD_PRED });
    P.curS = s;
    other.click();
    return "A1a";
  };
  P.A1c = async function () {
    var s = P.curS;
    await end(700);
    var spans = segSpans();
    var cur = spans.find(function (x) { return x.classList.contains("on"); });
    spans.find(function (x) { return x !== cur; }).click();     // 复位回科目列
    await sleep(700);
    return { els: perEl(s), fps: fps(s), anims: s.anims, removed: s.removed, identity: s.identity };
  };

  // ---- A1i：可打断（450 动画进行到 ~380ms 反向再触发；打断窗内步长 ≤ 2+2×全局中位步长 = 无瞬移）----
  P.A1Ib = function () {
    var cards = visibleCards();
    var spans = segSpans();
    var orig = spans.find(function (x) { return x.classList.contains("on"); });
    var other = spans.find(function (x) { return x !== orig; });
    if (!cards.length || !other) return "NO-DATA";
    var s = begin(cards, { snapAt: [100, 420, 560, 700], pred: CARD_PRED, mo: false });
    s.intAt = -1;
    P.curS = s;
    other.click();
    setTimeout(function () { s.intAt = Math.round(performance.now() - s.t0); orig.click(); }, 380);
    return "A1i";
  };
  P.A1Ic = async function () {
    var s = P.curS;
    await end(1000);
    var allSteps = [], winSteps = [];
    for (var k = 1; k < s.frames.length; k++) {
      for (var j = 0; j < s.els.length; j++) {
        var a1 = s.frames[k - 1].g[j], b1 = s.frames[k].g[j];
        if (!a1 || !b1) continue;
        var d = dist(a1, b1);
        if (d < 0.2) continue;
        allSteps.push(d);
        if (s.frames[k].t >= s.intAt - 40 && s.frames[k - 1].t <= s.intAt + 40) winSteps.push(round(d));
      }
    }
    var st = allSteps.slice().sort(function (a, b) { return a - b; });
    var med = st.length ? round(st[Math.floor(st.length / 2)]) : 0;
    var maxWin = winSteps.length ? Math.max.apply(null, winSteps) : 0;
    return { els: perEl(s), fps: fps(s), anims: s.anims, intAt: s.intAt,
             cont: { medStep: med, cap: round(2 + 2 * med, 1), maxWinStep: maxWin, winN: winSteps.length,
                     ok: winSteps.length > 0 && maxWin <= 2 + 2 * med } };
  };

  // ---- A2：隐藏科目（该列位移 450 + 渐隐 opacity 250；随后时间流=列底区）----
  P.A2b = function () {
    var heads = Array.prototype.slice.call(document.querySelectorAll("#plan-canvas .col-h"))
      .filter(function (h) { return h.style.display !== "none" && !h.classList.contains("dim"); })
      .sort(function (a, b) { return a.getBoundingClientRect().left - b.getBoundingClientRect().left; });
    if (!heads.length) return "NO-HEAD";
    var head = heads[0];
    P.course2 = head.dataset.course;
    var cards = visibleCards();
    var s = begin(cards, { snapAt: [40, 120, 260], pred: CARD_PRED });
    P.curS = s;
    P.a2before = {};   // A3「回到隐藏前位置」的参照系：此刻全部卡的课程态坐标（数字 key，不涉课名）
    cards.forEach(function (c) { var r = c.getBoundingClientRect(); P.a2before[c.dataset.key] = [r.left, r.top]; });
    head.click();
    return "A2b";
  };
  P.A2c = async function () {
    var s = P.curS;
    await end(700);
    var excludedIdx = [];
    for (var i = 0; i < s.els.length; i++) if (s.els[i].classList.contains("excluded")) excludedIdx.push(i);
    segSpans().find(function (x) { return x.dataset.v === "time"; }).click();  // 切时间流：隐藏卡沉入右列底
    await sleep(750);
    var sunk = Array.prototype.slice.call(document.querySelectorAll("#plan-canvas .card.task.excluded"));
    // 「下方活卡」= 既未隐藏又可作答的卡（unsolvable 依 §3 规则排更底，不计入）
    var act = visibleCards().filter(function (c) { return !c.classList.contains("excluded") && !c.classList.contains("unsolvable"); });
    var sunkInfo = sunk.map(function (c) {
      var r = c.getBoundingClientRect();
      var below = act.filter(function (o) { var or = o.getBoundingClientRect(); return Math.abs(or.left - r.left) < 2 && or.top > r.top + 4; });
      return { key: c.dataset.key, offsetTop: c.offsetTop, belowActive: below.length, op: getComputedStyle(c).opacity };
    });
    var els = perEl(s).filter(function (x, i) { return excludedIdx.indexOf(i) >= 0; });
    return { anims: s.anims, els: els, removed: s.removed, sunkInfo: sunkInfo };
  };

  // ---- A3：点「已隐藏·点恢复」→ 反向位移回位（终态=隐藏前位置 ≤1px）----
  P.A3b = async function () {
    segSpans().find(function (x) { return x.dataset.v === "course"; }).click();
    await sleep(750);
    var cards = visibleCards().filter(function (c) { return c.classList.contains("excluded"); });
    if (!cards.length) return "NO-SUNK";
    var s = begin(cards, { snapAt: [40, 120, 260], pred: CARD_PRED });
    P.curS = s;
    return { n: s.els.length };
  };
  P.A3click = function () {
    var s = P.curS;
    var sel = document.querySelector("#plan-canvas .col-h[data-course='" + P.course2 + "']");
    if (!sel) return "NO-HEAD-PILL";
    s.clickAt = Math.round(performance.now() - s.t0);   // 采样窗以真实触发点为基准（begin→截图→本点击有 ~0.5s 间隔）
    sel.click();
    // 动画快照必须锚在触发后（begin 期阈值 40/120/260 全在点击前，run3/4 实锤抓空）
    s.anims.push({ at: s.clickAt, list: anims(s.pred) });
    setTimeout(function () { s.anims.push({ at: Math.round(performance.now() - s.t0), list: anims(s.pred) }); }, 250);
    setTimeout(function () { s.anims.push({ at: Math.round(performance.now() - s.t0), list: anims(s.pred) }); }, 420);
    return "A3-clicked";
  };
  P.A3c = async function () {
    var s = P.curS;
    await end((s.clickAt || 0) + 700);
    var refBack = [];
    for (var i = 0; i < s.els.length; i++) {
      var k = s.els[i].dataset.key, ref = P.a2before && P.a2before[k];
      if (!ref) continue;
      var r = s.els[i].getBoundingClientRect();
      refBack.push({ key: k, d: round(Math.hypot(r.left - ref[0], r.top - ref[1])) });
    }
    return { anims: s.anims, els: perEl(s), fps: fps(s), removed: s.removed, refBack: refBack };
  };

  // ---- A4+A9：审核落定（applyAuditItem=真实落定路径：滑行沉底 + pill 原地换字 + 灰化 250ms）----
  P.A49b = function () {
    var cards = visibleCards().filter(function (c) { return !c.classList.contains("excluded") && !c.classList.contains("unsolvable"); });
    var pick = null;
    for (var i = 0; i < cards.length; i++) {
      var r = cards[i].getBoundingClientRect();
      var below = cards.filter(function (o) { var or = o.getBoundingClientRect(); return Math.abs(or.left - r.left) < 2 && or.top > r.top + 2; });
      if (below.length >= 1) { pick = cards[i]; break; }
    }
    if (!pick) return "NO-CAND";
    var pill = pick.querySelector(".pill");
    var s = begin([pick, pill], { snapAt: [40, 120, 260, 420], mo: false,
                                  pred: function (t) { return t === pick || t === pill; } });
    s.meta = { key: pick.dataset.key, pillBefore: pill.textContent, pillNode: pill,
               stageH: document.querySelector("#plan-stage").clientHeight };
    P.curS = s;
    window.__m2.applyAuditItem({ key: pick.dataset.key, solvable: false, qreal: 0, reason: "非作业", preview: "" });
    return "A49-drop";
  };
  P.A49c = async function () {
    var s = P.curS;
    await end(750);
    var m = s.meta;
    var cur = document.querySelector("#plan-canvas [data-key='" + m.key + "']");
    var pillNow = cur ? cur.querySelector(".pill") : null;
    return {
      els: perEl(s), fps: fps(s), anims: s.anims,
      pill: { before: m.pillBefore, after: pillNow ? pillNow.textContent : null,
              sameNode: pillNow === m.pillNode && !!pillNow && pillNow.isConnected },
      cardOp: cur ? getComputedStyle(cur).opacity : null,
      cardGeom: cur ? { top: getComputedStyle(cur).top, left: getComputedStyle(cur).left, h: getComputedStyle(cur).height } : null,
      geomBefore: { top: s.before[0].ctop, left: s.before[0].cleft },
      stageH: m.stageH,
    };
  };

  // ---- A6：hover 阴影升起 150ms + 勾选 150ms ----
  P.A6b = function () {
    var cards = visibleCards().filter(function (c) { return !c.classList.contains("excluded") && !c.classList.contains("unsolvable"); });
    if (!cards.length) return "NO-CAND";
    var c = cards[0], chk = c.querySelector(".chk");
    var s = begin([c, chk], { snapAt: [], mo: false, pred: function (t) { return t === c || t === chk; } });
    s.meta = { key: c.dataset.key, restShadow: getComputedStyle(c).boxShadow, chkBeforeBg: getComputedStyle(chk).backgroundColor, hoverShadow: null };
    P.curS = s;
    var r = c.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  };
  P.A6poke = function () {
    var s = P.curS;
    s.meta.hoverShadow = getComputedStyle(s.els[0]).boxShadow;   // 对照（≈鼠标移动后 60ms，阴影过渡仍在飞）
    s.anims.push({ at: Math.round(performance.now() - s.t0), list: anims(s.pred) });   // hover 在飞快照
    s.els[1].click();     // 勾选（真实 onclick → class + 150ms 过渡）
    setTimeout(function () {
      s.anims.push({ at: Math.round(performance.now() - s.t0), list: anims(s.pred) });  // 点击后 ~95ms：勾选过渡在飞
    }, 95);
    return { hoverShadow: s.meta.hoverShadow };
  };
  P.A6c = async function () {
    var s = P.curS;
    await end(1300);
    var chk = s.els[1];
    var all = [];
    s.anims.forEach(function (x) { x.list.forEach(function (a) { all.push(a); }); });
    return { anims: all, fps: fps(s), geom: perEl(s), chkAfterBg: getComputedStyle(chk).backgroundColor,
             chkOn: chk.classList.contains("on"), restShadow: s.meta.restShadow, hoverShadow: s.meta.hoverShadow,
             chkBeforeBg: s.meta.chkBeforeBg };
  };

  // ---- A8：弹窗 scale+opacity 200ms + backdrop 渐暗（真实 confirmDlg 路径）----
  P.A8b = function () {
    var mask = document.getElementById("modal-mask");
    var s = { t0: performance.now(), anims: [] };
    P.a8 = s;
    window.__m2.showConfirm();
    var dlg = document.querySelector("#modal-box .dlg");
    var snap = function () {
      var now = Math.round(performance.now() - s.t0);
      document.getAnimations().forEach(function (a) {
        try {
          var ef = a.effect; if (!ef || !ef.target) return;
          if (ef.target !== dlg && ef.target !== mask) return;
          var t = ef.getTiming();
          s.anims.push({ at: now, target: ef.target === dlg ? "dlg" : "mask",
                         props: propsOf(ef), dur: t.duration, ease: t.easing,
                         kf: ef.getKeyframes().map(function (k) {
                           return (k.transform === undefined ? "" : k.transform) + "@" + (k.opacity === undefined ? "" : k.opacity);
                         }).join(">") });
        } catch (e) { }
      });
    };
    snap();
    setTimeout(snap, 50); setTimeout(snap, 120); setTimeout(snap, 180);
    return "A8-shown";
  };
  P.A8c = async function () {
    await sleep(280);
    var s = P.a8;
    var dlg = document.querySelector("#modal-box .dlg");
    var out = { anims: s.anims, dlgTransformNow: dlg ? getComputedStyle(dlg).transform : null };
    var no = document.querySelector('#modal-box [data-x="no"]');
    if (no) no.click();     // 关闭，不留遮罩
    return out;
  };

  // ---- A1r：prefers-reduced-motion 降级（无 transform 位移，仅 opacity 淡入）----
  P.A1Rb = function () {
    var cards = visibleCards();
    if (!cards.length) return "NO-DATA";
    var s = begin(cards, { snapAt: [40, 120, 260], pred: CARD_PRED, mo: false });
    var other = segSpans().find(function (x) { return !x.classList.contains("on"); });
    P.curS = s;
    other.click();
    return "A1r";
  };
  P.A1Rc = async function () {
    var s = P.curS;
    await end(650);
    var out = { els: perEl(s), fps: fps(s), anims: s.anims };
    var spans = segSpans();
    var cur = spans.find(function (x) { return x.classList.contains("on"); });
    spans.find(function (x) { return x !== cur; }).click();     // 复位
    await sleep(700);
    return out;
  };
  return "PROBE-HELPER-OK";
})()
`;

// ---------- 场景调度 ----------
const results = [];
function mkBag() { return { ok: 0, fail: 0, lines: [] }; }
function check(bag, name, ok, actual, expected) {
  bag.lines.push((ok ? "  ✓ " : "  ✗ ") + name + "  实测=" + JSON.stringify(actual) + "  期望=" + expected);
  if (ok) bag.ok++; else bag.fail++;
}
async function shots(scene, tBegin) {
  for (const m of [0, 150, 300, 450]) {
    const w = tBegin + m - Date.now();
    if (w > 0) await sleep(w);
    try {
      const s = await send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(OUT, scene + "-t" + m + ".png"), Buffer.from(s.data, "base64"));
    } catch (e) { console.log("  [SHOT-SKIP] " + scene + " t=" + m + ": " + e.message); }
  }
}
// 拍平快照并**继承 at**（A1i 的「intAt 之后」判据依赖 at；run4 丢字段致误 FAIL）
const flatAnims = (snaps) => { const out = []; for (const s of snaps) for (const a of (s.list || [s])) out.push(Object.assign({ at: s.at }, a)); return out; };
const hasAnim = (anims, pred) => anims.find(pred) || null;
function animViolations(snaps, allow) {
  const bad = [];
  for (const a of flatAnims(snaps)) {
    for (const p of String(a.props || "").split(",")) {
      if (p && !allow.includes(p)) bad.push((a.cls || "") + "·" + p + "(dur=" + a.dur + ")");
    }
  }
  return [...new Set(bad)];
}
async function runScene(name, beginFn, collectFn, assertFn, opts = {}) {
  if (SKIP.has(name)) { console.log("[SKIP] " + name + "（--skip）"); return null; }
  const bag = mkBag();
  console.log("\n### 场景 " + name);
  let ev = null;
  try {
    if (opts.beforeBegin) await opts.beforeBegin();
    const tB = Date.now();
    const b = await beginFn();
    if (typeof b === "string" && b.startsWith("NO-")) throw new Error("前置缺数据: " + b);
    await shots(name, tB);
    if (opts.afterBegin) await opts.afterBegin(b);
    ev = await collectFn();
    assertFn(ev, bag);
    if (opts.afterCollect) await opts.afterCollect();
  } catch (e) {
    check(bag, "场景执行", false, String(e.message).slice(0, 200), "无异常");
  }
  const pass = bag.fail === 0;
  results.push({ scene: name, pass, evidence: ev, lines: bag.lines });
  console.log(bag.lines.join("\n"));
  console.log((pass ? "[PASS] " : "[FAIL] ") + name);
  return ev;
}

// ---------- 断言 ----------
const assertions = {
  A7(ev, bag) {
    check(bag, "进度推进存在（width transitionrun ≥2 次）", ev.fillRuns.length >= 2, ev.fillRuns.length, "≥2");
    const durs = [...new Set(ev.fillRuns.map((x) => x.dur))];
    check(bag, "width 过渡时长 200ms", durs.includes("0.2s"), durs, '"0.2s"');
    check(bag, "缓动=emphasized", ev.fillRuns.every((x) => easeOk(x.ease)), [...new Set(ev.fillRuns.map((x) => x.ease))], "cubic-bezier(0.65,0,0.35,1)");
    const wa = hasAnim(ev.anims, (a) => a.props === "width" && Math.abs(a.dur - 200) <= 20);
    check(bag, "CSSTransition(width) 在飞命中 200±20", !!wa, wa && { dur: wa.dur, ease: wa.ease }, "dur≈200");
    check(bag, "文字不抖：「扫描 n/36」盒宽恒定", ev.textW.length === 1, ev.textW, "1 个值");
    check(bag, "n 确实推进（采样文本 ≥3 种）", ev.nVariants >= 3, ev.nVariants, "≥3");
  },
  A5(ev, bag) {
    const anims = flatAnims(ev.anims);
    const a = hasAnim(anims, (x) => x.props === "transform");
    check(bag, "胶囊 transform 动画存在", !!a, a && { cls: a.cls, dur: a.dur, ease: a.ease }, "≥1");
    if (a) {
      check(bag, "时长 200ms±20", Math.abs(a.dur - 200) <= 20, a.dur, "200±20");
      check(bag, "缓动=emphasized", easeOk(a.ease), a.ease, "cubic-bezier(0.65,0,0.35,1)");
    }
    check(bag, "起止 x 差 = 段宽", ev.ok, { moved: ev.moved, 段宽: ev.meta.fromW, fromX: ev.meta.fromX, toX: ev.meta.toX }, "|moved-段宽|≤1");
    check(bag, "只动 transform", animViolations(ev.anims, ALLOW.A5).length === 0, animViolations(ev.anims, ALLOW.A5), "[]");
  },
  A1(ev, bag) {
    check(bag, "卡片零 removeChild", ev.removed === 0, ev.removed, "0");
    check(bag, "DOM 节点 identity 全保持", ev.identity.every((x) => x.same && x.connected), ev.identity.filter((x) => !x.same || !x.connected).length + " 失配", "0");
    const moved = ev.els.filter((x) => x.movedPx >= 8);
    check(bag, "确有位移（≥1 卡 moved≥8px）", moved.length >= 1, moved.length, "≥1");
    for (const el of moved.slice(0, 3)) {
      const mono = el.dBefore.every((d, i) => i === 0 || d >= el.dBefore[i - 1] - 0.8) &&
                   el.dFinal.every((d, i) => i === 0 || d <= el.dFinal[i - 1] + 0.8);
      check(bag, el.id.slice(-8) + " 位移单调且起点=当前位置", mono && el.dBefore[0] <= 2, { 首帧: el.dBefore[0], 末帧: el.dFinal[el.dFinal.length - 1], 帧数: el.frames }, "首≤2、单调");
      check(bag, el.id.slice(-8) + " 末帧≈目标位误差≤1px", el.dFinal[el.dFinal.length - 1] <= 1, el.dFinal[el.dFinal.length - 1], "≤1");
      check(bag, el.id.slice(-8) + " 几何量全程恒定", Object.values(el.geomConst).every(Boolean), el.geomConst, "top/left/width/height/margin 全 const");
    }
    const anims = flatAnims(ev.anims);
    const t450 = hasAnim(anims, (x) => x.props === "transform" && Math.abs(x.dur - 450) <= 20 && easeOk(x.ease));
    check(bag, "transform 动画 450±20 + emphasized", !!t450, t450 && { dur: t450.dur, ease: t450.ease }, "450±20 + 曲线对");
    check(bag, "只动 transform/opacity", animViolations(ev.anims, ALLOW.A1).length === 0, animViolations(ev.anims, ALLOW.A1), "[]");
    check(bag, "帧率：中位≤20ms", ev.fps.median <= 20, ev.fps, "median≤20");
    check(bag, "帧率：无>120ms 空档", ev.fps.max <= 120, ev.fps.max, "≤120");
  },
  A1i(ev, bag) {
    check(bag, "打断时刻≈380ms（动画在飞）", ev.intAt >= 330 && ev.intAt <= 500, ev.intAt, "330~500");
    check(bag, "打断瞬间连续（无瞬移）", ev.cont.ok, ev.cont, "maxWinStep ≤ 2+2×中位步长");
    const anims = flatAnims(ev.anims);
    const post = hasAnim(anims, (x) => x.props === "transform" && Math.abs(x.dur - 450) <= 20 && x.at > ev.intAt + 10);
    check(bag, "打断后新动画 450ms（当前位置起）", !!post, post && { at: post.at, dur: post.dur, ease: post.ease }, "intAt 后 450±20");
    // 两段折返：净位移=0 但中途峰值≥8px，终态必须落回原位
    const moved = ev.els.filter((x) => x.peakPx >= 8);
    const settle = moved.length >= 1 && moved.every((x) => x.dFinal[x.dFinal.length - 1] <= 1);
    check(bag, "两段位移终态落位≤1px", settle, moved.length + " 卡中途峰值≥8px，末帧误差=" + moved.map((x) => x.dFinal[x.dFinal.length - 1]), "全≤1");
    check(bag, "只动 transform/opacity", animViolations(ev.anims, ALLOW.A1i).length === 0, animViolations(ev.anims, ALLOW.A1i), "[]");
  },
  A2(ev, bag) {
    const anims = flatAnims(ev.anims);
    const t450 = hasAnim(anims, (x) => x.props === "transform" && Math.abs(x.dur - 450) <= 20 && easeOk(x.ease));
    const o250 = hasAnim(anims, (x) => x.props === "opacity" && Math.abs(x.dur - 250) <= 20);
    check(bag, "位移 450±20 emphasized", !!t450, t450 && { dur: t450.dur, ease: t450.ease }, "450±20");
    check(bag, "opacity 250±20 渐隐", !!o250, o250 && { dur: o250.dur }, "250±20");
    check(bag, "两类动画同批并存（§2 A2 判据）", !!(t450 && o250), anims.map((a) => a.props + "@" + a.dur).slice(0, 10), "transform450 + opacity250");
    check(bag, "隐藏卡沉底后列内最下（下方无未隐藏卡）", ev.sunkInfo.length > 0 && ev.sunkInfo.every((x) => x.belowActive === 0), ev.sunkInfo.map((x) => ({ offTop: x.offsetTop, 下方活卡: x.belowActive })), "belowActive=0");
    check(bag, "终态灰化 opacity=.35", ev.sunkInfo.every((x) => near(Number(x.op), 0.35, 0.02)), ev.sunkInfo.map((x) => x.op), "0.35");
    check(bag, "只动 transform/opacity", animViolations(ev.anims, ALLOW.A2).length === 0, animViolations(ev.anims, ALLOW.A2), "[]");
    check(bag, "卡片零 removeChild", ev.removed === 0, ev.removed, "0");
  },
  A3(ev, bag) {
    const moved = ev.els.filter((x) => x.movedPx >= 8);
    check(bag, "恢复确有反向位移", moved.length >= 1, moved.length, "≥1");
    const anims = flatAnims(ev.anims);
    const t450 = hasAnim(anims, (x) => x.props === "transform" && Math.abs(x.dur - 450) <= 20 && easeOk(x.ease));
    check(bag, "450±20 emphasized 回位", !!t450, t450 && { dur: t450.dur, ease: t450.ease }, "450±20");
    const refOk = ev.refBack.length >= 1 && ev.refBack.every((x) => x.d <= 1);
    check(bag, "回到隐藏前位置（参照=A2 隐藏前快照，≤1px）", refOk, ev.refBack.map((x) => x.d), "全≤1");
    check(bag, "只动 transform/opacity", animViolations(ev.anims, ALLOW.A3).length === 0, animViolations(ev.anims, ALLOW.A3), "[]");
    check(bag, "卡片零 removeChild", ev.removed === 0, ev.removed, "0");
  },
  A4(ev, bag) {
    const card = ev.els[0];
    check(bag, "沉底位移≥30px（禁瞬移的前提）", card.movedPx >= 30, card.movedPx, "≥30");
    const moving = card.deltas.filter((d) => d > 0.3).length;
    check(bag, "坐标变化跨 ≥5 帧", moving >= 5, moving, "≥5");
    const mono = card.dBefore.every((d, i) => i === 0 || d >= card.dBefore[i - 1] - 0.8) &&
                 card.dFinal.every((d, i) => i === 0 || d <= card.dFinal[i - 1] + 0.8);
    check(bag, "滑行单调", mono, { 首帧: card.dBefore[0], 末帧: card.dFinal[card.dFinal.length - 1] }, "首≤2 末≤1 单调");
    const maxStep = Math.max(...card.deltas);
    check(bag, "无单帧跳变 > 0.6×列高", maxStep <= 0.6 * ev.stageH, { maxStep: round(maxStep, 1), cap: round(0.6 * ev.stageH, 1) }, "≤cap");
    check(bag, "采样窗内几何量恒定（写 top/left 在窗外一次完成）", Object.values(card.geomConst).every(Boolean), card.geomConst, "top/left/width/height/margin 全 const");
    const cardAnims = flatAnims(ev.anims).filter((x) => x.target && x.target.includes("card"));   // pill 上色属 A9
    check(bag, "卡片只动 transform/opacity", animViolations(cardAnims.map((x) => ({ list: [x] })), ALLOW.A4).length === 0, animViolations(cardAnims.map((x) => ({ list: [x] })), ALLOW.A4), "[]");
    check(bag, "终态 opacity=.55（不可作答置灰）", near(Number(ev.cardOp), 0.55, 0.02), ev.cardOp, "0.55");
  },
  A9(ev, bag) {
    check(bag, "pill 节点不变（同实例在树）", ev.pill.sameNode, ev.pill.sameNode, "true");
    check(bag, "pill 文字原地换", ev.pill.before !== ev.pill.after && ev.pill.after === "非作业", { before: ev.pill.before, after: ev.pill.after }, "变且=非作业");
    const anims = flatAnims(ev.anims);
    const cardFade = hasAnim(anims, (x) => x.props === "opacity" && Math.abs(x.dur - 250) <= 20);
    const pillTint = hasAnim(anims, (x) => /background-color/.test(x.props) && Math.abs(x.dur - 250) <= 20);
    check(bag, "卡片灰化 opacity 250±20", !!cardFade, cardFade && { dur: cardFade.dur }, "250±20");
    check(bag, "pill 色底过渡 250±20", !!pillTint, pillTint && { props: pillTint.props, dur: pillTint.dur }, "250±20");
    check(bag, "只动 opacity/颜色（无布局属性动画）", animViolations(ev.anims, ALLOW.A9).length === 0, animViolations(ev.anims, ALLOW.A9), "[]");
  },
  A6(ev, bag) {
    const sh = hasAnim(ev.anims, (x) => /box-shadow/.test(x.props) && Math.abs(x.dur - 150) <= 20);
    check(bag, "hover 阴影升起 150±20 过渡", !!sh, sh && { dur: sh.dur, ease: sh.ease }, "150±20");
    check(bag, "hover 后 box-shadow ≠ 静息", !!ev.hoverShadow && ev.restShadow !== ev.hoverShadow, { rest: ev.restShadow, hover: ev.hoverShadow }, "不同值");
    // 每属性各一条 CSSTransition（背景与描边是两条独立动画）
    const bg = hasAnim(ev.anims, (x) => /background-color/.test(x.props) && Math.abs(x.dur - 150) <= 20);
    const bd = hasAnim(ev.anims, (x) => /border(-[a-z]+)?-color/.test(x.props) && Math.abs(x.dur - 150) <= 20);
    check(bag, "勾选 150±20（背景+描边两条）", !!bg && !!bd, { bg: bg && bg.dur, bd: bd && bd.props + "@" + bd.dur }, "各 150±20");
    check(bag, "勾选态生效（on + 底色变）", ev.chkOn && ev.chkAfterBg !== ev.chkBeforeBg, { on: ev.chkOn, before: ev.chkBeforeBg, after: ev.chkAfterBg }, "true");
    check(bag, "只动 paint 属性", animViolations(ev.anims.map((x) => ({ list: [x] })), ALLOW.A6).length === 0, animViolations(ev.anims.map((x) => ({ list: [x] })), ALLOW.A6), "[]");
  },
  A8(ev, bag) {
    const anims = ev.anims;
    const dlg = hasAnim(anims, (x) => x.target === "dlg" && /transform/.test(x.props) && /scale\(0?\.95\)/.test(x.kf) && Math.abs(x.dur - 200) <= 20);
    check(bag, "弹窗 scale 0.95→1 + 200±20", !!dlg, dlg && { props: dlg.props, dur: dlg.dur, kf: dlg.kf }, "scale(.95)→scale(1) 200");
    const opac = hasAnim(anims, (x) => x.target === "dlg" && /opacity/.test(x.props) && /@0/.test(x.kf));
    check(bag, "弹窗 opacity 0→1", !!opac, opac && opac.kf, "含 0→1");
    const mask = hasAnim(anims, (x) => x.target === "mask" && x.props === "opacity" && Math.abs(x.dur - 200) <= 20 && /0@0>1@1|@0.*>.*1/.test(x.kf));
    check(bag, "backdrop 渐暗 0→目标 200±20", !!mask, mask && { dur: mask.dur, kf: mask.kf }, "opacity 0→1 200");
    check(bag, "缓动=emphasized", anims.every((x) => easeOk(x.ease)), [...new Set(anims.map((x) => x.ease))], "cubic-bezier(0.65,0,0.35,1)");
    check(bag, "只动 transform/opacity", animViolations(anims.map((x) => ({ list: [x] })), ALLOW.A8).length === 0, animViolations(anims.map((x) => ({ list: [x] })), ALLOW.A8), "[]");
  },
  A1r(ev, bag) {
    const moved = ev.els.filter((x) => x.movedPx >= 8);
    check(bag, "reduce 下确有一次性换轨（≥1 卡位置变）", moved.length >= 1, moved.length, "≥1");
    const medSteps = moved.map((el) => {
      const s = el.deltas.filter((d) => d > 0).sort((a, b) => a - b);
      return s.length ? s[Math.floor(s.length / 2)] : 0;
    });
    const maxMed = Math.max(0, ...medSteps);
    check(bag, "无逐帧滑行（中位步长<2px=瞬移非动画位移）", maxMed < 2, medSteps.map(round), "<2");
    const anims = flatAnims(ev.anims);
    const tf = anims.filter((x) => /transform/.test(x.props));
    check(bag, "无 transform 动画", tf.length === 0, tf.map((x) => x.props + "@" + x.dur), "0 个");
    const of = hasAnim(anims, (x) => x.props === "opacity");
    check(bag, "仅 opacity 淡入动画存在", !!of, of && { props: of.props, dur: of.dur }, "≥1");
  },
};

// ---------- 主流程 ----------
async function main() {
  console.log("[setup] 出证目录 " + OUT);
  const srv = await startServer();
  console.log("[setup] server 就绪 port=" + srv.port + " token=***");
  let webPort = 1430, webSrv;
  try { webSrv = await startStatic(1430); }
  catch (e) { if (e.code === "EADDRINUSE") { webSrv = await startStatic(1420); webPort = 1420; } else throw e; }
  console.log("[setup] 静态服务 http://127.0.0.1:" + webPort + "（CORS 白名单内 origin，server 零改动）");

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
  await send("Page.enable"); await send("Runtime.enable");   // Input 域无需 enable（dispatchMouseEvent 直用）
  await send("Page.navigate", { url: `http://127.0.0.1:${webPort}/index.html?cxport=${srv.port}&cxtoken=${srv.token}&cxprobe=1&cxscan=0` });
  await wait("!!window.__m2 && document.body.dataset.m2hook === '1'", 60000);
  console.log("[setup] boot + __m2 钩子就绪");
  console.log("[setup] 页内采样器: " + (await evaluate(HELPER)));

  // A7：真实扫描+自动验卷（其余场景依赖卡片在场，此场景最先）
  await runScene("A7", () => evaluate("window.__P.A7b()"), () => evaluate("window.__P.A7c()"), assertions.A7,
    { afterBegin: async () => { await wait("document.body.dataset.m2scan === '1'", SCAN_TIMEOUT, 1000); } });
  await sleep(1200);

  await runScene("A5", () => evaluate("window.__P.A5b()"), () => evaluate("window.__P.A5c()"), assertions.A5);
  await runScene("A1", () => evaluate("window.__P.A1b()"), () => evaluate("window.__P.A1c()"), assertions.A1);
  await runScene("A1i", () => evaluate("window.__P.A1Ib()"), () => evaluate("window.__P.A1Ic()"), assertions.A1i);

  // A2 → A3（连续场景共享状态：A3 begin 内部先回科目列）
  await runScene("A2", () => evaluate("window.__P.A2b()"), () => evaluate("window.__P.A2c()"), assertions.A2);
  await runScene("A3", () => evaluate("window.__P.A3b()"), () => evaluate("window.__P.A3c()"), assertions.A3,
    { afterBegin: async (b) => { if (b && typeof b === "object") { const c = await evaluate("window.__P.A3click()"); if (c === "NO-HEAD-PILL") throw new Error("NO-HEAD-PILL"); } } });

  // A4+A9 同一落定事件，两组独立判据
  const ev49 = await runScene("A4+A9", () => evaluate("window.__P.A49b()"), () => evaluate("window.__P.A49c()"),
    (ev, bag) => check(bag, "落定事件已触发并采到证据", !!ev && !!ev.pill && ev.els.length === 2, !!ev && !!ev.pill, "pill+card 双元素证据"));
  if (ev49) {
    results.pop();
    for (const [label, fn] of [["A4", assertions.A4], ["A9", assertions.A9]]) {
      const bag = mkBag();
      try { fn(ev49, bag); } catch (e) { check(bag, "断言执行", false, String(e.message), "无异常"); }
      results.push({ scene: label, pass: bag.fail === 0, evidence: ev49, lines: bag.lines });
      console.log("\n### 场景 " + label);
      console.log(bag.lines.join("\n"));
      console.log((bag.fail === 0 ? "[PASS] " : "[FAIL] ") + label);
    }
  } else {
    for (const label of ["A4", "A9"]) results.push({ scene: label, pass: false, evidence: null, lines: ["  ✗ 场景前置失败"] });
  }

  // A6：真实鼠标 hover（CDP Input）+ 勾选
  let a6coords = null;
  await runScene("A6",
    async () => { a6coords = await evaluate("window.__P.A6b()"); if (a6coords && a6coords.x) return a6coords; return "NO-CAND"; },
    () => evaluate("window.__P.A6c()"), assertions.A6,
    { afterBegin: async () => {
        if (!a6coords || !a6coords.x) return;
        await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: a6coords.x, y: a6coords.y });
        await sleep(60);                              // 悬停过渡(150ms)在飞时 poke 抓样
        await evaluate("window.__P.A6poke()");
        await sleep(500);                             // 等勾选过渡阈值快照(640/780/920)落袋
      } });

  await runScene("A8", () => evaluate("window.__P.A8b()"), () => evaluate("window.__P.A8c()"), assertions.A8);

  // A1r：CDP 置 reduce → 场景 → 还原
  await runScene("A1r", () => evaluate("window.__P.A1Rb()"), () => evaluate("window.__P.A1Rc()"), assertions.A1r,
    { beforeBegin: async () => { await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] }); },
      afterCollect: async () => { await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] }); } });

  // ---------- 汇总 ----------
  const scenePass = (n) => { const r = results.find((x) => x.scene === n); return !!r && r.pass; };
  const rows = [
    ["A1", scenePass("A1") && scenePass("A1i") && scenePass("A1r")],
    ["A2", scenePass("A2")],
    ["A3", scenePass("A3")],
    ["A4", scenePass("A4")],
    ["A5", scenePass("A5")],
    ["A6", scenePass("A6")],
    ["A7", scenePass("A7")],
    ["A8", scenePass("A8")],
    ["A9", scenePass("A9")],
  ];
  const errs = await evaluate("window.__P.errs").catch(() => []);
  const passed = rows.filter((r) => r[1]).length;
  console.log("\n============================================================");
  for (const [n, ok] of rows) {
    console.log((ok ? "PASS  " : "FAIL  ") + n + "  " + (ok ? "" : "(" + results.filter((r) => !r.pass).map((r) => r.scene).join("/") + " 组内失败)"));
  }
  if (errs && errs.length) console.log("[WARN] 页内 JS 错误: " + JSON.stringify(errs));
  console.log("ANIM: " + passed + "/9 PASS");

  fs.writeFileSync(path.join(OUT, "scenes.json"), JSON.stringify({
    meta: { when: new Date().toISOString(), webOrigin: "http://127.0.0.1:" + webPort, serverPort: srv.port,
            window: "1280x860", ua: await evaluate("navigator.userAgent").catch(() => "") },
    summary: rows.map(([n, ok]) => ({ scene: n, pass: ok })), pageErrs: errs, results,
  }, null, 1), "utf-8");
  console.log("[out] 证据已落 " + path.join(OUT, "scenes.json"));

  try { await send("Browser.close"); } catch (_) { }
  await Promise.race([new Promise((r) => edge.on("exit", r)), sleep(8000)]);
  killAll();
  process.exit(0);
}

main().catch((e) => { console.error("PROBE-FATAL: " + e.message); try { if (ws) send("Browser.close").catch(() => { }); } catch (_) { } killAll(); process.exit(1); });
