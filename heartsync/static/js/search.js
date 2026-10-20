/* Advanced search: filter panel, active-filter chips, presets, pagination. */
(function () {
  "use strict";
  const { $, $$, esc, api, toast, fail } = HS;
  const STORE = "hs-search-filters";
  let cat, presets = [], advancedAllowed = false, filters = loadState(), cursor = null, loading = false;

  const LABELS = {
    age_min: "Min age", age_max: "Max age", genders: "Gender", goals: "Looking for", languages: "Languages", region: "State",
    city: "City", max_distance_km: "Within", relationship_status: "Status", education: "Education", profession: "Job",
    industry: "Industry", work_status: "Work", height_min: "Min height", height_max: "Max height", smoking: "Smoking",
    drinking: "Drinking", diet: "Diet", exercise: "Exercise", pets: "Pets", interests: "Interests", religion: "Religion",
    family_plans: "Family plans", communication: "Communication", partner_qualities: "Values", photo_verified: "Photo verified",
    phone_verified: "Phone verified", active_within_days: "Active within", joined_within_days: "Joined within",
    min_completeness: "Profile completeness", common_interests: "Shared interests", open_to_relocate: "Open to relocate",
    exclude: "Excluding",
  };
  const ADVANCED = ["relationship_status", "education", "profession", "industry", "work_status", "height_min", "height_max",
    "smoking", "drinking", "diet", "exercise", "pets", "interests", "religion", "family_plans", "communication",
    "partner_qualities", "photo_verified", "phone_verified", "active_within_days", "joined_within_days", "min_completeness",
    "common_interests", "open_to_relocate", "exclude"];

  function loadState() {
    try { return JSON.parse(sessionStorage.getItem(STORE)) || {}; } catch (e) { return {}; }
  }
  function saveState() { try { sessionStorage.setItem(STORE, JSON.stringify(filters)); } catch (e) { /* ignore */ } }

  function chips(name, options, values, disabled) {
    values = values || [];
    return `<div class="chips">${Object.entries(options).map(([k, v]) => `<label class="chip"><input type="checkbox" name="${name}" value="${esc(k)}" data-multi ${values.includes(k) ? "checked" : ""} ${disabled ? "disabled" : ""}><span>${esc(v)}</span></label>`).join("")}</div>`;
  }
  function sel(name, options, value, placeholder, disabled) {
    return `<select name="${name}" id="flt-${name}" ${disabled ? "disabled" : ""}><option value="">${esc(placeholder || "Any")}</option>${Object.entries(options).map(([k, v]) => `<option value="${esc(k)}" ${String(value) === String(k) ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  }
  function group(title, inner, open, locked) {
    return `<details ${open ? "open" : ""}><summary>${esc(title)} ${locked ? '<span class="lock">Plus</span>' : ""}</summary><div class="inner">${inner}</div></details>`;
  }
  function lbl(text, html, id) { return `<div class="field"><label ${id ? `for="${id}"` : ""}>${esc(text)}</label>${html}</div>`; }

  function renderPanel() {
    const f = filters, sf = cat.select_fields, lock = !advancedAllowed;
    const interests = {};
    cat.interests.forEach((i) => (interests[i.slug] = i.label));
    const ex = f.exclude || {};
    const upsell = lock ? `<p class="notice small">Advanced filters are part of <a href="/billing">HeartSync Plus</a>. Basic filters are always free.</p>` : "";
    $("#filter-fields").innerHTML = `
      ${group("Age & identity", `
        <div class="range-pair"><input type="number" name="age_min" min="18" max="100" value="${f.age_min || ""}" placeholder="18" aria-label="Minimum age"><span>to</span><input type="number" name="age_max" min="18" max="100" value="${f.age_max || ""}" placeholder="60" aria-label="Maximum age"></div>
        ${lbl("Gender", chips("genders", cat.genders, f.genders))}
        ${lbl("Looking for", chips("goals", cat.relationship_goals, f.goals))}
        ${lbl("Speaks", chips("languages", cat.languages, f.languages))}`, true)}
      ${group("Location", `
        ${lbl("State", sel("region", Object.fromEntries(cat.regions.map((r) => [r, r])), f.region), "flt-region")}
        ${lbl("City", sel("city", Object.fromEntries(cat.cities.filter((c) => !f.region || c.region === f.region).map((c) => [c.city, c.city])), f.city), "flt-city")}
        ${lbl("Maximum distance", sel("max_distance_km", { 10: "10 km", 25: "25 km", 50: "50 km", 100: "100 km", 250: "250 km", 500: "500 km", 1000: "1000 km" }, f.max_distance_km, "Any distance"), "flt-max_distance_km")}
        <p class="hint">Distance is approximate, based on each person's chosen city.</p>`, true)}
      ${upsell}
      ${group("Education & career", `
        ${lbl("Education", chips("education", sf.education, f.education, lock))}
        ${lbl("Industry", chips("industry", sf.industry, f.industry, lock))}
        ${lbl("Work status", chips("work_status", sf.work_status, f.work_status, lock))}
        ${lbl("Job title contains", `<input name="profession" id="flt-profession" maxlength="60" value="${esc(f.profession || "")}" ${lock ? "disabled" : ""}>`, "flt-profession")}`, false, lock)}
      ${group("Lifestyle", `
        <div class="field"><span class="label">Height (cm)</span><div class="range-pair"><input type="number" name="height_min" min="120" max="230" value="${f.height_min || ""}" ${lock ? "disabled" : ""} aria-label="Minimum height"><span>to</span><input type="number" name="height_max" min="120" max="230" value="${f.height_max || ""}" ${lock ? "disabled" : ""} aria-label="Maximum height"></div></div>
        ${lbl("Smoking", chips("smoking", sf.smoking, f.smoking, lock))}
        ${lbl("Drinking", chips("drinking", sf.drinking, f.drinking, lock))}
        ${lbl("Diet", chips("diet", sf.diet, f.diet, lock))}
        ${lbl("Exercise", chips("exercise", sf.exercise, f.exercise, lock))}
        ${lbl("Pets", chips("pets", sf.pets, f.pets, lock))}
        ${lbl("Exclude people who smoke…", chips("exclude_smoking", sf.smoking, ex.smoking, lock))}
        ${lbl("Exclude people who drink…", chips("exclude_drinking", sf.drinking, ex.drinking, lock))}`, false, lock)}
      ${group("Interests & values", `
        ${lbl("Interests", chips("interests", interests, f.interests, lock))}
        ${lbl("Match", sel("interests_mode", { any: "Any of these", all: "All of these" }, f.interests_mode || "any", null, lock).replace('<option value="">Any</option>', ""), "flt-interests_mode")}
        ${lbl("Values they look for", chips("partner_qualities", cat.multi_fields.partner_qualities, f.partner_qualities, lock))}
        ${lbl("Communication style", chips("communication", sf.communication_style, f.communication, lock))}
        ${lbl("Family plans", chips("family_plans", sf.family_plans, f.family_plans, lock))}
        ${lbl("Religion or spirituality", chips("religion", sf.religion, f.religion, lock))}
        <p class="hint">Religion and family-plan filters only include people who chose to show these publicly. We never guess.</p>
        <label class="check"><input type="checkbox" name="common_interests" ${f.common_interests ? "checked" : ""} ${lock ? "disabled" : ""}> Only people with interests in common with me</label>
        ${lbl("Open to relocating", sel("open_to_relocate", { true: "Yes" }, f.open_to_relocate === true ? "true" : "", "Doesn't matter", lock), "flt-open_to_relocate")}`, false, lock)}
      ${group("Profile & activity", `
        <label class="check"><input type="checkbox" name="photo_verified" ${f.photo_verified ? "checked" : ""} ${lock ? "disabled" : ""}> Photo-verified only</label>
        <label class="check"><input type="checkbox" name="phone_verified" ${f.phone_verified ? "checked" : ""} ${lock ? "disabled" : ""}> Verified phone only</label>
        ${lbl("Active within", sel("active_within_days", { 1: "24 hours", 3: "3 days", 7: "A week", 30: "A month" }, f.active_within_days, "Any time", lock), "flt-active_within_days")}
        ${lbl("Joined within", sel("joined_within_days", { 7: "A week", 30: "A month", 90: "3 months" }, f.joined_within_days, "Any time", lock), "flt-joined_within_days")}
        ${lbl("Profile completeness", sel("min_completeness", { 50: "50%+", 70: "70%+", 90: "90%+" }, f.min_completeness, "Any", lock), "flt-min_completeness")}`, false, lock)}
      ${group("Matching style", `
        <label class="check"><input type="checkbox" name="strict" ${f.strict === false ? "" : "checked"}> Strict: hide people who haven't filled in a filtered detail</label>
        <p class="hint">Turn off to also include profiles that left a filtered field blank.</p>`, false)}`;
  }

  function readPanel() {
    const form = $("#filters-form");
    const d = HS.formData(form);
    const out = {};
    const nums = ["age_min", "age_max", "max_distance_km", "height_min", "height_max", "active_within_days", "joined_within_days", "min_completeness"];
    Object.entries(d).forEach(([k, v]) => {
      if (v === null || v === "" || (Array.isArray(v) && !v.length)) return;
      if (k.startsWith("exclude_")) { out.exclude = out.exclude || {}; out.exclude[k.slice(8)] = v; return; }
      if (nums.includes(k)) out[k] = Number(v);
      else if (k === "open_to_relocate") out[k] = v === "true";
      else if (k === "strict") out.strict = !!v;
      else if (typeof v === "boolean") { if (v) out[k] = v; }
      else out[k] = v;
    });
    if (out.strict === undefined) out.strict = false;
    if (out.strict === true) delete out.strict;
    if (out.interests_mode === "any") delete out.interests_mode;
    return out;
  }

  function renderChips() {
    const box = $("#active-chips");
    const entries = Object.entries(filters).filter(([k]) => LABELS[k]);
    $("#filter-count").textContent = entries.length;
    $("#filter-count").classList.toggle("hidden", !entries.length);
    if (!entries.length) { box.innerHTML = '<span class="small muted">No filters — showing everyone who fits your preferences.</span>'; return; }
    box.innerHTML = entries.map(([k, v]) => {
      let text = Array.isArray(v) ? v.slice(0, 2).map((x) => x.replace(/_|-/g, " ")).join(", ") + (v.length > 2 ? ` +${v.length - 2}` : "")
        : typeof v === "object" ? Object.values(v).flat().join(", ") : v === true ? "" : String(v);
      if (k === "max_distance_km") text += " km";
      if (k.endsWith("_days")) text += " days";
      return `<button class="filter-chip" data-remove="${esc(k)}" aria-label="Remove filter ${esc(LABELS[k])}">${esc(LABELS[k])}${text ? ": " + esc(text) : ""}</button>`;
    }).join("") + '<button class="link-btn small" data-clear>Clear all</button>';
  }

  async function search(reset) {
    if (loading) return;
    loading = true;
    const results = $("#results");
    if (reset) {
      cursor = null;
      results.innerHTML = Array.from({ length: 6 }, () => '<div class="mini-card skeleton"></div>').join("");
    }
    $("#load-more").hidden = true;
    try {
      const body = Object.assign({}, filters, { sort: $("#sort").value, cursor, limit: 24 });
      const res = await api("POST", "/api/discover", body);
      advancedAllowed = res.advanced_allowed;
      if (reset) results.innerHTML = "";
      results.insertAdjacentHTML("beforeend", res.results.map((p) => HS.cards.mini(p)).join(""));
      cursor = res.next_cursor;
      $("#load-more").hidden = !cursor;
      const count = res.total_capped ? `${res.total}+` : res.total;
      $("#result-count").textContent = res.total ? `${count} ${res.total === 1 ? "person matches" : "people match"} your filters` : "";
      if (!res.total) {
        results.innerHTML = `<div class="card empty span-all"><div class="art">${HS.icon("search")}</div><h3>No one matches these filters yet</h3>
          <p>Try removing a filter or two, widening the distance, or turning off strict matching.</p><button class="btn primary" data-clear>Clear filters</button></div>`;
      }
    } catch (err) {
      if (err.status === 402) {
        results.innerHTML = `<div class="card empty"><h3>That's a Plus filter</h3><p>${esc(err.message)}</p><a class="btn accent" href="/billing">See plans</a> <button class="btn" data-clear>Clear filters</button></div>`;
      } else if (err.code === "bad_cursor") { search(true); }
      else { results.innerHTML = `<div class="card empty"><h3>Search failed</h3><p>${esc(err.message)}</p></div>`; }
    } finally {
      loading = false;
    }
  }

  function renderPresets() {
    const box = $("#presets");
    box.innerHTML = presets.length ? `<p class="label mb-05">Saved presets</p><div class="row gap-sm">${presets.map((p) =>
      `<span class="tag"><button type="button" class="link-btn" data-preset="${esc(p.id)}">${esc(p.name)}</button>
       <button type="button" class="link-btn" data-delete-preset="${esc(p.id)}" aria-label="Delete preset ${esc(p.name)}">×</button></span>`).join("")}</div>` : "";
  }

  // ------------------------------------------------------------- events
  const dlg = $("#filters-dialog");
  $("#open-filters").addEventListener("click", () => { renderPanel(); dlg.showModal(); });
  dlg.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) dlg.close(); });
  dlg.addEventListener("change", (e) => {
    if (e.target.name === "region") { filters = Object.assign(readPanel(), { city: undefined }); renderPanel(); }
  });
  $("#filters-form").addEventListener("submit", (e) => {
    e.preventDefault();
    filters = readPanel();
    saveState(); renderChips(); dlg.close(); search(true);
  });
  $("#clear-filters").addEventListener("click", () => { filters = {}; renderPanel(); });
  $("#save-preset").addEventListener("click", async () => {
    const name = prompt("Name this preset (e.g. 'Weekend in Lucknow')");
    if (!name) return;
    try {
      const f = readPanel();
      const p = await api("POST", "/api/presets", { name, filters: f });
      presets = presets.filter((x) => x.id !== p.id).concat([p]);
      renderPresets();
      toast("Preset saved", "success");
    } catch (err) { fail(err); }
  });
  document.addEventListener("click", async (e) => {
    const rm = e.target.closest("[data-remove]");
    if (rm) { delete filters[rm.dataset.remove]; saveState(); renderChips(); search(true); return; }
    if (e.target.closest("[data-clear]")) { filters = {}; saveState(); renderChips(); search(true); return; }
    const pr = e.target.closest("[data-preset]");
    if (pr) {
      const p = presets.find((x) => x.id === pr.dataset.preset);
      if (p) {
        filters = {};
        Object.entries(p.filters).forEach(([k, v]) => {
          if (["sort", "mode", "limit", "cursor"].includes(k)) return;
          if (v === null || (Array.isArray(v) && !v.length) || v === false || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length)) return;
          if (k === "strict" && v === true) return;
          if (k === "interests_mode" && v === "any") return;
          filters[k] = v;
        });
        if (p.filters.strict === false) filters.strict = false;
        saveState(); renderPanel(); renderChips(); dlg.close(); search(true);
      }
      return;
    }
    const dp = e.target.closest("[data-delete-preset]");
    if (dp) {
      try { await api("DELETE", "/api/presets/" + dp.dataset.deletePreset); presets = presets.filter((x) => x.id !== dp.dataset.deletePreset); renderPresets(); }
      catch (err) { fail(err); }
    }
  });
  $("#sort").addEventListener("change", () => search(true));
  $("#load-more").addEventListener("click", () => search(false));

  (async function init() {
    try {
      const [c, ps, me] = await Promise.all([api("GET", "/api/catalog"), api("GET", "/api/presets"), api("GET", "/api/me")]);
      cat = c; presets = ps; advancedAllowed = !!me.entitlements.advanced_filters;
      if (!advancedAllowed) ADVANCED.forEach((k) => delete filters[k]);
      renderPresets(); renderChips(); search(true);
    } catch (err) { fail(err); }
  })();
})();
