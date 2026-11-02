'use strict';

const INSPECTOR_MIN_WIDTH = 320;
const INSPECTOR_MAX_WIDTH = INSPECTOR_MIN_WIDTH * 1.4;
const INSPECTOR_KEY_STEP = 16;

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
    this.approvedSteps = new Set();
    this.litSteps = new Set();
    this.failedStep = null;
    this.save = debounce(() => this.persist(), 500);
    this.onKey = event => this.handleKey(event);
  }

  async persist() {
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

  // Changes

  changed() {
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
  }

  makeStep(kind) {
    const step = { id: nextStepId(this.flow), do: kind };
    if (['fill', 'click', 'select', 'check', 'read', 'wait_for'].includes(kind)) {
      step.target = { primary: '', fallbacks: [], frames: [], verified: false };
    }
    if (kind === 'fill' || kind === 'select') step.value = '';
    if (kind === 'click') step.wait_navigation = false;
    if (kind === 'check') step.checked = true;
    if (kind === 'goto') step.url = '';
    if (kind === 'login') { step.account = ''; step.password = ''; }
    if (kind === 'verify') { step.target = { primary: '', fallbacks: [], frames: [], verified: false }; step.equals = ''; }
    if (kind === 'manual_pause') step.message = '';
    if (kind === 'wait') step.seconds = DEFAULT_WAIT_SECONDS;
    if (kind === 'read') step.save_to_var = '';
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
    return this.flow.steps.reduce((total, step) => total + (step.steps ? step.steps.length : 1), 0);
  }

  async clearAll() {
    const message = t('Deletes {count} block(s), the loop and the Excel setup of this flow. This cannot be undone. The Excel file and Secrets stay as they are.', { count: this.blockCount() });
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
    if (event.key !== 'Delete') return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
    if (document.querySelector('.modal-back')) return;
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
    const visit = steps => steps.forEach(step => {
      if (step.save_to_var) names.push(step.save_to_var);
      if (step.steps) visit(step.steps);
    });
    visit(this.flow.steps);
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
    this.render();
  }

  // Rendering

  render() {
    renderCanvas(this);
    this.renderInspector();
    this.renderRunbar();
    this.clearNode.disabled = this.isEmpty();
    this.openExcelNode.disabled = !this.excelPath;
  }

  renderInspector() {
    renderProperties(this);
  }

  renderRunbar() {
    const bar = this.runbarNode;
    const loop = findLoop(this.flow);
    const parts = [];
    if (!loop || loop.source === 'excel') {
      const settings = this.flow.excel || {};
      const name = this.excelPath ? this.excelPath.split(/[\\/]/).pop() : t('No Excel file selected');
      parts.push(
        el('button.btn', { id: 'pick-excel', text: t('Select Excel'), onclick: () => this.pickExcel() }),
        el('span.file-name', { id: 'excel-name', text: name, title: this.excelPath }));
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
    parts.push(
      el('span.spacer'),
      this.saveStateNode,
      el('button.btn', { id: 'run-trial', text: t('Run once'), onclick: () => startRunFlow(this, 'trial') }),
      el('button.btn.primary', { id: 'run-full', text: t('▶ Run'), onclick: () => startRunFlow(this, 'full') }));
    bar.replaceChildren(...parts);
    this.showRowCount();
  }

  showRowCount() {
    if (!this.rowCountNode) return;
    const settings = this.flow.excel || {};
    const sheet = this.currentSheet();
    if (!sheet) { this.rowCountNode.textContent = ''; return; }
    if (!settings.end_row) {
      this.rowCountNode.textContent = runsUntilText(stopColumn(this.flow));
      return;
    }
    const start = settings.start_row || 2;
    this.rowCountNode.textContent = t('up to {count} run(s)', { count: Math.max(0, settings.end_row - start + 1) });
  }

  async pickExcel() {
    try {
      const { path } = await api('POST', '/api/dialog', {}, { timeout: 600000 });
      if (path) {
        await this.loadExcel(path);
        return;
      }
    } catch (error) { showError(error); }
    if (!this.excelPath) this.askExcelPath();
  }

  async askExcelPath() {
    const path = await promptBox(t('Excel file path'), t('Paste the full path'), this.excelPath, t('The file dialog was closed or could not open. You can paste the path here.'));
    if (path) await this.loadExcel(path.replace(/^"|"$/g, ''));
  }
}

// The side panel can grow from its normal size, up to 40% wider. The width is a per-browser convenience.
function inspectorResizer(builder) {
  let width = 0;
  const setWidth = wanted => {
    width = Math.min(INSPECTOR_MAX_WIDTH, Math.max(INSPECTOR_MIN_WIDTH, wanted));
    builder.style.setProperty('--inspector-width', `${width}px`);
    memory.set('inspector.width', String(width));
  };
  setWidth(parseFloat(memory.get('inspector.width')) || INSPECTOR_MIN_WIDTH);
  const handle = el('div.inspector-resize', {
    id: 'inspector-resize', role: 'separator', 'aria-orientation': 'vertical', tabindex: '0',
    'aria-label': t('Resize the side panel'),
    onkeydown: event => {
      if (event.key === 'ArrowLeft') setWidth(width + INSPECTOR_KEY_STEP);
      else if (event.key === 'ArrowRight') setWidth(width - INSPECTOR_KEY_STEP);
    },
  });
  handle.addEventListener('pointerdown', event => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = moveEvent => setWidth(startWidth + startX - moveEvent.clientX);
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  });
  return handle;
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
  setTopbarTitle(nameInput);

  editor.canvasNode = el('div.canvas', { id: 'canvas' });
  editor.canvasNode.addEventListener('pointerdown', event => startLasso(event, editor));
  editor.inspectorNode = el('aside.inspector', { id: 'inspector' });
  editor.runbarNode = el('div.runbar', { id: 'runbar' });
  editor.clearNode = el('button.btn.danger', { id: 'clear-all', text: t('Clear all'), style: 'width:100%;margin-top:6px', onclick: () => editor.clearAll() });

  editor.openExcelNode = attachHelp(el('button.btn', {
    id: 'open-excel', text: t('Open Excel'),
    onclick: () => api('POST', '/api/excel/open', { path: editor.excelPath }).catch(showError),
  }), t('Opens the chosen Excel file in Windows.'));
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
    editor.clearNode,
    el('div.palette-foot', null, logButton(), editor.openExcelNode));

  const builder = el('div.builder', null, palette, editor.canvasNode, editor.inspectorNode, editor.runbarNode);
  builder.append(inspectorResizer(builder));
  view.replaceChildren(builder);

  document.addEventListener('keydown', editor.onKey);
  window.leaveView = () => {
    document.removeEventListener('keydown', editor.onKey);
    editor.save.flush();
    window.leaveView = null;
    window.onSecretsChanged = null;
    window.currentEditor = null;
    hideHelp();
  };

  await editor.loadSecrets();
  editor.render();
  if (editor.excelPath) await editor.loadExcel(editor.excelPath);
}
