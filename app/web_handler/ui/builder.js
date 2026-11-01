'use strict';

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
    this.setSaveState('Đang lưu...');
    try {
      await api('PUT', '/api/flows/' + this.id, this.flow);
      this.setSaveState('Đã lưu');
    } catch (error) {
      this.setSaveState('Chưa lưu');
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
    this.setSaveState('Chưa lưu');
    this.save();
    this.render();
  }

  changedQuietly() {
    // Typing must not rebuild the form under the cursor; only the diagram is redrawn.
    this.setSaveState('Chưa lưu');
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
    if (kind === 'verify') { step.target = { primary: '', fallbacks: [], frames: [], verified: false }; step.equals = ''; }
    if (kind === 'manual_pause') step.message = '';
    if (kind === 'wait') step.seconds = DEFAULT_WAIT_SECONDS;
    if (kind === 'read') step.save_to_var = '';
    return step;
  }

  addStep(kind) {
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
    const message = `Xóa ${this.blockCount()} khối, vùng lặp và cấu hình Excel của quy trình này. Không hoàn tác được. File Excel và mục Secrets vẫn giữ nguyên.`;
    if (!await confirmBox('Xóa toàn bộ quy trình?', message, 'Clear all', 'danger')) return;
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
      const name = this.excelPath ? this.excelPath.split(/[\\/]/).pop() : 'Chưa chọn file Excel';
      parts.push(
        el('button.btn', { id: 'pick-excel', text: 'Select Excel', onclick: () => this.pickExcel() }),
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
          type: 'number', id: 'start-row', min: '1', value: settings.start_row || 2, 'aria-label': 'Từ hàng',
          oninput: () => {
            this.ensureExcelSettings().start_row = Math.max(1, parseInt(start.value, 10) || 1);
            this.save();
            this.showRowCount();
          },
        });
        const end = el('input', {
          type: 'number', id: 'end-row', min: '1', value: settings.end_row || '', placeholder: 'hết', 'aria-label': 'Đến hàng',
          oninput: () => {
            this.ensureExcelSettings().end_row = parseInt(end.value, 10) || null;
            this.save();
            this.showRowCount();
          },
        });
        this.rowCountNode = el('span.count-pill', { id: 'row-count' });
        parts.push(sheetSelect, el('span', { text: 'Hàng' }), start, el('span', { text: 'đến' }), end, this.rowCountNode);
      }
    } else {
      this.rowCountNode = null;
      const count = el('input', {
        type: 'number', id: 'run-count', min: '1', value: loop.count || 1, 'aria-label': 'Số lần chạy',
        oninput: () => {
          loop.count = Math.max(1, parseInt(count.value, 10) || 1);
          this.save();
          renderCanvas(this);
        },
      });
      parts.push(el('span', { text: 'Số lần chạy' }), count);
    }
    this.saveStateNode = el('span.save-state', { text: this.saveStateNode ? this.saveStateNode.textContent : '' });
    parts.push(
      el('span.spacer'),
      this.saveStateNode,
      el('button.btn', { id: 'run-trial', text: 'Run once', onclick: () => startRunFlow(this, 'trial') }),
      el('button.btn.primary', { id: 'run-full', text: '▶ Run', onclick: () => startRunFlow(this, 'full') }));
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
    this.rowCountNode.textContent = `tối đa ${Math.max(0, settings.end_row - start + 1)} lần`;
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
    const path = await promptBox('Đường dẫn file Excel', 'Dán đường dẫn đầy đủ', this.excelPath, 'Hộp thoại chọn file bị đóng hoặc không mở được. Bạn có thể dán đường dẫn vào đây.');
    if (path) await this.loadExcel(path.replace(/^"|"$/g, ''));
  }
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
    type: 'text', id: 'flow-name', value: flow.name, 'aria-label': 'Tên quy trình',
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
  editor.clearNode = el('button.btn.danger', { id: 'clear-all', text: 'Clear all', title: 'Xóa toàn bộ khối, vùng lặp và cấu hình Excel', style: 'width:100%;margin-top:6px', onclick: () => editor.clearAll() });

  const palette = el('nav.palette', { 'aria-label': 'Các loại khối' },
    el('div.section-label', { text: 'Kéo thả khối' }),
    PALETTE.map(kind => {
      const tool = el('div.tool', { 'data-kind': kind, text: STEP_NAMES[kind], tabindex: '0', role: 'button' });
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
    el('button.btn.record', { id: 'start-record', text: '● Record', title: 'Ghi thao tác', style: 'width:100%', onclick: () => startRecording(editor) }),
    editor.clearNode,
    logButton());

  view.replaceChildren(el('div.builder', null, palette, editor.canvasNode, editor.inspectorNode, editor.runbarNode));

  document.addEventListener('keydown', editor.onKey);
  window.leaveView = () => {
    document.removeEventListener('keydown', editor.onKey);
    editor.save.flush();
    window.leaveView = null;
    window.onSecretsChanged = null;
    window.currentEditor = null;
  };

  await editor.loadSecrets();
  editor.render();
  if (editor.excelPath) await editor.loadExcel(editor.excelPath);
}
