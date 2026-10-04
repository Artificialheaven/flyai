/* FlyAI 前端 · 鉴权与多用户管理（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

/* ==== 多用户 / 登录（独立登录页，不登录不能进） ==== */
function authHeaders(extra) {
  var h = Object.assign({}, extra || {});
  if (state.authToken) h["Authorization"] = "Bearer " + state.authToken;
  return h;
}
function goLogin() {
  window.location.href = "/login";
}
function renderUserBadge() {
  if (el.userName) {
    var tag = state.role === "admin" ? "（管理员 Lv256）" : "（Lv" + state.userLevel + "）";
    el.userName.textContent = "👤 " + (state.userName || "……") + tag;
  }
  if (el.btnUsers) el.btnUsers.classList.toggle("hidden", state.role !== "admin");
}
function clearAuth() {
  localStorage.removeItem("flyai-token");
  localStorage.removeItem("flyai-uid");
  localStorage.removeItem("flyai-role");
  state.authToken = null;
  state.userId = null;
  state.role = "";
  state.currentSessionId = null;
  state.currentAttrs = null;
  state.roomMembers = []; state.roomThreads = []; state.currentThreadId = null; state.currentSpeaker = null;
  try { clearMessages(); if (el.threads) el.threads.classList.add("hidden"); } catch (e) {}
}
function bootstrap() {
  var token = localStorage.getItem("flyai-token");
  if (!token) { goLogin(); return; }  // 不登录不能进
  state.authToken = token;
  state.imageModelId = localStorage.getItem("flyai-image-model") || "";
  state.sdEngine = localStorage.getItem("flyai-sd-engine") === "sd";
  fetchJSON("/api/auth/me").then(function (res) {
    state.userId = res.id;
    localStorage.setItem("flyai-uid", res.id);  // 供 owner 比对/调试，非凭证
    state.userName = res.name;
    state.role = res.role || "user";
    state.userLevel = parseInt(res.level, 10) || 0;
    localStorage.setItem("flyai-role", state.role);
    renderUserBadge();
    init();
  }).catch(function () { clearAuth(); goLogin(); });
}
function doLogout() {
  fetch("/api/auth/logout", { method: "POST", headers: authHeaders() }).catch(function () {});
  clearAuth();
  goLogin();
}

/* ==== 用户管理（仅管理员） ==== */
function openUserDialog() {
  safeCloseDialogs();
  el.userMsg.textContent = ""; el.userMsg.className = "msg-bar";
  renderUserList();
  el.userDialog.showModal();
  el.userNameInput.focus();
}
function renderUserList() {
  el.userList.innerHTML = "";
  fetchJSON("/api/auth/users", { method: "GET", headers: authHeaders() }).then(function (r) {
    (r.users || []).forEach(function (u) {
      var row = document.createElement("div");
      row.className = "user-row item";
      var name = document.createElement("span");
      name.className = "user-name";
      name.textContent = u.name;
      row.appendChild(name);
      // 等级：改完即 PATCH
      var levelWrap = document.createElement("label");
      levelWrap.className = "user-level";
      levelWrap.textContent = "Lv ";
      var level = document.createElement("input");
      level.type = "number";
      level.min = "0"; level.max = "255";
      level.value = String(u.level == null ? 0 : u.level);
      level.addEventListener("change", function () {
        changeUserLevel(u.id, level);
      });
      levelWrap.appendChild(level);
      row.appendChild(levelWrap);
      // 改密码：点击后行内展开密码输入
      var pwdBtn = document.createElement("button");
      pwdBtn.className = "btn small";
      pwdBtn.textContent = "🔑 改密";
      var pwdBox = makePasswordResetBox(u.id, u.name);
      pwdBtn.addEventListener("click", function () {
        pwdBtn.classList.add("hidden");
        row.appendChild(pwdBox);
        pwdBox.querySelector("input").focus();
      });
      pwdBox.addEventListener("done", function () {
        pwdBox.remove();
        pwdBtn.classList.remove("hidden");
      });
      row.appendChild(pwdBtn);
      var del = document.createElement("button");
      del.className = "btn small";
      del.textContent = "删除";
      del.addEventListener("click", function () { deleteUser(u.id, u.name, row, del); });
      row.appendChild(del);
      el.userList.appendChild(row);
    });
  }).catch(function (e) { el.userList.textContent = "加载失败: " + e.message; });
}
function changeUserLevel(uid, input) {
  var v = parseInt(input.value, 10);
  if (isNaN(v)) v = 0;
  el.userMsg.textContent = ""; el.userMsg.className = "msg-bar";
  fetchJSON("/api/auth/users/" + encodeURIComponent(uid), {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ level: v }),
  }).then(function (r) {
    input.value = String(r.level);
    el.userMsg.textContent = "等级已更新为 " + r.level;
    el.userMsg.className = "msg-bar ok";
  }).catch(function (e) {
    el.userMsg.textContent = "等级修改失败: " + e.message;
    el.userMsg.className = "msg-bar err";
  });
}
/* 行内密码重置框：新密码输入 + 确认/取消；确认走 PATCH .../password */
function makePasswordResetBox(uid, name) {
  var box = document.createElement("span");
  box.className = "pwd-reset";
  var input = document.createElement("input");
  input.type = "password";
  input.placeholder = "输入新密码（留空=无密码）";
  input.autocomplete = "new-password";
  var ok = document.createElement("button");
  ok.className = "btn small primary";
  ok.textContent = "确认";
  var cancel = document.createElement("button");
  cancel.className = "btn small";
  cancel.textContent = "取消";
  function finish() { box.dispatchEvent(new Event("done")); }
  function submit() {
    var password = input.value;
    ok.disabled = true;
    fetchJSON("/api/auth/users/" + encodeURIComponent(uid) + "/password", {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ password: password }),
    }).then(function (r) {
      el.userMsg.textContent = "已重置「" + name + "」的密码，其现有登录已全部失效"
        + "（吊销 " + intOr(r.revoked_sessions, 0) + " 个会话）";
      el.userMsg.className = "msg-bar ok";
      finish();
    }).catch(function (e) {
      el.userMsg.textContent = "密码重置失败: " + e.message;
      el.userMsg.className = "msg-bar err";
      ok.disabled = false;
    });
  }
  ok.addEventListener("click", submit);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
    if (e.key === "Escape") finish();
  });
  cancel.addEventListener("click", finish);
  box.appendChild(input);
  box.appendChild(ok);
  box.appendChild(cancel);
  return box;
}
function deleteUser(uid, name, row, btn) {
  if (!confirm("确定删除用户「" + name + "」？其角色卡/世界观/会话数据也会被一并移除。")) return;
  btn.disabled = true;
  fetchJSON("/api/auth/users/" + encodeURIComponent(uid), { method: "DELETE", headers: authHeaders() })
    .then(function () { row.remove(); })
    .catch(function (e) {
      el.userMsg.textContent = "删除失败: " + e.message; el.userMsg.className = "msg-bar err";
      btn.disabled = false;
    });
}
function createUser() {
  var name = el.userNameInput.value.trim();
  var password = el.userPasswordInput.value;
  var level = parseInt(el.userLevelInput.value, 10);
  if (isNaN(level)) level = 0;
  el.userMsg.textContent = ""; el.userMsg.className = "msg-bar";
  if (!name) { el.userMsg.textContent = "请输入用户名"; el.userMsg.className = "msg-bar err"; return; }
  fetchJSON("/api/auth/users", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name: name, password: password, level: level }),
  }).then(function () {
    el.userMsg.textContent = "已创建用户「" + name + "」"; el.userMsg.className = "msg-bar ok";
    el.userNameInput.value = ""; el.userPasswordInput.value = "";
    el.userLevelInput.value = "0";
    renderUserList();
    el.userNameInput.focus();
  }).catch(function (e) {
    el.userMsg.textContent = "创建失败: " + e.message; el.userMsg.className = "msg-bar err";
  });
}

