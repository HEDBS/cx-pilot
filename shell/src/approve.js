// approve.js —— 屏3 提交审批（M1 任务书 §2.3）
// 逐题预览 + 勾选 + confirm 弹窗；勾选态经 POST /approve 落 server 内存审批态，
// POST /submit confirm=true 的双闸门（approval_required / captcha_required）在 server 侧，
// 前端不预杀——「未勾选提交必须被 server 409 拦住」是验收项，必须让请求真发出去。
import { net } from "./api.js";
import { store, errText, withBusy } from "./store.js";
import { confirmDlg } from "./modal.js";
import { deleteJobUI } from "./plan.js";   // M3b R4：删卡共用实现（确认+先取消）

let listEl;

function threshold() {
  return (store.settings && store.settings.settings &&
          store.settings.settings.confidence_threshold) || 0.75;
}

// 默认勾选：ok 且置信≥阈值且非需人工（引擎语义：低置信留空、需人工不硬答）
function initAccepted(j) {
  if (j._acceptedInit) return;
  const thr = threshold();
  j.accepted = new Set(j.questions.filter((q) => {
    const r = j.results[q.qid];
    return r && r.status === "ok" && r.confidence >= thr && !r.need_manual;
  }).map((q) => q.qid));
  j._acceptedInit = true;
}

function render() {
  const ids = Object.keys(store.jobs);
  listEl.innerHTML = "";
  if (!ids.length) {
    listEl.innerHTML = `<div class="empty">没有待审批的 job——先到「解题队列」完成领卷解题。</div>`;
    return;
  }
  for (const jid of ids) {
    const j = store.jobs[jid];
    initAccepted(j);
    const card = document.createElement("div");
    card.className = "card ap-card";
    card.innerHTML = `
      <div class="job-h"><span class="jt"></span><span class="jn ap-count"></span>
        <span class="pill ${j.state === "interrupted" ? "grey" : j.state === "done" ? "ok" : "warn"} ap-st"></span>
        <span class="spacer"></span>
        <button class="card-del" title="删除此任务卡（运行中会先取消）" aria-label="删除">×</button></div>
      <div class="qs"></div>
      <div class="ap-foot">
        <button class="btn tiny grey dry-btn">预检 dry_run</button>
        <span class="ap-out"></span>
        <span class="spacer"></span>
        <button class="btn dark sub-btn">提交到学习通 ▸</button>
      </div>`;
    card.querySelector(".jt").textContent = `${j.title}（${j.course}）`;
    // M3b R1/R4：interrupted 灰 pill + × 删除（server 重启后 job 已丢，dry_run/提交会 404——
    // 文案已引导重新领卷；删卡清本地镜像是用户明确出口）
    const stEl = card.querySelector(".ap-st");
    stEl.textContent = j.state === "interrupted" ? "已中断" : (j.state || "");
    stEl.style.display = stEl.textContent ? "" : "none";
    card.querySelector(".card-del").onclick = () => deleteJobUI(jid);
    const qs = card.querySelector(".qs");
    j.questions.forEach((q) => qs.appendChild(previewRow(jid, j, q, card)));
    const out = card.querySelector(".ap-out");
    card.querySelector(".dry-btn").onclick = async () => {
      await withBusy("submit-dry", async () => {
        try {
          const d = await net.api("/submit", { method: "POST", body: { job_id: jid, confirm: false } });
          out.textContent = `dry_run：${d.form_fields} 个表单字段，未发送`;
          out.className = "ap-out ok";
          store.addLog(`预检 ${j.title}：${d.form_fields} 字段（dry_run 未发送）`, "ok");
        } catch (e) { out.textContent = errText("预检", "", e); out.className = "ap-out err"; }
      });
    };
    card.querySelector(".sub-btn").onclick = async () => {
      const acc = [...j.accepted];
      const ok = await confirmDlg("提交确认",
        [`将向学习通提交 1 份作业：《${j.title}》`, `已勾选 ${acc.length}/${j.questions.length} 题。`,
         "提交后服务器留痕，不可自动撤回。"],
        acc.length ? "确认提交" : "确认（未勾选）");
      if (!ok) return;
      await withBusy("submit", async () => {
        try {
          await net.api("/approve", { method: "POST", body: { job_id: jid, accepted: acc } });
          const d = await net.api("/submit", { method: "POST", body: { job_id: jid, confirm: true } });
          out.textContent = `提交返回：${d.status} ${String(d.msg || "").slice(0, 80)}`;
          out.className = "ap-out " + (d.status === "ok" ? "ok" : "warn");
          store.addLog(`提交《${j.title}》：${d.status}`, d.status === "ok" ? "ok" : "warn");
          if (d.status === "ok") { j.submitted = true; render(); }
        } catch (e) {
          if (e.status === 404) {
            out.textContent = "server 404 job_not_found：sidecar 已重启，内存 job 丢失，请重新领卷";
            out.className = "ap-out err";
          } else if (e.status === 409 && e.detail === "approval_required") {
            out.textContent = "server 409 approval_required：未勾选任何题目，审批闸门拦截（真 POST 未发出）";
            store.addLog("!! 提交被审批闸门拦截：accepted 为空 → 409 approval_required", "err");
          } else if (e.status === 409 && e.detail === "captcha_required") {
            out.textContent = "server 409 captcha_required：work/validate 命中验证码，请人工在学习通作答";
            store.addLog("!! 提交需验证码（code==2），已按引擎规则转人工", "warn");
          } else {
            out.textContent = errText("提交", "", e);
            store.addLog("!! " + errText(j.title, "提交", e, "查看运行日志"), "err");
          }
          out.className = "ap-out err";
        }
      });
    };
    listEl.appendChild(card);
  }
  updateCount();
}

function previewRow(jid, j, q, card) {
  const r = j.results[q.qid] || {};
  const low = r.status === "ok" && r.confidence < threshold();
  const row = document.createElement("div");
  row.className = "q-row ap-row" + (r.need_manual ? " need-manual" : "") + (low ? " low" : "");
  const ans = Array.isArray(r.answer) ? r.answer.join(" / ") : (r.answer || "");
  row.innerHTML = `
    <div class="chk"></div>
    <div class="body">
      <div class="q-head"><span class="q-type"></span><span class="q-stem"></span></div>
      <div class="opt-line"></div>
    </div>
    <div class="ans-box"><b>答案 </b><span class="ans"></span></div>
    <div class="q-pill"></div>`;
  row.querySelector(".q-type").textContent = q.qid;
  row.querySelector(".q-stem").textContent = q.stem || "(题面见图片)";
  const opt = row.querySelector(".opt-line");
  opt.textContent = Object.entries(q.options || {})
    .map(([k, v]) => `${k}. ${v}`).join("    ");
  row.querySelector(".ans").textContent = ans || (r.need_manual ? "待人工" : "(空=低置信/未解，留空不提交内容)");
  const pill = row.querySelector(".q-pill");
  pill.innerHTML = resPill(r);
  const chk = row.querySelector(".chk");
  const sync = () => chk.classList.toggle("on", j.accepted.has(q.qid));
  chk.onclick = () => {
    j.accepted.has(q.qid) ? j.accepted.delete(q.qid) : j.accepted.add(q.qid);
    sync(); updateCount(); store.persistJobs();
  };
  sync();
  return row;
}

function resPill(r) {
  if (!r.status) return `<span class="pill grey">未解</span>`;
  if (r.status === "ok") return `<span class="pill ${r.confidence < threshold() ? "warn" : "ok"}">${r.confidence < threshold() ? "低置信" : "ok"}</span>`;
  if (r.status === "need_manual") return `<span class="pill urgent">需人工</span>`;
  return `<span class="pill urgent">${r.status}</span>`;
}

function updateCount() {
  document.querySelectorAll(".ap-card").forEach((card) => {
    // 每 job 的已勾数：从 store.jobs 反查
    const jt = card.querySelector(".jt").textContent;
    const j = Object.values(store.jobs).find((x) => `${x.title}（${x.course}）` === jt);
    if (j) card.querySelector(".ap-count").textContent = `已勾 ${j.accepted.size} 题`;
  });
}

export function initApprove(rootEl) {
  rootEl.innerHTML = `
    <div class="toolbar"><div class="h1">提交审批</div>
      <div class="spacer"></div>
      <span class="hint">双闸门：/approve 勾选 + server confirm 校验；提交模式恒为人工确认</span></div>
    <div class="stage list-scroll" id="ap-list"></div>`;
  listEl = rootEl.querySelector("#ap-list");
  render();
  store.on((what) => { if (what === "jobs" || what === "sel") render(); });
}
