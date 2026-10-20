/* Admin & moderation dashboard. All authorization is enforced server-side. */
(function () {
  "use strict";
  const { $, $$, esc, api, toast, fail } = HS;
  const root = $("#admin-root");
  const main = $("#admin-main");
  const section = root.dataset.section;
  const itemId = root.dataset.item;
  const isAdmin = !!root.dataset.admin;
  const qs = new URLSearchParams(location.search);
  const page = Number(qs.get("page") || 1);

  const date = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");
  const status = (s) => `<span class="status ${esc(s)}">${esc(String(s || "").replace("_", " "))}</span>`;
  const pager = (r, base) => r.pages > 1 ? `<div class="row mt-1">${r.page > 1 ? `<a class="btn sm" href="${base}page=${r.page - 1}">← Previous</a>` : ""}<span class="small muted">Page ${r.page} of ${r.pages} · ${r.total} total</span>${r.page < r.pages ? `<a class="btn sm" href="${base}page=${r.page + 1}">Next →</a>` : ""}</div>` : `<p class="small muted mt-1">${r.total} total</p>`;
  const stat = (n, l) => `<div class="card stat"><div class="num">${esc(n)}</div><div class="lbl">${esc(l)}</div></div>`;

  const views = {
    async overview() {
      const { metrics: m, spam } = await api("GET", "/api/admin/metrics");
      const subs = Object.entries(m.subscriptions).map(([k, v]) => `${v} ${k}`).join(", ") || "none";
      main.innerHTML = `<div class="grid cols-3">
        ${stat(m.reports_open, "Open reports")}${stat(m.verifications_pending, "Pending photo verifications")}${stat(m.appeals_open, "Open appeals")}
        ${stat(m.flagged_users, "Auto-flagged members")}${stat(m.suspended_users, "Suspended members")}${stat(m.users_total, "Members")}
        ${stat(m.users_active_7d, "Active in last 7 days")}${stat(m.users_new_7d, "Joined in last 7 days")}${stat(m.profiles_onboarded, "Completed profiles")}
        ${stat(m.matches_7d, "Matches (7 days)")}${stat(m.messages_24h, "Messages (24h)")}${stat(m.likes_24h, "Likes (24h)")}
        ${stat(HS.money(m.revenue_30d_minor, "INR"), "Revenue (30 days)")}${stat(subs, "Active subscriptions")}${stat(m.realtime_connections, "Live connections")}
      </div>
      <section class="card"><h2>Spam & abuse indicators</h2><p class="small muted">Heuristic signals to prioritise review. They are not proof of wrongdoing.</p>
      ${spam.length ? `<div class="table-wrap"><table><thead><tr><th>Member</th><th>Signals</th></tr></thead><tbody>${spam.map((s) => `<tr><td>${s.user_id ? `<a href="/admin/users/${esc(s.user_id)}">${esc(s.display_name || s.email)}</a>` : "—"}</td><td>${s.signals.map(esc).join("<br>")}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">No signals right now.</p>'}</section>`;
    },

    async users() {
      const q = qs.get("q") || "", st = qs.get("status") || "", fl = qs.get("flagged") === "1";
      const r = await api("GET", `/api/admin/users?page=${page}&q=${encodeURIComponent(q)}&status=${st}${fl ? "&flagged=1" : ""}`);
      main.innerHTML = `<form class="row" method="get" action="/admin/users">
          <input type="search" name="q" value="${esc(q)}" placeholder="Email, name or ID" aria-label="Search members" class="w-auto">
          <select name="status" class="w-auto" aria-label="Status"><option value="">Any status</option>${["active", "suspended", "deactivated", "deleted"].map((s) => `<option ${s === st ? "selected" : ""}>${s}</option>`).join("")}</select>
          <label class="check"><input type="checkbox" name="flagged" value="1" ${fl ? "checked" : ""}> Flagged</label>
          <button class="btn sm primary">Search</button></form>
        <div class="table-wrap"><table><thead><tr><th>Member</th><th>Status</th><th>Role</th><th>Reports</th><th>Joined</th><th>Last active</th></tr></thead><tbody>
        ${r.users.map((u) => `<tr><td><a href="/admin/users/${esc(u.id)}">${esc(u.display_name || "(no profile)")}</a><br><span class="small muted">${esc(u.email)}</span>${u.flagged_at ? ' <span class="status open">flagged</span>' : ""}</td>
          <td>${status(u.status)}</td><td>${esc(u.role)}</td><td>${u.reports}</td><td class="small">${date(u.created_at)}</td><td class="small">${date(u.last_active_at)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">No members found.</td></tr>'}
        </tbody></table></div>${pager(r, `/admin/users?q=${encodeURIComponent(q)}&status=${st}${fl ? "&flagged=1" : ""}&`)}`;
    },

    async user() {
      const d = await api("GET", "/api/admin/users/" + itemId);
      const u = d.user;
      main.innerHTML = `<p><a href="/admin/users">← Members</a></p>
        <section class="card"><div class="row between"><div><h2 class="mb-0">${esc(d.profile ? d.profile.display_name : "(no profile)")}${d.age ? ", " + d.age : ""}</h2>
          <p class="small muted mb-0">${esc(u.email)} · ${esc(u.id)}</p></div>${status(u.status)}</div>
          <dl class="facts mt-1">
            <div class="fact"><dt>Role</dt><dd>${esc(u.role)}</dd></div><div class="fact"><dt>Plan</dt><dd>${esc(d.plan)}</dd></div>
            <div class="fact"><dt>Email verified</dt><dd>${u.email_verified_at ? "Yes" : "No"}</dd></div><div class="fact"><dt>Phone</dt><dd>${esc(d.phone || "—")} ${d.phone_verified ? "(verified)" : ""}</dd></div>
            <div class="fact"><dt>Photo verified</dt><dd>${d.profile && d.profile.photo_verified ? "Yes" : "No"}</dd></div><div class="fact"><dt>Joined</dt><dd>${date(u.created_at)}</dd></div>
            <div class="fact"><dt>Last active</dt><dd>${date(u.last_active_at)}</dd></div><div class="fact"><dt>Reports made</dt><dd>${d.reports_made}</dd></div>
          </dl>
          ${u.flagged_at ? `<p class="notice warn mt-1">Auto-flagged ${date(u.flagged_at)}: ${esc(u.flag_reason)}. Hidden from discovery until cleared.</p>` : ""}
          ${u.suspended_reason ? `<p class="notice danger mt-1">Suspended: ${esc(u.suspended_reason)}</p>` : ""}
          ${d.profile ? `<p class="mt-1"><strong>${esc(d.profile.headline || "")}</strong></p><p class="pre small">${esc(d.profile.about || "")}</p>` : ""}
          <div class="row">${d.photos.map((p) => `<a href="${esc(p.url)}" target="_blank" rel="noopener"><img class="avatar" src="${esc(p.url)}" alt="Profile photo"></a>`).join("")}</div>
        </section>
        ${u.status !== "deleted" ? `<section class="card stack"><h2>Actions</h2>
          <div class="field"><label for="reason">Reason / note (shown to the member for suspensions)</label><textarea id="reason" maxlength="500"></textarea></div>
          <div class="row">
            ${u.status === "suspended" ? '<button class="btn primary sm" data-action="reinstate">Reinstate</button>' : '<button class="btn danger solid sm" data-action="suspend">Suspend</button>'}
            <button class="btn sm" data-action="warn">Send warning</button>
            ${u.flagged_at ? '<button class="btn sm" data-action="clear_flag">Clear flag</button>' : ""}
          </div>
          ${isAdmin ? `<div class="row"><label for="role" class="small">Role</label><select id="role" class="w-auto">${["user", "moderator", "admin"].map((r) => `<option ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}</select><button class="btn sm" data-action="set_role">Update role</button></div>
          <div class="row"><label for="grant-plan" class="small">Complimentary plan</label><select id="grant-plan" class="w-auto"><option value="plus">Plus</option><option value="premium">Premium</option></select>
          <input id="grant-days" type="number" min="1" max="366" value="30" class="w-auto" aria-label="Days"><button class="btn sm" data-action="grant_plan">Grant</button></div>` : ""}
        </section>` : ""}
        <section class="card"><h2>Reports against this member</h2>${d.reports_against.length ? `<ul class="list">${d.reports_against.map((r) => `<li><a class="list-item" href="/admin/reports/${esc(r.id)}"><span class="body"><span class="title">${esc(r.category)}</span><span class="sub">${date(r.created_at)}</span></span>${status(r.status)}</a></li>`).join("")}</ul>` : '<p class="muted">None.</p>'}</section>
        <section class="card"><h2>Moderation history</h2>${d.actions.length ? `<ul class="list">${d.actions.map((a) => `<li class="list-item"><span class="body"><span class="title">${esc(a.action)}</span><span class="sub">${esc(a.reason || "")} — ${esc(a.actor || "system")}, ${date(a.created_at)}</span></span></li>`).join("")}</ul>` : '<p class="muted">No actions yet.</p>'}</section>`;
      if (views._userBound) return;
      views._userBound = true;
      main.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-action]");
        if (!b) return;
        const action = b.dataset.action;
        const body = { action, reason: $("#reason") ? $("#reason").value : null };
        if (action === "set_role") body.role = $("#role").value;
        if (action === "grant_plan") { body.plan = $("#grant-plan").value; body.days = Number($("#grant-days").value); }
        if (action === "suspend" && !(await HS.confirm("Suspend this member?", "They'll be signed out and can submit an appeal.", "Suspend", true))) return;
        try { await api("POST", `/api/admin/users/${itemId}/actions`, body); toast("Done", "success"); views.user(); }
        catch (err) { fail(err); }
      });
    },

    async reports() {
      const st = qs.get("status") || "active";
      const r = await api("GET", `/api/admin/reports?status=${st}&page=${page}`);
      main.innerHTML = `<div class="row">${[["active", "Open"], ["resolved", "Resolved"], ["dismissed", "Dismissed"]].map(([k, l]) => `<a class="btn sm ${k === st ? "primary" : ""}" href="/admin/reports?status=${k}">${l}</a>`).join("")}</div>
        <div class="table-wrap"><table><thead><tr><th>Category</th><th>Reported member</th><th>Reporter</th><th>Prior reports</th><th>Status</th><th>Received</th></tr></thead><tbody>
        ${r.reports.map((x) => `<tr><td><a href="/admin/reports/${esc(x.id)}">${esc(x.category_label)}</a>${x.message_id ? ' <span class="tag">message</span>' : ""}</td>
          <td>${esc(x.reported_name || "—")}</td><td>${esc(x.reporter_name || "—")}</td><td>${x.target_report_count}</td><td>${status(x.status)}</td><td class="small">${date(x.created_at)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">Queue is empty. 🎉</td></tr>'}
        </tbody></table></div>${pager(r, `/admin/reports?status=${st}&`)}`;
    },

    async report() {
      const d = await api("GET", "/api/admin/reports/" + itemId);
      const r = d.report, ev = d.evidence, t = d.reported;
      main.innerHTML = `<p><a href="/admin/reports">← Reports</a></p>
        <section class="card stack"><div class="row between"><h2 class="mb-0">${esc(r.category_label)}</h2>${status(r.status)}</div>
          <p class="small muted">Received ${date(r.created_at)}${r.resolved_at ? ` · Resolved ${date(r.resolved_at)}` : ""}</p>
          ${r.details ? `<div class="notice"><strong>Reporter's note:</strong><br><span class="pre">${esc(r.details)}</span></div>` : '<p class="muted">No details given.</p>'}
          ${ev ? `<div class="notice"><strong>Reported message</strong> (${date(ev.created_at)}):<br><span class="pre">${esc(ev.body || (ev.kind === "image" ? "[image]" : "[deleted]"))}</span>
            ${(ev.attachment_urls || []).map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener"><img class="mt-05" src="${esc(u)}" alt="Reported image" width="160"></a>`).join("")}</div>` : ""}
          ${r.resolution ? `<p><strong>Resolution:</strong> ${esc(r.resolution)}</p>` : ""}
        </section>
        <section class="card stack"><h2>Reported member: <a href="/admin/users/${esc(t.user.id)}">${esc(t.profile ? t.profile.display_name : t.user.email)}</a></h2>
          <p class="small muted">${t.reports_against.length} report(s) total · status ${esc(t.user.status)}</p>
          <div class="row">${t.photos.map((p) => `<img class="avatar" src="${esc(p.url)}" alt="">`).join("")}</div>
        </section>
        ${["open", "reviewing"].includes(r.status) ? `<section class="card stack"><h2>Decision</h2>
          <div class="field"><label for="note">Note (for suspensions this is shown to the member)</label><textarea id="note" maxlength="1000"></textarea></div>
          <div class="row"><button class="btn sm" data-resolve="reviewing">Mark reviewing</button><button class="btn sm" data-resolve="dismiss">Dismiss</button>
          <button class="btn sm" data-resolve="warn">Warn member</button><button class="btn sm danger solid" data-resolve="suspend">Suspend member</button></div></section>` : ""}`;
      main.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-resolve]");
        if (!b) return;
        try { await api("POST", `/api/admin/reports/${itemId}/resolve`, { action: b.dataset.resolve, note: $("#note").value || null }); toast("Report updated", "success"); location.href = "/admin/reports"; }
        catch (err) { fail(err); }
      });
    },

    async verifications() {
      const list = await api("GET", "/api/admin/verifications");
      main.innerHTML = `<p class="small muted">Compare the live selfie with the profile photos. Approve only if they clearly show the same person. This is not an identity document check.</p>` +
        (list.length ? list.map((v) => `<section class="card stack" data-ver="${esc(v.id)}"><div class="row between"><h3 class="mb-0"><a href="/admin/users/${esc(v.user_id)}">${esc(v.display_name)}</a></h3><span class="small muted">${date(v.created_at)}</span></div>
          <div class="row"><figure><img src="${esc(v.selfie_url)}" alt="Verification selfie" width="180"><figcaption class="small muted">Selfie</figcaption></figure>
          ${v.profile_photos.map((u) => `<figure><img src="${esc(u)}" alt="Profile photo" width="140"><figcaption class="small muted">Profile</figcaption></figure>`).join("")}</div>
          <div class="row"><button class="btn sm primary" data-review="1">Approve</button><button class="btn sm danger" data-review="0">Reject</button></div></section>`).join("")
          : '<div class="card empty"><h3>No pending verifications</h3></div>');
      main.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-review]");
        if (!b) return;
        const card = b.closest("[data-ver]");
        try { await api("POST", "/api/admin/verifications/" + card.dataset.ver, { approve: b.dataset.review === "1" }); card.remove(); toast("Saved", "success"); }
        catch (err) { fail(err); }
      });
    },

    async appeals() {
      const list = await api("GET", "/api/admin/appeals");
      main.innerHTML = list.length ? list.map((a) => `<section class="card stack" data-appeal="${esc(a.id)}"><div class="row between"><h3 class="mb-0"><a href="/admin/users/${esc(a.user_id)}">${esc(a.display_name || a.email)}</a></h3><span class="small muted">${date(a.created_at)}</span></div>
          <p class="small"><strong>Suspension reason:</strong> ${esc(a.suspended_reason || "—")}</p><div class="notice pre">${esc(a.message)}</div>
          <div class="field"><label>Decision note</label><textarea data-decision maxlength="1000"></textarea></div>
          <div class="row"><button class="btn sm primary" data-decide="1">Overturn & reinstate</button><button class="btn sm" data-decide="0">Uphold suspension</button></div></section>`).join("")
        : '<div class="card empty"><h3>No open appeals</h3></div>';
      main.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-decide]");
        if (!b) return;
        const card = b.closest("[data-appeal]");
        try { await api("POST", "/api/admin/appeals/" + card.dataset.appeal, { overturn: b.dataset.decide === "1", decision: card.querySelector("[data-decision]").value || "" }); card.remove(); toast("Saved", "success"); }
        catch (err) { fail(err); }
      });
    },

    async subscriptions() {
      const r = await api("GET", "/api/admin/subscriptions?page=" + page);
      main.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Member</th><th>Plan</th><th>Status</th><th>Provider</th><th>Period end</th><th>Started</th></tr></thead><tbody>
        ${r.subscriptions.map((s) => `<tr><td><a href="/admin/users/${esc(s.user_id)}">${esc(s.email)}</a></td><td>${esc(s.plan)}</td><td>${status(s.status)}</td><td>${esc(s.provider)}</td><td class="small">${date(s.current_period_end)}</td><td class="small">${date(s.created_at)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">None yet.</td></tr>'}
        </tbody></table></div>${pager(r, "/admin/subscriptions?")}`;
    },

    async audit() {
      const action = qs.get("action") || "";
      const r = await api("GET", `/api/admin/audit?page=${page}&action=${encodeURIComponent(action)}`);
      main.innerHTML = `<form class="row" method="get"><input name="action" value="${esc(action)}" placeholder="Filter by action prefix, e.g. admin." class="w-auto" aria-label="Action filter"><button class="btn sm">Filter</button></form>
        <div class="table-wrap"><table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Details</th></tr></thead><tbody>
        ${r.logs.map((l) => `<tr><td class="small nowrap">${date(l.created_at)}</td><td class="small">${esc(l.actor_name || l.actor_email || "system")}</td><td class="mono">${esc(l.action)}</td>
          <td class="small mono">${esc(l.target_type || "")} ${esc((l.target_id || "").slice(0, 12))}</td><td class="small mono">${esc(l.metadata === "{}" ? "" : l.metadata)}</td></tr>`).join("")}
        </tbody></table></div>${pager(r, `/admin/audit?action=${encodeURIComponent(action)}&`)}`;
    },

    async content() {
      const [faq, safety] = await Promise.all([api("GET", "/api/admin/content/faq"), api("GET", "/api/admin/content/safety-tips")]);
      const form = (slug, c, label) => `<section class="card stack"><h2>${label}</h2><form data-api="/api/admin/content/${slug}" data-method="PUT" data-stay data-success="Saved">
        <div class="field"><label>Title</label><input name="title" value="${esc(c.title)}" maxlength="120"></div>
        <div class="field"><label>Body (Markdown: ## headings, - lists, **bold**)</label><textarea name="body" rows="16" class="mono">${esc(c.body)}</textarea></div>
        <p class="notice" data-form-status hidden></p><button class="btn primary mt-1">Save</button></form></section>`;
      main.innerHTML = form("faq", faq, "Help & FAQ") + form("safety-tips", safety, "Safety tips");
    },
  };

  (views[section] || views.overview)().catch((err) => { main.innerHTML = `<div class="card empty"><h3>Couldn't load</h3><p>${esc(err.message)}</p></div>`; });
})();
