// Emergency page: the preview box follows the form (title, message and level colour). Classic script.
(function () {
  "use strict";
  var form = document.getElementById("em-form");
  var box = document.getElementById("em-preview");
  if (!form || !box) return;
  var title = form.querySelector('[data-pv="title"]');
  var message = form.querySelector('[data-pv="message"]');
  var pvTitle = document.getElementById("pv-title");
  var pvMsg = document.getElementById("pv-msg");

  function color() {
    var checked = form.querySelector('input[name="level"]:checked');
    return (checked && checked.getAttribute("data-color")) || "#b71c1c";
  }
  function update() {
    box.style.background = color();
    pvTitle.textContent = (title && title.value.trim()) || "Title";
    var text = (message && message.value.trim()) || "";
    pvMsg.textContent = text || "Your message appears here.";
    pvMsg.style.opacity = text ? "" : ".55";
  }
  ["input", "change"].forEach(function (name) { form.addEventListener(name, update); });
  update();
})();
