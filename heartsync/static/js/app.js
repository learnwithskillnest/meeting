/* HeartSync core client: API helper, toasts, forms, realtime, utilities. */
(function () {
  "use strict";

  const HS = (window.HS = window.HS || {});

  // ---------------------------------------------------------------- utils
  HS.$ = (sel, root) => (root || document).querySelector(sel);
  HS.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  HS.esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  HS.cookie = (name) => {
    const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : "";
  };
  HS.icon = (name, cls) => `<svg class="icon ${cls || ""}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  HS.initials = (name) => HS.esc((name || "?").trim().charAt(0).toUpperCase());
  HS.pageData = () => {
    const el = document.getElementById("page-data");
    try { return el ? JSON.parse(el.textContent) : {}; } catch (e) { return {}; }
  };

  HS.timeAgo = (iso) => {
    if (!iso) return "";
    const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
    if (s < 60) return "now";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    if (s < 604800) return Math.floor(s / 86400) + "d";
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  };
  HS.clock = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  HS.day = (iso) => {
    const d = new Date(iso), today = new Date();
    const y = new Date(); y.setDate(today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  };
  HS.money = (minor, cur) => {
    try { return new Intl.NumberFormat("en-IN", { style: "currency", currency: cur || "INR", maximumFractionDigits: minor % 100 ? 2 : 0 }).format(minor / 100); }
    catch (e) { return (cur || "") + " " + (minor / 100); }
  };
  HS.label = (map, key) => (map && map[key]) || (key ? String(key).replace(/_/g, " ") : "");

  // ------------------------------------------------------------------ API
  class ApiError extends Error {
    constructor(status, err) {
      super((err && err.message) || "Something went wrong.");
      this.status = status;
      this.code = err && err.code;
      this.fields = (err && err.fields) || {};
    }
  }
  HS.ApiError = ApiError;

  HS.api = async function (method, url, body, opts) {
    opts = opts || {};
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-CSRF-Token"] = HS.cookie("hs_csrf");
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    let res;
    try {
      res = await fetch(url, { method, headers, body: payload, credentials: "same-origin", signal: opts.signal });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError(0, { code: "network", message: "You appear to be offline. Check your connection and try again." });
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok || !data || data.ok === false) {
      const err = new ApiError(res.status, data && data.error);
      if (res.status === 401 && !opts.allow401) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname);
      } else if (err.code === "verification_required" && err.fields.next && !opts.noRedirect) {
        location.href = err.fields.next;
      } else if (err.code === "suspended") {
        location.href = "/account/suspended";
      }
      throw err;
    }
    return data.data;
  };

  HS.upgradePrompt = function (err) {
    if (err && (err.status === 402)) {
      HS.toast(`${HS.esc(err.message)} <a href="/billing">See plans</a>`, "error", true);
      return true;
    }
    return false;
  };

  // --------------------------------------------------------------- toasts
  HS.toast = function (msg, kind, html) {
    const box = document.getElementById("toasts");
    if (!box) return;
    const t = document.createElement("div");
    t.className = "toast " + (kind || "");
    if (html) t.innerHTML = msg; else t.textContent = msg;
    box.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, kind === "error" ? 6000 : 3500);
  };
  HS.fail = function (err) {
    if (err && err.name === "AbortError") return;
    if (HS.upgradePrompt(err)) return;
    HS.toast((err && err.message) || "Something went wrong.", "error");
  };

  // ---------------------------------------------------------------- forms
  HS.showFieldErrors = function (form, fields) {
    HS.$$("[data-error-for]", form).forEach((el) => { el.textContent = ""; el.hidden = true; });
    HS.$$("[aria-invalid]", form).forEach((el) => el.removeAttribute("aria-invalid"));
    let first = null;
    Object.entries(fields || {}).forEach(([name, msg]) => {
      const key = name.split(".")[0];
      const input = form.elements[key];
      const holder = HS.$(`[data-error-for="${key}"]`, form);
      if (holder) { holder.textContent = msg; holder.hidden = false; }
      const el = input && (input.length && !input.tagName ? input[0] : input);
      if (el && el.setAttribute) {
        el.setAttribute("aria-invalid", "true");
        if (holder && holder.id) el.setAttribute("aria-describedby", holder.id);
        first = first || el;
      }
    });
    if (first && first.focus) first.focus();
  };

  HS.formData = function (form) {
    const out = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name || el.disabled || el.type === "submit" || el.type === "file") return;
      if (el.dataset.multi !== undefined || (el.type === "checkbox" && form.querySelectorAll(`[name="${el.name}"]`).length > 1)) {
        out[el.name] = out[el.name] || [];
        if (el.checked) out[el.name].push(el.value);
      } else if (el.type === "checkbox") out[el.name] = el.checked;
      else if (el.type === "radio") { if (el.checked) out[el.name] = el.value; }
      else if (el.type === "number") out[el.name] = el.value === "" ? null : Number(el.value);
      else out[el.name] = el.value === "" ? null : el.value;
    });
    return out;
  };

  HS.busy = function (btn, on) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.innerHTML; btn.setAttribute("aria-busy", "true"); btn.disabled = true; }
    else { btn.removeAttribute("aria-busy"); btn.disabled = false; if (btn.dataset.label) btn.innerHTML = btn.dataset.label; }
  };

  // Declarative forms: <form data-api="/api/x" data-method="POST" data-next="/y">
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-api]");
    if (!form) return;
    e.preventDefault();
    const btn = form.querySelector("[type=submit]");
    const status = form.querySelector("[data-form-status]");
    if (status) { status.hidden = true; status.textContent = ""; }
    HS.showFieldErrors(form, {});
    HS.busy(btn, true);
    try {
      let data = HS.formData(form);
      if (form.dataset.transform && HS.transforms[form.dataset.transform]) data = HS.transforms[form.dataset.transform](data, form);
      const res = await HS.api(form.dataset.method || "POST", form.dataset.api, data, { allow401: form.dataset.allow401 !== undefined, noRedirect: true });
      const proceed = form.dispatchEvent(new CustomEvent("api:success", { detail: res, bubbles: true, cancelable: true }));
      if (form.dataset.success) {
        if (status) { status.className = "notice ok"; status.textContent = form.dataset.success; status.hidden = false; }
        else HS.toast(form.dataset.success, "success");
      }
      const next = (res && res.next) || form.dataset.next;
      if (proceed && next && form.dataset.stay === undefined) location.href = next;
    } catch (err) {
      if (err.fields && Object.keys(err.fields).length && err.code !== "verification_required") HS.showFieldErrors(form, err.fields);
      if (status) { status.className = "notice danger"; status.textContent = err.message; status.hidden = false; }
      else HS.fail(err);
      form.dispatchEvent(new CustomEvent("api:error", { detail: err, bubbles: true }));
    } finally {
      HS.busy(btn, false);
    }
  });
  HS.transforms = {};

  // Confirm dialogs
  HS.confirm = function (title, body, okLabel, danger) {
    return new Promise((resolve) => {
      const dlg = document.createElement("dialog");
      dlg.innerHTML = `<form method="dialog"><div class="dialog-body"><h2>${HS.esc(title)}</h2><p class="muted">${HS.esc(body)}</p></div>
        <div class="dialog-actions"><button class="btn" value="cancel">Cancel</button>
        <button class="btn ${danger ? "danger solid" : "primary"}" value="ok">${HS.esc(okLabel || "Confirm")}</button></div></form>`;
      document.body.appendChild(dlg);
      dlg.addEventListener("close", () => { resolve(dlg.returnValue === "ok"); dlg.remove(); });
      dlg.showModal();
    });
  };

  // ----------------------------------------------------------- theme/logout
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-theme-toggle]");
    if (t) {
      const root = document.documentElement;
      const current = root.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("hs-theme", next); } catch (err) { /* ignore */ }
      return;
    }
    if (e.target.closest("[data-reload]")) { location.reload(); return; }
    if (e.target.closest("[data-back]")) { if (history.length > 1) history.back(); else location.href = "/discover"; return; }
    const lo = e.target.closest("[data-logout]");
    if (lo) {
      e.preventDefault();
      try { await HS.api("POST", "/api/auth/logout", {}); } catch (err) { /* ignore */ }
      location.href = "/";
    }
  });

  // ----------------------------------------------------------- counts/SSE
  HS.setCount = function (name, n) {
    HS.$$(`[data-count="${name}"]`).forEach((b) => {
      b.textContent = n > 99 ? "99+" : String(n || "");
      b.classList.toggle("hidden", !n);
      if (n) b.setAttribute("aria-label", `${n} unread`);
    });
  };
  HS.refreshCounts = async function () {
    if (!document.body.dataset.user) return;
    try {
      const c = await HS.api("GET", "/api/counts", undefined, { allow401: true, noRedirect: true });
      HS.setCount("notifications", c.notifications);
      HS.setCount("messages", c.messages);
    } catch (e) { /* ignore */ }
  };

  const listeners = {};
  HS.on = (event, fn) => { (listeners[event] = listeners[event] || []).push(fn); };
  HS.emit = (event, data) => (listeners[event] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });

  let es = null, retry = 1000;
  HS.connect = function () {
    if (!document.body.dataset.canDate || !window.EventSource) return;
    es = new EventSource("/api/stream");
    const events = ["ready", "message", "message_updated", "message_deleted", "reaction", "read", "typing", "notification",
      "match", "conversation_closed", "phone_shared", "logout"];
    events.forEach((name) => es.addEventListener(name, (ev) => {
      let data = {};
      try { data = JSON.parse(ev.data || "{}"); } catch (e) { /* ignore */ }
      if (name === "ready") { retry = 1000; HS.emit("reconnected", data); }
      if (name === "logout") { es.close(); location.href = "/login"; return; }
      if (name === "notification" || name === "message" || name === "match") HS.refreshCounts();
      if (name === "match") HS.toast(`It's a match with ${HS.esc(data.name || "someone")}! <a href="/chat/${HS.esc(data.conversation_id)}">Say hi</a>`, "success", true);
      HS.emit(name, data);
    }));
    es.onerror = () => {
      es.close();
      HS.emit("disconnected", {});
      setTimeout(HS.connect, retry);
      retry = Math.min(retry * 2, 30000);
    };
  };

  document.addEventListener("DOMContentLoaded", () => {
    HS.refreshCounts();
    HS.connect();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) HS.refreshCounts(); });
  });
})();
