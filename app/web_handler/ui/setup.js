'use strict';

const SETUP_LABELS = { PASS: ['✓ Pass', 'ok'], FAIL: ['✗ Not passed', 'bad'], NOT_RUN: ['Not tried', 'neutral'] };

async function renderSetup(flowId) {
  setTopbarTitle(null);
  const view = document.getElementById('view');
  const page = el('div.page', { id: 'setup-page' });
  view.replaceChildren(el('div.page-scroll', null, page));
  let timer = null;
  let stopped = false;

  const draw = status => {
    const missing = status.items.filter(item => item.status === 'FAIL' && item.fixable);
    const busy = Boolean(status.busy);
    fill(page,
      el('div.page-head', null,
        flowId ? el('a.btn', { id: 'setup-back', href: '#/flow/' + flowId, text: t('Back to flow') }) : null,
        el('h1', { text: t('Setup') }),
        el('span.spacer'),
        el('button.btn', { id: 'setup-test', text: t('Re-check'), disabled: busy || !status.ready, onclick: runTests }),
        el('button.btn.primary', { id: 'setup-install', text: t('Install missing'), disabled: busy || !missing.length, onclick: () => confirmInstall(missing) })),
      status.busy || status.message ? el('div.banner.' + (status.failed ? 'bad' : 'ok'), { id: 'setup-message', text: status.message }) : null,
      el('div.card', { style: 'overflow:auto' }, el('table.data', { id: 'setup-table' },
        el('tr', null, el('th', { text: t('Item') }), el('th', { text: t('Result') }), el('th', { text: t('Details') })),
        status.items.map(item => {
          const [label, kind] = SETUP_LABELS[item.status] || [item.status, 'neutral'];
          return el('tr', { 'data-item': item.id },
            el('td', { text: item.title, style: 'font-weight:600' }),
            el('td', null, el('span.badge.' + kind, { text: t(label) })),
            el('td.muted', { text: item.detail, style: 'overflow-wrap:anywhere' }));
        }))),
      status.ready
        ? el('div', { style: 'margin-top:14px' }, el('a.btn.primary', { id: 'setup-done', href: '#/', text: t('Open app') }))
        : el('p.muted', { style: 'margin-top:14px', text: t('Click Install missing to install from the package. No internet or admin rights needed.') }));
    if (busy && !stopped) {
      clearTimeout(timer);
      timer = setTimeout(load, 700);
    }
  };

  const load = async () => {
    try { draw(await api('GET', '/api/setup', undefined, { timeout: 120000 })); } catch (error) { showError(error); }
  };

  const confirmInstall = async missing => {
    const list = missing.map(item => `${item.title}${item.size_mb ? t(' (about {size} MB)', { size: item.size_mb }) : ''}`).join(', ');
    if (!await confirmBox(t('Install what is missing'), t('This installs into the app folder: {list}. No internet needed, and it does not change the Python or Chrome on this machine.', { list }), t('Install'))) return;
    try {
      await api('POST', '/api/setup/install', {});
      load();
    } catch (error) { showError(error); }
  };

  const runTests = async () => {
    try {
      await api('POST', '/api/setup/test', {});
      load();
    } catch (error) { showError(error); }
  };

  window.leaveView = () => {
    stopped = true;
    clearTimeout(timer);
    window.leaveView = null;
  };
  page.append(el('p.muted', { text: t('Checking...') }));
  await load();
}
