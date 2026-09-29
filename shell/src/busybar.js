// busybar.js —— 顶部进行中横条（任务占线程时的即时反馈）。
// 分工：main.js 决定「该不该显示 + 默认文案」（由 store.busy 驱动）；
//       正在跑的任务自己可以 show() 把阶段说得更准——
//       用户实测反馈：「正在领卷并逐题解答…」这句话分不清当前到底在领卷还是解题。
let el = null;

function node() {
  if (!el) el = document.getElementById("busy-bar");
  return el;
}

export function showBusyBar(text) {
  const e = node();
  if (!e) return;
  e.innerHTML = `<span class="spin"></span><span class="bb-txt"></span>`;
  e.querySelector(".bb-txt").textContent = text;
  e.hidden = false;
}

export function hideBusyBar() {
  const e = node();
  if (e) e.hidden = true;
}

// 供探针断言当前阶段文案（无副作用）
export function busyBarText() {
  const e = node();
  const t = e && e.querySelector(".bb-txt");
  return t ? t.textContent : "";
}

export function busyBarShown() {
  const e = node();
  return !!e && !e.hidden;
}
