'use strict';

// Small DOM helper: el('div.card', {onclick}, child, 'text').
function el(spec, attrs, ...children) {
  const [tag, ...classes] = spec.split('.');
  const node = document.createElement(tag || 'div');
  if (classes.length) node.className = classes.join(' ');
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'value') node.value = value;
    else if (key === 'checked' || key === 'disabled' || key === 'selected') node[key] = Boolean(value);
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

// replaceChildren() would print a null child as the text "null".
function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter(child => child !== null && child !== undefined && child !== false));
}

const READ_ATTEMPTS = 3;
const REQUEST_TIMEOUT_MS = 30000;
const LONG_REQUEST_TIMEOUT_MS = 180000;

// Reads are retried: a network filter can drop a local response now and then.
async function api(method, path, body, options) {
  const attempts = method === 'GET' ? READ_ATTEMPTS : 1;
  const timeout = (options && options.timeout) || REQUEST_TIMEOUT_MS;
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {
        method,
        signal: controller.signal,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(data.error || `Lỗi ${response.status}`);
        error.fromServer = true;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.fromServer) throw error;
      lastError = error.name === 'AbortError' ? new Error('App không trả lời kịp.') : new Error('Không kết nối được tới app. Cửa sổ Start.bat còn mở không?');
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

function toast(message, kind) {
  const node = el('div.toast' + (kind ? '.' + kind : ''), { text: message });
  document.getElementById('toasts').append(node);
  setTimeout(() => node.remove(), kind === 'bad' ? 7000 : 3500);
}

function showError(error) {
  toast(error.message || String(error), 'bad');
}

function openModal({ title, body, actions, wide, onClose, dismissable = true }) {
  const root = document.getElementById('modal-root');
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  const onKey = event => { if (event.key === 'Escape' && dismissable) close(); };
  const foot = el('div.modal-foot', null, (actions || []).map(action => {
    const classes = 'btn' + (action.kind ? '.' + action.kind : '');
    return el('button.' + classes, {
      text: action.label,
      disabled: action.disabled,
      'data-action': action.id,
      onclick: async () => {
        if (action.onClick) {
          const keepOpen = await action.onClick(close);
          if (keepOpen === true) return;
        }
        close();
      },
    });
  }));
  const back = el('div.modal-back', {
    onmousedown: event => { if (event.target === back && dismissable) close(); },
  }, el('div.modal' + (wide ? '.wide' : ''), { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    el('div.modal-head', null, el('h2', { text: title })),
    el('div.modal-body', null, body),
    actions && actions.length ? foot : null));
  document.addEventListener('keydown', onKey);
  root.append(back);
  const first = back.querySelector('input, textarea, select');
  if (first) first.focus();
  return { close, root: back };
}

function confirmBox(title, message, label, kind) {
  return new Promise(resolve => {
    let answer = false;
    openModal({
      title,
      body: el('p', { text: message }),
      onClose: () => resolve(answer),
      actions: [
        { label: 'Cancel' },
        { label, kind: kind || 'primary', id: 'confirm', onClick: () => { answer = true; } },
      ],
    });
  });
}

function promptBox(title, label, initial, hint) {
  return new Promise(resolve => {
    let answer = null;
    const input = el('input', { type: 'text', value: initial || '' });
    const submit = modal => { answer = input.value.trim(); modal(); };
    const box = openModal({
      title,
      body: el('div.field', null, el('label.field-label', { text: label }), input, hint ? el('div.hint', { text: hint }) : null),
      onClose: () => resolve(answer),
      actions: [{ label: 'Cancel' }, { label: 'OK', kind: 'primary', id: 'confirm', onClick: () => { answer = input.value.trim(); } }],
    });
    input.addEventListener('keydown', event => { if (event.key === 'Enter') submit(box.close); });
    input.select();
  });
}

function setTopbarTitle(node) {
  const holder = document.getElementById('topbar-title');
  holder.replaceChildren();
  if (node) holder.append(node);
}

function debounce(action, wait) {
  let timer;
  const debounced = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => action(...args), wait);
  };
  debounced.flush = (...args) => { clearTimeout(timer); return action(...args); };
  debounced.cancel = () => clearTimeout(timer);
  return debounced;
}

// Per-viewer conveniences only; the page must work when storage is unavailable.
const memory = {
  get(key) {
    try { return localStorage.getItem('wh.' + key); } catch (error) { return null; }
  },
  set(key, value) {
    try { localStorage.setItem('wh.' + key, value); } catch (error) { /* not essential */ }
  },
};

const STEP_NAMES = {
  goto: 'Open link', fill: 'Fill', click: 'Click', select: 'Select', check: 'Check',
  read: 'Read value', wait_for: 'Wait for element', wait: 'Wait', verify: 'Verify', loop: 'Loop', manual_pause: 'Manual step',
};
const PALETTE = ['goto', 'fill', 'click', 'select', 'check', 'read', 'wait_for', 'wait', 'verify', 'manual_pause'];
const DEFAULT_WAIT_SECONDS = 3;

function statusBadge(status) {
  const kind = { 'OK': 'ok', 'Lỗi': 'bad', 'Bỏ qua': 'neutral', 'Chưa rõ': 'unknown' }[status] || 'neutral';
  return el('span.badge.' + kind, { text: status });
}

function columnLetters(count) {
  const letters = [];
  for (let index = 1; index <= count; index++) {
    let value = index;
    let name = '';
    while (value > 0) {
      const rest = (value - 1) % 26;
      name = String.fromCharCode(65 + rest) + name;
      value = Math.floor((value - 1) / 26);
    }
    letters.push(name);
  }
  return letters;
}
