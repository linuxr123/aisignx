// AiSignX admin helpers. Plain JS, no build step.
(function () {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  // <form data-confirm="Are you sure?"> asks before submitting.
  // POST forms are sent once: a double-click would otherwise submit twice (e.g. pairing a
  // display, then reporting the already-used code as invalid).
  document.addEventListener("submit", (event) => {
    const form = event.target;
    const message = form.dataset?.confirm;
    if (message && !window.confirm(message)) { event.preventDefault(); return; }
    if ((form.method || "").toLowerCase() !== "post") return;
    if (form.dataset.submitted) { event.preventDefault(); return; }
    form.dataset.submitted = "1";
    // Disable after this tick so the clicked button's name/value is still sent.
    setTimeout(() => form.querySelectorAll("button[type=submit], input[type=submit]")
      .forEach((b) => { b.disabled = true; }), 0);
  });
  // <div data-reload-after="4"> reloads the page after N seconds (e.g. waiting for a screen).
  const reloadEl = document.querySelector("[data-reload-after]");
  if (reloadEl) {
    const seconds = Math.max(2, Number(reloadEl.dataset.reloadAfter) || 4);
    setTimeout(() => window.location.reload(), seconds * 1000);
  }

  // Coming back via the browser's Back button restores a page whose forms were sent.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    document.querySelectorAll("form[data-submitted]").forEach((f) => {
      delete f.dataset.submitted;
      f.querySelectorAll("[type=submit]").forEach((b) => { b.disabled = false; });
    });
  });

  // <input data-filter-target="#grid"> hides children whose data-name doesn't match.
  document.querySelectorAll("[data-filter-target]").forEach((input) => {
    const grid = document.querySelector(input.dataset.filterTarget);
    if (!grid) return;
    input.addEventListener("input", () => {
      const term = input.value.trim().toLowerCase();
      grid.querySelectorAll("[data-name]").forEach((el) => {
        el.hidden = term !== "" && !el.dataset.name.includes(term);
      });
    });
  });

  // Reload once background thumbnails (web page screenshots) are ready.
  const pending = [...document.querySelectorAll("[data-thumb-pending]")].map((el) => el.dataset.thumbPending);
  if (pending.length) {
    const statusUrl = document.body.dataset.thumbStatusUrl;
    let tries = 0;
    const poll = async () => {
      tries += 1;
      try {
        const query = pending.map((id) => "id=" + encodeURIComponent(id)).join("&");
        const response = await fetch(statusUrl + "?" + query, { headers: { Accept: "application/json" } });
        const data = await response.json();
        if (data.pending.length < pending.length) { window.location.reload(); return; }
      } catch (err) { /* try again */ }
      if (tries < 40) window.setTimeout(poll, 3000);
    };
    window.setTimeout(poll, 3000);
  }

  // Display list: refresh status badges, last-seen times and live dots every 15 s.
  const statusBody = document.querySelector("[data-display-status-url]");
  if (statusBody) {
    const BADGES = {
      online: ["Online", "bg-success"],
      offline: ["Offline", "bg-secondary"],
      never: ["Never connected", "bg-light text-dark"],
      unpaired: ["Unpaired", "bg-warning text-dark"],
    };
    const formatSeen = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") + " UTC" : "—");
    const refresh = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(statusBody.dataset.displayStatusUrl, { headers: { Accept: "application/json" } });
        if (!response.ok) return;
        const data = await response.json();
        data.displays.forEach((d) => {
          const badge = document.querySelector(`[data-display-badge="${d.id}"]`);
          const [label, cls] = BADGES[d.state] || BADGES.offline;
          if (badge && badge.dataset.state !== d.state) {
            badge.className = "badge " + cls;
            badge.textContent = label;
            badge.dataset.state = d.state;
          }
          const seen = document.querySelector(`[data-display-seen="${d.id}"]`);
          if (seen) seen.textContent = formatSeen(d.last_seen);
          const live = document.querySelector(`[data-display-live="${d.id}"]`);
          if (live) live.classList.toggle("d-none", !d.live);
        });
      } catch (err) { /* try again next time */ }
    };
    window.setInterval(refresh, 15000);
  }

  // Drag-and-drop reordering for playlist items.
  const list = document.getElementById("playlistItems");
  if (list && list.dataset.reorderUrl) {
    let dragged = null;
    let startOrder = "";
    const currentOrder = () =>
      [...list.querySelectorAll("li[data-item-id]")].map((li) => Number(li.dataset.itemId));

    // Only the handle starts a drag, so the inputs inside each row stay usable.
    list.addEventListener("pointerdown", (event) => {
      const handle = event.target.closest(".drag-handle");
      if (handle) handle.closest("li[data-item-id]").draggable = true;
    });

    list.addEventListener("dragstart", (event) => {
      dragged = event.target.closest("li[data-item-id]");
      if (!dragged) return;
      startOrder = currentOrder().join(",");
      dragged.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
    });

    list.addEventListener("dragover", (event) => {
      if (!dragged) return;
      event.preventDefault();
      const target = event.target.closest("li[data-item-id]");
      if (!target || target === dragged) return;
      const rect = target.getBoundingClientRect();
      const after = event.clientY > rect.top + rect.height / 2;
      list.insertBefore(dragged, after ? target.nextSibling : target);
    });

    list.addEventListener("dragend", async () => {
      if (!dragged) return;
      dragged.classList.remove("dragging");
      dragged.draggable = false;
      dragged = null;
      const ids = currentOrder();
      if (ids.join(",") === startOrder) return;
      list.querySelectorAll(".item-number").forEach((el, i) => { el.textContent = i + 1; });
      try {
        const response = await fetch(list.dataset.reorderUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
          body: JSON.stringify({ item_ids: ids }),
        });
        if (!response.ok) throw new Error(response.statusText);
      } catch (err) {
        window.alert("Could not save the new order. The page will reload.");
      }
      // Reload so the up/down buttons and numbering match the saved order.
      window.location.reload();
    });
  }
})();
