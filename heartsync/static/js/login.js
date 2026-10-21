// Sign-in: password step, then (if enabled) the two-factor step.
(function () {
  const loginForm = document.getElementById("login-form");
  const mfaForm = document.getElementById("mfa-form");
  const go = (form, e) => {
    const next = form.dataset.next;
    const suggested = e.detail && e.detail.next;
    // Prefer a ?next= target over the default destination once the account is ready.
    if (next && suggested === "/discover") { e.preventDefault(); location.href = next; }
  };
  loginForm.addEventListener("api:success", (e) => {
    if (e.detail && e.detail.mfa_required) {
      e.preventDefault();
      loginForm.hidden = true;
      mfaForm.hidden = false;
      document.getElementById("mfa-code").focus();
      return;
    }
    go(loginForm, e);
  });
  mfaForm.addEventListener("api:success", (e) => go(mfaForm, e));
  mfaForm.addEventListener("api:error", (e) => {
    if (e.detail && e.detail.code === "mfa_challenge_expired") {
      setTimeout(() => { mfaForm.hidden = true; loginForm.hidden = false; HS.$("#password").value = ""; HS.$("#password").focus(); }, 1800);
    }
  });
})();
