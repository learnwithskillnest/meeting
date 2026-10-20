/* Smaller pages: likes, matches, saved, notifications, billing, settings. */
(function () {
  "use strict";
  const { $, $$, esc, api, toast, fail } = HS;
  const pageEl = document.querySelector("[data-page]");
  const page = pageEl && pageEl.dataset.page;

  const empty = (icon, title, text, cta) => `<div class="card empty span-all"><div class="art">${HS.icon(icon)}</div><h3>${esc(title)}</h3><p>${text}</p>${cta || ""}</div>`;

  const pages = {
    async likes() {
      const r = await api("GET", "/api/likes/incoming");
      $("#likes-summary").textContent = r.count ? `${r.count} ${r.count === 1 ? "person likes" : "people like"} you${r.superlikes ? ` (${r.superlikes} Super Like${r.superlikes > 1 ? "s" : ""})` : ""}.` : "No new likes yet.";
      const grid = $("#likes-grid");
      if (!r.count) { grid.innerHTML = empty("heart", "No likes yet", "Complete your profile and add more photos to stand out.", '<a class="btn primary" href="/profile">Improve profile</a>'); return; }
      if (!r.visible) {
        $("#likes-upsell").hidden = false;
        grid.innerHTML = Array.from({ length: Math.min(r.count, 12) }, () => HS.cards.mini({ display_name: "?", photos: [] }, { blurred: true })).join("");
        return;
      }
      grid.innerHTML = r.profiles.map((p) => HS.cards.mini(p).replace('<div class="info">', `${p.superlike ? '<span class="score">⭐ Super Like</span>' : ""}<div class="info">`)).join("");
    },
    async matches() {
      const ms = await api("GET", "/api/matches");
      const grid = $("#matches-grid");
      if (!ms.length) { grid.innerHTML = empty("heart", "No matches yet", "When someone you like likes you back, they'll appear here.", '<a class="btn primary" href="/discover">Discover people</a>'); return; }
      grid.innerHTML = ms.map((m) => `<a class="mini-card" href="/chat/${esc(m.conversation_id)}">
        ${m.photo ? `<img src="${esc(m.photo)}" alt="" loading="lazy">` : `<div class="no-photo">${HS.initials(m.display_name)}</div>`}
        ${m.has_messages ? "" : '<span class="score">New</span>'}
        <div class="info"><div class="name">${esc(m.display_name)}, ${esc(m.age)}</div><div class="sub">Matched ${esc(HS.timeAgo(m.matched_at))} ago · ${m.has_messages ? "Open chat" : "Say hello"}</div></div></a>`).join("");
    },
    async saved() {
      const list = await api("GET", "/api/saved");
      const grid = $("#saved-grid");
      grid.innerHTML = list.length ? list.map((p) => HS.cards.mini(p)).join("") : empty("bookmark", "Nothing saved yet", "Tap Save on any profile to shortlist it here.", '<a class="btn primary" href="/discover">Discover</a>');
    },
    async notifications() {
      let before = null;
      const list = $("#notif-list");
      const load = async (reset) => {
        const r = await api("GET", "/api/notifications" + (before ? "?before=" + before : ""));
        const html = r.items.map((n) => `<li><a class="list-item ${n.read_at ? "" : "unread"}" href="${esc(n.link || "#")}" data-notif="${n.id}">
          <span class="avatar sm">${HS.icon({ match: "heart", like: "heart", message: "chat", moderation: "shield", verification: "check", subscription: "star", phone: "phone" }[n.kind] || "bell")}</span>
          <span class="body"><span class="title">${esc(n.title)}</span>${n.body ? `<span class="sub">${esc(n.body)}</span>` : ""}</span>
          <time datetime="${esc(n.created_at)}">${HS.timeAgo(n.created_at)}</time></a></li>`).join("");
        if (reset) list.innerHTML = html || '<li class="empty">You\'re all caught up.</li>';
        else list.insertAdjacentHTML("beforeend", html);
        before = r.next_before;
        $("#notif-more").hidden = !before;
      };
      await load(true);
      $("#notif-more").addEventListener("click", () => load(false).catch(fail));
      $("#mark-all").addEventListener("click", async () => {
        await api("POST", "/api/notifications/read", { all: true });
        $$(".list-item.unread").forEach((el) => el.classList.remove("unread"));
        HS.refreshCounts();
      });
      list.addEventListener("click", (e) => {
        const a = e.target.closest("[data-notif]");
        if (a) api("POST", "/api/notifications/read", { ids: [Number(a.dataset.notif)] }).catch(() => {});
      });
    },
    async billing() {
      const r = await api("GET", "/api/billing");
      const ent = r.entitlements, sub = r.subscription, u = r.usage;
      const feat = (p) => [
        [p.daily_likes == null ? "Unlimited likes" : `${p.daily_likes} likes per day`, true],
        [`${p.daily_superlikes} Super Like${p.daily_superlikes > 1 ? "s" : ""} per day`, true],
        ["See who likes you", p.see_incoming_likes], ["Advanced filters", p.advanced_filters], ["Undo last swipe", p.undo],
        ["Read receipts", p.read_receipts], [p.monthly_boosts ? `${p.monthly_boosts} profile boost${p.monthly_boosts > 1 ? "s" : ""} / month` : "Profile boosts", !!p.monthly_boosts],
        [`${p.saved_presets} saved search preset${p.saved_presets > 1 ? "s" : ""}`, true],
      ];
      const plans = r.plans.map((p) => {
        const current = p.key === ent.plan;
        const btn = current ? '<button class="btn block" disabled>Current plan</button>'
          : p.key === "free" ? "" : `<button class="btn ${p.key === "plus" ? "accent" : "primary"} block" data-plan="${esc(p.key)}" ${r.configured ? "" : "disabled"}>Choose ${esc(p.name)}</button>`;
        return `<div class="card plan ${p.key === "plus" ? "featured" : ""}"><h3 class="mb-0">${esc(p.name)}</h3>
          <div class="price">${p.price_minor ? HS.money(p.price_minor, p.currency) : "Free"}${p.interval ? ` <small>/ ${esc(p.interval)}</small>` : ""}</div>
          <ul>${feat(p).map(([t, on]) => `<li class="${on ? "" : "no"}">${esc(t)}</li>`).join("")}</ul>${btn}</div>`;
      }).join("");
      const status = sub ? `<div class="card mb-15"><div class="row between"><div><h2 class="mb-0">${esc(r.plans.find((p) => p.key === sub.plan).name)}</h2>
          <p class="muted mb-0">${sub.status === "canceled" ? `Cancelled — access until ${new Date(sub.current_period_end).toLocaleDateString()}` : sub.status === "past_due" ? "Payment failed — please update your payment method" : `Renews on ${new Date(sub.current_period_end).toLocaleDateString()}`}</p></div>
          <span class="status ${esc(sub.status)}">${esc(sub.status.replace("_", " "))}</span></div>
          <div class="row mt-1">${sub.status === "canceled" && sub.provider !== "complimentary" ? '<button class="btn sm primary" id="resume-sub">Resume subscription</button>' : sub.provider !== "complimentary" && sub.status !== "canceled" ? '<button class="btn sm danger" id="cancel-sub">Cancel subscription</button>' : ""}</div></div>`
        : "";
      const usage = u ? `<p class="small muted">Today: ${u.likes_used} like${u.likes_used === 1 ? "" : "s"} used${u.likes_limit == null ? "" : ` of ${u.likes_limit}`}, ${u.superlikes_used} of ${u.superlikes_limit} Super Likes.</p>` : "";
      const unconfigured = r.configured ? "" : '<p class="notice warn">Payments are awaiting configuration. Plans can\'t be purchased yet.</p>';
      const hist = r.history.length ? `<h2 class="mt-2">Billing history</h2><div class="table-wrap"><table><thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Status</th><th>Reference</th></tr></thead><tbody>
        ${r.history.map((h) => `<tr><td>${new Date(h.created_at).toLocaleDateString()}</td><td>${esc(h.description || "")}</td><td>${HS.money(h.amount_minor, h.currency)}</td><td><span class="status ${esc(h.status)}">${esc(h.status)}</span></td><td class="mono">${esc(h.reference)}</td></tr>`).join("")}
        </tbody></table></div>` : "";
      $("#billing-root").innerHTML = status + unconfigured + usage + `<div class="plans">${plans}</div>` + hist;
      if (pages._billingBound) return;
      pages._billingBound = true;
      $("#billing-root").addEventListener("click", async (e) => {
        const b = e.target.closest("[data-plan]");
        if (b) {
          HS.busy(b, true);
          try { const c = await api("POST", "/api/billing/checkout", { plan: b.dataset.plan }); location.href = c.url; }
          catch (err) { fail(err); HS.busy(b, false); }
          return;
        }
        if (e.target.closest("#cancel-sub")) {
          if (!(await HS.confirm("Cancel your subscription?", "You'll keep premium features until the end of the current period. You won't be charged again.", "Cancel subscription", true))) return;
          try { await api("POST", "/api/billing/cancel", {}); toast("Subscription cancelled"); pages.billing(); } catch (err) { fail(err); }
        }
        if (e.target.closest("#resume-sub")) {
          try { await api("POST", "/api/billing/resume", {}); toast("Subscription resumed", "success"); pages.billing(); } catch (err) { fail(err); }
        }
      });
    },
    async "mock-checkout"() {
      const cid = pageEl.dataset.checkout;
      $$("[data-mock]").forEach((b) => b.addEventListener("click", async () => {
        HS.busy(b, true);
        try { const r = await api("POST", `/api/billing/mock/${cid}/complete`, { succeed: b.dataset.mock === "1" }); location.href = r.next; }
        catch (err) { fail(err); HS.busy(b, false); }
      }));
    },
    async "settings-account"() {
      const prof = await api("GET", "/api/profile");
      if (prof.phone_masked) $("#phone-masked").textContent = prof.phone_masked + " (verified)";
      const d = $("#deactivate-btn");
      if (d) d.addEventListener("click", async () => {
        if (!(await HS.confirm("Deactivate your account?", "Your profile will be hidden and you'll be signed out. Sign in again any time to come back.", "Deactivate", true))) return;
        try { await api("POST", "/api/account/deactivate", {}); location.href = "/"; } catch (err) { fail(err); }
      });
      $("#delete-form").addEventListener("api:success", (e) => { e.preventDefault(); location.href = "/?deleted=1"; });
    },
    async "settings-privacy"() {
      const prof = await api("GET", "/api/profile");
      const t = (name, label, hint, on) => `<label class="switch"><span><strong>${esc(label)}</strong><br><span class="small muted">${esc(hint)}</span></span><input type="checkbox" role="switch" data-vis="${name}" ${on ? "checked" : ""}></label>`;
      $("#visibility-toggles").innerHTML =
        t("visible", "Show me in Discover", "Turn off to hide your profile from new people. Existing matches can still chat with you.", prof.visibility !== "hidden") +
        t("show_online", "Show when I'm online", "Only your matches can ever see this.", !!prof.show_online_status) +
        t("show_last_seen", "Show recent activity", "e.g. \"Active today\". If off, you also won't appear in Recently active.", !!prof.show_last_seen);
      $("#visibility-toggles").addEventListener("change", async (e) => {
        const name = e.target.dataset.vis;
        try { await api("POST", "/api/profile/visibility", { [name]: e.target.checked }); toast("Saved"); }
        catch (err) { e.target.checked = !e.target.checked; fail(err); }
      });
      const renderBlocked = async () => {
        const bl = await api("GET", "/api/blocks");
        $("#blocked-list").innerHTML = bl.length ? bl.map((b) => `<li class="list-item"><span class="body"><span class="title">${esc(b.display_name)}</span><span class="sub">Blocked ${HS.timeAgo(b.blocked_at)} ago</span></span><button class="btn sm" data-unblock="${esc(b.user_id)}">Unblock</button></li>`).join("") : '<li class="muted small">You haven\'t blocked anyone.</li>';
      };
      await renderBlocked();
      $("#blocked-list").addEventListener("click", async (e) => {
        const b = e.target.closest("[data-unblock]");
        if (!b) return;
        if (!(await HS.confirm("Unblock?", "They may see you in Discover again. Your previous match won't be restored.", "Unblock"))) return;
        try { await api("DELETE", "/api/blocks/" + b.dataset.unblock); renderBlocked(); } catch (err) { fail(err); }
      });
      const reps = await api("GET", "/api/reports/mine");
      $("#reports-list").innerHTML = reps.length ? reps.map((r) => `<li class="list-item"><span class="body"><span class="title">${esc(r.display_name)}</span><span class="sub">${esc(r.category)} · ${HS.timeAgo(r.created_at)} ago</span></span><span class="status ${esc(r.status)}">${esc(r.status)}</span></li>`).join("") : '<li class="muted small">No reports.</li>';
    },
    async "settings-discovery"() {
      const p = await api("GET", "/api/preferences");
      const f = $("#prefs-form");
      f.age_min.value = p.age_min; f.age_max.value = p.age_max;
      f.max_distance_km.value = p.max_distance_km || ""; f.nearby_enabled.checked = !!p.nearby_enabled;
      HS.transforms.prefs = (d) => ({ age_min: d.age_min, age_max: d.age_max, max_distance_km: d.max_distance_km ? Number(d.max_distance_km) : null, nearby_enabled: !!d.nearby_enabled });
    },
    async "settings-notifications"() {
      const p = await api("GET", "/api/notifications/preferences");
      const f = $("#notif-form");
      ["inapp_likes", "inapp_messages", "email_matches", "email_messages", "email_marketing"].forEach((k) => (f[k].checked = !!p[k]));
      f.quiet_start.value = p.quiet_start || ""; f.quiet_end.value = p.quiet_end || "";
    },
    async "settings-security"() {
      const render = async () => {
        const ss = await api("GET", "/api/auth/sessions");
        $("#sessions-list").innerHTML = ss.map((s) => `<li class="list-item"><span class="body"><span class="title">${esc(describeUA(s.user_agent))}${s.current ? ' <span class="tag verified">This device</span>' : ""}</span>
          <span class="sub">Signed in ${HS.timeAgo(s.created_at)} ago · last active ${HS.timeAgo(s.last_seen_at)}</span></span>
          ${s.current ? "" : `<button class="btn sm" data-revoke="${esc(s.id)}">Sign out</button>`}</li>`).join("");
      };
      await render();
      $("#sessions-list").addEventListener("click", async (e) => {
        const b = e.target.closest("[data-revoke]");
        if (b) { try { await api("DELETE", "/api/auth/sessions/" + b.dataset.revoke); render(); } catch (err) { fail(err); } }
      });
      $("#logout-others").addEventListener("click", async () => {
        try { const r = await api("POST", "/api/auth/logout-others", {}); toast(`Signed out ${r.revoked} other session${r.revoked === 1 ? "" : "s"}`); render(); } catch (err) { fail(err); }
      });
    },
  };

  function describeUA(ua) {
    ua = ua || "";
    const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
    const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Windows/.test(ua) ? "Windows" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "Unknown device";
    return `${browser} on ${os}`;
  }

  if (page && pages[page]) pages[page]().catch((err) => fail(err));
})();
