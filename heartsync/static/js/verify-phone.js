(function () {
  const send = document.getElementById("send-form");
  const verify = document.getElementById("verify-form");
  send.addEventListener("api:success", (e) => {
    const d = e.detail || {};
    document.getElementById("sent-to").textContent = `If ${d.masked} can receive codes, one is on its way. It expires in ${d.expires_in_minutes} minutes.`;
    document.getElementById("dev-note").hidden = !d.dev_mode;
    send.hidden = true;
    verify.hidden = false;
    document.getElementById("code").focus();
  });
  document.getElementById("change-number").addEventListener("click", () => {
    verify.hidden = true;
    send.hidden = false;
    document.getElementById("phone").focus();
  });
})();
