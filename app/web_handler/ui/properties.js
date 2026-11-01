'use strict';

// The right-hand panel: what the selected block does and which element it acts on.

function field(label, control, hint) {
  return el('div.field', null, el('label.field-label', { text: label }), control, hint ? el('div.hint', { text: hint }) : null);
}

function textField(editor, step, key, label, options) {
  const input = el('input', {
    type: 'text',
    class: options && options.mono ? 'mono' : null,
    value: step[key] || '',
    placeholder: options && options.placeholder,
    'data-field': key,
    oninput: () => {
      step[key] = input.value;
      editor.changedQuietly();
    },
  });
  return field(label, input, options && options.hint);
}

// Where a value comes from is chosen first, so typed text and Excel data cannot be confused.
function valueField(editor, step, key, label, options) {
  const settings = options || {};
  const memoKey = `${step.id}.${key}`;
  const [found, name] = sourceOf(step[key]);
  const mode = step[key] ? found : (editor.sourceModes[memoKey] || 'text');
  const modes = [['text', 'Text'], ['excel', 'Excel']];
  if (key !== 'url') {
    modes.push(['secret', 'Secret']);
    if (mode === 'variable' || editor.variableNames().length) modes.push(['variable', 'Variable']);
  }
  const tabs = el('div.tabs', { role: 'tablist', 'aria-label': 'Nguồn dữ liệu' }, modes.map(([value, text]) => el('button', {
    class: mode === value ? 'on' : null,
    role: 'tab',
    'data-mode': value,
    text,
    onclick: () => {
      if (mode === value) return;
      editor.sourceModes[memoKey] = value;
      step[key] = '';
      editor.changed();
    },
  })));
  const panes = {
    text: () => textPane(editor, step, key, settings),
    excel: () => columnPane(editor, step, key, name, memoKey),
    secret: () => namePane(editor, step, key, 'secret', name, editor.secretNames),
    variable: () => namePane(editor, step, key, 'var', name, editor.variableNames()),
  };
  return el('div.field', null, el('label.field-label', { text: label }), tabs, panes[mode]());
}

function textPane(editor, step, key, settings) {
  const input = el('input', {
    type: 'text',
    class: key === 'url' ? 'mono' : null,
    value: step[key] || '',
    placeholder: settings.placeholder || 'Nhập nội dung cố định',
    'data-field': key,
    oninput: () => {
      step[key] = input.value;
      editor.changedQuietly();
    },
  });
  return el('div', null, input, settings.hint ? el('div.hint', { text: settings.hint }) : null);
}

function columnPane(editor, step, key, name, memoKey) {
  const listId = `columns-${key}`;
  const columns = editor.availableColumns();
  const preview = el('div.hint', { id: 'column-preview' });
  const showPreview = letter => {
    const column = editor.currentSheet() && columns.find(item => item.column === letter);
    if (column) {
      const sample = column.sample ? `. Hàng đầu: ${column.sample}` : '';
      preview.textContent = `Cột ${letter}: ${column.header || 'không có tiêu đề'}${sample}`;
    } else {
      preview.textContent = 'Nhập chữ cái của cột, ví dụ C là cột C. Mỗi hàng Excel dùng giá trị ở cột này.';
    }
  };
  const input = el('input', {
    type: 'text', class: 'mono', value: name || '', maxlength: '3', placeholder: 'C',
    list: listId, 'data-column': key, 'aria-label': 'Cột Excel',
    oninput: () => {
      const letter = input.value.trim().toUpperCase();
      input.value = letter;
      editor.sourceModes[memoKey] = 'excel';
      step[key] = /^[A-Z]{1,3}$/.test(letter) ? `{{${letter}}}` : '';
      editor.changedQuietly();
      showPreview(letter);
    },
  });
  showPreview(name);
  const loop = findLoop(editor.flow);
  const usable = loop && loop.source === 'excel' && loop.steps.includes(step);
  return el('div', null, input,
    el('datalist', { id: listId }, columns.map(column => el('option', { value: column.column, label: column.header || '' }))),
    preview,
    usable ? null : el('div.banner.warn', {
      id: 'needs-loop',
      text: 'Khối này chưa nằm trong vùng lặp theo Excel nên chưa có dữ liệu. Kéo chuột khoanh các khối cần lặp rồi bấm Create loop, hoặc kéo khối vào vùng lặp.',
      style: 'margin:8px 0 0',
    }));
}

function namePane(editor, step, key, kind, name, names) {
  const known = name && !names.includes(name) ? [...names, name] : names;
  const select = el('select', {
    'data-name': key,
    'aria-label': kind === 'secret' ? 'Tên bí mật' : 'Tên biến',
    onchange: () => {
      if (select.value === '__manage') {
        select.value = name || '';
        openSecrets();
        return;
      }
      step[key] = select.value ? `{{${kind}:${select.value}}}` : '';
      editor.changed();
    },
  },
  el('option', { value: '', text: 'Chọn...', selected: !name }),
  known.map(item => el('option', { value: item, text: item, selected: item === name })),
  kind === 'secret' ? el('option', { value: '__manage', text: 'Quản lý bí mật...' }) : null);
  return select;
}

function renderProperties(editor) {
  const panel = editor.inspectorNode;
  const scroll = panel.scrollTop;
  panel.replaceChildren();
  if (editor.selection.size > 1) {
    panel.append(el('p.muted', { text: 'Đang chọn nhiều khối. Chọn một khối để sửa, hoặc tạo vùng lặp từ các khối đã khoanh.' }));
    return;
  }
  const id = [...editor.selection][0];
  const step = id ? findStep(editor.flow, id) : null;
  if (!step) {
    panel.append(
      el('p.section-label', { text: 'Cách dùng' }),
      el('ol', { style: 'margin:0;padding-left:18px;line-height:1.7' },
        el('li', { text: 'Kéo khối từ bên trái, hoặc bấm Record.' }),
        el('li', { text: 'Chọn khối rồi dán HTML phần tử vào đây.' }),
        el('li', { text: 'Kéo chuột khoanh các khối cần lặp, rồi bấm Create loop.' }),
        el('li', { text: 'Bấm Select Excel, chọn dải hàng ở thanh dưới, rồi bấm Run.' })));
    return;
  }
  panel.append(el('div.row', { style: 'margin-bottom:12px' },
    el('h2', { text: STEP_NAMES[step.do] }),
    el('span.spacer'),
    step.do === 'loop' ? null : el('button.btn.small.danger', { id: 'delete-step', text: 'Delete block', onclick: () => editor.deleteSelection() })));
  const render = STEP_FORMS[step.do];
  if (render) render(panel, editor, step);
  panel.scrollTop = scroll;
}

const STEP_FORMS = {
  goto(panel, editor, step) {
    panel.append(valueField(editor, step, 'url', 'Link', {
      placeholder: 'https://...',
      hint: 'Link có phần thay đổi theo Excel thì ghép như sau: https://site/{{A}}/edit.',
    }));
  },
  fill(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(valueField(editor, step, 'value', 'Giá trị cần điền'));
  },
  click(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(el('label.check-line', null,
      el('input', {
        type: 'checkbox', id: 'wait-navigation', checked: step.wait_navigation,
        onchange: event => { step.wait_navigation = event.target.checked; editor.changedQuietly(); },
      }),
      'Chờ chuyển trang sau khi bấm'));
    panel.append(el('div.hint', { text: 'Bật khi nút này lưu hoặc mở trang khác, để bước sau không chạy quá sớm.' }));
  },
  select(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(valueField(editor, step, 'value', 'Lựa chọn (đúng chữ hiển thị)'));
  },
  check(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(el('label.check-line', null,
      el('input', {
        type: 'checkbox', checked: step.checked !== false,
        onchange: event => { step.checked = event.target.checked; editor.changedQuietly(); },
      }),
      'Tick ô này (bỏ chọn để bỏ tick)'));
  },
  read(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    const mode = step.save_to_column !== undefined && step.save_to_column !== null ? 'column' : 'var';
    const holder = el('div');
    const draw = current => {
      holder.replaceChildren();
      if (current === 'column') {
        const input = el('input', {
          type: 'text', value: step.save_to_column || '', placeholder: 'vd: E', 'data-field': 'save_to_column',
          oninput: () => { step.save_to_column = input.value.trim().toUpperCase(); step.save_to_var = null; editor.changedQuietly(); },
        });
        holder.append(field('Ghi vào cột của file kết quả', input));
      } else {
        const input = el('input', {
          type: 'text', value: step.save_to_var || '', placeholder: 'vd: ten_cu', 'data-field': 'save_to_var',
          oninput: () => { step.save_to_var = input.value.trim(); step.save_to_column = null; editor.changedQuietly(); },
        });
        holder.append(field('Lưu vào biến', input, 'Dùng ở bước sau bằng {{var:tên}}.'));
      }
    };
    const select = el('select', {
      'data-field': 'read-mode',
      onchange: () => {
        if (select.value === 'column') { step.save_to_column = step.save_to_column || ''; step.save_to_var = null; } else { step.save_to_var = step.save_to_var || ''; step.save_to_column = null; }
        draw(select.value);
        editor.changedQuietly();
      },
    }, el('option', { value: 'var', text: 'Lưu vào biến', selected: mode === 'var' }), el('option', { value: 'column', text: 'Ghi vào cột Excel', selected: mode === 'column' }));
    panel.append(field('Lưu ở đâu', select));
    draw(mode);
    panel.append(holder);
  },
  wait_for(panel, editor, step) {
    panel.append(targetEditor(editor, step));
  },
  wait(panel, editor, step) {
    const seconds = el('input', {
      type: 'number', id: 'wait-seconds', min: '0', step: 'any', value: step.seconds,
      oninput: () => {
        const value = parseFloat(seconds.value);
        step.seconds = value > 0 ? value : DEFAULT_WAIT_SECONDS;
        editor.changedQuietly();
      },
    });
    panel.append(field('Số giây', seconds, `Tạm ngưng rồi mới chạy khối tiếp theo. Để trống thì dùng ${DEFAULT_WAIT_SECONDS} giây.`));
  },
  verify(panel, editor, step) {
    const kinds = {
      equals: 'Phần tử có giá trị bằng', contains: 'Phần tử có chứa',
      url_contains: 'URL có chứa', url_not_contains: 'URL không chứa',
    };
    const current = Object.keys(kinds).find(key => step[key]) || 'equals';
    const body = el('div');
    const draw = kind => {
      body.replaceChildren();
      if (kind === 'equals' || kind === 'contains') body.append(targetEditor(editor, step), el('div.divider'));
      body.append(valueField(editor, step, kind, 'Giá trị mong đợi'));
    };
    const select = el('select', {
      'data-field': 'verify-kind',
      onchange: () => {
        const value = Object.keys(kinds).map(key => step[key]).find(Boolean) || '';
        Object.keys(kinds).forEach(key => { delete step[key]; });
        step[select.value] = value;
        draw(select.value);
        editor.changedQuietly();
      },
    }, Object.entries(kinds).map(([key, text]) => el('option', { value: key, text, selected: key === current })));
    panel.append(field('Điều kiện', select));
    panel.append(body);
    draw(current);
  },
  manual_pause(panel, editor, step) {
    panel.append(textField(editor, step, 'message', 'Lời nhắc', {
      placeholder: 'vd: Nhập mã xác thực rồi bấm Continue',
      hint: 'Tool dừng ở đây để bạn làm tay trong cửa sổ Chrome, ví dụ MFA hoặc captcha.',
    }));
  },
  loop(panel, editor, step) {
    const select = el('select', {
      id: 'loop-source',
      onchange: () => {
        step.source = select.value;
        if (step.source === 'excel') editor.ensureExcelSettings();
        editor.changed();
      },
    }, el('option', { value: 'excel', text: 'Mỗi hàng Excel', selected: step.source === 'excel' }), el('option', { value: 'count', text: 'Số lần cố định', selected: step.source === 'count' }));
    panel.append(field('Lặp theo', select));
    if (step.source === 'count') {
      const count = el('input', {
        type: 'number', id: 'loop-count', min: '1', value: step.count || 1,
        oninput: () => { step.count = Math.max(1, parseInt(count.value, 10) || 1); editor.changedQuietly(); editor.renderRunbar(); },
      });
      panel.append(field('Số lần chạy', count, 'Dùng {{i}} để lấy số thứ tự lần lặp.'));
    } else {
      panel.append(el('p.muted', { text: 'Chọn file Excel, sheet và dải hàng ở thanh phía dưới.' }));
      const excel = editor.flow.excel || {};
      const required = el('input', {
        type: 'text', id: 'excel-required', class: 'mono', value: (excel.required || []).join(', '), placeholder: 'A, C',
        oninput: () => {
          excel.required = required.value.split(',').map(item => item.trim().toUpperCase()).filter(Boolean);
          editor.flow.excel = excel;
          editor.changedQuietly();
        },
      });
      panel.append(el('div.divider'));
      panel.append(field('Cột bắt buộc có dữ liệu', required, 'Hàng thiếu cột này sẽ được bỏ qua, không chạy.'));
      const status = el('input', {
        type: 'text', id: 'excel-status', class: 'mono', value: excel.status_column || '', placeholder: 'D',
        oninput: () => {
          excel.status_column = status.value.trim().toUpperCase() || null;
          editor.flow.excel = excel;
          editor.changedQuietly();
        },
      });
      panel.append(field('Cột ghi trạng thái', status, 'Ghi vào file kết quả. File gốc giữ nguyên.'));
    }
  },
};

// Choosing the element a block acts on.

function ensureTarget(step) {
  if (!step.target) step.target = { primary: '', fallbacks: [], frames: [], verified: false };
  return step.target;
}

function targetEditor(editor, step) {
  const target = ensureTarget(step);
  const mode = editor.targetTab || 'element';
  const holder = el('div');
  const tabs = el('div.tabs', { role: 'tablist' }, [
    ['element', 'Paste element'], ['page', 'Paste page'], ['live', 'Scan open page'],
  ].map(([key, label]) => el('button', {
    class: mode === key ? 'on' : null,
    'data-tab': key,
    role: 'tab',
    text: label,
    onclick: () => { editor.targetTab = key; editor.renderInspector(); },
  })));
  holder.append(tabs);
  if (mode === 'element') holder.append(pasteElement(editor, step));
  if (mode === 'page') holder.append(pastePage(editor, step));
  if (mode === 'live') holder.append(livePage(editor, step));

  const selector = el('input', {
    type: 'text', class: 'mono', id: 'target-selector', value: target.primary,
    placeholder: '#id, input[name="x"] hoặc xpath=//...',
    oninput: () => {
      target.primary = selector.value.trim();
      // What was found for the old element says nothing about the new one.
      target.fallbacks = [];
      target.frames = [];
      target.verified = false;
      editor.changedQuietly();
      refreshBadge();
    },
  });
  const badge = el('span');
  const refreshBadge = () => {
    badge.replaceChildren(target.verified
      ? el('span.badge.ok', { id: 'verified-badge', text: '✓ Đã kiểm chứng' })
      : el('span.badge.warn', { id: 'verified-badge', text: 'Chưa kiểm chứng trên trang thật' }));
  };
  refreshBadge();
  holder.append(
    field('Cách nhận diện', selector),
    el('div', { style: 'margin:-6px 0 10px' }, badge),
    verifyControls(editor, step, refreshBadge));
  return holder;
}

function applyElement(editor, step, info, frames) {
  const target = ensureTarget(step);
  target.primary = info.selector;
  target.fallbacks = info.fallbacks || [];
  target.frames = frames || [];
  target.verified = false;
  if (info.is_password && step.do === 'fill' && !step.value) step.value = '{{secret:mat_khau}}';
  editor.changed();
}

function elementSummary(editor, step, info) {
  const box = el('div', { style: 'margin-top:8px' });
  const known = info.match_count !== null && info.match_count !== undefined;
  box.append(el('div.row.wrap', { style: 'margin-bottom:6px' },
    el('span.badge.neutral', { text: `Gợi ý: ${STEP_NAMES[info.action]}` }),
    known ? el(info.match_count === 1 ? 'span.badge.ok' : 'span.badge.bad', { text: `Khớp ${info.match_count} phần tử` }) : el('span.badge.neutral', { text: 'Số khớp: chưa rõ' }),
    info.action !== step.do && step.do !== 'read' && step.do !== 'verify' && step.do !== 'wait_for'
      ? el('button.btn.small', {
        id: 'switch-action',
        text: `Change to ${STEP_NAMES[info.action]}`,
        onclick: () => { step.do = info.action; editor.changed(); },
      })
      : null));
  if (info.warnings && info.warnings.length) {
    box.append(el('ul.note-list', null, info.warnings.map(text => el('li', { text }))));
  }
  if (info.match_count !== 1 && info.candidates && info.candidates.length > 1) {
    box.append(el('div.hint', { text: 'Cách nhận diện khác:', style: 'margin:6px 0 4px' }));
    for (const candidate of info.candidates) {
      box.append(el('label.candidate' + (ensureTarget(step).primary === candidate.selector ? '.on' : ''), {
        onclick: event => {
          event.preventDefault();
          const target = ensureTarget(step);
          target.primary = candidate.selector;
          target.fallbacks = info.candidates.filter(item => item.selector !== candidate.selector && (item.count === 1 || item.count === null)).map(item => item.selector).slice(0, 2);
          target.verified = false;
          editor.changed();
        },
      }, el('code', { text: candidate.selector }), candidate.count === null || candidate.count === undefined ? null : el('span.muted', { text: `${candidate.count}` })));
    }
  }
  return box;
}

function pasteElement(editor, step) {
  const area = el('textarea', {
    id: 'paste-element',
    placeholder: 'F12 → chuột phải vào phần tử → Copy → Copy outerHTML, rồi dán vào đây',
    value: editor.pasted.element || '',
  });
  const result = el('div');
  const read = debounce(async () => {
    editor.pasted.element = area.value;
    if (!area.value.trim()) { result.replaceChildren(); return; }
    try {
      const { element } = await api('POST', '/api/html', { html: area.value, mode: 'element' });
      if (!element) {
        result.replaceChildren(el('div.hint', { text: 'Không thấy phần tử nào trong nội dung đã dán.' }));
        return;
      }
      // Set first: applying the element redraws this panel, which shows the summary.
      editor.pastedInfo = element;
      applyElement(editor, step, element, []);
    } catch (error) { showError(error); }
  }, 250);
  area.addEventListener('input', read);
  if (editor.pastedInfo) result.append(elementSummary(editor, step, editor.pastedInfo));
  return el('div.field', null, area, result);
}

function elementRow(editor, step, info) {
  return el('button.element-row', {
    'data-selector': info.selector,
    onclick: () => {
      applyElement(editor, step, info, info.frames || []);
      editor.pastedInfo = info;
      editor.targetTab = 'element';
      editor.renderInspector();
    },
  },
  el('span.kind-tag', { text: STEP_NAMES[info.action] }),
  el('span.el-main', null, el('span.el-label', { text: info.label }), el('code', { text: info.selector })),
  info.match_count === 1 ? null : el('span.badge.bad', { text: `${info.match_count}` }));
}

function elementList(editor, step, elements) {
  const filter = el('input', { type: 'text', placeholder: 'Lọc theo tên...', id: 'element-filter' });
  const list = el('div.element-list');
  const draw = () => {
    const word = filter.value.trim().toLowerCase();
    const shown = elements.filter(info => !word || info.label.toLowerCase().includes(word) || info.selector.toLowerCase().includes(word));
    list.replaceChildren(...shown.slice(0, 300).map(info => elementRow(editor, step, info)));
    if (!shown.length) list.append(el('div.empty', { text: 'Không có phần tử phù hợp.' }));
  };
  filter.addEventListener('input', draw);
  draw();
  return el('div', { style: 'margin-top:8px' }, el('div.hint', { text: `Tìm thấy ${elements.length} phần tử. Bấm để chọn.`, style: 'margin-bottom:5px' }), filter, list);
}

function pastePage(editor, step) {
  const area = el('textarea', {
    id: 'paste-page',
    placeholder: 'F12 → Elements → chuột phải vào <html> → Copy → Copy outerHTML (không dùng Ctrl+U)',
    value: editor.pasted.page || '',
  });
  const result = el('div');
  const read = debounce(async () => {
    editor.pasted.page = area.value;
    if (!area.value.trim()) { result.replaceChildren(); editor.pagedElements = null; return; }
    try {
      const { elements } = await api('POST', '/api/html', { html: area.value, mode: 'page' });
      editor.pagedElements = elements;
      result.replaceChildren(elementList(editor, step, elements));
    } catch (error) { showError(error); }
  }, 300);
  area.addEventListener('input', read);
  if (editor.pagedElements) result.append(elementList(editor, step, editor.pagedElements));
  return el('div.field', null, area,
    el('div.hint', { text: 'HTML chỉ dùng tạm, không được lưu.' }),
    result);
}

function livePage(editor, step) {
  const url = el('input', { type: 'text', class: 'mono', id: 'live-url', placeholder: 'https://... (không bắt buộc)' });
  const result = el('div');
  const button = el('button.btn', {
    id: 'scan-live',
    text: 'Scan page',
    onclick: async () => {
      button.disabled = true;
      button.textContent = 'Scanning...';
      try {
        const link = url.value.trim();
        const response = link ? await watchInspection({ cmd: 'scan', url: link }) : await inspectAtBlock(editor, step, { cmd: 'scan' });
        if (!response || response.cancelled) return;
        if (response.error) throw new Error(response.error);
        editor.pagedElements = response.elements;
        result.replaceChildren(elementList(editor, step, response.elements));
      } catch (error) { showError(error); } finally {
        button.disabled = false;
        button.textContent = 'Scan page';
      }
    },
  });
  return el('div.field', null,
    field('Link', url, 'Để trống: tool chạy thật các khối phía trước tới khối này rồi quét. Điền link: mở thẳng link đó rồi quét.'),
    button, result);
}

// Bringing the real page to where a block acts: the blocks before it really run.

function runningOrderLabel(step) {
  return `${STEP_NAMES[step.do]} ${stepSummary(step)}`;
}

async function inspectAtBlock(editor, step, command) {
  editor.failedStep = null;
  renderCanvas(editor);
  const before = stepsBefore(editor.flow, step.id);
  const position = before.findIndex(stepIsIncomplete);
  if (position >= 0) {
    editor.failedStep = before[position].id;
    renderCanvas(editor);
    return { error: `Khối ${position + 1} (${STEP_NAMES[before[position].do]}) chưa điền đủ nên chưa chạy tới được đây. Hãy điền hoặc xóa khối đó.` };
  }
  const unapproved = before.filter(item => ['click', 'select', 'check'].includes(item.do) && !editor.approvedSteps.has(item.id));
  if (unapproved.length) {
    const names = unapproved.map(runningOrderLabel).join(', ');
    const message = `Để tới được khối này, tool sẽ chạy thật ${before.length} khối phía trước, gồm: ${names}. Khối trong vùng lặp dùng hàng đầu tiên của Excel. Riêng khối đang kiểm tra không được thực hiện.`;
    if (!await confirmBox('Chạy thật các khối phía trước?', message, 'Run to here')) return null;
    unapproved.forEach(item => editor.approvedSteps.add(item.id));
  }
  await editor.save.flush();
  editor.litSteps = new Set();
  const response = await watchInspection({
    ...command, flow_id: editor.id, excel_path: editor.excelPath, step_ids: before.map(item => item.id),
  }, event => {
    editor.litSteps.add(event.step);
    renderCanvas(editor);
  });
  editor.litSteps = new Set();
  editor.failedStep = response.failed_step || null;
  renderCanvas(editor);
  if (!before.some(item => item.do === 'goto')) response.no_link = true;
  return response;
}

function watchInspection(command, onStep) {
  return new Promise(resolve => {
    let box = null;
    let done = false;
    let source = null;
    const label = el('p', { id: 'inspect-step', style: 'margin-bottom:8px' });
    const pause = el('div');
    const show = () => {
      if (box) return;
      box = openModal({
        title: 'Đang chạy tới khối cần kiểm tra',
        dismissable: false,
        body: el('div', null, label, pause),
        actions: [{
          label: 'Stop', id: 'inspect-stop',
          onClick: async () => {
            try { await api('POST', '/api/inspect/command', { cmd: 'cancel' }); } catch (error) { showError(error); }
            return true;
          },
        }],
      });
    };
    const finish = result => {
      if (done) return;
      done = true;
      if (source) source.close();
      if (box) box.close();
      resolve(result);
    };
    api('POST', '/api/inspect', command).then(() => {
      source = new EventSource('/api/inspect/events');
      source.onmessage = message => {
        const event = JSON.parse(message.data);
        if (event.type === 'step') {
          show();
          label.textContent = `Khối ${event.index}/${event.total}: ${event.label}`;
          if (onStep) onStep(event);
        } else if (event.type === 'paused') {
          show();
          fill(pause, el('div.pause-box', { style: 'margin:0' },
            el('p', { text: event.reason, style: 'margin-bottom:8px' }),
            el('button.btn.primary', {
              id: 'inspect-continue',
              text: 'Continue',
              onclick: () => api('POST', '/api/inspect/command', { cmd: 'continue' }).catch(showError),
            })));
        } else if (event.type === 'resumed') {
          fill(pause);
        } else if (event.type === 'result') {
          finish(event);
        }
      };
      source.addEventListener('end', () => finish({ error: 'Trình duyệt đã bị đóng. Hãy bấm lại.' }));
    }).catch(error => finish({ error: error.message }));
  });
}

function checkResultNode(response) {
  const banner = (kind, text) => el('div.banner.' + kind, { id: 'check-result', text, style: 'margin:8px 0 0' });
  if (response.cancelled) return banner('warn', 'Đã dừng. Trang trong Chrome đang ở khối vừa chạy xong.');
  if (response.failed_step) return banner('bad', `Dừng ở khối ${response.position}. ${response.error}`);
  if (response.error) return banner('bad', response.error);
  if (response.problem) return banner('bad', response.problem);
  if (response.count === 1) return banner('ok', 'Tới đúng trang và khớp đúng 1 phần tử, đã tô đỏ trong Chrome. Hành động làm được.');
  const hint = response.no_link && response.count === 0 ? ' Phía trước chưa có khối Open link nên tool đã tìm trên trang đang mở.' : '';
  return banner('bad', `Khớp ${response.count} phần tử.${hint}`);
}

function verifyControls(editor, step, refreshBadge) {
  const target = ensureTarget(step);
  const result = el('div');
  const button = el('button.btn.live', {
    id: 'check-target',
    text: 'Check on live page',
    onclick: async () => {
      if (!target.primary) { toast('Hãy chọn phần tử trước.', 'bad'); return; }
      button.disabled = true;
      button.textContent = 'Checking...';
      try {
        const response = await inspectAtBlock(editor, step, { cmd: 'check', target, kind: step.do });
        if (!response) return;
        if (!response.cancelled && !response.failed_step) {
          target.verified = response.count === 1 && !response.problem;
          editor.changedQuietly();
          refreshBadge();
        }
        result.replaceChildren(checkResultNode(response));
      } finally {
        button.disabled = false;
        button.textContent = 'Check on live page';
      }
    },
  });
  return el('div', null, button,
    el('div.hint', { text: 'Chạy thật các khối phía trước tới khối này, tô đỏ phần tử và thử xem hành động có làm được không. Khối này chưa được thực hiện.' }),
    result);
}
