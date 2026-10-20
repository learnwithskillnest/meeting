// Applies the saved colour theme before first paint (avoids a flash).
(function () {
  try {
    var t = localStorage.getItem("hs-theme");
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage unavailable */ }
})();
