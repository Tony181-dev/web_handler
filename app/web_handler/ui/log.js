'use strict';

// The system log: a panel opened from the Log button, on every screen.

const LOG_POLL_MS = 1000;
const logPanel = { node: null, list: null, lastId: 0, timer: null };

function logButton() {
  return el('button.btn.log-toggle', { text: 'Log', title: 'Xem log chạy của hệ thống', onclick: toggleLogPanel });
}

function addLogLines(lines) {
  const list = logPanel.list;
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 30;
  for (const line of lines) {
    logPanel.lastId = line.id;
    list.append(el('div.' + line.level, { text: `${line.time.slice(11)}  ${line.text}`, title: line.time }));
  }
  if (lines.length && atBottom) list.scrollTop = list.scrollHeight;
  logPanel.empty.hidden = list.childElementCount > 0;
}

async function pollLog() {
  try {
    const { lines } = await api('GET', `/api/log?after=${logPanel.lastId}`);
    if (logPanel.node) addLogLines(lines);
  } catch (error) { /* The next poll tries again, and the app shows its own connection errors. */ }
  if (logPanel.node) logPanel.timer = setTimeout(pollLog, LOG_POLL_MS);
}

function closeLogPanel() {
  clearTimeout(logPanel.timer);
  if (logPanel.node) logPanel.node.remove();
  logPanel.node = null;
}

function toggleLogPanel() {
  if (logPanel.node) {
    closeLogPanel();
    return;
  }
  logPanel.lastId = 0;
  logPanel.list = el('div.log', { id: 'log-lines' });
  logPanel.empty = el('p.muted', { id: 'log-empty', text: 'Chưa có log.', style: 'padding:10px 12px' });
  logPanel.node = el('section.log-panel', { id: 'log-panel', 'aria-label': 'Log hệ thống' },
    el('div.log-panel-head', null,
      el('h3', { text: 'Log hệ thống' }),
      el('span.spacer'),
      el('button.btn.small', {
        id: 'log-clear',
        text: 'Clear',
        onclick: async () => {
          try {
            await api('DELETE', '/api/log');
            logPanel.list.replaceChildren();
            logPanel.empty.hidden = false;
          } catch (error) { showError(error); }
        },
      }),
      el('button.btn.small', { id: 'log-close', text: 'Close', onclick: closeLogPanel })),
    logPanel.empty,
    logPanel.list);
  document.body.append(logPanel.node);
  pollLog();
}
