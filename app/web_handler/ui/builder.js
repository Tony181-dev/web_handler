'use strict';

const INSPECTOR_MIN_WIDTH = 320;
// Opens at 40% above the narrowest, and can grow 30% more from there.
const INSPECTOR_DEFAULT_WIDTH = INSPECTOR_MIN_WIDTH * 1.4;
const INSPECTOR_MAX_WIDTH = INSPECTOR_DEFAULT_WIDTH * 1.3;
// The block list opens 35% wider than it used to be, and can shrink back to that or grow 30% more.
const PALETTE_MIN_WIDTH = 128;
const PALETTE_DEFAULT_WIDTH = Math.round(PALETTE_MIN_WIDTH * 1.35);
const PALETTE_MAX_WIDTH = Math.round(PALETTE_DEFAULT_WIDTH * 1.3);
const PANEL_KEY_STEP = 16;
// How far the diagram can be zoomed, and by how much each step goes.
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2;
const ZOOM_STEP = 0.1;
// A mouse wheel notch is one zoom step; a touchpad's small moves add up to one first.
const WHEEL_STEP_PX = 50;
const HISTORY_LIMIT = 200;
const PASSWORD_SAVE_DELAY_MS = 300;
// The labels stay in English in every language, as the names of these modes.
const BROWSER_MODES = {
  visible: ['Browser: Visible', 'Run and Run once open the window and bring it to the front.'],
  offscreen: ['Browser: Offscreen', 'Run and Run once keep the window in the taskbar. Click it there to see it.'],
  headless: ['Browser: Headless', 'Run and Run once have no window at all. A "Manual step" block cannot be used.'],
};

class FlowEditor {
  constructor(id, flow) {
    this.id = id;
    this.flow = flow;
    this.selection = new Set();
    this.targetTab = 'element';
    this.pasted = {};
    this.pastedInfo = null;
    this.pagedElements = null;
    this.secretNames = [];
    this.sheets = [];
    this.excelPath = memory.get('excel.' + id) || '';
    this.sourceModes = {};
    this.pendingPassword = null;
    this.approvedSteps = new Set();
    this.litSteps = new Set();
    this.failedStep = null;
    this.save = debounce(() => this.persist(), 500);
    this.onKey = event => this.handleKey(event);
    this.browserMode = 'visible';
    // How the diagram is shown is a per-browser convenience, the same for every flow.
    this.view = memory.get('canvas.view') === 'vertical' ? 'vertical' : 'horizontal';
    this.zoom = clampZoom(parseFloat(memory.get('canvas.zoom')) || 1);
    this.resetHistory();
  }

  // History: what Undo and Redo move through, kept while this flow stays open.

  snapshot() {
    return JSON.stringify({ flow: this.flow, excelPath: this.excelPath });
  }

  resetHistory() {
    this.history = [this.snapshot()];
    this.historyIndex = 0;
    this.refreshHistoryButtons();
  }

  recordHistory() {
    const current = this.snapshot();
    if (current === this.history[this.historyIndex]) return;
    this.history.length = this.historyIndex + 1;
    this.history.push(current);
    if (this.history.length > HISTORY_LIMIT) this.history.shift();
    this.historyIndex = this.history.length - 1;
    this.refreshHistoryButtons();
  }

  refreshHistoryButtons() {
    if (!this.undoNode) return;
    const unrecorded = this.snapshot() !== this.history[this.historyIndex];
    this.undoNode.disabled = this.historyIndex === 0 && !unrecorded;
    this.redoNode.disabled = this.historyIndex === this.history.length - 1 || unrecorded;
  }

  moveInHistory(step) {
    this.save.cancel();
    this.recordHistory();
    const index = this.historyIndex + step;
    if (index < 0 || index >= this.history.length) return;
    this.historyIndex = index;
    const { flow, excelPath } = JSON.parse(this.history[index]);
    // The same object stays in use, as the name box and the panels hold on to it.
    Object.keys(this.flow).forEach(key => { delete this.flow[key]; });
    Object.assign(this.flow, flow);
    this.selection = new Set([...this.selection].filter(id => findStep(this.flow, id)));
    this.pastedInfo = null;
    this.pagedElements = null;
    this.pasted = {};
    this.failedStep = null;
    if (this.nameNode) this.nameNode.value = this.flow.name;
    this.setSaveState(t('Not saved'));
    this.save();
    if (excelPath !== this.excelPath) this.loadExcel(excelPath);
    else this.render();
  }

  async persist() {
    this.recordHistory();
    this.setSaveState(t('Saving...'));
    try {
      await api('PUT', '/api/flows/' + this.id, this.flow);
      this.setSaveState(t('Saved'));
    } catch (error) {
      this.setSaveState(t('Not saved'));
      showError(error);
    }
  }

  setSaveState(text) {
    if (this.saveStateNode) this.saveStateNode.textContent = text;
  }

  // Selection

  select(id) {
    if (this.selection.size !== 1 || !this.selection.has(id)) {
      this.pastedInfo = null;
      this.pagedElements = null;
      this.pasted = {};
    }
    this.selection = new Set(id ? [id] : []);
    this.render();
  }

  selectMany(ids) {
    this.selection = new Set(ids);
    this.render();
  }

  selectAll() {
    const ids = allBlockIds(this.flow);
    if (ids.length === 1) this.select(ids[0]);
    else if (ids.length) this.selectMany(ids);
  }

  // Shift+click: the block joins the selection, or leaves it if it was in. The loop is only ever selected alone.
  toggleSelect(id) {
    const step = findStep(this.flow, id);
    const loop = findLoop(this.flow);
    if (!step || step.do === 'loop' || (loop && this.selection.has(loop.id))) { this.select(id); return; }
    const ids = new Set(this.selection);
    if (ids.has(id)) ids.delete(id);
    else ids.add(id);
    if (ids.size <= 1) this.select(ids.size ? [...ids][0] : null);
    else this.selectMany([...ids]);
  }

  // A typed password waits here only until it is saved, and is never part of the flow or its history.

  typePassword(name, value) {
    this.pendingPassword = { name, value };
    clearTimeout(this.passwordTimer);
    this.passwordTimer = setTimeout(() => this.flushPassword(), PASSWORD_SAVE_DELAY_MS);
  }

  async flushPassword() {
    clearTimeout(this.passwordTimer);
    const pending = this.pendingPassword;
    this.pendingPassword = null;
    if (!pending) return;
    try {
      await api('POST', '/api/secrets', pending);
      await this.loadSecrets();
    } catch (error) { showError(error); }
  }

  // Changes

  changed() {
    this.recordHistory();
    this.setSaveState(t('Not saved'));
    this.save();
    this.render();
  }

  changedQuietly() {
    // Typing must not rebuild the form under the cursor; only the diagram is redrawn.
    this.setSaveState(t('Not saved'));
    this.save();
    renderCanvas(this);
    this.showRowCount();
    this.refreshHistoryButtons();
  }

  makeStep(kind) {
    const step = { id: nextStepId(this.flow), do: kind };
    if (['fill', 'click', 'select', 'check', 'read', 'ocr', 'wait_for'].includes(kind)) {
      step.target = { primary: '', fallbacks: [], frames: [], verified: false };
    }
    if (kind === 'fill' || kind === 'select') step.value = '';
    if (kind === 'click') step.wait_navigation = false;
    if (kind === 'check') step.checked = true;
    if (kind === 'goto') step.url = '';
    if (kind === 'login') { step.account = ''; step.password = ''; }
    if (kind === 'verify') { step.target = { primary: '', fallbacks: [], frames: [], verified: false }; step.equals = ''; }
    if (kind === 'if') {
      Object.assign(step, { test: 'exists', negate: false, target: { primary: '', fallbacks: [], frames: [], verified: false }, value: '', then: [], else: [] });
    }
    if (kind === 'manual_pause') step.message = '';
    if (kind === 'wait') step.seconds = DEFAULT_WAIT_SECONDS;
    if (kind === 'read' || kind === 'ocr') step.save_to_var = '';
    return step;
  }

  addLoop(index = this.flow.steps.length) {
    const problem = findLoop(this.flow) ? t('A flow can have only one loop.') : '';
    if (problem) { toast(problem, 'bad'); return; }
    const loop = {
      id: nextStepId(this.flow), do: 'loop',
      source: this.excelPath || this.flow.excel ? 'excel' : 'count',
      count: 1, steps: [],
    };
    this.flow.steps.splice(index, 0, loop);
    if (loop.source === 'excel') this.ensureExcelSettings();
    this.select(loop.id);
    this.changed();
  }

  addStep(kind) {
    if (kind === 'loop') { this.addLoop(); return; }
    const step = this.makeStep(kind);
    this.flow.steps.push(step);
    this.select(step.id);
    this.changed();
  }

  insertSteps(steps) {
    for (const step of steps) {
      step.id = nextStepId(this.flow);
      this.flow.steps.push(step);
    }
    this.selection = new Set();
    this.changed();
  }

  duplicateSelection() {
    // Every list a block can sit in. A list inside a chosen "If" block is copied with it.
    const lists = [this.flow.steps, ...flattenSteps(this.flow.steps)
      .filter(step => !this.selection.has(step.id))
      .flatMap(branchesOf)];
    const copies = [];
    for (const list of lists) {
      const chosen = list.filter(step => this.selection.has(step.id) && step.do !== 'loop');
      if (!chosen.length) continue;
      let position = list.indexOf(chosen[chosen.length - 1]) + 1;
      for (const step of chosen) {
        const copy = JSON.parse(JSON.stringify(step));
        list.splice(position, 0, copy);
        position += 1;
        // Numbered once in the flow, so each id taken is seen by the next.
        for (const inner of flattenSteps([copy])) inner.id = nextStepId(this.flow);
        copies.push(copy.id);
      }
    }
    if (copies.length === 1) this.select(copies[0]);
    else this.selectMany(copies);
    this.changed();
  }

  deleteSelection() {
    for (const id of this.selection) {
      const list = listContaining(this.flow, id);
      if (list) {
        const index = list.findIndex(step => step.id === id);
        if (index >= 0) list.splice(index, 1);
      }
    }
    this.selection = new Set();
    this.changed();
  }

  isEmpty() {
    return !this.flow.steps.length && !this.flow.excel && !this.excelPath;
  }

  blockCount() {
    return flattenSteps(this.flow.steps).filter(step => step.do !== 'loop').length;
  }

  async clearAll() {
    const message = t('Deletes {count} block(s), the loop and the Excel setup of this flow. The Excel file and Secrets stay as they are. Undo brings it back while this flow stays open.', { count: this.blockCount() });
    if (!await confirmBox(t('Clear the whole flow?'), message, t('Clear all'), 'danger')) return;
    this.flow.steps = [];
    this.flow.excel = null;
    this.excelPath = '';
    this.sheets = [];
    memory.set('excel.' + this.id, '');
    this.select(null);
    this.changed();
  }

  handleKey(event) {
    const tag = document.activeElement && document.activeElement.tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
    if (document.querySelector('.modal-back')) return;
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && (key === 'z' || key === 'y')) {
      event.preventDefault();
      this.moveInHistory(key === 'y' || event.shiftKey ? 1 : -1);
      return;
    }
    // Ctrl+A takes every block rather than marking all the text on the page.
    if ((event.ctrlKey || event.metaKey) && key === 'a' && !event.altKey) {
      event.preventDefault();
      this.selectAll();
      return;
    }
    // The arrow keys move the view, unless a control that uses them has the focus.
    if (event.key.startsWith('Arrow') && !(event.ctrlKey || event.metaKey || event.altKey)
        && !(document.activeElement && document.activeElement.closest('.inspector, .panel-resize, [role=tablist], .modal'))) {
      event.preventDefault();
      panByKey(this, event.key);
      return;
    }
    // A Mac keyboard's delete key is Backspace.
    if (event.key !== 'Delete' && !(IS_MAC && event.key === 'Backspace')) return;
    if (this.selection.size) {
      const only = this.selection.size === 1 ? findStep(this.flow, [...this.selection][0]) : null;
      if (only && only.do === 'loop') return;
      this.deleteSelection();
    }
  }

  // Information for the forms

  ensureExcelSettings() {
    if (!this.flow.excel) {
      this.flow.excel = { sheet: '', start_row: 2, end_row: null, required: [], status_column: null };
    }
    return this.flow.excel;
  }

  currentSheet() {
    const name = this.flow.excel && this.flow.excel.sheet;
    return this.sheets.find(sheet => sheet.name === name) || null;
  }

  availableColumns() {
    const sheet = this.currentSheet();
    if (sheet) return sheet.columns;
    return columnLetters(8).map(column => ({ column, header: '' }));
  }

  variableNames() {
    const names = [];
    flattenSteps(this.flow.steps).forEach(step => {
      if (step.save_to_var) names.push(step.save_to_var);
    });
    return [...new Set(names)];
  }

  async loadSecrets() {
    try {
      const { names } = await api('GET', '/api/secrets');
      this.secretNames = names;
    } catch (error) { this.secretNames = []; }
  }

  async loadExcel(path) {
    this.excelPath = path;
    memory.set('excel.' + this.id, path);
    this.sheets = [];
    if (!path) { this.render(); return; }
    try {
      const info = await api('POST', '/api/excel', { path });
      this.sheets = info.sheets;
      const settings = this.ensureExcelSettings();
      if (!this.sheets.some(sheet => sheet.name === settings.sheet)) {
        settings.sheet = this.sheets[0] ? this.sheets[0].name : '';
        this.save();
      }
    } catch (error) {
      showError(error);
    }
    this.recordHistory();
    this.render();
  }

  // Rendering

  render() {
    renderCanvas(this);
    this.renderInspector();
    this.renderRunbar();
    this.clearNode.disabled = this.isEmpty();
  }

  renderInspector() {
    renderProperties(this);
  }

  renderRunbar() {
    const bar = this.runbarNode;
    const loop = findLoop(this.flow);
    const parts = [];
    // The file itself is chosen in the canvas's bottom left corner, level with the Log button. It stays
    // there for a loop counted by hand too, as choosing a file is how such a loop turns to the Excel rows.
    const name = this.excelPath ? this.excelPath.split(/[\\/]/).pop() : t('No Excel file selected');
    const nameNode = el('span.file-name' + (this.excelPath ? '' : '.empty'), { id: 'excel-name', text: name });
    const corner = [
      el('button.btn', { id: 'pick-excel', text: t('Select Excel'), onclick: () => this.pickExcel() }),
      this.excelPath ? attachHelp(nameNode, this.excelPath) : nameNode,
    ];
    if (!loop || loop.source === 'excel') {
      const settings = this.flow.excel || {};
      if (this.sheets.length) {
        const sheetSelect = el('select', {
          id: 'excel-sheet', 'aria-label': 'Sheet',
          onchange: () => {
            this.ensureExcelSettings().sheet = sheetSelect.value;
            this.changed();
          },
        }, this.sheets.map(sheet => el('option', { value: sheet.name, text: sheet.name, selected: sheet.name === settings.sheet })));
        const start = el('input', {
          type: 'number', id: 'start-row', min: '1', value: settings.start_row || 2, 'aria-label': t('From row'),
          oninput: () => {
            this.ensureExcelSettings().start_row = Math.max(1, parseInt(start.value, 10) || 1);
            this.save();
            this.showRowCount();
          },
        });
        const end = el('input', {
          type: 'number', id: 'end-row', min: '1', value: settings.end_row || '', placeholder: t('end'), 'aria-label': t('To row'),
          oninput: () => {
            this.ensureExcelSettings().end_row = parseInt(end.value, 10) || null;
            this.save();
            this.showRowCount();
          },
        });
        this.rowCountNode = el('span.count-pill', { id: 'row-count' });
        parts.push(sheetSelect, el('span', { text: t('Row') }), start, el('span', { text: t('to') }), end, this.rowCountNode);
      }
    } else {
      this.rowCountNode = null;
      const count = el('input', {
        type: 'number', id: 'run-count', min: '1', value: loop.count || 1, 'aria-label': t('Number of runs'),
        oninput: () => {
          loop.count = Math.max(1, parseInt(count.value, 10) || 1);
          this.save();
          renderCanvas(this);
        },
      });
      parts.push(el('span', { text: t('Number of runs') }), count);
    }
    this.saveStateNode = el('span.save-state', { text: this.saveStateNode ? this.saveStateNode.textContent : '' });
    const [modeLabel, modeHelp] = BROWSER_MODES[this.browserMode];
    const modeButton = el('button.btn', {
      id: 'browser-mode', text: modeLabel, title: t(modeHelp),
      onclick: () => this.nextBrowserMode(),
    });
    this.undoNode = el('button.btn', { id: 'undo', text: t('↶ Undo'), title: t('Undo the last change (Ctrl+Z)'), onclick: () => this.moveInHistory(-1) });
    this.redoNode = el('button.btn', { id: 'redo', text: t('↷ Redo'), title: t('Redo the change (Ctrl+Y)'), onclick: () => this.moveInHistory(1) });
    this.refreshHistoryButtons();
    parts.push(
      el('span.spacer'),
      this.saveStateNode,
      modeButton,
      keepBrowserButton(this.browserMode),
      this.undoNode,
      this.redoNode,
      el('button.btn', { id: 'run-trial', text: t('Run once'), onclick: () => startRunFlow(this, 'trial') }),
      el('button.btn.primary', { id: 'run-full', text: t('▶ Run'), onclick: () => startRunFlow(this, 'full') }));
    bar.replaceChildren(...parts);
    this.excelCornerNode.replaceChildren(...corner);
    this.showRowCount();
  }

  async nextBrowserMode() {
    const modes = Object.keys(BROWSER_MODES);
    const wanted = modes[(modes.indexOf(this.browserMode) + 1) % modes.length];
    try {
      this.browserMode = (await api('PUT', '/api/browser-mode', { mode: wanted })).mode;
      this.renderRunbar();
    } catch (error) { showError(error); }
  }

  // How the diagram is shown

  toggleView() {
    this.view = this.view === 'vertical' ? 'horizontal' : 'vertical';
    memory.set('canvas.view', this.view);
    renderCanvas(this);
    this.renderCanvasTools();
  }

  // anchor, a pointer event, is the point that stays where it is; without one the diagram grows from its top left.
  setZoom(wanted, anchor) {
    const before = this.zoom;
    this.zoom = clampZoom(wanted);
    memory.set('canvas.zoom', String(this.zoom));
    // Only the size changes, so the blocks are not drawn again.
    const content = document.getElementById('canvas-content');
    if (content) {
      const box = content.getBoundingClientRect();
      content.style.zoom = String(this.zoom);
      if (anchor && this.zoom !== before) {
        const grown = this.zoom / before - 1;
        this.canvasNode.scrollLeft += (anchor.clientX - box.left) * grown;
        this.canvasNode.scrollTop += (anchor.clientY - box.top) * grown;
      }
    }
    this.renderCanvasTools();
  }

  // The wheel zooms the diagram around the pointer. Shift + wheel scrolls it up and down instead,
  // and a sideways touchpad swipe still scrolls it sideways.
  onWheel(event) {
    if (event.shiftKey && !event.ctrlKey) {
      event.preventDefault();
      this.canvasNode.scrollTop += event.deltaY || event.deltaX;
      return;
    }
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
    event.preventDefault();
    const delta = event.deltaY * (event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 800 : 1);
    // Turning the other way starts the count again.
    if (Math.sign(delta) !== Math.sign(this.wheelRest || 0)) this.wheelRest = 0;
    this.wheelRest += delta;
    if (Math.abs(this.wheelRest) < WHEEL_STEP_PX) return;
    this.wheelRest = 0;
    this.setZoom(this.zoom + (delta < 0 ? ZOOM_STEP : -ZOOM_STEP), event);
  }

  resetZoom() {
    this.setZoom(1);
    panHome(this);
  }

  renderCanvasTools() {
    const vertical = this.view === 'vertical';
    this.canvasToolsNode.replaceChildren(
      el('button.btn.small', {
        id: 'view-toggle', text: vertical ? t('⇅ Vertical') : t('⇄ Horizontal'),
        title: vertical ? t('Show the blocks from left to right') : t('Show the blocks from top to bottom'),
        'aria-pressed': String(vertical), onclick: () => this.toggleView(),
      }),
      el('button.btn.small', { id: 'zoom-out', text: '−', title: t('Zoom out (mouse wheel down)'), 'aria-label': t('Zoom out'), disabled: this.zoom <= ZOOM_MIN, onclick: () => this.setZoom(this.zoom - ZOOM_STEP) }),
      el('span.zoom-level', { id: 'zoom-level', text: `${Math.round(this.zoom * 100)}%`, title: t('Mouse wheel zooms. Shift + mouse wheel scrolls.') }),
      el('button.btn.small', { id: 'zoom-in', text: '+', title: t('Zoom in (mouse wheel up)'), 'aria-label': t('Zoom in'), disabled: this.zoom >= ZOOM_MAX, onclick: () => this.setZoom(this.zoom + ZOOM_STEP) }),
      el('button.btn.small', { id: 'zoom-reset', text: t('⟲ Reset'), title: t('Back to 100% and to the start of the diagram'), onclick: () => this.resetZoom() }));
  }

  showRowCount() {
    if (!this.rowCountNode) return;
    const settings = this.flow.excel || {};
    const sheet = this.currentSheet();
    if (!sheet) { this.rowCountNode.textContent = ''; return; }
    if (!settings.end_row) {
      this.rowCountNode.textContent = runsUntilText(stopColumn(this.flow));
      this.rowCountNode.title = this.rowCountNode.textContent;
      return;
    }
    const start = settings.start_row || 2;
    this.rowCountNode.textContent = t('up to {count} run(s)', { count: Math.max(0, settings.end_row - start + 1) });
  }

  async pickExcel() {
    try {
      const { path } = await api('POST', '/api/dialog', {}, { timeout: 600000 });
      if (path) {
        await this.useExcel(path);
        return;
      }
    } catch (error) { showError(error); }
    if (!this.excelPath) this.askExcelPath();
  }

  async askExcelPath() {
    const path = await promptBox(t('Excel file path'), t('Paste the full path'), this.excelPath, t('The file dialog was closed or could not open. You can paste the path here.'));
    if (path) await this.useExcel(path.replace(/^"|"$/g, ''));
  }

  // A file chosen for a loop counted by hand turns the loop to the file's rows, which is what a file is for.
  async useExcel(path) {
    const loop = findLoop(this.flow);
    if (loop && loop.source === 'count') {
      loop.source = 'excel';
      this.ensureExcelSettings();
      this.changed();
      toast(t('The loop now runs once for each Excel row.'));
    }
    await this.loadExcel(path);
  }
}

function clampZoom(value) {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 10) / 10));
}

// A column beside the canvas that can be dragged wider or narrower. The width is a per-browser convenience.
// grows is 1 when dragging to the right widens it, -1 when dragging to the left does.
function panelResizer(builder, { id, property, storageKey, min, start, max, grows, label }) {
  let width = 0;
  const setWidth = wanted => {
    width = Math.min(max, Math.max(min, wanted));
    builder.style.setProperty(property, `${width}px`);
    memory.set(storageKey, String(width));
  };
  setWidth(parseFloat(memory.get(storageKey)) || start);
  const handle = el('div.panel-resize', {
    id, role: 'separator', 'aria-orientation': 'vertical', tabindex: '0', 'aria-label': label,
    onkeydown: event => {
      if (event.key === 'ArrowRight') setWidth(width + grows * PANEL_KEY_STEP);
      else if (event.key === 'ArrowLeft') setWidth(width - grows * PANEL_KEY_STEP);
    },
  });
  handle.addEventListener('pointerdown', event => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = moveEvent => setWidth(startWidth + grows * (moveEvent.clientX - startX));
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  });
  return handle;
}

// The side panel can shrink to its narrowest or grow 30% past where it opens.
function inspectorResizer(builder) {
  // A new name, so a width kept under the old default does not hold the new one back.
  return panelResizer(builder, {
    id: 'inspector-resize', property: '--inspector-width', storageKey: 'inspector.panel-width',
    min: INSPECTOR_MIN_WIDTH, start: INSPECTOR_DEFAULT_WIDTH, max: INSPECTOR_MAX_WIDTH, grows: -1,
    label: t('Resize the side panel'),
  });
}

function paletteResizer(builder) {
  return panelResizer(builder, {
    id: 'palette-resize', property: '--palette-width', storageKey: 'palette.width',
    min: PALETTE_MIN_WIDTH, start: PALETTE_DEFAULT_WIDTH, max: PALETTE_MAX_WIDTH, grows: 1,
    label: t('Resize the block list'),
  });
}

async function renderBuilder(id) {
  const view = document.getElementById('view');
  const { flow } = await api('GET', '/api/flows/' + id);
  const editor = new FlowEditor(id, flow);
  window.currentEditor = editor;
  window.onSecretsChanged = async () => {
    await editor.loadSecrets();
    editor.renderInspector();
  };

  const nameInput = el('input', {
    type: 'text', id: 'flow-name', value: flow.name, 'aria-label': t('Flow name'),
    oninput: () => {
      flow.name = nameInput.value;
      editor.save();
    },
  });
  editor.nameNode = nameInput;
  setTopbarTitle(nameInput);

  editor.canvasNode = el('div.canvas', { id: 'canvas' });
  // Any press in the diagram ends text marked on the page, even one a block takes for itself.
  editor.canvasNode.addEventListener('pointerdown', () => {
    const marked = window.getSelection();
    if (marked && !marked.isCollapsed) marked.removeAllRanges();
  }, true);
  editor.canvasNode.addEventListener('pointerdown', event => startCanvasPointer(event, editor));
  // The room around the diagram follows the size of the view, also once the view first gets one.
  new ResizeObserver(() => fitPanRoom(editor)).observe(editor.canvasNode);
  // The middle button moves the view rather than starting the browser's own scrolling.
  editor.canvasNode.addEventListener('mousedown', event => { if (event.button === 1) event.preventDefault(); });
  // Not passive, so that the wheel zooms the diagram rather than scrolling it or zooming the whole app.
  editor.canvasNode.addEventListener('wheel', event => editor.onWheel(event), { passive: false });
  editor.canvasToolsNode = el('div.canvas-tools', { id: 'canvas-tools' });
  editor.inspectorNode = el('aside.inspector', { id: 'inspector' });
  editor.runbarNode = el('div.runbar', { id: 'runbar' });
  editor.excelCornerNode = el('div.canvas-excel', { id: 'canvas-excel' });
  editor.clearNode = el('button.btn.danger', { id: 'clear-all', text: t('Clear all'), style: 'width:100%;margin-top:6px', onclick: () => editor.clearAll() });

  attachHelp(editor.clearNode, t('Deletes every block, the loop and the Excel setup of this flow.'));

  const palette = el('nav.palette', { 'aria-label': t('Block types') },
    el('div.section-label', { text: t('Drag blocks') }),
    PALETTE.map(kind => {
      const tool = el('div.tool', { 'data-kind': kind, text: STEP_NAMES[kind], tabindex: '0', role: 'button' });
      attachHelp(tool, t(STEP_HELP[kind]));
      tool.addEventListener('pointerdown', event => startPaletteDrag(event, editor, kind));
      tool.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          editor.addStep(kind);
        }
      });
      return tool;
    }),
    el('div', { style: 'height:10px' }),
    attachHelp(el('button.btn.record', { id: 'start-record', text: t('● Record'), style: 'width:100%', onclick: () => startRecording(editor) }), t('Do the work once by hand in Chrome, and the tool turns it into blocks.')),
    editor.clearNode);

  const builder = el('div.builder', null, palette, editor.canvasNode, editor.inspectorNode, editor.runbarNode);
  builder.append(paletteResizer(builder), inspectorResizer(builder), editor.canvasToolsNode, editor.excelCornerNode, el('div.canvas-log', null, logButton()));
  view.replaceChildren(builder);

  document.addEventListener('keydown', editor.onKey);
  window.leaveView = () => {
    document.removeEventListener('keydown', editor.onKey);
    editor.flushPassword();
    editor.save.flush();
    window.leaveView = null;
    window.onSecretsChanged = null;
    window.currentEditor = null;
    hideHelp();
  };

  await editor.loadSecrets();
  editor.browserMode = await api('GET', '/api/browser-mode').then(data => data.mode).catch(() => 'visible');
  editor.renderCanvasTools();
  editor.render();
  if (editor.excelPath) await editor.loadExcel(editor.excelPath);
  // Filling in what the open Excel file shows is not something to undo.
  editor.resetHistory();
}
