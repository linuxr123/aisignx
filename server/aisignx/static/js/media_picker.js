// The playlist "Add media" picker: filters, paging and a selection that survives them (playlists/picker.py gives the data).
(function () {
  "use strict";
  var form = document.querySelector("[data-media-picker]");
  if (!form) return;
  var url = form.dataset.mediaPicker;
  var $ = function (s) { return form.querySelector(s); };
  var grid = $("[data-pk-grid]"), moreBtn = $("[data-pk-more]"), empty = $("[data-pk-empty]"), summary = $("[data-pk-summary]");
  var allBtn = $("[data-pk-all]"), clearBtn = $("[data-pk-clear]"), submit = $("[data-pk-submit]"), hidden = $("[data-pk-hidden]");
  var typeBar = $("[data-pk-types]"), plugBar = $("[data-pk-plugins]"), source = $("[data-pk-source]"), sort = $("[data-pk-sort]"), search = $("[data-pk-search]"), hide = $("[data-pk-hide]");
  var TYPE_LABEL = { "": "All", image: "Images", video: "Videos", web: "Web pages", plugin: "Plugins" };
  var state = { type: "", plugin: "", source: "uploads", q: "", sort: "newest", hideAdded: false, page: 1 };
  var selected = new Map();     // id -> name: kept while the filters change
  var last = null, token = 0, loaded = false, timer = null;

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function query(page) {
    var p = new URLSearchParams({ type: state.type, plugin: state.plugin, source: state.source, q: state.q, sort: state.sort, page: String(page) });
    if (state.hideAdded) p.set("hide_added", "1");
    return url + "?" + p.toString();
  }

  function card(it) {
    var col = document.createElement("div");
    col.className = "col";
    var thumb = it.thumb ? '<img class="card-img-top media-thumb" loading="lazy" alt="" src="' + esc(it.thumb) + '">'
      : '<div class="media-thumb media-thumb-placeholder">' + esc(it.pending ? "Preview…" : (it.plugin || it.type)) + "</div>";
    col.innerHTML = '<label class="card h-100 picker-card"><input class="form-check-input picker-check" type="checkbox" value="' + it.id + '">' + thumb +
      '<span class="card-body p-2 small"><span class="d-block text-truncate fw-semibold" title="' + esc(it.name) + '">' + esc(it.name) + "</span>" +
      '<span class="badge text-bg-secondary me-1">' + esc(it.plugin || it.type) + "</span>" +
      (it.added ? '<span class="badge text-bg-light text-dark">in playlist</span>' : "") + "</span></label>";
    var box = col.querySelector("input");
    box.checked = selected.has(it.id);
    box.addEventListener("change", function () { if (box.checked) selected.set(it.id, it.name); else selected.delete(it.id); refreshFooter(); });
    return col;
  }

  function chips(bar, items, current, onPick) {
    bar.innerHTML = "";
    items.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-sm " + (current === c.value ? "btn-primary" : "btn-outline-secondary");
      b.textContent = c.label + (c.count !== undefined ? " (" + c.count + ")" : "");
      b.addEventListener("click", function () { onPick(c.value); });
      bar.appendChild(b);
    });
  }

  function drawFilters(d) {
    var total = Object.keys(d.types).reduce(function (a, k) { return a + d.types[k]; }, 0);
    var items = [{ value: "", label: "All", count: total }];
    ["image", "video", "web", "plugin"].forEach(function (t) { if (d.types[t]) items.push({ value: t, label: TYPE_LABEL[t], count: d.types[t] }); });
    chips(typeBar, items, state.type, function (v) { state.type = v; state.plugin = ""; reload(); });
    var pl = state.type === "plugin" ? d.plugins.map(function (p) { return { value: p.key, label: p.key.replace(/_/g, " "), count: p.count }; }) : [];
    if (pl.length > 1) pl.unshift({ value: "", label: "Every plugin" });
    chips(plugBar, pl.length > 1 ? pl : [], state.plugin, function (v) { state.plugin = v; reload(); });
    if (!source.dataset.ready || source.dataset.ready !== String(d.sources.length)) {
      source.innerHTML = '<option value="uploads">Uploads and plugins (' + d.uploads + ")</option>" + '<option value="all">Everything</option>' +
        d.sources.map(function (s) { return '<option value="' + s.id + '">' + esc(s.name) + " (" + s.count + ")</option>"; }).join("");
      source.dataset.ready = String(d.sources.length);
    }
    source.value = state.source;
  }

  function refreshFooter() {
    var n = selected.size;
    summary.textContent = n + " selected" + (last ? " · " + last.total + " match the filters" : "");
    submit.disabled = n === 0;
    submit.textContent = n ? "Add " + n + " selected" : "Add selected";
    clearBtn.hidden = n === 0;
    allBtn.hidden = !(last && last.total > 0 && last.ids.some(function (i) { return !selected.has(i); }));
    allBtn.textContent = "Select all " + (last ? Math.min(last.total, last.ids.length) : "") + " matching";
    hidden.innerHTML = "";
    selected.forEach(function (_n, id) { var i = document.createElement("input"); i.type = "hidden"; i.name = "media_ids"; i.value = id; hidden.appendChild(i); });
  }

  function load(append) {
    var mine = ++token;
    fetch(query(state.page), { headers: { Accept: "application/json" }, credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
      if (mine !== token) return;
      last = d;
      if (!append) grid.innerHTML = "";
      d.items.forEach(function (it) { grid.appendChild(card(it)); });
      empty.hidden = d.total > 0;
      moreBtn.hidden = state.page >= d.pages;
      drawFilters(d);
      refreshFooter();
    }).catch(function () { summary.textContent = "Could not load the library. Try again."; });
  }
  function reload() { state.page = 1; load(false); }

  moreBtn.addEventListener("click", function () { state.page += 1; load(true); });
  source.addEventListener("change", function () { state.source = source.value; reload(); });
  sort.addEventListener("change", function () { state.sort = sort.value; reload(); });
  hide.addEventListener("change", function () { state.hideAdded = hide.checked; reload(); });
  search.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { state.q = search.value.trim(); reload(); }, 250); });
  allBtn.addEventListener("click", function () {
    var names = {};
    grid.querySelectorAll("input").forEach(function (b) { names[b.value] = b.closest("label").querySelector(".fw-semibold").textContent; });
    last.ids.forEach(function (id) { selected.set(id, names[id] || "#" + id); });
    grid.querySelectorAll("input").forEach(function (b) { b.checked = selected.has(Number(b.value)); });
    refreshFooter();
  });
  clearBtn.addEventListener("click", function () { selected.clear(); grid.querySelectorAll("input").forEach(function (b) { b.checked = false; }); refreshFooter(); });
  document.getElementById("addMedia").addEventListener("shown.bs.modal", function () { if (!loaded) { loaded = true; reload(); } search.focus(); });
  form.addEventListener("submit", function (e) { if (!selected.size) e.preventDefault(); });
})();
