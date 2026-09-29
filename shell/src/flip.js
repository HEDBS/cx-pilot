// flip.js —— M2 动效基础设施（M2 动效任务书 §0/§3.1）。
// 唯一位移通道 animateLayout()：FLIP 批量——首帧取**视觉** rect（getBoundingClientRect 含在飞
// transform，故打断时以当前位置为起点立即接续，不跳回起始帧）；layoutFn 只写一次 top/left
// （layout 属性瞬写，发生在采样窗外），随后每元素一段 translate→none 的 WAAPI 动画——
// 动画期间几何量（computed top/left/width/height/margin）恒定，只动 transform。
// 曲线/时长 = §0 统一 Apple emphasized 450ms；prefers-reduced-motion 降级为 opacity 淡入（无位移）。
// EASE 与 styles.css 的 --ease 同值（CSS 侧过渡用令牌，JS 侧 WAAPI 用本常量，两处必须一致）。
export const EASE = "cubic-bezier(0.65, 0, 0.35, 1)";
export const DUR_MOVE = 450;   // A1/A2/A3/A4 位移
export const DUR_FADE = 250;   // A2 渐隐 / A9 灰化 / reduce 降级淡入

export function reduced() {
  return matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// 单个元素的入场动效（新出现的元素）：淡入 + 轻微上移；reduce 降级为纯淡入。
// §0 纪律：只动 transform/opacity。animateLayout 的新元素分支与侧栏后端条目共用。
export function animateEnter(el, dur = DUR_MOVE) {
  const rd = reduced();
  const a = el.animate(
    rd ? [{ opacity: 0 }, { opacity: 1 }]
       : [{ opacity: 0, transform: "translateY(12px)" }, { opacity: 1, transform: "none" }],
    { duration: rd ? DUR_FADE : dur, easing: rd ? "linear" : EASE });
  return a;
}

// 对 container 直接子元素（计划画布：卡片/列头/恢复条）做批量 FLIP。
// 元素「常驻不重建」（DESIGN 交互规则4）是位移连续的前提——本函数只改 transform/opacity。
export function animateLayout(container, layoutFn, dur = DUR_MOVE) {
  const els = [...container.children];
  const firsts = new Map();
  for (const el of els) {
    firsts.set(el, el.getBoundingClientRect());   // 在飞位置也照实记录 = 打断接续起点
    const a = el.__cxflip;
    if (a) { a.cancel(); el.__cxflip = null; }    // 立即以当前位置起新动画
  }
  layoutFn();
  const rd = reduced();
  for (const el of els) {
    const f = firsts.get(el);
    const l = el.getBoundingClientRect();
    if (!f.width && l.width) {
      // 新出现的元素（扫描逐份入库时卡片陆续冒出）：走统一入场动效，别再 pop-in
      const a = animateEnter(el);
      el.__cxflip = a;
      a.finished.then(() => { if (el.__cxflip === a) el.__cxflip = null; }).catch(() => {});
      continue;
    }
    if (!f.width || !l.width) continue;           // 消失（display 翻转）不做位移动画
    const dx = f.left - l.left, dy = f.top - l.top;
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
    let a;
    if (rd) {
      const o = Number(getComputedStyle(el).opacity);
      a = el.animate([{ opacity: Math.max(0, o - 0.45) }, { opacity: o }],
        { duration: DUR_FADE, easing: "linear" }); // 降级：仅淡入淡出，无位移
    } else {
      a = el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration: dur, easing: EASE });
    }
    el.__cxflip = a;
    a.finished.then(() => { if (el.__cxflip === a) el.__cxflip = null; }).catch(() => {});
  }
}
