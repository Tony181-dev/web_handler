'use strict';

const SETUP_LABELS = { PASS: ['✓ Đạt', 'ok'], FAIL: ['✗ Chưa đạt', 'bad'], NOT_RUN: ['Chưa thử', 'neutral'] };

async function renderSetup() {
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
        el('h1', { text: 'Setup' }),
        el('span.spacer'),
        el('button.btn', { id: 'setup-test', text: 'Re-check', disabled: busy || !status.ready, onclick: runTests }),
        el('button.btn.primary', { id: 'setup-install', text: 'Install missing', disabled: busy || !missing.length, onclick: () => confirmInstall(missing) })),
      status.busy || status.message ? el('div.banner.' + (status.message.startsWith('Lỗi') ? 'bad' : 'ok'), { id: 'setup-message', text: status.message }) : null,
      el('div.card', { style: 'overflow:auto' }, el('table.data', { id: 'setup-table' },
        el('tr', null, el('th', { text: 'Hạng mục' }), el('th', { text: 'Kết quả' }), el('th', { text: 'Chi tiết' })),
        status.items.map(item => {
          const [label, kind] = SETUP_LABELS[item.status] || [item.status, 'neutral'];
          return el('tr', { 'data-item': item.id },
            el('td', { text: item.title, style: 'font-weight:600' }),
            el('td', null, el('span.badge.' + kind, { text: label })),
            el('td.muted', { text: item.detail, style: 'overflow-wrap:anywhere' }));
        }))),
      status.ready
        ? el('div', { style: 'margin-top:14px' }, el('a.btn.primary', { id: 'setup-done', href: '#/', text: 'Open app' }))
        : el('p.muted', { style: 'margin-top:14px', text: 'Bấm "Install missing" để cài từ gói có sẵn. Không cần mạng và không cần quyền admin.' }));
    if (busy && !stopped) {
      clearTimeout(timer);
      timer = setTimeout(load, 700);
    }
  };

  const load = async () => {
    try { draw(await api('GET', '/api/setup', undefined, { timeout: 120000 })); } catch (error) { showError(error); }
  };

  const confirmInstall = async missing => {
    const list = missing.map(item => `${item.title}${item.size_mb ? ` (khoảng ${item.size_mb} MB)` : ''}`).join(', ');
    if (!await confirmBox('Cài những gì còn thiếu', `Sẽ cài vào thư mục của app: ${list}. Không cần mạng, không sửa Python hay Chrome của máy.`, 'Install')) return;
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
  page.append(el('p.muted', { text: 'Đang kiểm tra...' }));
  await load();
}
