'use strict';

const ACTIVE_JOB_POLL_MS = 2000;
// The flow last open, so Setup can lead back to it.
let lastFlowId = null;

async function route() {
  if (window.leaveView) window.leaveView();
  refreshActiveJob();
  document.getElementById('modal-root').replaceChildren();
  const hash = location.hash.replace(/^#\/?/, '');
  const [section, id] = hash.split('/');
  // The builder keeps its Log button in the left column; other screens use the one in the corner.
  document.getElementById('log-corner').hidden = section === 'flow' && Boolean(id);
  try {
    if (section === 'flow' && id) {
      lastFlowId = id;
      await renderBuilder(id);
    }
    else if (section === 'run' && id) await renderRun(id);
    else if (section === 'setup') await renderSetup(lastFlowId);
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
// running while a run uses the browser, kept while its window is left open after the run, or null.
let browserState = null;
let keepBrowser = false;

async function refreshActiveJob() {
  let answer;
  try {
    answer = await api('GET', '/api/active');
  } catch (error) { return; }
  activeJob = answer.job;
  browserState = answer.browser || null;
  const node = document.getElementById('active-job');
  if (activeJob) {
    node.hidden = location.hash === `#/run/${activeJob.run_id}`;
    node.textContent = activeJob.kind === 'run' ? t('● Flow running') : t('● Recording actions');
  } else {
    node.hidden = browserState !== 'kept';
    node.textContent = t('● Browser kept open');
  }
  showBrowserState();
}

const BROWSER_STATE_TEXT = {
  running: 'The browser is open: the flow is running.',
  kept: 'The browser is open: kept after the run.',
};

function markBrowserState(button) {
  button.dataset.browser = browserState || 'none';
  const note = BROWSER_STATE_TEXT[browserState];
  button.title = button.dataset.help + (note ? '\n' + t(note) : '');
}

function showBrowserState() {
  document.querySelectorAll('.keep-browser').forEach(markBrowserState);
}

// The window is only left open where someone can see it: a headless browser would linger unnoticed.
function keepBrowserButton(browserMode) {
  const headless = browserMode === 'headless';
  const button = el('button.btn.keep-browser', {
    id: 'keep-browser', 'aria-pressed': String(keepBrowser && !headless), disabled: headless,
    'data-help': headless ? t('Not used in Headless mode, where nobody would see a browser left open.') : t('When on, the browser stays open after Run and Run once, so you can carry on by hand. Close its window when you are done.'),
    onclick: async () => {
      try {
        keepBrowser = (await api('PUT', '/api/keep-browser', { keep: !keepBrowser })).keep;
        button.replaceWith(keepBrowserButton(browserMode));
      } catch (error) { showError(error); }
    },
  }, el('span.browser-dot', { 'aria-hidden': 'true' }), el('span', { text: keepBrowser ? t('Keep browser: On') : t('Keep browser: Off') }));
  markBrowserState(button);
  return button;
}

async function closeKeptBrowser() {
  try {
    await api('POST', '/api/active/close-browser', {}, { timeout: LONG_REQUEST_TIMEOUT_MS });
  } catch (error) { showError(error); }
  refreshActiveJob();
}

function openKeptBrowserBox() {
  openModal({
    title: t('The browser is still open'),
    body: el('p', { text: t('The window of the last run was kept open. Running, recording or checking again closes it by itself.') }),
    actions: [
      { label: t('Close'), id: 'close' },
      { label: t('Close browser'), kind: 'danger', id: 'close-kept-browser', onClick: closeKeptBrowser },
    ],
  });
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
  document.getElementById('active-job').addEventListener('click', () => (activeJob ? openActiveJobBox() : openKeptBrowserBox()));
  setInterval(refreshActiveJob, ACTIVE_JOB_POLL_MS);
  window.addEventListener('hashchange', route);
  window.addEventListener('beforeunload', () => {
    if (window.currentEditor) {
      window.currentEditor.flushPassword();
      window.currentEditor.save.flush();
    }
  });
  keepBrowser = await api('GET', '/api/keep-browser').then(data => data.keep).catch(() => false);
  await refreshActiveJob();
  checkForUpdate();
  route();
}

start();
