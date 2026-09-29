// api.js —— M1-1 数据源对接（M1 任务书 §1）
//  1) Rust cx_info 拿 {port, token}（浏览器开发回退：URL 参数 cxport/cxtoken，仅非 Tauri 态生效）；
//  2) api(path, opts)：统一带 X-CX-Token，JSON 进出；
//  3) sse(path, body, onEvent)：POST + fetch 流式读 text/event-stream（EventSource 不支持 POST，弃用）；
//  4) 401 → 重登、428 no_credentials → 首登：以 window 'cx-auth' 事件广播，reason 区分；
//     SSE 流内 error 事件 msg=session_expired/no_credentials 同样触发（TASK §1）。
const listeners = { auth: [] };

export const net = {
  onAuth(fn) { listeners.auth.push(fn); },
  fireAuth(reason) { listeners.auth.forEach((fn) => fn(reason)); },

  info: null, // {port, token, mode}

  baseUrl() {
    if (!net.info) throw new Error("sidecar 未握手");
    return `http://127.0.0.1:${net.info.port}`;
  },

  // 握手：优先 Rust cx_info（真壳）；无 __TAURI__ 时用 URL 参数（headless 验收/开发预览）
  async handshake(tries = 60) {
    const invoke = window.__TAURI__ ? window.__TAURI__.core.invoke : null;
    if (invoke) {
      for (let i = 0; i < tries; i++) {
        const info = await invoke("cx_info").catch(() => null);
        if (info && info.port) { net.info = info; return info; }
        await new Promise((r) => setTimeout(r, 500));
      }
      return null;
    }
    const q = new URLSearchParams(location.search);
    const port = Number(q.get("cxport") || 0);
    const token = q.get("cxtoken") || "";
    if (port && token) {
      net.info = { port, token, mode: "browser-dev" };
      return net.info;
    }
    return null;
  },

  // 统一 JSON 请求；401/428 自动触发 cx-auth（登录框自身除外，防递归）
  async api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    init.headers["X-CX-Token"] = net.info ? net.info.token : "";
    const r = await fetch(net.baseUrl() + path, init);
    let data = null;
    try { data = await r.json(); } catch (_) { /* 空 body */ }
    if (r.status === 401 && path !== "/auth/login") net.fireAuth("session_expired");
    if (r.status === 428) net.fireAuth("no_credentials");
    if (!r.ok) {
      const e = new Error((data && data.detail) || `HTTP ${r.status}`);
      e.status = r.status; e.detail = data && data.detail; e.data = data;
      throw e;
    }
    return data;
  },

  // POST SSE：逐事件回调 onEvent(obj)；非 200（busy 409 / 鉴权 401/428）按 JSON 错误抛出。
  // 流内 error 事件命中会话态也广播 cx-auth（TASK §1：401→重登 / 428→首登）。
  async sse(path, body, onEvent) {
    const r = await fetch(net.baseUrl() + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CX-Token": net.info.token },
      body: JSON.stringify(body || {}),
    });
    const ctype = r.headers.get("content-type") || "";
    if (!r.ok || !ctype.includes("event-stream")) {
      let data = null;
      try { data = await r.json(); } catch (_) { /* ignore */ }
      if (r.status === 401) net.fireAuth("session_expired");
      if (r.status === 428) net.fireAuth("no_credentials");
      const e = new Error((data && data.detail) || `HTTP ${r.status}`);
      e.status = r.status; e.detail = data && data.detail;
      throw e;
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder("utf-8");
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
          if (ev.type === "error" &&
              (ev.msg === "session_expired" || ev.msg === "no_credentials")) {
            net.fireAuth(ev.msg);
          }
          onEvent(ev);
        }
      }
    }
  },
};
