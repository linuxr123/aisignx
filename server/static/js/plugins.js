// Dynamic plugin form + playlist item actions for playlist_detail.html
(() => {
  let REGISTRY = [];
  let CURRENT_EDIT = null; // { itemId, plugin_type, plugin_config, duration }
  let PLAYLIST_ID = null;

  function $(sel) { return document.querySelector(sel); }
  function $all(sel) { return Array.from(document.querySelectorAll(sel)); }

  async function fetchRegistry() {
    try {
      const res = await fetch('/api/plugins', { credentials: 'same-origin' });
      const data = await res.json();
      if (data && data.status === 'success') {
        REGISTRY = data.plugins || [];
      } else {
        REGISTRY = [];
      }
    } catch (e) {
      console.error('Failed to load plugin registry', e);
      REGISTRY = [];
    }
  }

  function findPlugin(keyOrType) {
    return REGISTRY.find(p => p.key === keyOrType || p.type === keyOrType);
  }

  function populatePluginSelect(selected) {
    const select = $('#pluginType');
    const desc = $('#pluginDescription');
    select.innerHTML = '';
    for (const p of REGISTRY) {
      const opt = document.createElement('option');
      opt.value = p.key || p.type;
      opt.textContent = `${p.name || p.type} (${p.version || '1.0.0'})`;
      select.appendChild(opt);
    }
    if (selected) select.value = selected;
    const meta = findPlugin(select.value);
    desc.textContent = meta ? (meta.description || '') : '';
  }

  function renderFieldsFor(pluginKey, values = {}) {
    const meta = findPlugin(pluginKey);
    const container = $('#pluginConfigFields');
    container.innerHTML = '';
    const schema = (meta && meta.schema) || [];
    for (const field of schema) {
      const id = `pcfg_${field.name}`;
      const type = (field.type || 'string').toLowerCase();
      const label = field.label || field.name;
      const defv = values[field.name] !== undefined ? values[field.name] : field.default;

      const col = document.createElement('div');
      col.className = 'col-md-6';

      const wrapper = document.createElement('div');
      wrapper.className = 'form-group';

      const lab = document.createElement('label');
      lab.className = 'form-label';
      lab.setAttribute('for', id);
      lab.textContent = label;

      let input;
      if (type === 'boolean') {
        input = document.createElement('select');
        input.className = 'form-select';
        input.id = id;
        input.dataset.fieldName = field.name;
        input.innerHTML = `<option value="true">True</option><option value="false">False</option>`;
        input.value = String(defv === undefined ? false : !!defv);
      } else if (type === 'number' || type === 'integer') {
        input = document.createElement('input');
        input.type = 'number';
        input.className = 'form-control';
        input.id = id;
        input.dataset.fieldName = field.name;
        input.value = defv !== undefined ? defv : 0;
        if (type === 'integer') input.step = '1';
      } else if (type === 'select' && Array.isArray(field.options)) {
        input = document.createElement('select');
        input.className = 'form-select';
        input.id = id;
        input.dataset.fieldName = field.name;
        for (const optv of field.options) {
          const opt = document.createElement('option');
          if (typeof optv === 'object') {
            opt.value = optv.value;
            opt.textContent = optv.label || optv.value;
          } else {
            opt.value = optv;
            opt.textContent = optv;
          }
          input.appendChild(opt);
        }
        if (defv !== undefined) input.value = defv;
      } else if (type === 'media_multi') {
        // Multi-select picker for image library media. Renders as a
        // scrollable thumbnail grid with checkboxes; the gathered value
        // is a comma-separated list of media IDs. media_filter (optional
        // schema attr) restricts the list, e.g. "image" -> images only.
        col.className = 'col-md-12';
        input = document.createElement('div');
        input.className = 'border rounded p-2';
        input.style.maxHeight = '320px';
        input.style.overflowY = 'auto';
        input.id = id;
        input.dataset.fieldName  = field.name;
        input.dataset.fieldType  = 'media_multi';
        input.dataset.mediaFilter = field.media_filter || 'image';
        input.dataset.initialIds = (defv === undefined || defv === null) ? '' : String(defv);
        input.innerHTML = '<div class="text-muted small">Loading...</div>';
        // Async populate. We do this lazily so the modal opens snappily
        // even on a server with hundreds of images.
        const filter = input.dataset.mediaFilter;
        fetch(`/api/media?type=${encodeURIComponent(filter)}`, { credentials: 'same-origin' })
          .then(r => r.json())
          .then(data => {
            if (data.status !== 'success') {
              input.innerHTML = '<div class="text-danger small">Failed to load media list.</div>';
              return;
            }
            const initial = new Set(
              (input.dataset.initialIds || '').split(',').map(s => s.trim()).filter(Boolean)
            );
            input.innerHTML = '';
            if (!data.media.length) {
              input.innerHTML = '<div class="text-muted small">No images uploaded yet. Add some via the Media page.</div>';
              return;
            }
            const grid = document.createElement('div');
            grid.style.display       = 'grid';
            grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(120px, 1fr))';
            grid.style.gap           = '0.5rem';
            for (const m of data.media) {
              const card = document.createElement('label');
              card.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:.25rem;cursor:pointer;font-size:.8rem;border:2px solid transparent;border-radius:6px;padding:.25rem;';
              const cb = document.createElement('input');
              cb.type = 'checkbox';
              cb.value = String(m.id);
              cb.dataset.role = 'media-pick';
              if (initial.has(String(m.id))) {
                cb.checked = true;
                card.style.borderColor = '#0d6efd';
              }
              cb.addEventListener('change', () => {
                card.style.borderColor = cb.checked ? '#0d6efd' : 'transparent';
              });
              const img = document.createElement('img');
              img.src = m.thumbnail_url || `/uploads/${m.filename}`;
              img.style.cssText = 'width:100%;height:80px;object-fit:cover;border-radius:4px;background:#222;';
              img.alt = m.name;
              const cap = document.createElement('div');
              cap.textContent = m.name;
              cap.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;width:100%;text-align:center;';
              card.appendChild(img);
              card.appendChild(cap);
              card.appendChild(cb);
              grid.appendChild(card);
            }
            input.appendChild(grid);
          })
          .catch(() => {
            input.innerHTML = '<div class="text-danger small">Failed to load media list.</div>';
          });
      } else if (type === 'folder_picker') {
        // Browses subdirectories under uploads/ on the server. Stored value
        // is a relative path string like 'images/vacation-2025'.
        col.className = 'col-md-12';
        input = document.createElement('div');
        input.className = 'border rounded p-2';
        input.id = id;
        input.dataset.fieldName = field.name;
        input.dataset.fieldType = 'folder_picker';
        input.dataset.value     = (defv === undefined || defv === null) ? '' : String(defv);

        var crumb = document.createElement('div');
        crumb.className = 'mb-2 small text-muted';
        var listEl = document.createElement('div');
        listEl.style.cssText = 'display:flex;flex-wrap:wrap;gap:.4rem;';
        var pathLabel = document.createElement('div');
        pathLabel.className = 'mt-2 small';
        pathLabel.style.fontFamily = 'monospace';
        input.appendChild(crumb);
        input.appendChild(listEl);
        input.appendChild(pathLabel);

        function _renderPicked() {
          var v = input.dataset.value || '';
          pathLabel.innerHTML = v
            ? 'Selected folder: <strong>uploads/' + v + '</strong> &nbsp;<button type="button" class="btn btn-sm btn-link p-0" data-clear>Clear</button>'
            : '<em>No folder selected (use the buttons above to pick one).</em>';
          var clr = pathLabel.querySelector('[data-clear]');
          if (clr) clr.addEventListener('click', function () {
            input.dataset.value = '';
            _renderPicked();
          });
        }
        function _loadFolder(path) {
          listEl.innerHTML = '<div class="text-muted small">Loading...</div>';
          fetch('/api/media/folders?path=' + encodeURIComponent(path), { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              listEl.innerHTML = '';
              if (data.status !== 'success') {
                listEl.innerHTML = '<div class="text-danger small">' + (data.message || 'Failed to load folders.') + '</div>';
                return;
              }
              // Breadcrumb: root + path segments + (Up) shortcut
              var parts = path ? path.split('/') : [];
              crumb.innerHTML = '';
              var rootBtn = document.createElement('a');
              rootBtn.href = '#'; rootBtn.textContent = 'uploads/'; rootBtn.className = 'me-1';
              rootBtn.addEventListener('click', function (e) { e.preventDefault(); _loadFolder(''); });
              crumb.appendChild(rootBtn);
              var acc = '';
              parts.forEach(function (seg, i) {
                acc += (i ? '/' : '') + seg;
                var sep = document.createElement('span'); sep.textContent = ' / '; crumb.appendChild(sep);
                var s = document.createElement('a'); s.href = '#'; s.textContent = seg;
                var captured = acc;
                s.addEventListener('click', function (e) { e.preventDefault(); _loadFolder(captured); });
                crumb.appendChild(s);
              });

              // "Use this folder" button always available (lets admin pick
              // the current dir even if it has no subfolders).
              var useBtn = document.createElement('button');
              useBtn.type = 'button';
              useBtn.className = 'btn btn-sm btn-primary';
              useBtn.textContent = path ? 'Use uploads/' + path : 'Use uploads/ (root)';
              useBtn.addEventListener('click', function () {
                input.dataset.value = path;
                _renderPicked();
              });
              listEl.appendChild(useBtn);

              if (!data.folders.length) {
                var none = document.createElement('div');
                none.className = 'text-muted small ms-2 align-self-center';
                none.textContent = '(no subfolders here)';
                listEl.appendChild(none);
              }
              data.folders.forEach(function (f) {
                var b = document.createElement('button');
                b.type = 'button';
                b.className = 'btn btn-sm btn-outline-secondary';
                b.innerHTML = '<i class="bi bi-folder"></i> ' + f.name + ' <span class="badge bg-secondary ms-1">' + f.image_count + '</span>';
                b.addEventListener('click', function () { _loadFolder(f.rel_path); });
                listEl.appendChild(b);
              });
            })
            .catch(function () {
              listEl.innerHTML = '<div class="text-danger small">Failed to load folders.</div>';
            });
        }
        // Start at the parent folder of the currently-selected value, if any
        var startAt = (input.dataset.value || '').split('/').slice(0, -1).join('/');
        _loadFolder(startAt);
        _renderPicked();
      } else {
        input = document.createElement('input');
        input.type = 'text';
        input.className = 'form-control';
        input.id = id;
        input.dataset.fieldName = field.name;
        input.value = defv !== undefined ? defv : '';
        if (field.placeholder) input.placeholder = field.placeholder;
      }

      wrapper.appendChild(lab);
      wrapper.appendChild(input);
      col.appendChild(wrapper);
      container.appendChild(col);
    }
  }

  function gatherConfig() {
    const fields = $all('#pluginConfigFields [id^="pcfg_"]');
    const cfg = {};
    for (const el of fields) {
      const name = el.dataset.fieldName;
      if (!name) continue;
      if (el.dataset.fieldType === 'media_multi') {
        // Comma-separated list of selected media IDs
        const picks = el.querySelectorAll('input[type="checkbox"][data-role="media-pick"]:checked');
        cfg[name] = Array.from(picks).map(cb => cb.value).join(',');
      } else if (el.dataset.fieldType === 'folder_picker') {
        cfg[name] = el.dataset.value || '';
      } else if (el.tagName === 'SELECT') {
        if (el.options.length === 2 && ['true', 'false'].includes(el.options[0].value)) {
          cfg[name] = (el.value === 'true');
        } else {
          cfg[name] = el.value;
        }
      } else if (el.type === 'number') {
        const v = el.value;
        cfg[name] = v === '' ? null : Number(v);
      } else {
        cfg[name] = el.value;
      }
    }
    return cfg;
  }

  async function addPluginItem() {
    const pluginType = $('#pluginType').value;
    const duration = Math.max(1, parseInt($('#pluginDuration').value || '30', 10));
    const plugin_config = gatherConfig();

    const res = await fetch(`/api/playlists/${PLAYLIST_ID}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        plugin_type: pluginType,
        plugin_config,
        duration
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      location.reload();
    } else {
      alert(`Failed to add plugin item: ${data.message || 'Unknown error'}`);
    }
  }

  async function updatePluginItem(itemId) {
    const plugin_config = gatherConfig();
    const duration = Math.max(1, parseInt($('#pluginDuration').value || '30', 10));
    const res = await fetch(`/api/playlists/${PLAYLIST_ID}/items/${itemId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        duration,
        plugin_config
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      location.reload();
    } else {
      alert(`Failed to update plugin item: ${data.message || 'Unknown error'}`);
    }
  }

  function openModalForCreate() {
    CURRENT_EDIT = null;
    $('#pluginItemModalLabel').textContent = 'Add Plugin Item';
    $('#pluginItemId').value = '';
    $('#pluginDuration').value = '30';
    populatePluginSelect();
    renderFieldsFor($('#pluginType').value, {});
    const modal = new bootstrap.Modal(document.getElementById('pluginItemModal'));
    modal.show();
  }

  function openModalForEdit(item) {
    CURRENT_EDIT = {
      itemId: item.dataset.itemId,
      plugin_type: item.dataset.pluginType,
      plugin_config: JSON.parse(item.dataset.pluginConfig || '{}'),
      duration: parseInt(item.dataset.duration || '30', 10)
    };
    $('#pluginItemModalLabel').textContent = 'Edit Plugin Item';
    $('#pluginItemId').value = CURRENT_EDIT.itemId;
    $('#pluginDuration').value = String(CURRENT_EDIT.duration || 30);
    populatePluginSelect(CURRENT_EDIT.plugin_type);
    renderFieldsFor(CURRENT_EDIT.plugin_type, CURRENT_EDIT.plugin_config);
    const modal = new bootstrap.Modal(document.getElementById('pluginItemModal'));
    modal.show();
  }

  async function init() {
    // Read playlist id from inline script
    PLAYLIST_ID = window.PLAYLIST_ID;

    await fetchRegistry();

    const addBtn = document.getElementById('btnAddPluginItem');
    if (addBtn) addBtn.addEventListener('click', openModalForCreate);

    const select = $('#pluginType');
    if (select) {
      select.addEventListener('change', () => {
        const meta = findPlugin(select.value);
        $('#pluginDescription').textContent = meta ? (meta.description || '') : '';
        renderFieldsFor(select.value, {});
      });
    }

    const saveBtn = $('#btnSavePluginItem');
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        const itemId = $('#pluginItemId').value;
        if (itemId) {
          await updatePluginItem(itemId);
        } else {
          await addPluginItem();
        }
      });
    }

    // Bind edit buttons for existing plugin items
    $all('.btn-edit-plugin').forEach(btn => {
      btn.addEventListener('click', () => openModalForEdit(btn));
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();