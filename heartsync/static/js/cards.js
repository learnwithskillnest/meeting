/* Shared profile rendering helpers. */
(function () {
  "use strict";
  const { esc } = HS;
  HS.cards = {};

  HS.cards.photo = (p, i) => {
    const ph = (p.photos || [])[i || 0];
    return ph ? `<img src="${esc(ph.url)}" alt="Photo of ${esc(p.display_name)}" loading="lazy" draggable="false">`
      : `<div class="no-photo" aria-hidden="true">${HS.initials(p.display_name)}</div>`;
  };

  HS.cards.verified = (p) => (p.verification && p.verification.photo_verified)
    ? `<span class="badge-verified" title="Photo verified" aria-label="Photo verified">${HS.icon("check")}</span>` : "";

  HS.cards.mini = (p, opts) => {
    opts = opts || {};
    const score = p.compatibility ? `<span class="score" title="Compatibility based on what you both shared">${p.compatibility.score}%</span>` : "";
    const sub = [p.distance, p.last_seen].filter(Boolean).join(" · ");
    return `<a class="mini-card ${opts.blurred ? "blurred" : ""}" href="${opts.blurred ? "/billing" : "/u/" + esc(p.id)}">
      ${HS.cards.photo(p)}${opts.blurred ? "" : score}
      <div class="info"><div class="name">${opts.blurred ? "Someone" : esc(p.display_name) + ", " + esc(p.age)}</div>
      <div class="sub">${opts.blurred ? "Upgrade to see who" : esc(sub || (p.headline || ""))}</div></div></a>`;
  };

  HS.cards.big = (p) => {
    const n = (p.photos || []).length;
    const dots = n > 1 ? `<div class="dots">${Array.from({ length: n }, (_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("")}</div>` : "";
    const reasons = ((p.compatibility && p.compatibility.reasons) || []).slice(0, 3).map((r) => `<span>${esc(r)}</span>`).join("");
    const meta = [p.profession, p.distance, p.last_seen].filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("");
    return `<article class="profile-card" data-id="${esc(p.id)}" data-photo="0" aria-label="${esc(p.display_name)}, ${esc(p.age)}">
      <div class="photo">${HS.cards.photo(p, 0)}</div>
      ${dots}
      ${n > 1 ? '<button class="tap prev" data-photo-nav="-1" aria-label="Previous photo"></button><button class="tap next" data-photo-nav="1" aria-label="Next photo"></button>' : ""}
      <div class="shade"></div>
      <span class="stamp like" aria-hidden="true">LIKE</span><span class="stamp nope" aria-hidden="true">NOPE</span>
      <div class="info">
        <div class="name">${esc(p.display_name)} <span class="age">${esc(p.age)}</span> ${HS.cards.verified(p)}</div>
        ${p.headline ? `<div class="soft">${esc(p.headline)}</div>` : ""}
        <div class="meta mt-05">${meta}</div>
        ${reasons ? `<div class="reasons" aria-label="Why you might click">${reasons}</div>` : ""}
        <a class="btn sm mt-05" href="/u/${esc(p.id)}">View profile</a>
      </div>
    </article>`;
  };

  HS.cards.cyclePhoto = (card, profile, dir) => {
    const n = (profile.photos || []).length;
    if (n < 2) return;
    let i = (Number(card.dataset.photo) + dir + n) % n;
    card.dataset.photo = i;
    card.querySelector(".photo").innerHTML = HS.cards.photo(profile, i);
    card.querySelectorAll(".dots i").forEach((d, j) => d.classList.toggle("on", j === i));
  };

  HS.showMatch = (data, other) => {
    const dlg = document.getElementById("match-dialog");
    if (!dlg) { HS.toast("It's a match! Head to Chats to say hello.", "success"); return; }
    document.getElementById("match-pair").innerHTML =
      `<span class="avatar">${HS.initials(document.body.dataset.name || "You")}</span>` +
      (other && other.photos && other.photos[0] ? `<img class="avatar" src="${esc(other.photos[0].url)}" alt="">` : `<span class="avatar">${HS.initials(other && other.display_name)}</span>`);
    document.getElementById("match-text").textContent = `You and ${(other && other.display_name) || "your match"} liked each other.`;
    HS.api("GET", "/api/matches").then((ms) => {
      const m = ms.find((x) => x.match_id === data.match_id);
      if (m) document.getElementById("match-chat").href = "/chat/" + m.conversation_id;
    }).catch(() => {});
    dlg.showModal();
    document.getElementById("match-keep").onclick = () => dlg.close();
  };
})();
