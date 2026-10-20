/* Realtime chat: conversation list, message thread, composer, phone sharing. */
(function () {
  "use strict";
  const { $, $$, esc, api, toast, fail } = HS;
  const root = $("#chat-root");
  const pane = $("#chat-pane");
  const me = root.dataset.me;
  let active = root.dataset.active || null;
  let archived = false, convs = [], thread = null, messages = [], hasMore = false, replyTo = null;
  let typingTimer = null, lastTypingSent = 0, loadingOlder = false;

  // ------------------------------------------------------------ list
  async function loadList() {
    const q = $("#conv-search").value.trim();
    try {
      convs = await api("GET", `/api/conversations?archived=${archived ? 1 : 0}${q ? "&q=" + encodeURIComponent(q) : ""}`);
      renderList();
    } catch (err) { fail(err); }
  }

  function renderList() {
    const list = $("#conv-list");
    if (!convs.length) {
      list.innerHTML = `<li class="empty small">${archived ? "No archived chats." : $("#conv-search").value ? "No conversations match." : 'No chats yet. <a href="/discover">Find matches</a>'}</li>`;
      return;
    }
    list.innerHTML = convs.map((c) => `<li><a class="list-item ${c.unread ? "unread" : ""}" href="/chat/${esc(c.id)}" data-conv="${esc(c.id)}" ${c.id === active ? 'aria-current="page"' : ""}>
      <span class="avatar-wrap">${c.photo ? `<img class="avatar" src="${esc(c.photo)}" alt="">` : `<span class="avatar">${HS.initials(c.display_name)}</span>`}${c.online ? '<span class="online-dot" title="Online"></span>' : ""}</span>
      <span class="body"><span class="title">${esc(c.display_name)}${c.muted ? ' <span class="small muted">· muted</span>' : ""}</span>
      <span class="sub">${esc(c.last_message || "You matched! Say hello 👋")}</span></span>
      <span class="stack"><time datetime="${esc(c.last_message_at)}">${HS.timeAgo(c.last_message_at)}</time>${c.unread ? `<span class="badge">${c.unread}</span>` : ""}</span>
    </a></li>`).join("");
  }

  // ---------------------------------------------------------- thread
  async function open(id, push) {
    active = id;
    root.classList.add("has-active");
    if (push) history.pushState({ id }, "", "/chat/" + id);
    $$("[data-conv]").forEach((a) => a.toggleAttribute("aria-current", a.dataset.conv === id));
    pane.innerHTML = '<div class="chat-empty"><div class="skeleton sk-md w-full"></div></div>';
    replyTo = null;
    try {
      const res = await api("GET", `/api/conversations/${id}/messages`);
      thread = res.conversation; messages = res.messages; hasMore = res.has_more;
      thread.reactions = res.reactions;
      renderThread();
      markRead();
      loadPhone();
    } catch (err) {
      pane.innerHTML = `<div class="chat-empty"><div class="empty"><h2>Conversation unavailable</h2><p>${esc(err.message)}</p><a class="btn" href="/chat">Back to chats</a></div></div>`;
    }
  }

  function renderThread() {
    const o = thread.other;
    pane.innerHTML = `
      <div class="chat-head">
        <a class="icon-btn mobile-only" href="/chat" aria-label="Back to chats" id="back-to-list">${HS.icon("back")}</a>
        <a href="/u/${esc(o.id)}" class="avatar-wrap">${o.photos && o.photos[0] ? `<img class="avatar sm" src="${esc(o.photos[0].url)}" alt="">` : `<span class="avatar sm">${HS.initials(o.display_name)}</span>`}${o.online ? '<span class="online-dot"></span>' : ""}</a>
        <div class="who"><strong><a href="/u/${esc(o.id)}">${esc(o.display_name)}</a></strong><small id="presence">${o.online ? "Online" : esc(o.last_seen || "")}</small></div>
        <button class="icon-btn" id="thread-menu-btn" aria-haspopup="true" aria-expanded="false" aria-label="Conversation options">${HS.icon("more")}</button>
      </div>
      <div class="menu card hidden" id="thread-menu" role="menu">
        <button class="btn ghost block" role="menuitem" data-menu="phone">${HS.icon("phone")} Phone number…</button>
        <button class="btn ghost block" role="menuitem" data-menu="mute">${thread.muted ? "Unmute" : "Mute"} notifications</button>
        <button class="btn ghost block" role="menuitem" data-menu="archive">${thread.archived ? "Move to inbox" : "Archive chat"}</button>
        <a class="btn ghost block" role="menuitem" href="/u/${esc(o.id)}">View profile</a>
        <button class="btn ghost block" role="menuitem" data-menu="unmatch">Unmatch</button>
        <button class="btn ghost block" role="menuitem" data-menu="report">Report</button>
        <button class="btn danger block" role="menuitem" data-menu="block">Block</button>
      </div>
      <div id="phone-slot"></div>
      <div class="messages" id="messages" role="log" aria-live="polite" aria-label="Messages with ${esc(o.display_name)}"></div>
      <div class="typing" id="typing" aria-live="polite"></div>
      <div id="reply-slot"></div>
      <form class="composer" id="composer" autocomplete="off">
        <label class="icon-btn" title="Send a photo" aria-label="Send a photo">${HS.icon("image")}<input type="file" accept="image/jpeg,image/png,image/webp" id="image-input" class="sr-only"></label>
        <label for="msg-input" class="sr-only">Message</label>
        <textarea id="msg-input" rows="1" maxlength="2000" placeholder="Write a message…"></textarea>
        <button class="icon-btn" type="submit" aria-label="Send" id="send-btn">${HS.icon("send")}</button>
      </form>`;
    renderMessages(true);
    if (!messages.length) showIcebreakers();
    $("#msg-input").focus();
  }

  function msgHtml(m, prev) {
    let sep = "";
    if (!prev || HS.day(prev.created_at) !== HS.day(m.created_at)) sep = `<div class="day-sep">${esc(HS.day(m.created_at))}</div>`;
    if (m.kind === "system") {
      const who = m.sender_id === me ? "You" : thread.other.display_name;
      return sep + `<div class="system-msg" data-mid="${m.id}">${esc(who)} ${esc(m.body)}</div>`;
    }
    const reply = m.reply_to ? `<div class="reply-quote">${esc(m.reply_to.sender_id === me ? "You" : thread.other.display_name)}: ${esc(m.reply_to.body || "Message deleted")}</div>` : "";
    let body;
    if (m.deleted) body = `<div class="bubble deleted">Message deleted</div>`;
    else if (m.kind === "image") body = `<div class="bubble image">${m.attachments.map((a) => `<a href="${esc(a.url)}" target="_blank" rel="noopener"><img src="${esc(a.url)}" alt="Photo" loading="lazy"></a>`).join("")}</div>`;
    else body = `<div class="bubble">${esc(m.body)}</div>`;
    const reactions = (m.reactions || []).length ? `<div class="reactions">${m.reactions.map((r) => `<span title="${r.user_id === me ? "You" : esc(thread.other.display_name)}">${esc(r.emoji)}</span>`).join("")}</div>` : "";
    const status = m.mine ? (m.pending ? "Sending…" : m.failed ? `<button class="link-btn" data-retry="${esc(m.client_id)}">Failed — retry</button>` : m.status === "read" ? "Read" : m.status === "delivered" ? "Delivered" : "Sent") : "";
    const tools = m.deleted || m.pending ? "" : `<div class="msg-tools">
      <button type="button" data-reply="${m.id}" aria-label="Reply">↩</button>
      ${thread.reactions.map((e) => `<button type="button" data-react="${m.id}" data-emoji="${e}" aria-label="React ${e}">${e}</button>`).join("")}
      ${m.mine && m.kind === "text" ? `<button type="button" data-edit="${m.id}" aria-label="Edit">✎</button>` : ""}
      ${m.mine ? `<button type="button" data-delete="${m.id}" aria-label="Delete">🗑</button>` : `<button type="button" data-report-msg="${m.id}" aria-label="Report message">⚑</button>`}
    </div>`;
    return sep + `<div class="msg ${m.mine ? "mine" : ""}" data-mid="${m.id || ""}" data-cid="${esc(m.client_id || "")}">${tools}${reply}${body}${reactions}
      <div class="meta"><time datetime="${esc(m.created_at)}">${HS.clock(m.created_at)}</time>${m.edited ? " · edited" : ""}${status ? " · " + status : ""}</div></div>`;
  }

  function renderMessages(scrollBottom) {
    const box = $("#messages");
    if (!box) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    const older = hasMore ? '<div class="center"><button class="btn sm ghost" id="load-older">Load earlier messages</button></div>' : "";
    box.innerHTML = older + messages.map((m, i) => msgHtml(m, messages[i - 1])).join("");
    if (scrollBottom || atBottom) box.scrollTop = box.scrollHeight;
  }

  async function showIcebreakers() {
    try {
      const r = await api("GET", `/api/conversations/${active}/icebreakers`);
      const box = $("#messages");
      if (!box || messages.length) return;
      box.innerHTML = `<div class="system-msg">You matched with ${esc(thread.other.display_name)}! Say hello 👋</div>
        <div class="card mt-1"><p class="small muted mb-05">${esc(r.label)}</p><div class="stack">${r.suggestions.map((s) => `<button class="btn sm ghost block" data-suggest="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
    } catch (e) { /* optional */ }
  }

  async function loadOlder() {
    if (loadingOlder || !hasMore || !messages.length) return;
    loadingOlder = true;
    const box = $("#messages");
    const prevHeight = box.scrollHeight;
    try {
      const res = await api("GET", `/api/conversations/${active}/messages?before=${messages.find((m) => m.id).id}`);
      messages = res.messages.concat(messages);
      hasMore = res.has_more;
      renderMessages(false);
      box.scrollTop = box.scrollHeight - prevHeight;
    } catch (err) { fail(err); } finally { loadingOlder = false; }
  }

  async function catchUp() {
    if (!active || !thread) return;
    const last = [...messages].reverse().find((m) => m.id);
    try {
      const res = await api("GET", `/api/conversations/${active}/messages?after=${last ? last.id : 0}`);
      res.messages.forEach(upsert);
      renderMessages(false);
      markRead();
    } catch (e) { /* ignore */ }
  }

  function upsert(m) {
    const i = messages.findIndex((x) => (m.id && x.id === m.id) || (m.client_id && x.client_id === m.client_id));
    if (i >= 0) messages[i] = Object.assign({}, messages[i], m, { pending: false, failed: false });
    else messages.push(m);
  }

  async function markRead() {
    const lastOther = [...messages].reverse().find((m) => m.id && !m.mine);
    if (!lastOther || document.hidden) return;
    try { await api("POST", `/api/conversations/${active}/read`, { up_to_id: lastOther.id }); HS.refreshCounts(); } catch (e) { /* ignore */ }
    const c = convs.find((x) => x.id === active);
    if (c && c.unread) { c.unread = 0; renderList(); }
  }

  // ---------------------------------------------------------- sending
  async function send(body) {
    const client_id = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    const optimistic = { id: null, client_id, mine: true, kind: "text", body, created_at: new Date().toISOString(), pending: true,
      reactions: [], attachments: [], reply_to: replyTo ? { id: replyTo.id, body: replyTo.body, sender_id: replyTo.sender_id } : null };
    messages.push(optimistic);
    renderMessages(true);
    const reply_to_id = replyTo ? replyTo.id : null;
    clearReply();
    await deliver(optimistic, reply_to_id);
  }

  async function deliver(m, reply_to_id) {
    try {
      const saved = await api("POST", `/api/conversations/${active}/messages`, { body: m.body, reply_to_id, client_id: m.client_id });
      upsert(Object.assign(saved, { client_id: m.client_id }));
    } catch (err) {
      m.pending = false; m.failed = true;
      if (err.status !== 0) fail(err);
    }
    renderMessages(true);
    loadList();
  }

  function setReply(m) {
    replyTo = m;
    $("#reply-slot").innerHTML = `<div class="reply-preview"><span>Replying to ${esc(m.mine ? "yourself" : thread.other.display_name)}: ${esc(m.body || "Photo")}</span><button class="link-btn" id="cancel-reply" aria-label="Cancel reply">×</button></div>`;
    $("#msg-input").focus();
  }
  function clearReply() { replyTo = null; const s = $("#reply-slot"); if (s) s.innerHTML = ""; }

  // ------------------------------------------------------------ phone
  async function loadPhone() {
    if (!thread) return;
    try {
      const st = await api("GET", `/api/matches/${thread.match_id}/phone`);
      thread.phone = st;
      renderPhone();
    } catch (e) { /* ignore */ }
  }
  function renderPhone() {
    const slot = $("#phone-slot");
    if (!slot || !thread.phone) return;
    const st = thread.phone;
    slot.innerHTML = st.they_shared ? `<div class="phone-card">${HS.icon("phone")} <span>${esc(thread.other.display_name)} shared their number.</span>
      <button class="btn sm" id="reveal-phone">Show number</button><span id="phone-value"></span></div>` : "";
  }

  async function phoneMenu() {
    const st = thread.phone || {};
    if (st.i_shared) {
      if (await HS.confirm("Stop sharing your number?", `${thread.other.display_name} will no longer be able to see your number in the app. (We can't remove it from their device if they saved it.)`, "Stop sharing", true)) {
        try { await api("POST", `/api/matches/${thread.match_id}/phone/revoke`, {}); toast("You've stopped sharing your number."); loadPhone(); } catch (err) { fail(err); }
      }
      return;
    }
    if (!st.can_share) {
      toast('Verify your phone number first. <a href="/verify-phone?change=1">Verify now</a>', "error", true);
      return;
    }
    const dlg = $("#phone-dialog");
    const box = $("#phone-confirm");
    box.checked = false; $("#phone-share-ok").disabled = true;
    dlg.querySelector("[data-phone-name]").textContent = thread.other.display_name;
    box.onchange = () => { $("#phone-share-ok").disabled = !box.checked; };
    dlg.onclose = async () => {
      if (dlg.returnValue !== "ok" || !box.checked) return;
      try { await api("POST", `/api/matches/${thread.match_id}/phone/share`, { confirm: true }); toast("Number shared", "success"); loadPhone(); catchUp(); }
      catch (err) { fail(err); }
    };
    dlg.showModal();
  }

  // ----------------------------------------------------------- events
  document.addEventListener("click", async (e) => {
    const conv = e.target.closest("[data-conv]");
    if (conv && !e.metaKey && !e.ctrlKey) { e.preventDefault(); open(conv.dataset.conv, true); return; }
    if (e.target.closest("#back-to-list")) { e.preventDefault(); active = null; root.classList.remove("has-active"); history.pushState({}, "", "/chat"); loadList(); return; }
    const arch = e.target.closest("[data-archived]");
    if (arch) { archived = arch.dataset.archived === "1"; $$("[data-archived]").forEach((b) => b.setAttribute("aria-pressed", String(b === arch))); loadList(); return; }
    if (e.target.closest("#load-older")) { loadOlder(); return; }
    const sug = e.target.closest("[data-suggest]");
    if (sug) { const input = $("#msg-input"); input.value = sug.dataset.suggest; input.focus(); return; }
    if (e.target.closest("#cancel-reply")) { clearReply(); return; }
    const menuBtn = e.target.closest("#thread-menu-btn");
    const menu = $("#thread-menu");
    if (menuBtn) { const open_ = menu.classList.toggle("hidden"); menuBtn.setAttribute("aria-expanded", String(!open_)); return; }
    if (menu && !e.target.closest("#thread-menu")) menu.classList.add("hidden");
    const item = e.target.closest("[data-menu]");
    if (item) {
      menu.classList.add("hidden");
      const action = item.dataset.menu, o = thread.other;
      if (action === "phone") phoneMenu();
      if (action === "mute" || action === "archive") {
        const body = action === "mute" ? { muted: !thread.muted } : { archived: !thread.archived };
        try { await api("POST", `/api/conversations/${active}/flags`, body); Object.assign(thread, body); renderThread(); loadList(); toast(action === "mute" ? (thread.muted ? "Muted" : "Unmuted") : (thread.archived ? "Archived" : "Moved to inbox")); }
        catch (err) { fail(err); }
      }
      if (action === "unmatch" && await HS.unmatch(thread.match_id, o.display_name)) { location.href = "/chat"; }
      if (action === "block" && await HS.block(o.id, o.display_name)) { location.href = "/chat"; }
      if (action === "report") { const r = await HS.report(o.id, o.display_name); if (r.blocked) location.href = "/chat"; }
      return;
    }
    if (e.target.closest("#reveal-phone")) {
      try { const r = await api("GET", `/api/matches/${thread.match_id}/phone/reveal`); $("#phone-value").innerHTML = `<strong>${esc(r.phone)}</strong> <a class="btn sm" href="tel:${esc(r.phone)}">Call</a>`; $("#reveal-phone").remove(); }
      catch (err) { fail(err); loadPhone(); }
      return;
    }
    const rp = e.target.closest("[data-reply]");
    if (rp) { const m = messages.find((x) => x.id === Number(rp.dataset.reply)); if (m) setReply(m); return; }
    const re = e.target.closest("[data-react]");
    if (re) {
      const m = messages.find((x) => x.id === Number(re.dataset.react));
      const mine = (m.reactions || []).find((r) => r.user_id === me);
      const emoji = mine && mine.emoji === re.dataset.emoji ? null : re.dataset.emoji;
      try { m.reactions = await api("POST", `/api/messages/${m.id}/reactions`, { emoji }); renderMessages(false); } catch (err) { fail(err); }
      return;
    }
    const ed = e.target.closest("[data-edit]");
    if (ed) {
      const m = messages.find((x) => x.id === Number(ed.dataset.edit));
      const body = prompt("Edit message (within 15 minutes of sending):", m.body);
      if (body && body.trim() && body !== m.body) {
        try { upsert(await api("PATCH", `/api/messages/${m.id}`, { body })); renderMessages(false); } catch (err) { fail(err); }
      }
      return;
    }
    const del = e.target.closest("[data-delete]");
    if (del) {
      if (!(await HS.confirm("Delete this message?", "It will be removed for both of you.", "Delete", true))) return;
      try { await api("DELETE", `/api/messages/${del.dataset.delete}`); const m = messages.find((x) => x.id === Number(del.dataset.delete)); m.deleted = true; m.body = null; m.attachments = []; renderMessages(false); }
      catch (err) { fail(err); }
      return;
    }
    const rm = e.target.closest("[data-report-msg]");
    if (rm) { const r = await HS.report(thread.other.id, thread.other.display_name, Number(rm.dataset.reportMsg)); if (r.blocked) location.href = "/chat"; return; }
    const retry = e.target.closest("[data-retry]");
    if (retry) { const m = messages.find((x) => x.client_id === retry.dataset.retry); if (m) { m.failed = false; m.pending = true; renderMessages(true); deliver(m, null); } }
  });

  document.addEventListener("submit", (e) => {
    if (e.target.id !== "composer") return;
    e.preventDefault();
    const input = $("#msg-input");
    const body = input.value.trim();
    if (!body) return;
    if (body.length > 2000) { toast("Messages are limited to 2000 characters.", "error"); return; }
    input.value = "";
    input.style.height = "";
    send(body);
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.id === "msg-input" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
    if (e.key === "Escape") { const m = $("#thread-menu"); if (m) m.classList.add("hidden"); }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "conv-search") { clearTimeout(typingTimer); typingTimer = setTimeout(loadList, 250); return; }
    if (e.target.id !== "msg-input") return;
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px";
    if (Date.now() - lastTypingSent > 3000 && active) {
      lastTypingSent = Date.now();
      api("POST", `/api/conversations/${active}/typing`, {}).catch(() => {});
    }
  });

  document.addEventListener("change", async (e) => {
    if (e.target.id !== "image-input") return;
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast("Only JPEG, PNG or WebP photos can be sent.", "error"); return; }
    if (file.size > 8 * 1024 * 1024) { toast("Photos must be smaller than 8 MB.", "error"); return; }
    const fd = new FormData();
    fd.append("file", file);
    toast("Sending photo…");
    try { upsert(await api("POST", `/api/conversations/${active}/images`, fd)); renderMessages(true); loadList(); }
    catch (err) { fail(err); }
  });

  $("#messages") || null;
  document.addEventListener("scroll", (e) => { if (e.target.id === "messages" && e.target.scrollTop < 40) loadOlder(); }, true);

  // --------------------------------------------------------- realtime
  HS.on("message", (m) => {
    if (m.conversation_id === active) {
      upsert(m); renderMessages(false); markRead();
      $("#typing").textContent = "";
    }
    loadList();
  });
  HS.on("message_updated", (m) => { if (m.conversation_id === active) { upsert(m); renderMessages(false); } });
  HS.on("message_deleted", (d) => {
    if (d.conversation_id !== active) return;
    const m = messages.find((x) => x.id === d.id);
    if (m) { m.deleted = true; m.body = null; m.attachments = []; renderMessages(false); }
  });
  HS.on("reaction", (d) => {
    if (d.conversation_id !== active) return;
    const m = messages.find((x) => x.id === d.message_id);
    if (m) { m.reactions = d.reactions; renderMessages(false); }
  });
  HS.on("read", (d) => {
    if (d.conversation_id !== active) return;
    messages.forEach((m) => { if (m.mine && m.id && m.id <= d.up_to_id) m.status = "read"; });
    renderMessages(false);
  });
  let typingClear = null;
  HS.on("typing", (d) => {
    if (d.conversation_id !== active || !thread) return;
    $("#typing").textContent = `${thread.other.display_name} is typing…`;
    clearTimeout(typingClear);
    typingClear = setTimeout(() => { const t = $("#typing"); if (t) t.textContent = ""; }, 4000);
  });
  HS.on("conversation_closed", (d) => {
    if (d.conversation_id === active) {
      pane.innerHTML = '<div class="chat-empty"><div class="empty"><h2>This conversation has ended</h2><p>This match is no longer available.</p><a class="btn" href="/chat">Back to chats</a></div></div>';
      active = null;
    }
    loadList();
  });
  HS.on("phone_shared", (d) => { if (d.conversation_id === active) { loadPhone(); catchUp(); } });
  HS.on("reconnected", () => { catchUp(); loadList(); });
  HS.on("disconnected", () => { const p = $("#presence"); if (p) p.textContent = "Reconnecting…"; });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && active) markRead(); });
  window.addEventListener("popstate", () => {
    const m = location.pathname.match(/^\/chat\/([0-9a-f]{32})$/);
    if (m) open(m[1], false); else { active = null; root.classList.remove("has-active"); }
  });

  loadList();
  if (active) open(active, false);
})();
