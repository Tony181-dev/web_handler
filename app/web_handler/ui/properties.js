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
  // A flow saved earlier may hold a secret here. As text it shows what the run check complains about.
  const mode = step[key] ? (found === 'secret' && settings.noSecret ? 'text' : found) : (editor.sourceModes[memoKey] || 'text');
  const modes = [['text', t('Text')], ['excel', t('Excel')]];
  if (key !== 'url') {
    if (!settings.noSecret) modes.push(['secret', t('Secret')]);
    if (mode === 'variable' || editor.variableNames().length) modes.push(['variable', t('Variable')]);
  }
  const tabs = el('div.tabs', { role: 'tablist', 'aria-label': t('Data source') }, modes.map(([value, text]) => el('button', {
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

// A button that hides or shows what a box holds. It only covers the screen: it does not change what is saved.
function withEye(input, startHidden, onChange) {
  let hidden = startHidden;
  const apply = () => {
    input.type = hidden ? 'password' : 'text';
    button.setAttribute('aria-pressed', String(hidden));
  };
  const button = el('button.btn.small.eye-toggle', {
    type: 'button', text: t('👁 Hide/show'),
    onclick: () => {
      hidden = !hidden;
      apply();
      if (onChange) onChange(hidden);
    },
  });
  apply();
  return el('div.eye-field', null, input, button);
}

function textPane(editor, step, key, settings) {
  const input = el('input', {
    type: 'text',
    class: key === 'url' ? 'mono' : null,
    value: step[key] || '',
    placeholder: settings.placeholder || t('Type fixed text'),
    autocomplete: 'off',
    'data-field': key,
    oninput: () => {
      step[key] = input.value;
      editor.changedQuietly();
    },
  });
  const memoKey = `${step.id}.${key}`;
  const hideable = (step.do === 'fill' && key === 'value') || key === 'account';
  const box = hideable
    ? withEye(input, editor.hiddenValues.has(memoKey), hidden => {
      if (hidden) editor.hiddenValues.add(memoKey);
      else editor.hiddenValues.delete(memoKey);
    })
    : input;
  return el('div', null, box, settings.hint ? el('div.hint', { text: settings.hint }) : null);
}

// A typed password goes to Windows Credential Manager under this block's own name, never into the flow file.
function passwordField(editor, step) {
  const ownName = `password_${editor.id}_${step.id}`;
  const [, secret] = sourceOf(step.password);
  const memoKey = `${step.id}.password`;
  const mode = editor.sourceModes[memoKey] || (secret && secret !== ownName ? 'secret' : 'typed');
  const tabs = el('div.tabs', { role: 'tablist', 'aria-label': t('Password') }, [['typed', t('Type in')], ['secret', t('Saved secret')]].map(([value, text]) => el('button', {
    class: mode === value ? 'on' : null,
    role: 'tab',
    'data-password-mode': value,
    text,
    onclick: () => {
      editor.sourceModes[memoKey] = value;
      editor.renderInspector();
    },
  })));
  if (mode === 'secret') {
    return el('div.field', null, el('label.field-label', { text: t('Password') }), tabs,
      namePane(editor, step, 'password', 'secret', secret, editor.secretNames));
  }
  const input = el('input', {
    type: 'password', id: 'login-password', autocomplete: 'new-password',
    placeholder: secret ? t('Saved. Type here to change it.') : t('Type the password'),
    oninput: () => {
      if (!input.value) return;
      step.password = `{{secret:${ownName}}}`;
      editor.typePassword(ownName, input.value);
      editor.changedQuietly();
    },
  });
  return el('div.field', null, el('label.field-label', { text: t('Password') }), tabs, withEye(input, true),
    el('div.hint', { text: t('Saved in Windows Credential Manager under Secrets, not in the flow file.') }));
}

function columnPane(editor, step, key, name, memoKey) {
  const listId = `columns-${key}`;
  const columns = editor.availableColumns();
  const preview = el('div.hint', { id: 'column-preview' });
  const showPreview = letter => {
    const column = editor.currentSheet() && columns.find(item => item.column === letter);
    if (column) {
      const sample = column.sample ? t('. First row: {value}', { value: column.sample }) : '';
      preview.textContent = t('Column {column}: {header}', { column: letter, header: column.header || t('no heading') }) + sample;
    } else {
      preview.textContent = t('Type the column letter, for example C is column C. Each Excel row uses the value in this column.');
    }
  };
  const input = el('input', {
    type: 'text', class: 'mono', value: name || '', maxlength: '3', placeholder: 'C',
    list: listId, 'data-column': key, 'aria-label': t('Excel column'),
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
      text: t('This block is not inside a loop over Excel rows, so it has no data yet. Drag a box around the blocks to repeat and click Create loop, or drag this block into the loop.'),
      style: 'margin:8px 0 0',
    }));
}

function namePane(editor, step, key, kind, name, names) {
  const known = name && !names.includes(name) ? [...names, name] : names;
  const select = el('select', {
    'data-name': key,
    'aria-label': kind === 'secret' ? t('Secret name') : t('Variable name'),
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
  el('option', { value: '', text: t('Choose...'), selected: !name }),
  known.map(item => el('option', { value: item, text: kind === 'secret' ? t('Secret value of {name}', { name: item }) : item, selected: item === name })),
  kind === 'secret' ? el('option', { value: '__manage', text: t('Manage secrets...') }) : null);
  return select;
}

function renderProperties(editor) {
  const panel = editor.inspectorNode;
  const scroll = panel.scrollTop;
  panel.replaceChildren();
  if (editor.selection.size > 1) {
    panel.append(el('p.muted', { text: t('Several blocks are selected. Select one to edit it, or create a loop from the selected blocks.') }));
    return;
  }
  const id = [...editor.selection][0];
  const step = id ? findStep(editor.flow, id) : null;
  if (!step) {
    panel.append(
      el('p.section-label', { text: t('How to use') }),
      el('ol', { style: 'margin:0;padding-left:18px;line-height:1.7' },
        el('li', { text: t('Drag blocks from the left, or click Record.') }),
        el('li', { text: t('Select a block and paste the HTML of the element here.') }),
        el('li', { text: t('Drag a box around the blocks to repeat, then click Create loop.') }),
        el('li', { text: t('Click Select Excel, choose the rows in the bar below, then click Run.') })));
    return;
  }
  panel.append(el('div.row', { style: 'margin-bottom:12px' },
    el('h2', { text: STEP_NAMES[step.do] }),
    el('span.spacer'),
    step.do === 'loop' ? null : el('button.btn.small.danger', { id: 'delete-step', text: t('Delete block'), onclick: () => editor.deleteSelection() })));
  const render = STEP_FORMS[step.do];
  if (render) render(panel, editor, step);
  if (!['loop', 'wait'].includes(step.do)) panel.append(delayField(editor, step));
  panel.scrollTop = scroll;
}

// Collapsed until someone needs it, but a delay in use stays in sight.
function delayField(editor, step) {
  const input = el('input', {
    type: 'number', id: 'delay-after', min: '0', step: 'any', value: step.delay_after || '',
    oninput: () => {
      const value = parseFloat(input.value);
      if (value > 0) step.delay_after = value;
      else delete step.delay_after;
      editor.changedQuietly();
    },
  });
  return el('details.delay-field', { id: 'delay-details', open: Boolean(step.delay_after) },
    el('summary', { text: t('Delay') }),
    field(t('Delay (seconds)'), input, t('Enter the time to wait before moving on to the next block. Leave empty for no delay.')));
}

const LOGIN_SELECTOR_LABELS = { account: 'Account field', password: 'Password field', submit: 'Login button' };

const STEP_FORMS = {
  goto(panel, editor, step) {
    panel.append(valueField(editor, step, 'url', t('Link'), {
      placeholder: 'https://...',
      hint: t('If part of the link changes with Excel, write it like this: https://site/{{A}}/edit.'),
    }));
  },
  login(panel, editor, step) {
    panel.append(valueField(editor, step, 'account', t('Account'), { noSecret: true }));
    panel.append(passwordField(editor, step));
    const finding = ['account', 'password', 'submit'].map(name => textField(editor, step, `${name}_selector`, t(LOGIN_SELECTOR_LABELS[name]), { mono: true }));
    panel.append(el('details', null, el('summary', { text: t('Specify the login form yourself') }),
      el('div.hint', { text: t('Leave empty and the tool finds the form by itself. Fill in only if it finds the wrong one.'), style: 'margin:6px 0' }),
      finding));
  },
  fill(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(valueField(editor, step, 'value', t('Value to fill in')));
  },
  click(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(el('label.check-line', null,
      el('input', {
        type: 'checkbox', id: 'wait-navigation', checked: step.wait_navigation,
        onchange: event => { step.wait_navigation = event.target.checked; editor.changedQuietly(); },
      }),
      t('Wait for the page to change after clicking')));
    panel.append(el('div.hint', { text: t('Turn this on when the button saves or opens another page, so the next block does not run too early.') }));
  },
  select(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(valueField(editor, step, 'value', t('Option (exactly the text it shows)')));
  },
  check(panel, editor, step) {
    panel.append(targetEditor(editor, step));
    panel.append(el('div.divider'));
    panel.append(el('label.check-line', null,
      el('input', {
        type: 'checkbox', checked: step.checked !== false,
        onchange: event => { step.checked = event.target.checked; editor.changedQuietly(); },
      }),
      t('Tick this box (turn off to untick it)')));
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
          type: 'text', value: step.save_to_column || '', placeholder: t('e.g. E'), 'data-field': 'save_to_column',
          oninput: () => { step.save_to_column = input.value.trim().toUpperCase(); step.save_to_var = null; editor.changedQuietly(); },
        });
        holder.append(field(t('Write to this column of the result file'), input));
      } else {
        const input = el('input', {
          type: 'text', value: step.save_to_var || '', placeholder: t('e.g. old_name'), 'data-field': 'save_to_var',
          oninput: () => { step.save_to_var = input.value.trim(); step.save_to_column = null; editor.changedQuietly(); },
        });
        holder.append(field(t('Save to a variable'), input, t('Use it in a later block with {{var:name}}.')));
      }
    };
    const select = el('select', {
      'data-field': 'read-mode',
      onchange: () => {
        if (select.value === 'column') { step.save_to_column = step.save_to_column || ''; step.save_to_var = null; } else { step.save_to_var = step.save_to_var || ''; step.save_to_column = null; }
        draw(select.value);
        editor.changedQuietly();
      },
    }, el('option', { value: 'var', text: t('Save to a variable'), selected: mode === 'var' }), el('option', { value: 'column', text: t('Write to an Excel column'), selected: mode === 'column' }));
    panel.append(field(t('Where to save'), select));
    draw(mode);
    panel.append(holder);
  },
  ocr(panel, editor, step) {
    STEP_FORMS.read(panel, editor, step);
    const select = el('select', {
      id: 'ocr-case',
      onchange: () => {
        if (select.value) step.text_case = select.value;
        else delete step.text_case;
        editor.changedQuietly();
      },
    }, [['', t('As read')], ['upper', t('UPPERCASE')], ['lower', t('lowercase')]].map(([value, text]) => el('option', { value, text, selected: (step.text_case || '') === value })));
    panel.append(field(t('Letter case'), select, t('Point this block at the picture, then use the variable in a Fill block. Hard pictures can be read wrongly: use a Manual step for those.')));
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
    panel.append(field(t('Seconds'), seconds, t('Pauses before running the next block. Leave empty for {seconds} s.', { seconds: DEFAULT_WAIT_SECONDS })));
  },
  verify(panel, editor, step) {
    const kinds = {
      equals: t('The element equals'), contains: t('The element contains'),
      url_contains: t('The URL contains'), url_not_contains: t('The URL does not contain'),
    };
    const current = Object.keys(kinds).find(key => step[key]) || 'equals';
    const body = el('div');
    const draw = kind => {
      body.replaceChildren();
      if (kind === 'equals' || kind === 'contains') body.append(targetEditor(editor, step), el('div.divider'));
      body.append(valueField(editor, step, kind, t('Expected value')));
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
    panel.append(field(t('Condition'), select));
    panel.append(body);
    draw(current);
  },
  manual_pause(panel, editor, step) {
    panel.append(textField(editor, step, 'message', t('Reminder'), {
      placeholder: t('e.g. Enter the login code, then click Continue'),
      hint: t('The tool stops here so you can work by hand in the Chrome window, for example for MFA or a captcha.'),
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
    }, el('option', { value: 'excel', text: t('Each Excel row'), selected: step.source === 'excel' }), el('option', { value: 'count', text: t('A fixed number of times'), selected: step.source === 'count' }));
    panel.append(field(t('Repeat over'), select));
    if (step.source === 'count') {
      const count = el('input', {
        type: 'number', id: 'loop-count', min: '1', value: step.count || 1,
        oninput: () => { step.count = Math.max(1, parseInt(count.value, 10) || 1); editor.changedQuietly(); editor.renderRunbar(); },
      });
      panel.append(field(t('Number of runs'), count, t('Use {{i}} to get the number of the current repeat.')));
    } else {
      panel.append(el('p.muted', { text: t('Choose the Excel file, sheet and rows in the bar below.') }));
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
      panel.append(field(t('Columns that must have data'), required, t('A row missing one of these is skipped, not run.')));
      const status = el('input', {
        type: 'text', id: 'excel-status', class: 'mono', value: excel.status_column || '', placeholder: 'D',
        oninput: () => {
          excel.status_column = status.value.trim().toUpperCase() || null;
          editor.flow.excel = excel;
          editor.changedQuietly();
        },
      });
      panel.append(field(t('Column for the status'), status, t('Written to the result file. The original file stays as it is.')));
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
    ['element', t('Paste element')], ['page', t('Paste page')], ['live', t('Scan open page')],
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
    placeholder: t('#id, input[name="x"] or xpath=//...'),
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
      ? el('span.badge.ok', { id: 'verified-badge', text: t('✓ Checked') })
      : el('span.badge.warn', { id: 'verified-badge', text: t('Not checked on a live page') }));
  };
  refreshBadge();
  holder.append(
    el('div.finder-box', { id: 'finder-box' },
      field(t('How to find it'), selector),
      el('div', null, badge),
      editor.pastedInfo ? otherWays(editor, step, editor.pastedInfo) : null),
    verifyControls(editor, step, refreshBadge));
  return holder;
}

function applyElement(editor, step, info, frames) {
  const target = ensureTarget(step);
  target.primary = info.selector;
  target.fallbacks = info.fallbacks || [];
  target.frames = frames || [];
  target.verified = false;
  if (info.is_password && step.do === 'fill' && !step.value) step.value = '{{secret:your_password}}';
  editor.changed();
}

function elementSummary(editor, step, info) {
  const box = el('div', { style: 'margin-top:8px' });
  const known = info.match_count !== null && info.match_count !== undefined;
  box.append(el('div.row.wrap', { style: 'margin-bottom:6px' },
    el('span.badge.neutral', { text: t('Suggested: {name}', { name: STEP_NAMES[info.action] }) }),
    known ? el(info.match_count === 1 ? 'span.badge.ok' : 'span.badge.bad', { text: t('Matched {count} element(s)', { count: info.match_count }) }) : el('span.badge.neutral', { text: t('Matches: unknown') }),
    info.action !== step.do && step.do !== 'read' && step.do !== 'ocr' && step.do !== 'verify' && step.do !== 'wait_for'
      ? el('button.btn.small', {
        id: 'switch-action',
        text: t('Change to {name}', { name: STEP_NAMES[info.action] }),
        onclick: () => { step.do = info.action; editor.changed(); },
      })
      : null));
  if (info.warnings && info.warnings.length) {
    box.append(el('ul.note-list', null, info.warnings.map(text => el('li', { text }))));
  }
  return box;
}

function otherWays(editor, step, info) {
  if (info.match_count === 1 || !info.candidates || info.candidates.length < 2) return null;
  return el('div', { style: 'margin-top:10px' },
    el('div.hint', { text: t('Other ways to find it:'), style: 'margin:0 0 4px' }),
    info.candidates.map(candidate => el('label.candidate' + (ensureTarget(step).primary === candidate.selector ? '.on' : ''), {
      onclick: event => {
        event.preventDefault();
        const target = ensureTarget(step);
        target.primary = candidate.selector;
        target.fallbacks = info.candidates.filter(item => item.selector !== candidate.selector && (item.count === 1 || item.count === null)).map(item => item.selector).slice(0, 2);
        target.verified = false;
        editor.changed();
      },
    }, el('code', { text: candidate.selector }), candidate.count === null || candidate.count === undefined ? null : el('span.muted', { text: `${candidate.count}` }))));
}

function pasteElement(editor, step) {
  const area = el('textarea', {
    id: 'paste-element',
    placeholder: t('F12 → right-click the element → Copy → Copy outerHTML, then paste it here'),
    value: editor.pasted.element || '',
  });
  const result = el('div');
  const read = debounce(async () => {
    editor.pasted.element = area.value;
    if (!area.value.trim()) { result.replaceChildren(); return; }
    try {
      const { element } = await api('POST', '/api/html', { html: area.value, mode: 'element' });
      if (!element) {
        result.replaceChildren(el('div.hint', { text: t('No element found in what was pasted.') }));
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
  const filter = el('input', { type: 'text', placeholder: t('Filter by name...'), id: 'element-filter' });
  const list = el('div.element-list');
  const draw = () => {
    const word = filter.value.trim().toLowerCase();
    const shown = elements.filter(info => !word || info.label.toLowerCase().includes(word) || info.selector.toLowerCase().includes(word));
    list.replaceChildren(...shown.slice(0, 300).map(info => elementRow(editor, step, info)));
    if (!shown.length) list.append(el('div.empty', { text: t('No matching elements.') }));
  };
  filter.addEventListener('input', draw);
  draw();
  return el('div', { style: 'margin-top:8px' }, el('div.hint', { text: t('Found {count} element(s). Click one to choose it.', { count: elements.length }), style: 'margin-bottom:5px' }), filter, list);
}

function pastePage(editor, step) {
  const area = el('textarea', {
    id: 'paste-page',
    placeholder: t('F12 → Elements → right-click <html> → Copy → Copy outerHTML (not Ctrl+U)'),
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
    el('div.hint', { text: t('The HTML is only used for now and is not saved.') }),
    result);
}

function livePage(editor, step) {
  const url = el('input', { type: 'text', class: 'mono', id: 'live-url', placeholder: t('https://... (optional)') });
  const result = el('div');
  const button = el('button.btn', {
    id: 'scan-live',
    text: t('Scan page'),
    onclick: async () => {
      button.disabled = true;
      button.textContent = t('Scanning...');
      try {
        const link = url.value.trim();
        const response = link ? await watchInspection({ cmd: 'scan', url: link }) : await inspectAtBlock(editor, step, { cmd: 'scan' });
        if (!response || response.cancelled) return;
        if (response.error) throw new Error(response.error);
        editor.pagedElements = response.elements;
        result.replaceChildren(elementList(editor, step, response.elements));
      } catch (error) { showError(error); } finally {
        button.disabled = false;
        button.textContent = t('Scan page');
      }
    },
  });
  return el('div.field', null,
    field(t('Link'), url, t('Empty: the tool really runs the blocks before this one, then scans. With a link: it opens that link directly, then scans.')),
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
    return { error: t('Block {position} ({name}) is not filled in, so the run cannot get here yet. Fill it in or delete it.', { position: position + 1, name: STEP_NAMES[before[position].do] }) };
  }
  const unapproved = before.filter(item => ['login', 'click', 'select', 'check'].includes(item.do) && !editor.approvedSteps.has(item.id));
  if (unapproved.length) {
    const names = unapproved.map(runningOrderLabel).join(', ');
    const message = t('To get to this block, the tool will really run the {count} block(s) before it, including: {names}. Blocks in the loop use the first Excel row. The block being checked is not carried out.', { count: before.length, names });
    if (!await confirmBox(t('Really run the blocks before this one?'), message, t('Run to here'))) return null;
    unapproved.forEach(item => editor.approvedSteps.add(item.id));
  }
  await editor.flushPassword();
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
        title: t('Running up to the block to check'),
        dismissable: false,
        body: el('div', null, label, pause),
        actions: [{
          label: t('Stop'), id: 'inspect-stop',
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
          label.textContent = t('Block {index}/{total}: {label}', { index: event.index, total: event.total, label: event.label });
          if (onStep) onStep(event);
        } else if (event.type === 'paused') {
          show();
          fill(pause, el('div.pause-box', { style: 'margin:0' },
            el('p', { text: event.reason, style: 'margin-bottom:8px' }),
            el('button.btn.primary', {
              id: 'inspect-continue',
              text: t('Continue'),
              onclick: () => api('POST', '/api/inspect/command', { cmd: 'continue' }).catch(showError),
            })));
        } else if (event.type === 'resumed') {
          fill(pause);
        } else if (event.type === 'result') {
          finish(event);
        }
      };
      source.addEventListener('end', () => finish({ error: t('The browser was closed. Please click again.') }));
    }).catch(error => finish({ error: error.message }));
  });
}

function checkResultNode(response) {
  const banner = (kind, text) => el('div.banner.' + kind, { id: 'check-result', text, style: 'margin:8px 0 0' });
  if (response.cancelled) return banner('warn', t('Stopped. The page in Chrome is at the last block that finished.'));
  if (response.failed_step) return banner('bad', t('Stopped at block {position}. {error}', { position: response.position, error: response.error }));
  if (response.error) return banner('bad', response.error);
  if (response.problem) return banner('bad', response.problem);
  if (response.count === 1) return banner('ok', t('Reached the page and matched exactly 1 element, outlined in red in Chrome. The action can be done.'));
  const hint = response.no_link && response.count === 0 ? t(' There is no Open link block before this, so the tool looked on the open page.') : '';
  return banner('bad', t('Matched {count} element(s).', { count: response.count }) + hint);
}

function verifyControls(editor, step, refreshBadge) {
  const target = ensureTarget(step);
  const result = el('div');
  const button = el('button.btn.live', {
    id: 'check-target',
    text: t('Check on live page'),
    onclick: async () => {
      if (!target.primary) { toast(t('Choose an element first.'), 'bad'); return; }
      button.disabled = true;
      button.textContent = t('Checking...');
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
        button.textContent = t('Check on live page');
      }
    },
  });
  return el('div', null, button,
    el('div.hint', { text: t('Really runs the blocks before this one, outlines the element in red and tries whether the action can be done. This block itself is not carried out.') }),
    result);
}
