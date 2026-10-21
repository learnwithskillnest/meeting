/* Profile builder: onboarding wizard + edit page. */
(function () {
  "use strict";
  const { $, $$, esc, api, toast, fail } = HS;
  const root = document.getElementById("profile-root");
  const onboarding = !!root.dataset.onboarding;
  let cat, prof;

  const FIELD_LABELS = {
    height_cm: "Height", education: "Education", profession: "Job title", industry: "Industry", work_status: "Work status",
    work_schedule: "Work schedule", relationship_status: "Relationship status", smoking: "Smoking", drinking: "Drinking",
    diet: "Diet", religion: "Religion / spirituality", family_plans: "Family plans", sleep_habits: "Sleep", city: "City",
    exercise: "Exercise", pets: "Pets", communication_style: "Communication style",
  };

  function select(name, options, value, placeholder) {
    const opts = Object.entries(options).map(([k, v]) => `<option value="${esc(k)}" ${k === value ? "selected" : ""}>${esc(v)}</option>`).join("");
    return `<select id="f-${name}" name="${name}"><option value="">${esc(placeholder || "Prefer not to say")}</option>${opts}</select>`;
  }
  function chips(name, options, values, max) {
    values = values || [];
    return `<div class="chips" role="group" data-max="${max || 0}">` + Object.entries(options).map(([k, v]) =>
      `<label class="chip"><input type="checkbox" name="${name}" value="${esc(k)}" data-multi ${values.includes(k) ? "checked" : ""}><span>${esc(v)}</span></label>`).join("") + "</div>";
  }
  function field(label, html, hint, name) {
    return `<div class="field"><label ${name ? `for="f-${name}"` : ""}>${esc(label)}</label>${html}${hint ? `<p class="hint">${esc(hint)}</p>` : ""}${name ? `<p class="error-text" data-error-for="${name}" hidden></p>` : ""}</div>`;
  }
  function fieldset(legend, html, hint, name) {
    return `<fieldset class="field"><legend class="label">${esc(legend)}</legend>${hint ? `<p class="hint">${esc(hint)}</p>` : ""}${html}${name ? `<p class="error-text" data-error-for="${name}" hidden></p>` : ""}</fieldset>`;
  }

  function citySelect(p) {
    const byRegion = {};
    cat.cities.forEach((c) => (byRegion[c.region] = byRegion[c.region] || []).push(c));
    const current = p.city ? `${p.region}|${p.city}` : "";
    let html = `<select id="f-city" name="city_choice"><option value="">Choose your city</option>`;
    Object.keys(byRegion).sort().forEach((r) => {
      html += `<optgroup label="${esc(r)}">` + byRegion[r].map((c) => {
        const v = `${c.region}|${c.city}`;
        return `<option value="${esc(v)}" ${v === current ? "selected" : ""}>${esc(c.city)}</option>`;
      }).join("") + "</optgroup>";
    });
    return html + "</select>";
  }

  function interestChips(selected) {
    const groups = {};
    cat.interests.forEach((i) => (groups[i.category] = groups[i.category] || []).push(i));
    return Object.entries(groups).map(([g, items]) => `<p class="small muted mb-05 mt-1">${esc(g)}</p><div class="chips">` +
      items.map((i) => `<label class="chip"><input type="checkbox" name="interests" value="${esc(i.slug)}" data-multi ${selected.includes(i.slug) ? "checked" : ""}><span>${esc(i.label)}</span></label>`).join("") + "</div>").join("");
  }

  function photosSection() {
    const photos = prof.photos || [];
    let slots = "";
    for (let i = 0; i < 6; i++) {
      const p = photos[i];
      if (p) {
        slots += `<div class="photo-slot" data-photo="${esc(p.id)}"><img src="${esc(p.url)}" alt="Your photo ${i + 1}" loading="lazy">
          ${i === 0 ? '<span class="main-label">Main</span>' : ""}
          <div class="tools">
            ${i > 0 ? `<button type="button" data-move="${esc(p.id)}" aria-label="Move photo earlier">${HS.icon("arrow-left")}</button>` : ""}
            <button type="button" data-delete-photo="${esc(p.id)}" aria-label="Remove photo">${HS.icon("trash")}</button>
          </div></div>`;
      } else {
        slots += `<div class="photo-slot"><label class="add">${i === photos.length ? `<span>${HS.icon("image")}<br>Add photo</span>
          <input type="file" accept="image/jpeg,image/png,image/webp" data-upload aria-label="Add a photo">` : ""}</label></div>`;
      }
    }
    return `<section class="card stack" data-step="photos">
      <h2>Photos</h2>
      <p class="muted small">Add 1–6 photos. Clear, recent photos of your face work best. We strip location data from every image.</p>
      <div class="photo-grid" id="photo-grid">${slots}</div>
      <p class="error-text" data-error-for="photos" hidden></p>
    </section>`;
  }

  function render() {
    const p = prof;
    const vis = p.field_visibility || cat.visibility_fields;
    const sf = cat.select_fields;
    const steps = `
      ${photosSection()}
      <section class="card stack" data-step="basics">
        <h2>The basics</h2>
        ${field("First name or nickname", `<input id="f-display_name" name="display_name" maxlength="40" required value="${esc(p.display_name || "")}" autocomplete="given-name">`, "Shown on your profile. No surnames, numbers or handles.", "display_name")}
        <div class="grid cols-2">
          ${field("I am a", select("gender", cat.genders, p.gender, "Choose"), null, "gender")}
          ${field("Pronouns (optional)", `<input id="f-pronouns" name="pronouns" maxlength="30" value="${esc(p.pronouns || "")}" placeholder="e.g. she/her">`, null, "pronouns")}
        </div>
        ${fieldset("I'd like to meet", chips("interested_in", cat.genders, p.interested_in), "Choose all that apply.", "interested_in")}
        ${field("City", citySelect(p), "Only your city is shown — never your exact location.", "city")}
        ${fieldset("Languages I speak", chips("languages", cat.languages, p.languages), null, "languages")}
      </section>
      <section class="card stack" data-step="about">
        <h2>About you</h2>
        ${field("Headline", `<input id="f-headline" name="headline" maxlength="90" value="${esc(p.headline || "")}" placeholder="e.g. Weekend trekker, weekday spreadsheet wrangler">`, null, "headline")}
        ${field("About me", `<textarea id="f-about" name="about" maxlength="1200" placeholder="What makes you smile? What are you looking for?">${esc(p.about || "")}</textarea>`, "Please don't include phone numbers, emails or social handles.", "about")}
        ${onboarding ? "" : `<div class="assist" id="assist">
          <div class="row between wrap gap-sm">
            <span class="label">Writing help <span class="tag">Suggestions only</span></span>
            <span class="row gap-sm wrap">
              <label class="sr-only" for="assist-tone">Tone</label>
              <select id="assist-tone" class="sm"><option value="warm">Warm</option><option value="playful">Playful</option><option value="direct">Direct</option></select>
              <button type="button" class="btn sm" data-assist="bio">Draft my “About me”</button>
              <button type="button" class="btn sm" data-assist="tips">Profile tips</button>
            </span>
          </div>
          <div id="assist-out" aria-live="polite"></div>
        </div>`}
        ${fieldset("Interests", interestChips(p.interests || []), "Pick up to 15. Shared interests help us explain why you might click.", "interests")}
      </section>
      <section class="card stack" data-step="lifestyle">
        <h2>Lifestyle</h2>
        <div class="grid cols-2">
          ${field("Height (cm)", `<input id="f-height_cm" name="height_cm" type="number" min="120" max="230" value="${p.height_cm || ""}">`, null, "height_cm")}
          ${field("Relationship status", select("relationship_status", sf.relationship_status, p.relationship_status), null, "relationship_status")}
          ${field("Education", select("education", sf.education, p.education), null, "education")}
          ${field("Job title", `<input id="f-profession" name="profession" maxlength="60" value="${esc(p.profession || "")}">`, null, "profession")}
          ${field("Industry", select("industry", sf.industry, p.industry), null, "industry")}
          ${field("Work status", select("work_status", sf.work_status, p.work_status), null, "work_status")}
          ${field("Work schedule", select("work_schedule", sf.work_schedule, p.work_schedule), null, "work_schedule")}
          ${field("Smoking", select("smoking", sf.smoking, p.smoking), null, "smoking")}
          ${field("Drinking", select("drinking", sf.drinking, p.drinking), null, "drinking")}
          ${field("Diet", select("diet", sf.diet, p.diet), null, "diet")}
          ${field("Exercise", select("exercise", sf.exercise, p.exercise), null, "exercise")}
          ${field("Pets", select("pets", sf.pets, p.pets), null, "pets")}
          ${field("Sleep", select("sleep_habits", sf.sleep_habits, p.sleep_habits), null, "sleep_habits")}
          ${field("Religion or spirituality (optional)", select("religion", sf.religion, p.religion), "Visible to matches only by default.", "religion")}
        </div>
      </section>
      <section class="card stack" data-step="goals">
        <h2>What you're looking for</h2>
        ${fieldset("Relationship goals", chips("relationship_goals", cat.relationship_goals, p.relationship_goals), null, "relationship_goals")}
        ${fieldset("Qualities I value in a partner", chips("partner_qualities", cat.multi_fields.partner_qualities, p.partner_qualities), null, "partner_qualities")}
        <div class="grid cols-2">
          ${field("Communication style", select("communication_style", sf.communication_style, p.communication_style), null, "communication_style")}
          ${field("Family plans (optional)", select("family_plans", sf.family_plans, p.family_plans), "Visible to matches only by default.", "family_plans")}
          ${field("Open to relocating?", `<select id="f-open_to_relocate" name="open_to_relocate"><option value="">Prefer not to say</option><option value="true" ${p.open_to_relocate === 1 ? "selected" : ""}>Yes</option><option value="false" ${p.open_to_relocate === 0 ? "selected" : ""}>No</option></select>`, null, "open_to_relocate")}
        </div>
        ${fieldset("Travel style", chips("travel_interests", cat.multi_fields.travel_interests, p.travel_interests), null, "travel_interests")}
        ${field("Future goals", `<textarea id="f-future_goals" name="future_goals" maxlength="300" placeholder="Where do you see yourself in a few years?">${esc(p.future_goals || "")}</textarea>`, null, "future_goals")}
      </section>
      <section class="card stack" data-step="privacy">
        <h2>Who sees what</h2>
        <p class="muted small">Choose who can see each detail. "Matches" means only people you've matched with.</p>
        <div class="grid cols-2">
          ${Object.keys(cat.visibility_fields).map((f) => field(FIELD_LABELS[f] || f, `<select id="f-vis-${f}" name="vis__${f}">
            ${["everyone", "matches", "hidden"].map((l) => `<option value="${l}" ${vis[f] === l ? "selected" : ""}>${{ everyone: "Everyone", matches: "Matches only", hidden: "Only me" }[l]}</option>`).join("")}
          </select>`)).join("")}
        </div>
      </section>`;

    root.innerHTML = `
      ${onboarding ? `<div class="steps" id="wizard-steps" aria-hidden="true">${"<i></i>".repeat(6)}</div>` : `
        <div class="card mb-1"><div class="row between"><strong>Profile ${p.completeness || 0}% complete</strong>
        <span class="small muted">${p.completeness >= 80 ? "Looking great!" : "Add more details to stand out."}</span></div>
        <div class="progress mt-05"><i id="completeness-bar"></i></div></div>`}
      <form id="profile-form" class="stack" novalidate>
        ${steps}
        <p class="notice danger" data-form-status hidden></p>
        <div class="row between" id="form-actions">
          ${onboarding ? `<button type="button" class="btn" id="prev-step">Back</button><span class="spacer"></span>
            <button type="button" class="btn primary" id="next-step">Next</button>
            <button type="submit" class="btn accent" id="finish" hidden>Finish & start discovering</button>` :
            `<span class="spacer"></span><button type="submit" class="btn primary">Save changes</button>`}
        </div>
      </form>`;
    const bar = $("#completeness-bar");
    if (bar) bar.style.width = (p.completeness || 0) + "%";
    if (onboarding) setupWizard();
  }

  // ---------------------------------------------------------------- wizard
  let step = 0;
  function setupWizard() {
    const sections = $$("[data-step]", root);
    const show = () => {
      sections.forEach((s, i) => (s.hidden = i !== step));
      $$("#wizard-steps i").forEach((el, i) => el.classList.toggle("done", i <= step));
      $("#prev-step").style.visibility = step === 0 ? "hidden" : "visible";
      $("#next-step").hidden = step === sections.length - 1;
      $("#finish").hidden = step !== sections.length - 1;
      const h = sections[step].querySelector("h2");
      if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
    };
    $("#prev-step").addEventListener("click", () => { step = Math.max(0, step - 1); show(); });
    $("#next-step").addEventListener("click", () => {
      if (!validateStep(sections[step])) return;
      step = Math.min(sections.length - 1, step + 1);
      show();
    });
    show();
  }

  function validateStep(section) {
    const name = section.dataset.step;
    const form = $("#profile-form");
    const errs = {};
    if (name === "photos" && !(prof.photos || []).length) errs.photos = "Add at least one photo to continue.";
    if (name === "basics") {
      if (!form.display_name.value.trim()) errs.display_name = "Please enter a name.";
      if (!form.gender.value) errs.gender = "Please choose an option.";
      if (!$$('input[name="interested_in"]:checked', form).length) errs.interested_in = "Choose at least one.";
      if (!form.city_choice.value) errs.city = "Choose your city.";
    }
    HS.showFieldErrors(form, errs);
    return !Object.keys(errs).length;
  }

  // --------------------------------------------------------------- payload
  function payload() {
    const form = $("#profile-form");
    const d = HS.formData(form);
    const out = { field_visibility: {} };
    Object.entries(d).forEach(([k, v]) => {
      if (k.startsWith("vis__")) out.field_visibility[k.slice(5)] = v;
      else if (k === "city_choice") {
        if (v) { const [region, city] = v.split("|"); out.region = region; out.city = city; out.country = "IN"; }
        else { out.region = null; out.city = null; }
      } else if (k === "open_to_relocate") out.open_to_relocate = v === null ? null : v === "true";
      else out[k] = v;
    });
    ["interested_in", "languages", "relationship_goals", "partner_qualities", "travel_interests", "interests"].forEach((k) => { out[k] = out[k] || []; });
    return out;
  }

  // ---------------------------------------------------------------- events
  root.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const btn = form.querySelector("[type=submit]");
    const status = form.querySelector("[data-form-status]");
    status.hidden = true;
    HS.busy(btn, true);
    try {
      prof = await api(onboarding ? "POST" : "PUT", onboarding ? "/api/profile/complete" : "/api/profile", payload());
      if (onboarding) { location.href = "/discover?welcome=1"; return; }
      toast("Profile saved", "success");
      const bar = $("#completeness-bar");
      if (bar) bar.style.width = (prof.completeness || 0) + "%";
    } catch (err) {
      HS.showFieldErrors(form, err.fields);
      status.textContent = err.message;
      status.hidden = false;
      if (onboarding && err.fields) {
        // Jump to the step containing the first error.
        const first = Object.keys(err.fields)[0];
        const sections = $$("[data-step]", root);
        sections.forEach((s, i) => { if (s.querySelector(`[name="${first}"], [data-error-for="${first}"]`)) step = i; });
        sections.forEach((s, i) => (s.hidden = i !== step));
        $("#next-step").hidden = step === sections.length - 1;
        $("#finish").hidden = step !== sections.length - 1;
      }
    } finally {
      HS.busy(btn, false);
    }
  });

  // Enforce interest max (15)
  root.addEventListener("change", async (e) => {
    if (e.target.name === "interests") {
      const checked = $$('input[name="interests"]:checked', root);
      if (checked.length > 15) { e.target.checked = false; toast("You can choose up to 15 interests."); }
    }
    if (e.target.matches("[data-upload]")) {
      const file = e.target.files[0];
      if (!file) return;
      if (file.size > 8 * 1024 * 1024) { toast("Photos must be smaller than 8 MB.", "error"); return; }
      const fd = new FormData();
      fd.append("file", file);
      const slot = e.target.closest(".photo-slot");
      slot.classList.add("skeleton");
      try {
        await api("POST", "/api/profile/photos", fd);
        await reloadPhotos();
        toast("Photo added", "success");
      } catch (err) { fail(err); slot.classList.remove("skeleton"); }
    }
  });

  root.addEventListener("click", async (e) => {
    const asst = e.target.closest("[data-assist]");
    if (asst) {
      const out = $("#assist-out");
      HS.busy(asst, true);
      try {
        const kind = asst.dataset.assist;
        const r = await api("POST", "/api/profile/assist", { kind, tone: $("#assist-tone").value });
        if (kind === "bio") {
          out.innerHTML = `<div class="assist-card"><p class="small muted">${esc(r.label)}. Based on what you saved — edit it so it sounds like you.</p>
            <p class="assist-draft">${esc(r.draft)}</p>
            <div class="row gap-sm"><button type="button" class="btn sm primary" data-use-draft>Use this draft</button>
            <button type="button" class="btn sm" data-assist="bio">Try another</button></div></div>`;
          out.querySelector("[data-use-draft]").addEventListener("click", () => {
            const ta = $("#f-about");
            ta.value = r.draft;
            ta.focus();
            toast("Draft added — review it, then save changes.", "success");
          });
        } else {
          out.innerHTML = r.tips.length
            ? `<div class="assist-card"><p class="small muted">${esc(r.label)}</p><ul class="assist-tips">${r.tips.map((t) => `<li>${esc(t.text)}</li>`).join("")}</ul></div>`
            : `<div class="assist-card"><p class="small">Your profile already covers the essentials. Nice work.</p></div>`;
        }
      } catch (err) { fail(err); }
      finally { HS.busy(asst, false); }
      return;
    }
    const del = e.target.closest("[data-delete-photo]");
    if (del) {
      if (!(await HS.confirm("Remove this photo?", "It will be deleted permanently.", "Remove", true))) return;
      try { await api("DELETE", "/api/profile/photos/" + del.dataset.deletePhoto); await reloadPhotos(); }
      catch (err) { fail(err); }
      return;
    }
    const mv = e.target.closest("[data-move]");
    if (mv) {
      const ids = prof.photos.map((p) => p.id);
      const i = ids.indexOf(mv.dataset.move);
      if (i > 0) { [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]]; }
      try { await api("POST", "/api/profile/photos/order", { ids }); await reloadPhotos(); }
      catch (err) { fail(err); }
    }
  });

  async function reloadPhotos() {
    const fresh = await api("GET", "/api/profile");
    prof.photos = fresh.photos;
    prof.completeness = fresh.completeness;
    const section = $('[data-step="photos"]', root);
    const tmp = document.createElement("div");
    tmp.innerHTML = photosSection();
    const replacement = tmp.firstElementChild;
    replacement.hidden = section.hidden;
    section.replaceWith(replacement);
  }

  (async function init() {
    try {
      [cat, prof] = await Promise.all([api("GET", "/api/catalog"), api("GET", "/api/profile")]);
      render();
    } catch (err) {
      root.innerHTML = `<div class="empty"><h2>Couldn't load your profile</h2><p>${esc(err.message)}</p><button class="btn" data-reload>Try again</button></div>`;
    }
  })();
})();
