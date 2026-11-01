'use strict';

async function startRecording(editor) {
  const url = await promptBox('Ghi thao tác', 'Link bắt đầu', '', 'Tool mở một cửa sổ Chrome riêng. Bạn làm tay một lượt, tool ghi lại các bước. Ô mật khẩu không bao giờ được ghi.');
  if (url === null) return;
  await editor.save.flush();
  try {
    await api('POST', '/api/record', { url });
  } catch (error) {
    showError(error);
    return;
  }

  const entries = [];
  const warnings = [];
  const list = el('div', { id: 'record-live' });
  const warningBox = el('ul.note-list');
  const draw = () => {
    list.replaceChildren(entries.length
      ? el('table.data', null, entries.map((entry, index) => el('tr', null,
        el('td', { text: String(index + 1), class: 'muted', style: 'width:32px' }),
        el('td', { text: entry.label }))))
      : el('p.muted', { text: 'Đang chờ thao tác trong cửa sổ Chrome...' }));
    list.scrollTop = list.scrollHeight;
  };
  draw();

  let finished = false;
  let source;
  const finish = result => {
    if (finished) return;
    finished = true;
    if (source) source.close();
    box.close();
    showRecordReview(editor, (result && result.entries) || entries, (result && result.warnings) || warnings);
  };
  const box = openModal({
    title: '● Đang ghi thao tác',
    dismissable: false,
    body: el('div', null,
      el('p', { text: 'Làm thao tác trong cửa sổ Chrome như bình thường. Xong thì bấm Stop recording.', style: 'margin-bottom:10px' }),
      list, warningBox),
    actions: [{
      label: 'Stop recording', kind: 'primary', id: 'stop-record',
      onClick: async () => {
        try { await api('POST', '/api/record/stop', {}); } catch (error) { showError(error); }
        return true;
      },
    }],
  });

  source = new EventSource('/api/record/events');
  source.onmessage = message => {
    const event = JSON.parse(message.data);
    if (event.type === 'recorded') {
      entries.push(event.entry);
      draw();
    } else if (event.type === 'recorded_update') {
      const index = entries.findIndex(entry => entry.step.id === event.entry.step.id);
      if (index >= 0) entries[index] = event.entry;
    } else if (event.type === 'recorder_warning') {
      warnings.push(event.text);
      warningBox.append(el('li', { text: event.text }));
    } else if (event.type === 'finished') {
      if (event.outcome === 'failed') {
        showError(new Error(event.message || 'Không ghi được.'));
        finish({ entries, warnings });
      } else {
        finish(event);
      }
    }
  };
  source.addEventListener('end', () => finish(null));
}

function sourceOptions(editor, step) {
  const loop = findLoop(editor.flow);
  const options = [el('option', { value: '', text: 'Giữ nguyên giá trị đã ghi' })];
  const columns = editor.availableColumns();
  if (columns.length) {
    options.push(el('optgroup', { label: 'Cột Excel' }, columns.map(column => el('option', {
      value: step.do === 'goto' ? `{{${column.column}}}` : `{{${column.column}}}`,
      text: column.header ? `Cột ${column.column}: ${column.header}` : `Cột ${column.column}`,
    }))));
  }
  if (editor.secretNames.length) {
    options.push(el('optgroup', { label: 'Bí mật' }, editor.secretNames.map(name => el('option', { value: `{{secret:${name}}}`, text: name }))));
  }
  return options;
}

function showRecordReview(editor, entries, warnings) {
  if (!entries.length) {
    toast('Không có thao tác nào được ghi.');
    return;
  }
  const rows = entries.map(entry => ({ entry, keep: true }));
  const table = el('table.data', { id: 'record-review' },
    el('tr', null, el('th', { text: 'Thao tác' }), el('th', { text: 'Giá trị đã ghi' }), el('th', { text: 'Khi chạy lấy từ' }), el('th')));
  rows.forEach(row => {
    const { step, label } = row.entry;
    const field = step.do === 'goto' ? 'url' : 'value';
    const hasValue = step[field] !== undefined;
    const isSecret = hasValue && String(step[field]).startsWith('{{secret:');
    let control = el('span.muted', { text: 'Không cần dữ liệu' });
    if (hasValue && isSecret) {
      const nameInput = el('input', {
        type: 'text', class: 'mono', value: 'mat_khau', 'data-secret-name': step.id,
        'aria-label': 'Tên bí mật',
        oninput: () => { step[field] = `{{secret:${nameInput.value.trim()}}}`; },
      });
      step[field] = '{{secret:mat_khau}}';
      control = el('div', null, nameInput, el('div.hint', { text: 'Tên bí mật. Lưu giá trị ở mục Secrets.' }));
    } else if (hasValue) {
      const text = el('input', {
        type: 'text', class: 'mono', value: step[field], 'data-recorded': step.id, 'aria-label': 'Giá trị khi chạy',
        oninput: () => { step[field] = text.value; },
      });
      const select = el('select', {
        'data-source': step.id, 'aria-label': 'Nguồn dữ liệu', style: 'margin-top:4px',
        onchange: () => {
          if (select.value) { text.value = select.value; step[field] = select.value; }
          select.value = '';
        },
      }, sourceOptions(editor, step));
      control = el('div', null, text, select);
    }
    const shown = isSecret ? '•••• (không lưu)' : (hasValue ? String(step[field]) : '');
    const tr = el('tr', null,
      el('td', null,
        el('div', { text: label }),
        step.target ? el('code.muted', { text: step.target.primary, style: 'display:block;overflow-wrap:anywhere' }) : null,
        row.entry.warnings && row.entry.warnings.length ? el('ul.note-list', null, row.entry.warnings.map(text => el('li', { text }))) : null),
      el('td.muted', { text: shown, style: 'overflow-wrap:anywhere;max-width:190px' }),
      el('td', { style: 'min-width:190px' }, control),
      el('td', null, el('button.btn.small.danger', {
        text: 'Delete',
        'data-remove': step.id,
        onclick: () => { row.keep = false; tr.remove(); },
      })));
    table.append(tr);
  });

  openModal({
    title: 'Xem lại bản ghi',
    wide: true,
    dismissable: false,
    body: el('div', null,
      el('p.muted', { text: 'Chọn nguồn dữ liệu cho từng bước và xóa bước thừa trước khi đưa vào sơ đồ.', style: 'margin-bottom:10px' }),
      warnings.length ? el('ul.note-list', { style: 'margin-bottom:10px' }, warnings.map(text => el('li', { text }))) : null,
      table),
    actions: [
      { label: 'Discard', id: 'discard-record' },
      {
        label: 'Add to flow', kind: 'primary', id: 'accept-record',
        onClick: () => {
          const chosen = rows.filter(row => row.keep).map(row => {
            const step = { ...row.entry.step };
            delete step.id;
            return step;
          });
          editor.insertSteps(chosen);
          toast(`Đã thêm ${chosen.length} bước. Kéo chuột khoanh các khối cần lặp để tạo vùng lặp.`);
        },
      },
    ],
  });
}
