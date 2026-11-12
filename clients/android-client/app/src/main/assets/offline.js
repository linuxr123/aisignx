(function () {
  "use strict";
  var reason = document.getElementById("reason");
  var cd = document.getElementById("cd");
  if (location.hash.length > 1) {
    try { reason.textContent = decodeURIComponent(location.hash.slice(1)).slice(0, 200); } catch (e) { /* keep default */ }
  }
  var n = 10;
  setInterval(function () {
    n = n <= 1 ? 10 : n - 1;
    cd.textContent = "Retrying in " + n + " second" + (n === 1 ? "" : "s");
  }, 1000);
})();
