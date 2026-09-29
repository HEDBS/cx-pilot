// tools/e2e_ui.mjs —— M1 五屏验收驱动（CDP over headless Edge；node>=22 内置 fetch/WebSocket）
// 用途：拉起 headless Edge → 导航 → 轮询等待表达式 → 存 outerHTML 证据 / 截图。
// 例：node tools/e2e_ui.mjs --url "http://localhost:1420/?..." --wait "document.body.dataset.m1pipeline" \
//       --dom out/dom-plan.html --shot out/shot-plan.png --port 9333 --profile out/edge-profile --timeout 300000
// 说明：--dump-dom 在 load 即出、不等异步渲染，不可用于 SPA；本工具以 CDP 显式等待替代。
import { spawn } from "node:child_process";
import fs from "node:fs";

const arg = (k, d = "") => {
  const i = process.argv.indexOf("--" + k);
  return i > 0 ? process.argv[i + 1] : d;
};
const URL_ = arg("url");
const WAIT = arg("wait", "true");
const DOM = arg("dom");
const SHOT = arg("shot");
const PORT = Number(arg("port", "9333"));
const PROFILE = arg("profile");
const TIMEOUT = Number(arg("timeout", "120000"));
const EDGE = arg("edge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe");
const SHOT_WAIT = Number(arg("shot-wait", "1200"));   // 满足 wait 后的稳定期（等 layout 落位）

if (!URL_) { console.error("需要 --url"); process.exit(2); }

const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--no-first-run",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
  "--window-size=1280,860", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getWsUrl() {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page && page.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch (_) { /* edge 未就绪 */ }
    await sleep(400);
  }
  throw new Error("CDP 端点未出现（edge 启动失败？）");
}

let mid = 0;
const pending = new Map();
let ws;
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++mid;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error("eval 异常: " + JSON.stringify(r.exceptionDetails).slice(0, 200));
  return r.result.value;
}

function killEdge() {
  try { spawn("taskkill", ["/T", "/F", "/PID", String(edge.pid)], { stdio: "ignore" }); } catch (_) {}
}
process.on("exit", killEdge);

try {
  const wsUrl = await getWsUrl();
  ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
      m.error ? reject(new Error(m.error.message)) : resolve(m.result);
    }
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: URL_ });

  const deadline = Date.now() + TIMEOUT;
  let okFlag = false;
  while (Date.now() < deadline) {
    try {
      if (await evaluate(`(function(){try{return !!(${WAIT})}catch(_){return false}})()`)) { okFlag = true; break; }
    } catch (_) { /* 导航早期 eval 失败容忍 */ }
    await sleep(500);
  }
  if (!okFlag) { console.error(`WAIT-TIMEOUT: ${WAIT}`); process.exit(3); }
  await sleep(SHOT_WAIT);
  console.log(`WAIT-OK: ${WAIT}`);

  if (DOM) {
    const html = await evaluate("document.documentElement.outerHTML");
    fs.writeFileSync(DOM, html, "utf-8");
    console.log("DOM->" + DOM + " (" + Buffer.byteLength(html) + "B)");
  }
  if (SHOT) {
    const s = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(SHOT, Buffer.from(s.data, "base64"));
    console.log("SHOT->" + SHOT);
  }
  // 优雅退出（Browser.close=干净关停）：taskkill /F 会丢 localStorage 落盘（实测），
  // 而验收链依赖 jobs/log 水合——必须等浏览器自己 flush。
  try { await send("Browser.close"); } catch (_) { /* ws 先关属预期 */ }
  await Promise.race([new Promise((r) => edge.on("exit", r)), sleep(8000)]);
  killEdge();   // 兜底
  process.exit(0);
} catch (e) {
  console.error("E2E-UI-FAIL: " + e.message);
  try { await send("Browser.close"); } catch (_) {}
  await sleep(1500);
  killEdge();
  process.exit(1);
}
