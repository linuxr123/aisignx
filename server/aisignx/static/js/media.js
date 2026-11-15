// Media library: drag-and-drop upload with a progress row per file, and instant filters.
// Plain JS, no build step. Without JavaScript the page still works: the file input and the
// Upload button post the form, and the filter chips simply do nothing.
(function () {
  "use strict";

  // ---- Upload ----------------------------------------------------------------------------
  const form = document.getElementById("uploadForm");
  const zone = document.getElementById("dropzone");
  const list = document.getElementById("uploadList");
  const ALLOWED = /\.(jpe?g|png|gif|webp|bmp|mp4|webm|mov|m4v|mkv)$/i;
  const PARALLEL = 2;

  function fmtSize(bytes) {
    if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + " GB";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  if (form && zone && list) {
    const input = zone.querySelector("input[type=file]");
    const maxBytes = (Number(form.dataset.maxMb) || 2048) * 1048576;
    const csrf = (form.querySelector("input[name=csrf_token]") || {}).value || "";
    const nojs = form.querySelector("[data-nojs-submit]");
    if (nojs) nojs.hidden = true;           // the drop zone replaces the Upload button
    input.removeAttribute("required");
    zone.classList.add("enhanced");

    const pending = [];
    let running = 0, okCount = 0, failCount = 0, total = 0;

    function addRow(file) {
      const row = document.createElement("div");
      row.className = "upload-row";
      const name = document.createElement("div");
      name.className = "upload-name text-truncate";
      name.textContent = file.name;
      name.title = file.name;
      const size = document.createElement("div");
      size.className = "upload-size text-secondary small";
      size.textContent = fmtSize(file.size);
      const bar = document.createElement("div");
      bar.className = "progress upload-bar";
      const fill = document.createElement("div");
      fill.className = "progress-bar";
      fill.style.width = "0%";
      bar.appendChild(fill);
      const status = document.createElement("div");
      status.className = "upload-status small text-secondary";
      status.textContent = "Waiting";
      row.append(name, size, bar, status);
      list.appendChild(row);
      return { row, fill, status };
    }
    function fail(ui, message) {
      ui.row.classList.add("failed");
      ui.fill.classList.add("bg-danger");
      ui.fill.style.width = "100%";
      ui.status.className = "upload-status small text-danger";
      ui.status.textContent = message;
    }

    function finishedAll() {
      if (running > 0 || pending.length > 0) return;
      if (okCount > 0 && failCount === 0) {
        setTimeout(() => window.location.reload(), 900);
      } else if (okCount > 0) {
        const done = document.createElement("button");
        done.type = "button";
        done.className = "btn btn-sm btn-primary mt-2";
        done.textContent = "Show the uploaded files";
        done.addEventListener("click", () => window.location.reload());
        list.appendChild(done);
      }
    }

    function send(job) {
      running += 1;
      const { file, ui } = job;
      ui.status.textContent = "Uploading…";
      const body = new FormData();
      body.append("csrf_token", csrf);
      body.append("files", file, file.name);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.dataset.uploadUrl);
      xhr.setRequestHeader("X-Requested-With", "fetch");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round((e.loaded * 100) / e.total);
        ui.fill.style.width = pct + "%";
        ui.status.textContent = pct < 100 ? "Uploading… " + pct + "%" : "Processing…";
      };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
        if (xhr.status === 200 && data.added > 0) {
          okCount += 1;
          ui.row.classList.add("done");
          ui.fill.classList.add("bg-success");
          ui.fill.style.width = "100%";
          ui.status.className = "upload-status small text-success";
          ui.status.textContent = "Done";
          (data.warnings || []).forEach((w) => {
            const note = document.createElement("div");
            note.className = "upload-note small text-warning-emphasis";
            note.textContent = w;
            ui.row.appendChild(note);
          });
        } else {
          failCount += 1;
          const message = (data.errors && data.errors[0]) ||
            (xhr.status === 413 ? "The file is too big for this server." : "Upload failed (" + xhr.status + ").");
          fail(ui, message);
        }
      };
      xhr.onerror = () => { failCount += 1; fail(ui, "Could not reach the server."); };
      xhr.onloadend = () => { running -= 1; pump(); finishedAll(); };
      xhr.send(body);
    }

    function pump() {
      while (running < PARALLEL && pending.length) send(pending.shift());
    }

    function addFiles(files) {
      [...files].forEach((file) => {
        total += 1;
        const ui = addRow(file);
        if (!ALLOWED.test(file.name)) { failCount += 1; fail(ui, "Not an image or video file this server accepts."); return; }
        if (file.size > maxBytes) { failCount += 1; fail(ui, "Larger than the " + fmtSize(maxBytes) + " limit."); return; }
        pending.push({ file, ui });
      });
      pump();
      finishedAll();
    }

    // Choose files: the whole zone is a button (click or Enter/Space).
    const pick = () => { input.value = ""; input.click(); };
    zone.addEventListener("click", (e) => { if (e.target !== input) pick(); });
    zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    document.querySelectorAll("[data-upload-pick]").forEach((b) => b.addEventListener("click", pick));
    input.addEventListener("change", () => { if (input.files.length) addFiles(input.files); input.value = ""; });
    input.addEventListener("click", (e) => e.stopPropagation());

    // Drag and drop onto the zone, or anywhere on the page.
    let depth = 0;
    const hasFiles = (e) => e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files");
    document.addEventListener("dragenter", (e) => { if (hasFiles(e)) { depth += 1; zone.classList.add("over"); } });
    document.addEventListener("dragleave", (e) => { if (hasFiles(e)) { depth = Math.max(0, depth - 1); if (!depth) zone.classList.remove("over"); } });
    document.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
    document.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      zone.classList.remove("over");
      if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
    });
  }

  // ---- Filters: type chips and a search box, applied instantly ---------------------------------
  const filters = document.getElementById("mediaFilters");
  if (filters) {
    const search = document.getElementById("mediaSearch");
    const shown = document.getElementById("mediaShown");
    const cards = [...document.querySelectorAll("[data-type][data-name]")];
    let type = filters.dataset.initialType || "";
    const chips = [...filters.querySelectorAll("[data-type-filter]")];

    function apply() {
      const term = (search.value || "").trim().toLowerCase();
      let n = 0;
      cards.forEach((card) => {
        const ok = (!type || card.dataset.type === type) && (!term || card.dataset.name.includes(term));
        card.hidden = !ok;
        if (ok) n += 1;
      });
      chips.forEach((c) => c.classList.toggle("active", c.dataset.typeFilter === type));
      if (shown) shown.textContent = (type || term) ? n + " of " + cards.length + " shown" : "";
    }
    chips.forEach((c) => c.addEventListener("click", () => { type = c.dataset.typeFilter; apply(); }));
    search.addEventListener("input", apply);
    if (!chips.some((c) => c.dataset.typeFilter === type)) type = "";
    apply();
  }
})();
