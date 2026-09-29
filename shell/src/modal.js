// modal.js —— 对话框：首登/重登（HANDOFF §3 GUI 首登：错误就地红字、成功续跑失败动作）
// 与提交确认弹窗（审批屏 §2.3）。A8（M2 任务书 §2）：弹窗 scale 0.95→1 + opacity 200ms，
// backdrop 渐暗（opacity 0→1 即 rgba(0,0,0,.32) 浮现）；reduced-motion 降级纯淡入（无缩放）。
import { net } from "./api.js";
import { store } from "./store.js";
import { EASE, reduced } from "./flip.js";

const mask = document.getElementById("modal-mask");
const box = document.getElementById("modal-box");

function close() { mask.hidden = true; box.innerHTML = ""; }

function shell(html) {
  box.innerHTML = html;
  mask.hidden = false;
  const dlg = box.firstElementChild;
  mask.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: EASE });
  if (dlg) {
    if (reduced()) dlg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: "linear" });
    else dlg.animate(
      [{ transform: "scale(0.95)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }],
      { duration: 200, easing: EASE });
  }
  return box;
}

// 首登/重登：reason = no_credentials(首登) | session_expired(重登) | unauthorized
export function showLogin(reason, onSuccess) {
  const first = reason === "no_credentials";
  const el = shell(`
    <div class="dlg lg">
      <div class="dlg-title">${first ? "登录学习通（首次使用）" : "会话过期 · 重新登录"}</div>
      <div class="dlg-hint">账号仅存本机 %APPDATA%\\cx-pilot，用于会话过期后自动重登。</div>
      <label class="fld">账号<input id="lg-user" autocomplete="off"></label>
      <label class="fld">密码<input id="lg-pwd" type="password"></label>
      <div class="dlg-err" id="lg-err"></div>
      <div class="dlg-btns">
        <button class="btn grey" data-x="cancel">取消</button>
        <button class="btn blue" data-x="ok">登录</button>
      </div>
    </div>`);
  const errEl = el.querySelector("#lg-err");
  const submit = async () => {
    const user = el.querySelector("#lg-user").value.trim();
    const pwd = el.querySelector("#lg-pwd").value;
    if (!user || !pwd) { errEl.textContent = "user/pwd 必填"; return; }
    el.querySelector("[data-x=ok]").disabled = true;
    try {
      const d = await net.api("/auth/login", { method: "POST", body: { user, pwd } });
      store.addLog(d.ok ? "登录成功，凭据已落盘（0600）" : "!! 登录失败：ensure_login 未通过", d.ok ? "ok" : "err");
      close();
      if (d.ok && onSuccess) onSuccess();
    } catch (e) {
      errEl.textContent = String(e.detail || e.message || e).slice(0, 120); // 就地红字
      el.querySelector("[data-x=ok]").disabled = false;
    }
  };
  el.querySelector("[data-x=cancel]").onclick = close;
  el.querySelector("[data-x=ok]").onclick = submit;
  el.querySelectorAll("input").forEach((x) => x.onkeydown = (ev) => { if (ev.key === "Enter") submit(); });
  el.querySelector("#lg-user").focus();
}

// 通用确认弹窗（提交确认走这里，DESIGN §2.3）；返回 Promise<boolean>
export function confirmDlg(title, lines, okLabel) {
  return new Promise((resolve) => {
    const el = shell(`
      <div class="dlg lg">
        <div class="dlg-title">${title}</div>
        <div class="dlg-body">${lines.map((s) => `<p>${s}</p>`).join("")}</div>
        <div class="dlg-btns">
          <button class="btn grey" data-x="no">取消</button>
          <button class="btn dark" data-x="yes">${okLabel || "确认提交"}</button>
        </div>
      </div>`);
    el.querySelector("[data-x=no]").onclick = () => { close(); resolve(false); };
    el.querySelector("[data-x=yes]").onclick = () => { close(); resolve(true); };
  });
}

// 全局 auth 钩子接线：401/428 到达即弹框；onSuccess 续跑当前失败的动作由调用方给
net.onAuth((reason) => {
  showLogin(reason, () => store.retryLast && store.retryLast());
});
