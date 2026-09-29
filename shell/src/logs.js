// logs.js —— 屏4 运行日志（M1 任务书 §2.4：Consolas 等宽 + 语义色，DESIGN B3d 日志行）
import { store } from "./store.js";

let box;

function render() {
  box.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const ln of store.log) {
    const el = document.createElement("div");
    el.className = "ln " + (ln.cls || "");
    const ts = document.createElement("span");
    ts.className = "ts"; ts.textContent = ln.ts;
    const msg = document.createElement("span");
    msg.textContent = ln.msg;
    el.append(ts, msg);
    frag.appendChild(el);
  }
  box.appendChild(frag);
  box.parentElement.scrollTop = 1e9;   // 贴底跟随
}

export function initLogs(rootEl) {
  rootEl.innerHTML = `
    <div class="toolbar"><div class="h1">运行日志</div>
      <div class="spacer"></div>
      <span class="hint">引擎 log / SSE 事件 / HTTP 动作统一汇入（!! err · ⚠ warn · ok）</span>
      <button class="btn tiny grey" id="log-clear">清屏</button></div>
    <div class="stage log-stage"><div class="logbox" id="logbox"></div></div>`;
  box = rootEl.querySelector("#logbox");
  rootEl.querySelector("#log-clear").onclick = () => { store.log.length = 0; render(); };
  render();
  store.on((what) => { if (what === "log") render(); });
}
