// store.js —— 前端共享态 + 全局日志汇（M1-2 各屏的唯一数据面）
// 任务/审批/jobs 的真相在 server（内存态），这里只镜像渲染所需 + 本地 UI 态：
// 勾选集（DESIGN 底栏「已选数」）、黑名单（交互规则1：仍可勾选、置灰沉底）、日志。
// 持久化层纪律：Tauri dev 协议 http://tauri.localhost 下 WebView2 判不可靠 origin，
// localStorage 直接访问会 SecurityError（模块 import 期抛错=整图崩，实测截图定案），
// 一律走 lsGet/lsSet 吞错降级为会话态。M3 打包/稳定 origin 下自然恢复持久。
import { net } from "./api.js";

const LS = (() => {
  try {
    localStorage.setItem("__cxprobe", "1"); localStorage.removeItem("__cxprobe");
    return localStorage;
  } catch (_) { return null; }   // 不可靠 origin：持久化静默降级
})();
const lsGet = (k) => { try { return LS && LS.getItem(k); } catch (_) { return null; } };
const lsSet = (k, v) => { try { if (LS) LS.setItem(k, v); } catch (_) {} };
export { lsGet, lsSet };   // M3 I2 折叠态持久化等 UI 态复用（吞错降级语义同在）

export const store = {
  tasks: [],                 // GET /tasks 镜像（含审核三态字段）
  selected: new Set(),       // 作业计划勾选（任务 key）
  excluded: new Set(JSON.parse(localStorage.getItem("cx.excluded") || "[]")),
  jobs: {},                  // job_id -> {title, ref, questions, results, progress, state}
  settings: null,            // GET /settings 原样（providers 已脱敏）
  busy: null,                // "scan"|"audit"|"solve"|null（409 busy 同步置位）
  log: [],                   // [{ts, msg, cls}]
  scanTotal: 0,              // /scan 分母（进度条 + 「扫描 n/total」）
  listeners: [],

  on(fn) { store.listeners.push(fn); },
  emit(what) {
    if (what === "jobs") store.persistJobs();
    if (what === "log") store.persistLog();
    store.listeners.forEach((fn) => fn(what));
  },

  // 日志缓冲持久化：页面重载（headless 验收逐屏截图 / 用户刷新）不失忆
  persistLog() {
    try { localStorage.setItem("cx.log", JSON.stringify(store.log.slice(-2000))); } catch (_) {}
  },
  hydrateLog() {
    try {
      const arr = JSON.parse(localStorage.getItem("cx.log") || "[]");
      if (Array.isArray(arr) && arr.length && !store.log.length) { store.log.push(...arr); store.emit("log"); }
    } catch (_) {}
  },

  persistExcluded() {
    localStorage.setItem("cx.excluded", JSON.stringify([...store.excluded]));
  },

  // jobs 水合：server 审批/提交以内存 STATE.jobs 为准（sidecar 重启即失），
  // 视图镜像持久化在 localStorage——刷新/重进窗口后队列与审批屏不空；
  // 若 server 已重启，提交路径按 404 job_not_found 报「需重新领卷」。
  persistJobs() {
    try {
      const out = {};
      for (const [id, j] of Object.entries(store.jobs)) {
        out[id] = { ...j, accepted: [...(j.accepted || [])] };
      }
      localStorage.setItem("cx.jobs", JSON.stringify(out));
    } catch (_) { /* 容量满等静默 */ }
  },
  hydrateJobs() {
    try {
      const raw = JSON.parse(localStorage.getItem("cx.jobs") || "{}");
      for (const [id, j] of Object.entries(raw)) {
        if (!store.jobs[id]) store.jobs[id] = { ...j, accepted: new Set(j.accepted || []), hydrated: true };
      }
    } catch (_) { /* 坏档忽略 */ }
  },

  // M3b R1 幽灵任务自愈：sidecar 重启=新进程，STATE.jobs 早空、无 worker 在跑——
  // hydrate 回来的 running 卡永远不推进。boot 时对账 GET /jobs（R3）：
  //   server 无此 job   → interrupted +「已中断（重启前未完成）」，取消运行中外观（渲染/删除见 queue/approve）；
  //   server 有此 job   → 以 server 真实 state/progress/low 为准（不信任 localStorage；
  //                        server 侧 busy 释放后悬在 init/running 的流也会被报成 interrupted——诚实态）；
  //   对账失败（网络/401/428）→ 保持原样 + 记一条日志，绝不误判 interrupted、不清空、不伪造。
  async reconcileJobs() {
    const ids = Object.keys(store.jobs);
    if (!ids.length) return { ok: true, checked: 0 };
    let d;
    try {
      d = await net.api("/jobs");
    } catch (e) {
      const why = e.status === 401 ? "未登录/凭据失效"
        : e.status === 428 ? "无凭据" : `server 不可达（${String(e.message || e).slice(0, 60)}）`;
      store.addLog(`!! 任务对账跳过：${why}——${ids.length} 个 job 保持原状态（未误标已中断）`, "warn");
      return { ok: false, kept: ids.length };
    }
    const byId = {};
    (d.jobs || []).forEach((x) => { byId[x.job_id] = x; });
    let lost = 0, synced = 0;
    for (const id of ids) {
      const j = store.jobs[id];
      const live = byId[id];
      if (!live) {
        j.state = "interrupted";
        j.interruptNote = "已中断（重启前未完成）";
        lost++;
      } else if (live.state === "interrupted") {
        j.state = "interrupted";
        j.interruptNote = "已中断（服务端任务已停）";
        lost++;
      } else {
        j.state = live.state;
        if (live.progress) j.progress = live.progress;
        j.low = live.low || [];
        if (live.started) j.started = live.started;
        delete j.interruptNote;
        synced++;
      }
    }
    if (d.busy && !store.busy) store.busy = d.busy.mode;   // server 占用态镜像（前端没起任务时）
    if (lost || synced) {
      store.addLog(`任务对账：${synced} 个以 server 为准，${lost} 个标记已中断（可删除）`,
                   lost ? "warn" : "");
      store.emit("jobs");   // 落 localStorage（interrupted 态持久化，重启不反复横跳）
    }
    return { ok: true, lost, synced };
  },

  // M3b R4：删卡=删本地镜像条目；emit("jobs") 联动 persistJobs + 徽标 + 审批勾选态
  //（accepted 挂在 job 对象上，随删消失；localStorage["cx.jobs"] 重写）
  removeJob(id) {
    if (!store.jobs[id]) return false;
    delete store.jobs[id];
    store.emit("jobs");
    return true;
  },

  // 全局日志行（运行日志屏 + 引擎 SSE log 事件同源汇入）
  // 语义色规则（DESIGN B3d/日志）：「!! 」err / 「⚠ 」warn / 完成·成功 ok / 其余正文
  addLog(msg, cls) {
    if (!cls) {
      cls = msg.startsWith("!! ") || /失败|error/i.test(msg) ? "err"
          : msg.startsWith("⚠") ? "warn"
          : /完成|成功|OK|通过/.test(msg) ? "ok" : "";
    }
    const ts = new Date().toTimeString().slice(0, 8);
    store.log.push({ ts, msg, cls });
    if (store.log.length > 2000) store.log.splice(0, store.log.length - 2000);
    store.emit("log");
  },

  taskByRef(courseId, workId) {
    return store.tasks.find((t) => t.courseId === courseId && t.workId === workId) || null;
  },
};

// 课程色点：按课程哈希取 [红橙黄绿青蓝紫] 环，同科恒同色（DESIGN 色彩表）
const RING = ["#FF3B30", "#FF9F0A", "#FFD60A", "#34C759", "#5AC8FA", "#0A84FF", "#5E5CE6"];
export function courseColor(name) {
  let h = 0;
  const s = String(name || "");
  for (let i = 0; i < s.length; i++) { h = (h * 131 + s.charCodeAt(i)) | 0; }
  return RING[Math.abs(h) % RING.length];
}

// 错误提示句式（DESIGN 文案规范）：<对象> <动作>失败：<原因(截120)>，可<补救>
export function errText(obj, act, e, rescue) {
  const reason = String((e && (e.detail || e.message)) || e || "未知").slice(0, 120);
  return `${obj} ${act}失败：${reason}，可${rescue || "重试"}`;
}

// 长任务包装：置 busy（按钮禁用态靠它），409 busy 归一提示
// 人话映射：用户不该看到 audit/solve 这种内部代号，也不该只被告知「被拒」
const MODE_LABEL = { scan: "扫描", audit: "验卷（逐条核验作业能否作答）", solve: "解题", submit: "提交" };
const modeName = (m) => MODE_LABEL[m] || m || "其它任务";
const busyHint = (m) => `${modeName(m)}正在跑，同一时刻只允许一个任务`
  + `；等运行日志里它显示完成后，再点一次即可；急着用可在日志屏点「取消」中断它`;

export async function withBusy(mode, fn) {
  if (store.busy) {
    store.addLog(`!! ${busyHint(store.busy)}（本次${modeName(mode)}没有提交）`, "err");
    return false;
  }
  store.busy = mode; store.emit("busy");
  try { await fn(); return true; }
  catch (e) {
    if (e.status === 409 && e.detail === "busy") {
      store.addLog(`!! ${busyHint((e.data && e.data.current) || "?")}（本次${modeName(mode)}没有提交）`, "err");
    } else if (e.status !== 401 && e.status !== 428) {
      store.addLog("!! " + errText(mode, "请求", e), "err");
    }
    return false;
  } finally {
    store.busy = null; store.emit("busy");
  }
}

// 拉任务表（纯内存零网络，M0 任务书 §2.6）
export async function refreshTasks() {
  const d = await net.api("/tasks");
  store.tasks = d.tasks;
  store.emit("tasks");
  return d;
}
