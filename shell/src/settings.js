// settings.js —— 屏5 设置（M1 任务书 §2.5 + M3 任务书 B2/B3/I1）
// provider 开关/model、key 脱敏显示（sk-***末4位）；写回纪律：脱敏回显值绝不回传，
// 未改动字段整体缺省——server 端按 provider merge 保盘上真 key（M1 必要适配）。
// M3：B2 识图后端「+ 新增后端」预设模板（不含任何 key，HANDOFF §4.6 纪律）+ 可删；
//     B3「留空=不修改」独立 hint-inline，placeholder 只留脱敏值；
//     I1 provider 行 pointer 拖动排序（禁第三方库；行 _cfg 引用随 DOM 不丢；
//     拖动行跟手 transform，落位走 flip.js animateLayout 450ms emphasized，reduce 降级）。
import { net } from "./api.js";
import { store, errText } from "./store.js";
import { animateLayout } from "./flip.js";

let rootEl, form;

// 识图后端预设（B2）：只填 kind/name/base_url/model 占位，**不写死任何 key**。
// 硅基流动需实名后启用；智谱新号 401 风控——菜单提示文字如实标注（HANDOFF §4.6）。
const VISION_PRESETS = [
  { key: "siliconflow", label: "硅基流动", note: "需实名认证后启用（1s/图）",
    cfg: { kind: "openai_compat", name: "硅基流动", base_url: "https://api.siliconflow.cn/v1",
           model: "Qwen/Qwen2.5-VL-7B-Instruct" } },
  { key: "zhipu", label: "智谱", note: "新号 401 风控，老号再用",
    cfg: { kind: "openai_compat", name: "智谱", base_url: "https://open.bigmodel.cn/api/paas/v4",
           model: "glm-4v-flash" } },
  { key: "openrouter", label: "OpenRouter", note: "推理型视觉 80–200s/图",
    cfg: { kind: "openai_compat", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1",
           model: "google/gemini-2.5-flash" } },
  { key: "custom", label: "自定义", note: "base_url/model 自己填",
    cfg: { kind: "openai_compat", name: "自定义识图", base_url: "", model: "" } },
];

function keyOf(cfg, keys) {
  if (cfg.key_ref && keys[cfg.key_ref]) return keys[cfg.key_ref];
  return cfg.api_key || "";
}

function provRow(cfg, grp, keys) {
  const masked = keyOf(cfg, keys);
  const row = document.createElement("div");
  row.className = "card set-row";
  row.innerHTML = `
    <span class="grip" title="按住拖动调整优先级">⠿</span>
    <label class="sw"><input type="checkbox" class="en"><b class="nm"></b></label>
    <span class="kind"></span>
    <span class="fld inline">base_url<input class="burl"></span>
    <span class="fld inline">model<input class="model"></span>
    <span class="fld inline">key<input class="key"><span class="hint-inline"${masked ? "" : " hidden"}>留空=不修改</span></span>
    ${grp === "vision_providers" ? '<button class="btn tiny grey del">删除</button>' : ""}
    <span class="masked">${masked || "—"}</span>`;
  row.querySelector(".en").checked = !!cfg.enabled;
  row.querySelector(".nm").textContent = cfg.name || "(未命名)";
  row.querySelector(".kind").textContent = cfg.kind + (cfg.key_ref ? ` · ${cfg.key_ref}` : "");
  row.querySelector(".burl").value = cfg.base_url || "";
  row.querySelector(".model").value = cfg.model || "";
  const keyInp = row.querySelector(".key");
  keyInp.dataset.ref = cfg.key_ref || "";
  keyInp.placeholder = masked || "粘贴新 key";   // B3：placeholder 只留脱敏值本身，提示走 .hint-inline
  row._cfg = cfg; row._grp = grp;
  const del = row.querySelector(".del");
  if (del) del.onclick = () => row.remove();     // 删除即从 DOM 消失，collect 按 DOM 序重建数组
  bindDrag(row);
  return row;
}

// I1：指针拖动排序。只改本组内行序（providers / vision_providers 互不越组）；
// 拖动行 transform 跟手，落位经 animateLayout FLIP（450ms emphasized / reduce 淡入）。
function bindDrag(row) {
  const grip = row.querySelector(".grip");
  if (!grip) return;
  grip.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const box = row.parentElement;
    const sibs = [...box.querySelectorAll(".set-row")].filter((x) => x !== row);
    const rects = sibs.map((x) => x.getBoundingClientRect());   // 未动兄弟：布局位=视觉位
    const startTop = row.getBoundingClientRect().top;
    const h = row.offsetHeight || 1;
    let dy = 0;
    row.classList.add("dragging");
    try { grip.setPointerCapture(e.pointerId); } catch (_) { /* 合成指针事件无活动指针可捕获 */ }
    const onMove = (ev) => {
      dy = ev.clientY - e.clientY;                              // 相对 pointerdown 的位移
      row.style.transform = "translateY(" + dy + "px)";         // 跟手（transform-only）
    };
    const onUp = () => {
      grip.removeEventListener("pointermove", onMove);
      grip.removeEventListener("pointerup", onUp);
      grip.removeEventListener("pointercancel", onUp);
      const center = startTop + h / 2 + dy;                     // 拖后视觉中线
      let ref = null;
      for (let i = 0; i < sibs.length; i++) {
        if (rects[i].top + rects[i].height / 2 > center) { ref = sibs[i]; break; }
      }
      animateLayout(box, () => {
        row.style.transform = "";
        row.classList.remove("dragging");
        if (!sibs.length) return;
        const last = sibs[sibs.length - 1];
        box.insertBefore(row, ref ? ref : last.nextSibling);    // 尾槽：排到本组最后一行之后
      });
    };
    grip.addEventListener("pointermove", onMove);
    grip.addEventListener("pointerup", onUp);
    grip.addEventListener("pointercancel", onUp);
  });
}

function collect() {
  const settings = { providers: [], vision_providers: [],
                     confidence_threshold: Number(form.querySelector("#thr").value) || 0.75 };
  const keys = {};
  for (const grp of ["providers", "vision_providers"]) {
    // 按 DOM 序收集=拖动后的实际优先级（I1；solver 链即数组顺序）
    for (const row of form.querySelectorAll(`.set-group[data-grp="${grp}"] .set-row`)) {
      const c = { kind: row._cfg.kind, name: row._cfg.name, key_ref: row._cfg.key_ref };
      c.base_url = row.querySelector(".burl").value.trim();
      c.enabled = row.querySelector(".en").checked;
      c.model = row.querySelector(".model").value.trim();
      const newKey = row.querySelector(".key").value.trim();
      if (newKey) {                       // 只有用户真的输入了新 key 才回传（绝不带 sk-***）
        if (c.key_ref) keys[c.key_ref] = newKey;
        else c.api_key = newKey;
      }
      settings[grp].push(c);
    }
  }
  return { settings, keys };
}

async function save() {
  const body = collect();
  if (!Object.keys(body.keys).length) delete body.keys;
  try {
    const d = await net.api("/settings", { method: "POST", body });
    store.settings = d;
    store.addLog("设置已保存（key 走原子替换，脱敏值未回写）", "ok");
    paint(d);
  } catch (e) {
    store.addLog("!! " + errText("设置", "保存", e), "err");
  }
}

// B2：新增识图后端——同名防撞（server 按 name merge，重名会互相吞字段）
function uniqueName(base, grp) {
  const taken = new Set(
    [...form.querySelectorAll(`.set-group[data-grp="${grp}"] .set-row`)].map((r) => r._cfg.name));
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) { const n = base + " " + i; if (!taken.has(n)) return n; }
}

function visionAddMenu(box, btn) {
  const open = box.querySelector(".preset-menu");
  if (open) { open.remove(); return; }
  const menu = document.createElement("div");
  menu.className = "preset-menu card";
  menu.innerHTML = `
    <div class="pm-title">新增识图后端（预设只填地址/模型占位，<b>不含任何 key</b>，建好须自行粘贴）</div>
    <div class="pm-hint">纪律（交接书 §4.6）：硅基流动需实名；智谱新号 401 风控；OpenRouter 推理型视觉 80–200s/图。</div>
    <div class="pm-items">${VISION_PRESETS.map((p) =>
      `<button class="btn tiny ghost pm-item" data-p="${p.key}">${p.label}
         <span class="pm-note">${p.note}</span></button>`).join("")}</div>`;
  menu.querySelectorAll(".pm-item").forEach((b) => {
    b.onclick = () => {
      const preset = VISION_PRESETS.find((x) => x.key === b.dataset.p);
      const cfg = { ...preset.cfg, name: uniqueName(preset.cfg.name, "vision_providers"),
                    enabled: true, key_ref: "" };
      box.insertBefore(provRow(cfg, "vision_providers", {}), btn);
      menu.remove();
      store.addLog(`已新增识图后端「${cfg.name}」（未配置 key，粘贴后保存才生效）`);
    };
  });
  box.appendChild(menu);
}

function paint(d) {
  const keys = d.keys || {};
  const s = d.settings || {};
  form.innerHTML = "";
  const bar = document.createElement("div");
  bar.className = "toolbar";
  bar.innerHTML = `<div class="h1">设置</div><div class="spacer"></div>
    <span class="hint">在链后端（拖动可改优先级）：${(d.active || []).join(" > ") || "（无）"}</span>
    <span class="fld inline">低置信阈值<input type="number" step="0.05" min="0" max="1" id="thr"
      value="${s.confidence_threshold ?? 0.75}"></span>
    <button class="btn blue" id="set-save">保存</button>`;
  form.appendChild(bar);
  const sec = (title, list, grp) => {
    const h = document.createElement("div");
    h.className = "side-sec"; h.textContent = title;
    form.appendChild(h);
    const box = document.createElement("div");
    box.className = "set-group"; box.dataset.grp = grp;
    (list || []).forEach((cfg) => box.appendChild(provRow(cfg, grp, keys)));
    if (grp === "vision_providers") {
      if (!(list || []).length) {
        const e = document.createElement("div");
        e.className = "empty vp-empty"; e.textContent = "暂无识图后端：本地 OCR 之后直接人工。点下方「+ 新增后端」接入视觉 API。";
        box.appendChild(e);
      }
      const add = document.createElement("button");
      add.className = "btn tiny ghost vp-add"; add.textContent = "＋ 新增后端";
      add.onclick = () => {
        box.querySelector(".vp-empty")?.remove();
        visionAddMenu(box, add);
      };
      box.appendChild(add);
    }
    form.appendChild(box);
  };
  sec("解题后端 providers（拖动排序=调用优先级）", s.providers, "providers");
  sec("识图后端 vision_providers", s.vision_providers, "vision_providers");
  bar.querySelector("#thr").onchange = () => {};
  bar.querySelector("#set-save").onclick = save;
}

export async function initSettings(root) {
  rootEl = root;
  rootEl.innerHTML = `<div class="stage list-scroll" id="set-form"></div>`;
  form = rootEl.querySelector("#set-form");
  try {
    const d = await net.api("/settings");
    store.settings = d;
    paint(d);
  } catch (e) {
    form.innerHTML = `<div class="empty">!! ${errText("设置", "读取", e)}</div>`;
  }
}
