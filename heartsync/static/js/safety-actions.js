/* Shared report / block / unmatch actions. */
(function () {
  "use strict";
  const { $, api, toast, fail } = HS;

  HS.report = function (userId, name, messageId) {
    return new Promise((resolve) => {
      const dlg = $("#report-dialog");
      const form = $("#report-form");
      form.reset();
      form.querySelector("[data-form-status]").hidden = true;
      dlg.querySelectorAll("[data-report-name]").forEach((el) => (el.textContent = name || "this member"));
      const close = () => { dlg.close(); };
      dlg.querySelector("[data-close]").onclick = close;
      form.onsubmit = async (e) => {
        e.preventDefault();
        const d = HS.formData(form);
        if (!d.category) { HS.showFieldErrors(form, { category: "Please choose a reason." }); return; }
        const btn = form.querySelector("[type=submit]");
        HS.busy(btn, true);
        try {
          await api("POST", "/api/reports", { user_id: userId, category: d.category, details: d.details, message_id: messageId || null, block: !!d.block });
          dlg.close();
          toast(d.block ? "Thanks for reporting. We've also blocked them for you." : "Thanks for reporting. Our team will review it.", "success");
          resolve({ reported: true, blocked: !!d.block });
        } catch (err) {
          const st = form.querySelector("[data-form-status]");
          st.textContent = err.message; st.hidden = false;
        } finally { HS.busy(btn, false); }
      };
      dlg.addEventListener("close", () => resolve({ reported: false }), { once: true });
      dlg.showModal();
    });
  };

  HS.block = async function (userId, name) {
    const ok = await HS.confirm(`Block ${name || "this member"}?`, "You won't see each other anywhere on the app. Any match and conversation ends, and number sharing is revoked. They won't be notified.", "Block", true);
    if (!ok) return false;
    try { await api("POST", "/api/blocks", { user_id: userId }); toast("Blocked", "success"); return true; }
    catch (err) { fail(err); return false; }
  };

  HS.unmatch = async function (matchId, name) {
    const ok = await HS.confirm(`Unmatch ${name || ""}?`.replace(" ?", "?"), "Your conversation will close and any shared phone number will be hidden. This can't be undone.", "Unmatch", true);
    if (!ok) return false;
    try { await api("DELETE", "/api/matches/" + matchId); toast("Unmatched"); return true; }
    catch (err) { fail(err); return false; }
  };
})();
