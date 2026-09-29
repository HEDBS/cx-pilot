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

// 风控验证码弹窗（【9010】罚站的人工出口）。
// 沿用同一 shell()（.dlg/.lg 玻璃 + A8 入场）→ 与首登/确认窗风格一致。
// 只做「取图给人看 + 代提交」：自动识别验证码图属于绕过人机验证，不做。
export function captchaDlg(onOk) {
  const el = shell(`
    <div class="dlg lg">
      <div class="dlg-title">学习通风控验证</div>
      <div class="dlg-hint">超星判定操作异常，输入图中 4 位字符即可继续。
        通过后会自动接着跑刚才的任务，不用去手机 App。</div>
      <div class="cap-row">
        <img id="cap-img" alt="加载中…">
        <button class="btn tiny grey" data-x="reload" title="看不清就换一张">↻ 换一张</button>
      </div>
      <label class="fld">验证码<input id="cap-code" maxlength="4" autocomplete="off"
        placeholder="图中 4 位字符"></label>
      <div class="dlg-err" id="cap-err"></div>
      <div class="dlg-btns">
        <button class="btn grey" data-x="cancel">取消</button>
        <button class="btn blue" data-x="ok">提交</button>
      </div>
    </div>`);
  const img = el.querySelector("#cap-img");
  const errEl = el.querySelector("#cap-err");
  const okBtn = el.querySelector("[data-x=ok]");
  const codeEl = el.querySelector("#cap-code");

  const load = async () => {
    errEl.textContent = "";
    img.removeAttribute("src");
    img.alt = "加载中…";
    try {
      const d = await net.api("/captcha", { method: "GET" });
      if (d && d.ok) { img.src = "data:image/png;base64," + d.png; img.alt = "验证码"; }
      else { img.alt = "无图"; errEl.textContent = String((d && d.msg) || "没取到验证码"); }
    } catch (e) {
      errEl.textContent = String(e.detail || e.message || e).slice(0, 120);
    }
  };

  const submit = async () => {
    const code = codeEl.value.trim();
    if (!code) { errEl.textContent = "请输入图中的 4 位字符"; return; }
    okBtn.disabled = true; okBtn.classList.add("loading");   // 与重试键同一套加载态
    try {
      const d = await net.api("/captcha", { method: "POST", body: { code } });
      if (!d || !d.ok) {
        errEl.textContent = String((d && d.msg) || "验证码不对");
        okBtn.disabled = false; okBtn.classList.remove("loading");
        load();                                              // 自动换一张
        return;
      }
      store.addLog("风控验证码已通过，风控解除", "ok");
      close();
      if (onOk) onOk();
    } catch (e) {
      errEl.textContent = String(e.detail || e.message || e).slice(0, 120);
      okBtn.disabled = false; okBtn.classList.remove("loading");
    }
  };

  el.querySelector("[data-x=reload]").onclick = load;
  el.querySelector("[data-x=cancel]").onclick = close;
  okBtn.onclick = submit;
  codeEl.addEventListener("keydown", (ev) => { if (ev.key === "Enter") submit(); });
  load();
  codeEl.focus();
}

// 全局 auth 钩子接线：401/428 到达即弹框；onSuccess 续跑当前失败的动作由调用方给
net.onAuth((reason) => {
  showLogin(reason, () => store.retryLast && store.retryLast());
});
