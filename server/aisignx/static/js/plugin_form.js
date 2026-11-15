// AiSignX admin: the plugin item form (templates/plugins/form.html, _fields.html).
// Only three jobs; the form itself is rendered and validated on the server:
//   - show_if: hide fields whose condition does not hold (the server still validates them);
//   - media_multi: a picker of library images (Bootstrap modal #media-picker);
//   - location: the "Find" button (GET /plugins/geocode?q=).
// No innerHTML: every node is built with the DOM API.
(function () {
  "use strict";

  const form = document.querySelector("[data-plugin-form]");
  if (!form) return;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ---- show_if ------------------------------------------------------------------------
  function fieldValue(name) {
    const inputs = form.querySelectorAll(`[name="${CSS.escape(name)}"]`);
    if (!inputs.length) return undefined;
    const first = inputs[0];
    if (first.type === "checkbox") return first.checked;
    return first.value;
  }

  function matches(want, have) {
    if (Array.isArray(want)) return want.some((w) => matches(w, have));
    if (typeof have === "boolean") return have === (want === true || want === "1" || want === "true");
    return String(want) === String(have);
  }

  const conditional = [...form.querySelectorAll("[data-show-if]")];
  function applyShowIf() {
    conditional.forEach((wrap) => {
      let rule = {};
      try { rule = JSON.parse(wrap.dataset.showIf || "{}") || {}; } catch (e) { rule = {}; }
      wrap.hidden = !Object.keys(rule).every((name) => matches(rule[name], fieldValue(name)));
    });
  }
  if (conditional.length) {
    form.addEventListener("change", applyShowIf);
    form.addEventListener("input", applyShowIf);
    applyShowIf();
  }

  // ---- media_multi picker -------------------------------------------------------------
  const picker = document.getElementById("media-picker");
  let images = [];
  if (picker) {
    try { images = JSON.parse(picker.dataset.images || "[]"); } catch (e) { images = []; }
  }
  const byId = new Map(images.map((m) => [String(m.id), m]));
  let target = null; // the [data-media-multi] being edited

  function chosenIds(box) {
    return [...box.querySelectorAll("input[type=hidden]")].map((i) => i.value);
  }

  function renderChosen(box, ids) {
    const list = box.querySelector("[data-media-chosen]");
    const name = box.dataset.mediaMulti;
    list.replaceChildren(...ids.map((id) => {
      const chip = el("span", "badge text-bg-secondary", (byId.get(id) || {}).name || `#${id}`);
      chip.dataset.mediaChip = id;
      const input = el("input");
      input.type = "hidden";
      input.name = name;
      input.value = id;
      chip.appendChild(input);
      return chip;
    }));
    const count = box.querySelector("[data-media-count]");
    if (count) count.textContent = `${ids.length} chosen (at most ${box.dataset.max})`;
    form.dispatchEvent(new Event("change"));
  }

  function renderGrid() {
    if (!picker || !target) return;
    const grid = picker.querySelector("[data-media-grid]");
    const term = (picker.querySelector("[data-media-search]").value || "").trim().toLowerCase();
    const ids = chosenIds(target);
    const max = Number(target.dataset.max) || 200;
    const cells = images.filter((m) => !term || m.name.toLowerCase().includes(term)).map((m) => {
      const id = String(m.id);
      const order = ids.indexOf(id);
      const col = el("div", "col");
      const btn = el("button", `btn p-1 w-100 text-start ${order >= 0 ? "btn-primary" : "btn-outline-secondary"}`);
      btn.type = "button";
      btn.setAttribute("aria-pressed", order >= 0 ? "true" : "false");
      if (m.thumb) {
        const img = el("img", "media-thumb rounded");
        img.src = m.thumb;
        img.alt = "";
        img.loading = "lazy";
        btn.appendChild(img);
      } else {
        btn.appendChild(el("div", "media-thumb media-thumb-placeholder rounded", "Image"));
      }
      btn.appendChild(el("div", "small text-truncate mt-1", (order >= 0 ? `${order + 1}. ` : "") + m.name));
      btn.addEventListener("click", () => {
        const now = chosenIds(target);
        const at = now.indexOf(id);
        if (at >= 0) now.splice(at, 1);
        else if (now.length < max) now.push(id);
        renderChosen(target, now);
        renderGrid();
      });
      col.appendChild(btn);
      return col;
    });
    grid.replaceChildren(...cells);
    picker.querySelector("[data-media-picked]").textContent = `${ids.length} of at most ${max} chosen`;
  }

  if (picker) {
    picker.addEventListener("show.bs.modal", (event) => {
      target = event.relatedTarget ? event.relatedTarget.closest("[data-media-multi]") : null;
      picker.querySelector("[data-media-search]").value = "";
      renderGrid();
    });
    picker.querySelector("[data-media-search]").addEventListener("input", renderGrid);
  }

  // ---- location "Find" ----------------------------------------------------------------
  const geocodeUrl = form.dataset.geocodeUrl;

  function setPoint(box, lat, lon, label) {
    const wrap = box.parentElement;
    wrap.querySelector("[data-location-lat]").value = lat === null ? "" : String(lat);
    wrap.querySelector("[data-location-lon]").value = lon === null ? "" : String(lon);
    wrap.querySelector("[data-location-point]").textContent =
      lat === null ? "No place chosen" : `${lat}, ${lon}`;
    const labelInput = wrap.querySelector("[data-location-label]");
    if (label && labelInput && !labelInput.value.trim()) labelInput.value = label;
  }

  form.querySelectorAll("[data-location]").forEach((box) => {
    const query = box.querySelector("input");
    const results = box.parentElement.querySelector("[data-location-results]");
    query.addEventListener("input", () => setPoint(box, null, null, ""));
    query.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); box.querySelector("[data-location-find]").click(); }
    });
    box.querySelector("[data-location-find]").addEventListener("click", async () => {
      const q = query.value.trim();
      results.replaceChildren();
      results.hidden = true;
      if (!q) return;
      let body = null;
      let message = "";
      try {
        const res = await fetch(`${geocodeUrl}?q=${encodeURIComponent(q)}`,
          { headers: { Accept: "application/json" }, credentials: "same-origin" });
        body = await res.json();
        if (!res.ok) message = body.message || "The lookup failed.";
      } catch (e) {
        message = "The lookup failed.";
      }
      const found = body && Array.isArray(body.results) ? body.results : [];
      if (!message && !found.length) message = "Nothing found. Try another name, or enter latitude,longitude.";
      if (message) {
        results.appendChild(el("div", "list-group-item small text-danger", message));
      }
      found.forEach((r) => {
        const item = el("button", "list-group-item list-group-item-action small",
          `${r.label} (${r.lat}, ${r.lon})`);
        item.type = "button";
        item.addEventListener("click", () => {
          setPoint(box, r.lat, r.lon, r.label);
          results.replaceChildren();
          results.hidden = true;
        });
        results.appendChild(item);
      });
      results.hidden = false;
    });
  });
})();
