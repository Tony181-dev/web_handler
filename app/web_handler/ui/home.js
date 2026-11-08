'use strict';

async function renderHome() {
  setTopbarTitle(null);
  const view = document.getElementById('view');
  const page = el('div.page');
  view.replaceChildren(el('div.page-scroll', null, page));

  const setup = await api('GET', '/api/setup').catch(() => null);
  if (setup && !setup.ready) {
    location.hash = '#/setup';
    return;
  }
  const { flows, templates } = await api('GET', '/api/flows');

  const list = el('div.flow-list');

  fill(page,
    el('div.page-head', null,
      el('h1', { text: t('Your flows') }),
      el('div.spacer'),
      el('button.btn.primary', { id: 'new-flow', text: t('+ New flow'), onclick: () => openNewFlow(templates) })),
    el('div.demo-card', null,
      el('div', null,
        el('h2', { text: t('Try it on the demo site') }),
        el('p.muted', { text: t('A flow that logs in and renames items from Excel, on a small website built into the app. It does not touch any real system.') })),
      el('button.btn', { id: 'open-demo', text: t('Open sample flow'), onclick: openDemo })),
    list);

  if (!flows.length) {
    list.append(el('div.card.empty', { text: t('No flows yet. Create one or open the sample flow.') }));
  }
  for (const flow of flows) {
    const open = () => { location.hash = '#/flow/' + flow.id; };
    list.append(el('div.card.flow-item', { 'data-flow': flow.id },
      el('button.name', { text: flow.name, onclick: open }),
      el('span.muted', { text: t('{count} block(s)', { count: flow.steps }) }),
      el('button.btn.small', { text: t('Open'), onclick: open }),
      el('button.btn.small', {
        text: t('Duplicate'),
        onclick: async () => {
          try {
            await api('POST', `/api/flows/${flow.id}/duplicate`);
            renderHome();
          } catch (error) { showError(error); }
        },
      }),
      el('button.btn.small.danger', {
        text: t('Delete'),
        onclick: async () => {
          if (!await confirmBox(t('Delete this flow?'), t('Delete "{name}". This cannot be undone.', { name: flow.name }), t('Delete'), 'danger')) return;
          try {
            await api('DELETE', `/api/flows/${flow.id}`);
            renderHome();
          } catch (error) { showError(error); }
        },
      })));
  }
}

function openNewFlow(templates) {
  const name = el('input', { type: 'text', id: 'new-flow-name', placeholder: t('Leave empty to name it automatically') });
  let creating = false;
  const create = async template => {
    // One click creates the flow at once, so a double click must not create two.
    if (creating) return;
    creating = true;
    try {
      const created = await api('POST', '/api/flows', { name: name.value.trim(), template });
      box.close();
      location.hash = '#/flow/' + created.id;
    } catch (error) {
      creating = false;
      showError(error);
    }
  };
  const choice = (id, label, hint, template) => el('div.template-item', null,
    el('button.btn', { 'data-template': id, text: label, onclick: () => create(template) }),
    el('span.muted', { text: hint }));
  const box = openModal({
    title: t('New flow'),
    body: el('div', null,
      el('div.field', null, el('label.field-label', { text: t('Flow name'), for: 'new-flow-name' }), name),
      el('p.section-label', { text: t('Choose how to start') }),
      el('div.template-list', null,
        choice('blank', t('Blank flow'), t('An empty flow: drag blocks in yourself or click Record.'), null),
        templates.map(item => choice(item.id, item.label, item.hint, item.id)))),
    actions: [{ label: t('Cancel') }],
  });
  name.addEventListener('keydown', event => { if (event.key === 'Enter') create(null); });
}

async function openDemo() {
  try {
    const demo = await api('POST', '/api/demo');
    memory.set('excel.' + demo.id, demo.excel_path);
    location.hash = '#/flow/' + demo.id;
    toast(t('Sample flow opened. Click Run once or Run to see the result.'));
  } catch (error) { showError(error); }
}

function openSecrets() {
  const list = el('div');
  const nameInput = el('input', { type: 'text', id: 'secret-name', placeholder: 'your_account' });
  const valueInput = el('input', { type: 'password', id: 'secret-value', placeholder: 'your_password', autocomplete: 'new-password' });
  const reload = async () => {
    const { names } = await api('GET', '/api/secrets');
    list.replaceChildren(names.length ? el('table.data', null, names.map(name => el('tr', null,
      el('td', null, el('code', { text: name })),
      el('td', { style: 'text-align:right' },
        el('button.btn.small', { text: t('Change value'), onclick: () => { nameInput.value = name; valueInput.focus(); } }), ' ',
        el('button.btn.small.danger', {
          text: t('Delete'),
          onclick: async () => {
            try {
              await api('DELETE', '/api/secrets/' + encodeURIComponent(name));
              reload();
              if (window.onSecretsChanged) window.onSecretsChanged();
            } catch (error) { showError(error); }
          },
        }))))) : el('p.muted', { text: t('No secrets yet.') }));
  };
  const save = async () => {
    try {
      await api('POST', '/api/secrets', { name: nameInput.value.trim(), value: valueInput.value });
      nameInput.value = '';
      valueInput.value = '';
      toast(t('Saved to {store}.', { store: secretStoreName() }));
      reload();
      if (window.onSecretsChanged) window.onSecretsChanged();
    } catch (error) { showError(error); }
  };
  openModal({
    title: t('Secrets (passwords, accounts)'),
    body: el('div', null,
      el('p.muted', { text: t('Values are kept in {store} and never shown again. The flow file holds only the name, for example {{secret:your_password}}.', { store: secretStoreName() }), style: 'margin-bottom:12px' }),
      list,
      el('div.divider'),
      el('div.field', null, el('label.field-label', { text: t('Name'), for: 'secret-name' }), nameInput, el('div.hint', { text: t('A name you choose, used to pick this secret in a block.') })),
      el('div.field', null, el('label.field-label', { text: t('Value'), for: 'secret-value' }), valueInput, el('div.hint', { text: t('The real account or password. Any characters are allowed.') })),
      el('button.btn.primary', { id: 'secret-save', text: t('Save secret'), onclick: save })),
    actions: [{ label: t('Close'), id: 'close' }],
  });
  reload().catch(showError);
}
