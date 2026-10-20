// Prefer a ?next= target over the default destination once the account is ready.
document.getElementById("login-form").addEventListener("api:success", (e) => {
  const next = e.target.dataset.next;
  const suggested = e.detail && e.detail.next;
  if (next && suggested === "/discover") {
    e.preventDefault();
    location.href = next;
  }
});
