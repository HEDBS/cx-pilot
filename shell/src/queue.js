// queue.js —— 屏2 解题队列（M1 任务书 §2.2：任务卡、识图块、手动面板）
// 数据面=store.jobs（/solve SSE 事件镜像）；手动面板「采用」→ POST /approve {manual} 写回
// server job.results（语义同 app_v2.manual_panel:773），低置信/失败/需人工均可人工兜底。
import { net } from "./api.js";
import { store, errText, withBusy } from "./store.js";
import { deleteJobUI, applySolveEvent } from "./plan.js";   // M3b R4 删卡；重试复用真实 /solve 事件处理

let listEl;
let allBtn;   // 工具条的「重试失败题」——要跟全局 busy 联动（用户实测反馈）

// 线程是否被"解题/重试"占着：占用期间所有重试入口都该是灰+转圈，
// 而不是让人点了才知道"被拒"（用户实测反馈：重试所有题时那个键也该进加载态）。
const solveBusy = () => store.busy === "solve";

function paintRetryAll() {
  if (!allBtn) return;
  const lock = solveBusy();
  allBtn.disabled = lock;
  allBtn.classList.toggle("loading", lock);
}

const TYPE_CN = { single: "单选", multi: "多选", blank: "填空", judge: "判断", subject: "主观" };

function resPill(q, r, thr) {
  if (!r) return `<span class="pill grey">待解</span>`;
  if (r.status === "ok") {
    const low = r.confidence < thr;
    return `<span class="pill ${low ? "warn" : "ok"}">${low ? "低置信" : "ok"} ${(r.confidence || 0).toFixed(2)}</span>`;
  }
  if (r.status === "need_manual") return `<span class="pill urgent">需人工</span>`;
  if (r.status === "refused") return `<span class="pill urgent">拒答</span>`;
  if (r.status === "provider_err") return `<span class="pill urgent">后端错</span>`;
  return `<span class="pill urgent">${r.status}</span>`;
}

function render() {
  const ids = Object.keys(store.jobs);
  listEl.innerHTML = "";
  if (!ids.length) {
    listEl.innerHTML = `<div class="empty">解题队列暂时为空——在「作业计划」勾选任务后点「开始解题」。</div>`;
    return;
  }
  const thr = (store.settings && store.settings.settings &&
               store.settings.settings.confidence_threshold) || 0.75;
  for (const jid of ids) {
    const j = store.jobs[jid];
    const card = document.createElement("div");
    card.className = "card job-card";
    const done = Object.keys(j.results).length, tot = j.questions.length;
    const nMan = j.questions.filter((q) => (j.results[q.qid] || {}).need_manual).length;
    // M3b R1/R4：interrupted=已中断灰态（取消「运行中」外观）；右上角 × 删除（二次确认在 deleteJobUI）
    const stCls = j.state === "done" ? "ok" : j.state === "running" ? "run"
                : j.state === "interrupted" ? "grey" : "warn";
    const stTxt = j.state === "interrupted" ? "已中断" : (j.state || "running");
    // 失败/未解的题数：>0 或已中断 → 露出「重试」（用户实测反馈：失败了没有出路）
    const bad = j.questions.filter((q) => {
      const r = j.results[q.qid];
      return !r || r.status !== "ok";
    }).length;
    card.innerHTML = `
      <div class="job-h">
        <span class="jt"></span>
        <span class="jn">${done}/${tot} 题${nMan ? ` · 需人工 ${nMan}` : ""}</span>
        <span class="jnote"></span>
        <span class="spacer"></span>
        <span class="pill ${stCls} st"></span>
        <button class="btn tiny retry-btn">重试</button>
        <button class="card-del" title="删除此任务卡（运行中会先取消）" aria-label="删除">×</button>
      </div>
      <div class="prog wide"><i style="width:${tot ? 100 * done / tot : 0}%"></i></div>
      <div class="qs"></div>`;
    card.querySelector(".jt").textContent = `${j.title}（${j.course}）`;
    card.querySelector(".st").textContent = stTxt;
    const rbtn = card.querySelector(".retry-btn");
    const lock = j.state === "running" || solveBusy();
    rbtn.textContent = bad > 0 ? `重试 ${bad} 题` : "重试";
    rbtn.style.display = (j.state === "running" || bad > 0 || j.state === "interrupted") ? "" : "none";
    // 用户实测反馈：点完不知道有没有生效。占用中=灰掉+转圈，空闲=正常可点。
    rbtn.disabled = lock;
    rbtn.classList.toggle("loading", lock);
    rbtn.title = lock ? "正在解题中，等它跑完再重试…"
      : "重新领卷并重解这份作业（只重解，不提交）";
    rbtn.onclick = () => {
      if (rbtn.disabled) return;
      rbtn.disabled = true; rbtn.classList.add("loading");   // 点击立刻出圈，不等 SSE 回来
      retryJobs([jid]);
    };
    const jn = card.querySelector(".jnote");
    jn.textContent = j.interruptNote || "";
    jn.style.display = j.interruptNote ? "" : "none";
    card.querySelector(".card-del").onclick = () => deleteJobUI(jid);
    const qs = card.querySelector(".qs");
    j.questions.forEach((q) => qs.appendChild(questionRow(jid, j, q, thr)));
    listEl.appendChild(card);
  }
}

function questionRow(jid, j, q, thr) {
  const r = j.results[q.qid];
  const row = document.createElement("div");
  row.className = "q-row";
  const ansTxt = r ? (Array.isArray(r.answer) ? r.answer.join(" / ") : r.answer || r.status) : "";
  row.innerHTML = `
    <div class="q-main">
      <span class="q-type"></span>
      <span class="q-stem"></span>
      ${q.image_flag ? `<span class="img-flag" title="题干含图，走识图三层">图</span>` : ""}
    </div>
    <div class="q-ans"></div>
    <div class="q-pill">${resPill(q, r, thr)}</div>
    <button class="btn tiny ghost mp-btn">人工填写</button>`;
  row.querySelector(".q-type").textContent = TYPE_CN[q.type] || q.type;
  row.querySelector(".q-stem").textContent = q.stem || "(题面见图片)";
  const ansEl = row.querySelector(".q-ans");
  ansEl.textContent = ansTxt ? String(ansTxt).slice(0, 80) : "—";
  if (r && r.source) ansEl.title = `来源 ${r.source}`;
  const needMan = r && r.need_manual;
  if (needMan) row.classList.add("need-manual");
  const panel = document.createElement("div");
  panel.className = "manual-panel";
  panel.hidden = !(needMan || (r && r.status !== "ok"));
  const nBlank = q.type === "blank" ? (q.blank_count || 1) : 1;
  const inputs = [];
  for (let i = 0; i < nBlank; i++) {
    const inp = document.createElement("textarea");
    inp.rows = 1; inp.placeholder = q.type === "blank" ? `第 ${i + 1} 空` :
      (q.type === "single" || q.type === "judge" ? "选项字母，如 A" :
       q.type === "multi" ? "选项字母串，如 ABD" : "人工答案（可多行）");
    inputs.push(inp); panel.appendChild(inp);
  }
  if (needMan) {
    const tip = document.createElement("div");
    tip.className = "mp-tip";
    tip.textContent = "识图三层（RapidOCR→判脏→vision_api）未出可用文本：对照原图人工填写后点「采用」";
    panel.insertBefore(tip, panel.firstChild);
  }
  const btn = document.createElement("button");
  btn.className = "btn tiny blue"; btn.textContent = "采用";
  btn.onclick = async () => {
    const ans = nBlank === 1 ? inputs[0].value.trim() : inputs.map((x) => x.value.trim());
    if (!ans || (Array.isArray(ans) && ans.every((x) => !x))) return;
    await withBusy("approve-manual", async () => {
      try {
        await net.api("/approve", { method: "POST",
          body: { job_id: jid, manual: { [q.qid]: ans } } });   // 不带 accepted：不动审批态
        j.results[q.qid] = Object.assign({}, r, { answer: ans, status: "ok",
          confidence: 1.0, source: "manual", need_manual: false });
        store.addLog(`采用人工答案：${q.type} qid=${q.qid}`);
        store.emit("jobs");
      } catch (e) { store.addLog("!! " + errText("人工答案写回", "保存", e), "err"); }
    });
  };
  panel.appendChild(btn);
  row.appendChild(panel);
  row.querySelector(".mp-btn").onclick = () => { panel.hidden = !panel.hidden; };
  return row;
}

// ---------- 重试（用户实测反馈：失败/需人工的题没有出路，只能删卡重来） ----------
// 复用真实 /solve 流（keys → 重新领卷 → 逐题解）；同一 job_id 原地刷新结果。
// 风控/掉线的自愈在 server 侧统一处理，这里不重造。只重解，绝不提交。
async function retryJobs(ids) {
  const list = (ids || []).filter((k) => store.jobs[k]);
  if (!list.length) { store.addLog("没有可重试的题目", "warn"); return; }
  const names = list.map((k) => store.jobs[k].title).join("、");
  const body = { keys: list, solve: true };
  store.lastSolveBody = body;      // 风控弹窗通过后据此自动续跑
  await withBusy("solve", async () => {
    store.addLog(`重试 ${list.length} 份：${names}（重新领卷 + 重解，不提交）`);
    try {
      await net.sse("/solve", body, applySolveEvent);
      store.addLog(`重试结束：${list.length} 份`, "ok");
    } catch (e) {
      store.addLog("!! " + errText("重试", "", e, "看运行日志"), "err");
    }
  });
}

export function initQueue(rootEl) {
  rootEl.innerHTML = `
    <div class="toolbar"><div class="h1">解题队列</div>
      <button class="btn tiny" id="qu-retry-all" title="把所有未解出/失败的题重新解一遍（不提交）">重试失败题</button>
      <div class="spacer"></div><span class="hint">领卷→识图→逐题解；人工兜底不硬答</span></div>
    <div class="stage list-scroll" id="queue-list"></div>`;
  listEl = rootEl.querySelector("#queue-list");
  allBtn = rootEl.querySelector("#qu-retry-all");
  allBtn.onclick = () => {
    const ids = Object.keys(store.jobs).filter((k) => {
      const j = store.jobs[k];
      return j.questions.some((q) => {
        const r = j.results[q.qid];
        return !r || r.status !== "ok";
      });
    });
    if (!ids.length) { store.addLog("没有需要重试的题目", "ok"); return; }
    allBtn.disabled = true; allBtn.classList.add("loading");   // 点击立刻出圈
    Promise.resolve(retryJobs(ids)).then(paintRetryAll);
  };
  render();
  paintRetryAll();
  store.on((what) => {
    if (what === "jobs") render();
    // busy 变化要重画：占用时所有重试入口（含本键）转圈+灰，空闲时自动恢复
    else if (what === "busy") { render(); paintRetryAll(); }
  });
}
