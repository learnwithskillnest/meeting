// Settings → Security: two-factor sign-in management.
(function () {
  const $ = HS.$, api = HS.api;
  const card = $("#mfa-card");
  if (!card) return;
  const show = (id, on) => { $(id).hidden = !on; };

  function showCodes(codes) {
    $("#mfa-code-list").textContent = codes.join("\n");
    show("#mfa-codes", true);
    $("#mfa-codes").scrollIntoView({ behavior: "smooth", block: "center" });
  }
  async function copy(text, label) {
    try { await navigator.clipboard.writeText(text); HS.toast(label + " copied", "success"); }
    catch (e) { HS.toast("Couldn't copy — select the text instead.", "error"); }
  }

  async function render() {
    try {
      const st = await api("GET", "/api/auth/mfa");
      $("#mfa-state").textContent = st.enabled ? "On" : "Off";
      $("#mfa-state").className = "tag" + (st.enabled ? " verified" : "");
      show("#mfa-on", st.enabled);
      show("#mfa-off", !st.enabled);
      $("#mfa-remaining").textContent = st.recovery_codes_remaining;
    } catch (err) { HS.fail(err); }
  }

  async function submit(form, fn) {
    const btn = form.querySelector("[type=submit]");
    HS.showFieldErrors(form, {});
    HS.busy(btn, true);
    try { await fn(HS.formData(form)); }
    catch (err) { if (err.fields && Object.keys(err.fields).length) HS.showFieldErrors(form, err.fields); else HS.fail(err); }
    finally { HS.busy(btn, false); }
  }

  $("#mfa-start").addEventListener("submit", (e) => {
    e.preventDefault();
    submit(e.target, async (data) => {
      const r = await api("POST", "/api/auth/mfa/setup", data);
      $("#mfa-secret").textContent = r.secret;
      $("#mfa-link").href = r.otpauth_uri;
      show("#mfa-start", false);
      show("#mfa-setup", true);
      $("#mfa-first").focus();
    });
  });
  $("#mfa-copy").addEventListener("click", () => copy($("#mfa-secret").textContent.replace(/\s/g, ""), "Setup key"));
  $("#mfa-confirm").addEventListener("submit", (e) => {
    e.preventDefault();
    submit(e.target, async (data) => {
      const r = await api("POST", "/api/auth/mfa/enable", data);
      e.target.reset();
      show("#mfa-setup", false);
      show("#mfa-start", true);
      HS.toast("Two-factor sign-in is on. Other devices were signed out.", "success");
      showCodes(r.recovery_codes);
      render();
    });
  });
  $("#mfa-off-btn").addEventListener("click", () => { show("#mfa-disable", true); $("#mfa-dpw").focus(); });
  $("#mfa-disable").addEventListener("submit", (e) => {
    e.preventDefault();
    submit(e.target, async (data) => {
      await api("POST", "/api/auth/mfa/disable", data);
      e.target.reset();
      show("#mfa-disable", false);
      show("#mfa-codes", false);
      HS.toast("Two-factor sign-in is off.", "success");
      render();
    });
  });
  $("#mfa-regen").addEventListener("click", async () => {
    show("#mfa-disable", false);
    const form = document.createElement("form");
    form.className = "mt-1";
    form.innerHTML = `<div class="field"><label for="mfa-rpw">Confirm your password to replace your recovery codes</label>
      <input id="mfa-rpw" name="password" type="password" autocomplete="current-password" required>
      <p class="error-text" data-error-for="password" hidden></p></div><button class="btn primary sm" type="submit">Replace codes</button>`;
    $("#mfa-regen").closest(".row").after(form);
    $("#mfa-regen").disabled = true;
    form.querySelector("input").focus();
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      submit(form, async (data) => {
        const r = await api("POST", "/api/auth/mfa/recovery-codes", data);
        form.remove();
        $("#mfa-regen").disabled = false;
        showCodes(r.recovery_codes);
        render();
      });
    });
  });
  $("#mfa-codes-copy").addEventListener("click", () => copy($("#mfa-code-list").textContent, "Recovery codes"));
  render();
})();
