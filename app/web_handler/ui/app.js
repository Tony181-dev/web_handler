'use strict';

async function route() {
  if (window.leaveView) window.leaveView();
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
      el('a.btn', { href: '#/', text: 'Back to home' })));
  }
}

document.getElementById('log-corner').append(logButton());
document.getElementById('nav-setup').addEventListener('click', () => { location.hash = '#/setup'; });
document.getElementById('nav-secrets').addEventListener('click', openSecrets);
window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', () => {
  if (window.currentEditor) window.currentEditor.save.flush();
});
route();
