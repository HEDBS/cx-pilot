// theme.js —— M4 T3 双主题 + T2 可见性暂停（TASK-M4-visual.md）
// 职责：① system/light/dark 三态循环（顶栏 #theme-btn），持久化 localStorage("cx.theme")；
//       ② system 模式下 matchMedia(prefers-color-scheme) 实时跟随；
//       ③ document.hidden → html.aurora-paused（CSS 暂停 Aurora background-position 动画——
//          线性重绘动画不许在后台偷吃 CPU/GPU，任务书 §T2 性能红线）。
// 首帧主题由 index.html 内联引导脚本 pre-paint 落定（防闪色 + 防 N2p「刷新无在跑过渡」），
// 本模块只接管交互与持久化；dataset.theme 值域与引导脚本严格一致（"dark"|"light"）。
import { lsGet, lsSet } from "./store.js";

export const MODES = ["system", "light", "dark"];
const LABEL = { system: "跟随系统", light: "浅色", dark: "深色" };
const ICON = { system: "◐", light: "☀", dark: "☾" };

let mi = Math.max(0, MODES.indexOf(lsGet("cx.theme") || "system"));
const mq = matchMedia("(prefers-color-scheme: dark)");

function resolved(mode) {
  return mode === "dark" || (mode === "system" && mq.matches) ? "dark" : "light";
}

export function themeMode() { return MODES[mi]; }

export function applyTheme() {
  document.documentElement.dataset.theme = resolved(MODES[mi]);
  const btn = document.getElementById("theme-btn");
  if (btn) {
    btn.title = "主题：" + LABEL[MODES[mi]] + "（点击切换）";
    document.getElementById("theme-ic").textContent = ICON[MODES[mi]];
    document.getElementById("theme-label").textContent = LABEL[MODES[mi]];
  }
}

// system 模式下系统主题实时变化 → 跟随（非 system 模式忽略）
mq.addEventListener("change", () => { if (MODES[mi] === "system") applyTheme(); });

export function initTheme() {
  applyTheme();
  const btn = document.getElementById("theme-btn");
  if (btn) {
    btn.onclick = () => {
      mi = (mi + 1) % MODES.length;
      lsSet("cx.theme", MODES[mi]);
      applyTheme();
    };
  }
  const pause = () =>
    document.documentElement.classList.toggle("aurora-paused", document.hidden);
  document.addEventListener("visibilitychange", pause);
  pause();   // boot 时窗口可能本就隐藏（后台拉起/探针 background tab）
}
