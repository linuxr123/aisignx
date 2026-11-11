// AiSignX admin helpers. Plain JS, no build step.
(function () {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  // <form data-confirm="Are you sure?"> asks before submitting.
  document.addEventListener("submit", (event) => {
    const message = event.target.dataset?.confirm;
    if (message && !window.confirm(message)) event.preventDefault();
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
