'use strict';

const ACTIVE_JOB_POLL_MS = 2000;

async function route() {
  if (window.leaveView) window.leaveView();
  refreshActiveJob();
  document.getElementById('modal-root').replaceChildren();
  const hash = location.hash.replace(/^#\/?/, '');
  const [section, id] = hash.split('/');
  // The builder keeps its Log button in the left column; other screens use the one in the corner.
  document.getElementById('log-corner').hidden = section === 'flow' && Boolean(id);
  try {
    if (section === 'flow' && id) await renderBuilder(id);
    else if (section === 'run' && id) await renderRun(id);
    else if (section === 'setup') await renderSetup();
    else await renderHome();
  } catch (error) {
    showError(error);
    document.getElementById('view').replaceChildren(el('div.page', null,
      el('div.banner.bad', { text: error.message }),
      el('a.btn', { href: '#/', text: t('Back to home') })));
  }
}

// Whatever is using the browser stays reachable from every screen, so it can never block the next run.
let activeJob = null;

async function refreshActiveJob() {
  try {
    activeJob = (await api('GET', '/api/active')).job;
  } catch (error) { return; }
  const node = document.getElementById('active-job');
  node.hidden = !activeJob || location.hash === `#/run/${activeJob.run_id}`;
  if (activeJob) node.textContent = activeJob.kind === 'run' ? t('● Flow running') : t('● Recording actions');
}

async function stopActiveJob() {
  try {
    await api('POST', '/api/active/stop', {}, { timeout: LONG_REQUEST_TIMEOUT_MS });
    toast(t('Stopped as requested.'));
  } catch (error) { showError(error); }
  refreshActiveJob();
}

function openActiveJobBox() {
  const isRun = activeJob && activeJob.kind === 'run';
  const runId = isRun ? activeJob.run_id : '';
  openModal({
    title: isRun ? t('A flow is still running') : t('Actions are being recorded.'),
    body: el('p', { text: isRun ? t('"{name}" is still running. Open it to watch it, or stop it now.', { name: activeJob.flow_name }) : t('Stop the recording before starting anything else.') }),
    actions: [
      { label: t('Close'), id: 'close' },
      isRun ? { label: t('Open running flow'), id: 'open-active', onClick: () => { location.hash = '#/run/' + runId; } } : null,
      { label: t('Stop now'), kind: 'danger', id: 'stop-active', onClick: stopActiveJob },
    ].filter(Boolean),
  });
}

async function checkForUpdate() {
  const answer = await api('GET', '/api/ping').catch(() => null);
  if (!answer || !answer.version_on_disk || answer.version_on_disk === answer.version) return;
  const notice = document.getElementById('update-notice');
  notice.textContent = t('The app was updated. Close its window and reopen it.');
  notice.hidden = false;
}

function setUpLanguagePicker() {
  const picker = document.getElementById('language');
  picker.replaceChildren(...LANGUAGES.map(([value, text]) => el('option', { value, text, selected: value === currentLanguage })));
  picker.addEventListener('change', async () => {
    try {
      if (window.currentEditor) await window.currentEditor.save.flush();
      await api('PUT', '/api/language', { language: picker.value });
      location.reload();
    } catch (error) {
      picker.value = currentLanguage;
      showError(error);
    }
  });
}

async function start() {
  await loadLanguage();
  document.getElementById('brand-author').textContent = t('author: {name}', { name: 'Hoang Nguyen' });
  document.getElementById('nav-secrets').textContent = t('Secrets');
  document.getElementById('nav-setup').textContent = t('Setup');
  setUpLanguagePicker();
  document.getElementById('log-corner').append(logButton());
  document.getElementById('nav-setup').addEventListener('click', () => { location.hash = '#/setup'; });
  document.getElementById('nav-secrets').addEventListener('click', openSecrets);
  document.getElementById('active-job').addEventListener('click', openActiveJobBox);
  setInterval(refreshActiveJob, ACTIVE_JOB_POLL_MS);
  window.addEventListener('hashchange', route);
  window.addEventListener('beforeunload', () => {
    if (window.currentEditor) {
      window.currentEditor.flushPassword();
      window.currentEditor.save.flush();
    }
  });
  await refreshActiveJob();
  checkForUpdate();
  route();
}

start();
