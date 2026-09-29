// nav.js —— 极小的切屏注册表（M5+ 新增）。
// main.js 拥有真实路由 go()（含离/进场动画、inited 缓存、hash 同步）；它启动时把 go 注册进来，
// 其他模块（plan/queue/...）通过 go(screen) 触发真实切屏。
// 这样避免「main.js ←→ plan.js」循环导入，也不用复制一份切屏逻辑（复制必然漂移）。
let _go = null;

export function registerNav(fn) { _go = fn; }

export function go(screen) {
  if (_go) _go(screen);
}
