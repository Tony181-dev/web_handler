'use strict';

async function startRecording(editor) {
  const url = await promptBox(t('Record actions'), t('Starting link'), '', t('The tool opens a separate Chrome window. You do the work once by hand and it records the steps. Password fields are never recorded.'));
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
      : el('p.muted', { text: t('Waiting for actions in the Chrome window...') }));
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
    title: t('● Recording actions'),
    dismissable: false,
    body: el('div', null,
      el('p', { text: t('Work in the Chrome window as usual. When done, click Stop recording.'), style: 'margin-bottom:10px' }),
      list, warningBox),
    actions: [{
      label: t('Stop recording'), kind: 'primary', id: 'stop-record',
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
      draw();
    } else if (event.type === 'recorder_warning') {
      warnings.push(event.text);
      warningBox.append(el('li', { text: event.text }));
    } else if (event.type === 'finished') {
      if (event.outcome === 'failed') {
        showError(new Error(event.message || t('Could not record.')));
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
  const options = [el('option', { value: '', text: t('Keep the recorded value') })];
  const columns = editor.availableColumns();
  if (columns.length) {
    options.push(el('optgroup', { label: t('Excel column') }, columns.map(column => el('option', {
      value: step.do === 'goto' ? `{{${column.column}}}` : `{{${column.column}}}`,
      text: column.header ? t('Column {column}: {header}', { column: column.column, header: column.header }) : t('Column {column}', { column: column.column }),
    }))));
  }
  if (editor.secretNames.length) {
    options.push(el('optgroup', { label: t('Secret') }, editor.secretNames.map(name => el('option', { value: `{{secret:${name}}}`, text: t('Secret value of {name}', { name }) }))));
  }
  return options;
}

function showRecordReview(editor, entries, warnings) {
  if (!entries.length) {
    toast(t('No actions were recorded.'));
    return;
  }
  const rows = entries.map(entry => ({ entry, keep: true }));
  const table = el('table.data', { id: 'record-review' },
    el('tr', null, el('th', { text: t('Action') }), el('th', { text: t('Recorded value') }), el('th', { text: t('When running, take from') }), el('th')));
  rows.forEach(row => {
    const { step, label } = row.entry;
    const field = step.do === 'goto' ? 'url' : 'value';
    const hasValue = step[field] !== undefined;
    const isSecret = Boolean(row.entry.password);
    let control = el('span.muted', { text: t('No data needed') });
    if (isSecret) {
      control = el('div.hint', { text: t('Left empty. After adding it, choose the password in the block: it is marked with a red !.') });
    } else if (hasValue) {
      const text = el('input', {
        type: 'text', class: 'mono', value: step[field], 'data-recorded': step.id, 'aria-label': t('Value when running'),
        oninput: () => { step[field] = text.value; },
      });
      const select = el('select', {
        'data-source': step.id, 'aria-label': t('Data source'), style: 'margin-top:4px',
        onchange: () => {
          if (select.value) { text.value = select.value; step[field] = select.value; }
          select.value = '';
        },
      }, sourceOptions(editor, step));
      control = el('div', null, text, select);
    }
    const shown = isSecret ? t('•••• (not saved)') : (hasValue ? String(step[field]) : '');
    const tr = el('tr', null,
      el('td', null,
        el('div', { text: label }),
        step.target ? el('code.muted', { text: step.target.primary, style: 'display:block;overflow-wrap:anywhere' }) : null,
        row.entry.warnings && row.entry.warnings.length ? el('ul.note-list', null, row.entry.warnings.map(text => el('li', { text }))) : null),
      el('td.muted', { text: shown, style: 'overflow-wrap:anywhere;max-width:190px' }),
      el('td', { style: 'min-width:190px' }, control),
      el('td', null, el('button.btn.small.danger', {
        text: t('Delete'),
        'data-remove': step.id,
        onclick: () => { row.keep = false; tr.remove(); },
      })));
    table.append(tr);
  });

  openModal({
    title: t('Review the recording'),
    wide: true,
    dismissable: false,
    body: el('div', null,
      el('p.muted', { text: t('Choose a data source for each step and delete extra steps before adding them to the diagram.'), style: 'margin-bottom:10px' }),
      warnings.length ? el('ul.note-list', { style: 'margin-bottom:10px' }, warnings.map(text => el('li', { text }))) : null,
      table),
    actions: [
      { label: t('Discard'), id: 'discard-record' },
      {
        label: t('Add to flow'), kind: 'primary', id: 'accept-record',
        onClick: () => {
          const chosen = rows.filter(row => row.keep).map(row => {
            const step = { ...row.entry.step };
            delete step.id;
            return step;
          });
          editor.insertSteps(chosen);
          toast(t('Added {count} step(s). Drag a box around the blocks to repeat to create a loop.', { count: chosen.length }));
        },
      },
    ],
  });
}
