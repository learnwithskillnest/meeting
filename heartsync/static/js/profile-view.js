/* Full profile view with gallery, details and actions. */
(function () {
  "use strict";
  const { $, $$, esc, api, toast, fail } = HS;
  const root = $("#profile-view");
  const id = root.dataset.id;
  const self = !!root.dataset.self;
  let p, cat;

  const DETAIL_FIELDS = [
    ["height_cm", "Height", (v) => v + " cm"], ["education", "Education", "education"], ["profession", "Work", null],
    ["industry", "Industry", "industry"], ["work_status", "Work status", "work_status"], ["work_schedule", "Schedule", "work_schedule"],
    ["relationship_status", "Status", "relationship_status"], ["smoking", "Smoking", "smoking"], ["drinking", "Drinking", "drinking"],
    ["diet", "Diet", "diet"], ["exercise", "Exercise", "exercise"], ["pets", "Pets", "pets"], ["sleep_habits", "Sleep", "sleep_habits"],
    ["religion", "Religion", "religion"], ["family_plans", "Family plans", "family_plans"], ["communication_style", "Communication", "communication_style"],
  ];

  function render() {
    const sf = cat.select_fields;
    const photos = p.photos || [];
    const facts = DETAIL_FIELDS.filter(([k]) => p[k]).map(([k, label, fmt]) => {
      const v = typeof fmt === "function" ? fmt(p[k]) : fmt ? HS.label(sf[fmt], p[k]) : p[k];
      return `<div class="fact"><dt>${esc(label)}</dt><dd>${esc(v)}</dd></div>`;
    }).join("");
    const interestLabels = {};
    cat.interests.forEach((i) => (interestLabels[i.slug] = i.label));
    const goals = (p.relationship_goals || []).map((g) => `<span class="tag shared">${esc(HS.label(cat.relationship_goals, g))}</span>`).join("");
    const langs = (p.languages || []).map((l) => `<span class="tag">${esc(HS.label(cat.languages, l))}</span>`).join("");
    const interests = (p.interests || []).map((i) => `<span class="tag">${esc(interestLabels[i] || i)}</span>`).join("");
    const qualities = (p.partner_qualities || []).map((q) => `<span class="tag">${esc(HS.label(cat.multi_fields.partner_qualities, q))}</span>`).join("");
    const travel = (p.travel_interests || []).map((q) => `<span class="tag">${esc(HS.label(cat.multi_fields.travel_interests, q))}</span>`).join("");
    const badges = [p.verification.photo_verified ? `<span class="tag verified">${HS.icon("check")} Photo verified</span>` : "",
      p.verification.phone_verified ?`<span class="tag verified">${HS.icon("phone")} Phone verified</span>` : ""].join("");

    let actions = "";
    if (self) {
      actions = `<p class="notice">This is how other members see your profile. Fields you've limited to matches are shown here because it's you.</p><a class="btn primary" href="/profile">Edit profile</a>`;
    } else if (p.matched) {
      actions = `<a class="btn accent" href="/chat" id="open-chat">${HS.icon("chat")} Message</a>`;
    } else if (!p.my_swipe || p.my_swipe === "pass") {
      actions = `<button class="btn" data-act="pass">${HS.icon("x")} Pass</button>
        <button class="btn accent" data-act="like">${HS.icon("heart")} Like</button>
        <button class="btn" data-act="superlike">${HS.icon("star")} Super Like</button>`;
    } else {
      actions = `<p class="small muted">You liked ${esc(p.display_name)}. If they like you back, you'll match.</p>`;
    }

    root.innerHTML = `<div class="profile-layout">
      <div class="gallery">
        <div class="main" id="gallery-main">${HS.cards.photo(p, 0)}</div>
        ${photos.length > 1 ? `<div class="thumbs">${photos.map((ph, i) => `<button type="button" data-thumb="${i}" aria-label="Show photo ${i + 1}" aria-current="${i === 0}"><img src="${esc(ph.url)}" alt="" loading="lazy"></button>`).join("")}</div>` : ""}
      </div>
      <div class="stack">
        <div>
          <h1 class="mb-0">${esc(p.display_name)}, ${esc(p.age)} ${HS.cards.verified(p)}</h1>
          <p class="muted">${[p.pronouns, p.city, p.distance, p.online ? "Online now" : p.last_seen].filter(Boolean).map(esc).join(" · ")}</p>
          ${p.headline ? `<p class="lede"><em>${esc(p.headline)}</em></p>` : ""}
          <div class="tags">${badges}</div>
        </div>
        ${p.compatibility ? "" : ""}
        <div class="row">${actions}</div>
        ${p.about ? `<section><h2>About</h2><p class="pre">${esc(p.about)}</p></section>` : ""}
        ${goals ? `<section><h3>Looking for</h3><div class="tags">${goals}</div></section>` : ""}
        ${facts ? `<section><h3>Details</h3><dl class="facts">${facts}</dl></section>` : ""}
        ${interests ? `<section><h3>Interests</h3><div class="tags">${interests}</div></section>` : ""}
        ${qualities ? `<section><h3>Values in a partner</h3><div class="tags">${qualities}</div></section>` : ""}
        ${langs ? `<section><h3>Languages</h3><div class="tags">${langs}</div></section>` : ""}
        ${travel ? `<section><h3>Travel style</h3><div class="tags">${travel}</div></section>` : ""}
        ${p.future_goals ? `<section><h3>Future goals</h3><p>${esc(p.future_goals)}</p></section>` : ""}
        ${self ? "" : `<hr class="divider"><div class="row">
          <button class="btn sm" id="save-btn">${HS.icon("bookmark")} ${p.saved ? "Saved" : "Save"}</button>
          <span class="spacer"></span>
          <button class="btn sm ghost" id="report-btn">${HS.icon("flag")} Report</button>
          <button class="btn sm danger" id="block-btn">Block</button></div>`}
      </div></div>`;
  }

  root.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-thumb]");
    if (t) {
      $("#gallery-main").innerHTML = HS.cards.photo(p, Number(t.dataset.thumb));
      $$("[data-thumb]").forEach((b) => b.setAttribute("aria-current", String(b === t)));
      return;
    }
    const a = e.target.closest("[data-act]");
    if (a) {
      try {
        const res = await api("POST", "/api/swipes", { target_id: id, kind: a.dataset.act });
        if (res.matched) { HS.showMatch(res, p); p.matched = true; }
        else if (a.dataset.act === "pass") { toast("Passed"); setTimeout(() => history.back(), 400); return; }
        else toast(a.dataset.act === "superlike" ? "Super Like sent ⭐" : "Liked ❤️", "success");
        p.my_swipe = a.dataset.act; render();
      } catch (err) { fail(err); }
      return;
    }
    if (e.target.closest("#save-btn")) {
      try { await api(p.saved ? "DELETE" : "POST", "/api/saved/" + id); p.saved = !p.saved; render(); toast(p.saved ? "Saved to your shortlist" : "Removed from shortlist"); }
      catch (err) { fail(err); }
      return;
    }
    if (e.target.closest("#report-btn")) {
      const r = await HS.report(id, p.display_name);
      if (r.blocked) location.href = "/discover";
      return;
    }
    if (e.target.closest("#block-btn")) {
      if (await HS.block(id, p.display_name)) location.href = "/discover";
      return;
    }
    if (e.target.closest("#open-chat")) {
      e.preventDefault();
      try {
        const ms = await api("GET", "/api/matches");
        const m = ms.find((x) => x.user_id === id);
        location.href = m ? "/chat/" + m.conversation_id : "/chat";
      } catch (err) { fail(err); }
    }
  });

  (async function init() {
    try {
      [cat, p] = await Promise.all([api("GET", "/api/catalog"), api("GET", "/api/profiles/" + id)]);
      document.title = `${p.display_name} · ${document.title.split("·").pop().trim()}`;
      render();
    } catch (err) {
      root.innerHTML = `<div class="empty"><div class="art">${HS.icon("user")}</div><h2>Profile unavailable</h2><p>${esc(err.message)}</p><a class="btn primary" href="/discover">Back to Discover</a></div>`;
    }
  })();
})();
