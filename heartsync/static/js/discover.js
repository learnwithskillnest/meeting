/* Swipe deck with gestures, keyboard support and discovery modes. */
(function () {
  "use strict";
  const { $, $$, esc, api, toast, fail } = HS;
  const deck = $("#deck");
  let mode = "all", queue = [], cursor = null, loading = false, exhausted = false, busy = false, usage = null;

  if (new URLSearchParams(location.search).get("welcome")) toast("Welcome! Your profile is live. 🎉", "success");

  async function loadUsage() {
    try {
      usage = await api("GET", "/api/usage");
      const likes = usage.likes_limit == null ? "Unlimited likes" : `${Math.max(0, usage.likes_limit - usage.likes_used)} likes left today`;
      const supers = `${Math.max(0, usage.superlikes_limit - usage.superlikes_used)} Super Like${usage.superlikes_limit - usage.superlikes_used === 1 ? "" : "s"}`;
      $("#usage-line").textContent = `${likes} · ${supers}`;
    } catch (e) { /* non-critical */ }
  }

  async function load(reset) {
    if (loading || (exhausted && !reset)) return;
    loading = true;
    if (reset) { queue = []; cursor = null; exhausted = false; deck.innerHTML = '<div class="profile-card skeleton" aria-hidden="true"></div>'; }
    try {
      const res = await api("POST", "/api/discover", { mode, cursor, limit: 10 });
      queue = queue.concat(res.results);
      cursor = res.next_cursor;
      exhausted = !cursor;
      render();
    } catch (err) {
      if (err.code === "nearby_disabled") {
        deck.innerHTML = `<div class="card empty"><div class="art">${HS.icon("pin")}</div><h3>Nearby is off</h3><p>Turn on Nearby discovery to see people close to you. We only use your chosen city — never GPS.</p><a class="btn primary" href="/settings/discovery">Discovery settings</a></div>`;
      } else if (err.code === "location_unavailable") {
        deck.innerHTML = `<div class="card empty"><h3>Add your city</h3><p>${esc(err.message)}</p><a class="btn primary" href="/profile">Edit profile</a></div>`;
      } else {
        deck.innerHTML = `<div class="card empty"><h3>Couldn't load profiles</h3><p>${esc(err.message)}</p><button class="btn" data-reload>Try again</button></div>`;
      }
    } finally {
      loading = false;
    }
  }

  function render() {
    if (!queue.length) {
      deck.innerHTML = `<div class="card empty"><div class="art">${HS.icon("compass")}</div>
        <h3>You're all caught up</h3><p>No more profiles match right now. Try another mode, widen your age range or distance, or check back later — new people join every day.</p>
        <div class="row centered"><a class="btn primary" href="/search">Adjust filters</a><a class="btn" href="/settings/discovery">Preferences</a></div></div>`;
      $("#profile-peek").hidden = true;
      return;
    }
    deck.innerHTML = queue.slice(0, 2).reverse().map((p, i, arr) => {
      const html = HS.cards.big(p);
      return i < arr.length - 1 ? html.replace('class="profile-card"', 'class="profile-card behind" aria-hidden="true" inert') : html;
    }).join("");
    bindDrag(deck.querySelector(".profile-card:not(.behind)"));
    renderPeek(queue[0]);
    if (queue.length < 4 && !exhausted) load();
  }

  function renderPeek(p) {
    const peek = $("#profile-peek");
    if (!p) { peek.hidden = true; return; }
    const tags = (p.interests || []).slice(0, 8).map((i) => `<span class="tag">${esc(i.replace(/-/g, " "))}</span>`).join("");
    peek.innerHTML = `<h3>About ${esc(p.display_name)}</h3>
      ${p.about ? `<p class="small">${esc(p.about.slice(0, 240))}${p.about.length > 240 ? "…" : ""}</p>` : '<p class="small muted">No bio yet.</p>'}
      ${tags ? `<div class="tags">${tags}</div>` : ""}
      <p class="small muted mt-1">${p.compatibility ? `Compatibility ${p.compatibility.score}% — based only on what you both shared.` : ""}</p>`;
    peek.hidden = false;
  }

  async function act(kind) {
    if (busy) return;
    if (kind === "undo") return undo();
    const p = queue[0];
    if (!p) return;
    busy = true;
    const card = deck.querySelector(".profile-card:not(.behind)");
    try {
      const res = await api("POST", "/api/swipes", { target_id: p.id, kind });
      animateOut(card, kind);
      queue.shift();
      setTimeout(render, 220);
      if (res.matched) HS.showMatch(res, p);
      else if (kind === "superlike") toast(`Super Like sent to ${p.display_name} ⭐`);
      loadUsage();
    } catch (err) {
      resetCard(card);
      if (err.status === 404) { queue.shift(); render(); }
      else fail(err);
    } finally {
      busy = false;
    }
  }

  async function undo() {
    try {
      await api("POST", "/api/swipes/undo", {});
      toast("Last swipe undone");
      load(true);
      loadUsage();
    } catch (err) { fail(err); }
  }

  function animateOut(card, kind) {
    if (!card) return;
    const x = kind === "pass" ? -1 : 1;
    card.style.transform = kind === "superlike" ? "translateY(-120%) rotate(0)" : `translateX(${x * 130}%) rotate(${x * 18}deg)`;
    card.style.opacity = "0";
  }
  function resetCard(card) {
    if (!card) return;
    card.style.transform = "";
    card.style.opacity = "";
    card.querySelectorAll(".stamp").forEach((s) => (s.style.opacity = 0));
  }

  // ---------------------------------------------------------- gestures
  function bindDrag(card) {
    if (!card) return;
    let startX = 0, startY = 0, dx = 0, dy = 0, dragging = false;
    card.addEventListener("pointerdown", (e) => {
      if (e.target.closest("a, button")) return;
      dragging = true; startX = e.clientX; startY = e.clientY; dx = dy = 0;
      card.setPointerCapture(e.pointerId);
      card.style.transition = "none";
    });
    card.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      dx = e.clientX - startX; dy = e.clientY - startY;
      card.style.transform = `translate(${dx}px, ${dy * 0.3}px) rotate(${dx / 18}deg)`;
      card.querySelector(".stamp.like").style.opacity = Math.max(0, Math.min(1, dx / 90));
      card.querySelector(".stamp.nope").style.opacity = Math.max(0, Math.min(1, -dx / 90));
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      card.style.transition = "";
      if (dx > 110) act("like");
      else if (dx < -110) act("pass");
      else if (dy < -140) act("superlike");
      else resetCard(card);
    };
    card.addEventListener("pointerup", end);
    card.addEventListener("pointercancel", end);
  }

  deck.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-photo-nav]");
    if (nav) {
      const card = nav.closest(".profile-card");
      HS.cards.cyclePhoto(card, queue.find((p) => p.id === card.dataset.id), Number(nav.dataset.photoNav));
    }
  });
  $("#actions").addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (b) act(b.dataset.act);
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select, dialog")) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); act("pass"); }
    else if (e.key === "ArrowRight") { e.preventDefault(); act("like"); }
    else if (e.key === "ArrowUp") { e.preventDefault(); act("superlike"); }
    else if (e.key === "Enter" && queue[0] && e.target === document.body) location.href = "/u/" + queue[0].id;
  });

  $$("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.mode;
    $$("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    load(true);
  }));

  $("#boost-btn").addEventListener("click", async () => {
    try {
      const r = await api("POST", "/api/boost", {});
      toast(`Boosted until ${HS.clock(r.boost_until)} ⚡`, "success");
    } catch (err) { fail(err); }
  });

  load(true);
  loadUsage();
})();
