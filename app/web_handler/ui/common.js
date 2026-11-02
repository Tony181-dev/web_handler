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

// English is written in the code. Other languages come from ui/lang/<language>.json.
const LANGUAGES = [['en', 'English'], ['vi', 'Tiếng Việt']]; // i18n-exempt: each language's own name
let currentLanguage = 'en';
let translations = {};

function t(text, values) {
  let result = translations[text] || text;
  for (const [name, value] of Object.entries(values || {})) result = result.split(`{${name}}`).join(value);
  return result;
}

const READ_ATTEMPTS = 3;
const REQUEST_TIMEOUT_MS = 30000;
const LONG_REQUEST_TIMEOUT_MS = 180000;
const LANGUAGE_REQUEST_TIMEOUT_MS = 3000;

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
        const error = new Error(data.error || t('Error {status}', { status: response.status }));
        error.fromServer = true;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.fromServer) throw error;
      lastError = error.name === 'AbortError' ? new Error(t('The app did not answer in time.')) : new Error(t('Cannot reach the app. Is the Start.bat window still open?'));
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
        { label: t('Cancel') },
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
      actions: [{ label: t('Cancel') }, { label: t('OK'), kind: 'primary', id: 'confirm', onClick: () => { answer = input.value.trim(); } }],
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
  goto: 'Open link', login: 'Login', fill: 'Fill', click: 'Click', select: 'Select', check: 'Check',
  read: 'Read value', wait_for: 'Wait for element', wait: 'Wait', verify: 'Verify', loop: 'Loop', manual_pause: 'Manual step',
};
const PALETTE = ['goto', 'login', 'fill', 'click', 'select', 'check', 'read', 'wait_for', 'wait', 'verify', 'manual_pause', 'loop'];
const DEFAULT_WAIT_SECONDS = 3;
const STEP_HELP = {
  goto: 'Opens a web address in the browser. The address can be typed in or come from an Excel column.',
  login: 'Logs in on the open page: finds the login form, fills in the account and password, and sends it. The block fails if the form is still there afterwards.',
  fill: 'Fills a value into a box on the page. The value can be: text you type in, the value of each cell in an Excel column, a saved Secret, or a Variable.',
  click: 'Clicks a button or link on the page. Tick the option to wait if the click opens another page.',
  select: 'Picks an option from a dropdown list by the text it shows.',
  check: 'Ticks or unticks a checkbox.',
  read: 'Reads a value from the page and keeps it in a variable or writes it to a column of the result file.',
  wait_for: 'Waits until an element appears on the page, then goes on. The block fails if it does not appear in time.',
  wait: 'Pauses for a number of seconds, 3 by default, then goes on to the next block.',
  verify: 'Checks that the page is as expected, by the value of an element or by the address. The block fails if it is not.',
  loop: 'Adds an empty loop. Drag blocks into it, and they repeat for each Excel row or a set number of times.',
  manual_pause: 'Pauses so you can do something by hand in Chrome, such as entering a login code, then click Continue.',
};

async function loadLanguage() {
  try {
    // Nothing shows until this answers, so a dropped answer must not leave the page blank for long.
    const { language } = await api('GET', '/api/language', undefined, { timeout: LANGUAGE_REQUEST_TIMEOUT_MS });
    if (language !== 'en') translations = await (await fetch(`/ui/lang/${language}.json`)).json();
    currentLanguage = language;
  } catch (error) { /* The interface still works in English. */ }
  document.documentElement.lang = currentLanguage;
  for (const kind of Object.keys(STEP_NAMES)) STEP_NAMES[kind] = t(STEP_NAMES[kind]);
}

// A short explanation beside a control, there only while the pointer or keyboard focus is on it.
let helpNode = null;

function hideHelp() {
  if (helpNode) helpNode.remove();
  helpNode = null;
}

function showHelp(control, text) {
  hideHelp();
  const box = control.getBoundingClientRect();
  helpNode = el('div.tool-help', { id: 'tool-help', role: 'tooltip', text });
  helpNode.style.left = `${box.right + 8}px`;
  helpNode.style.top = `${box.top}px`;
  document.body.append(helpNode);
  const overflow = helpNode.getBoundingClientRect().bottom - window.innerHeight + 8;
  if (overflow > 0) helpNode.style.top = `${box.top - overflow}px`;
}

function attachHelp(control, text) {
  control.addEventListener('pointerenter', () => showHelp(control, text));
  control.addEventListener('pointerleave', hideHelp);
  control.addEventListener('focus', () => showHelp(control, text));
  control.addEventListener('blur', hideHelp);
  // Out of the way as soon as the control is pressed or dragged.
  control.addEventListener('pointerdown', hideHelp);
  return control;
}

function statusBadge(status) {
  const kind = { OK: 'ok', Failed: 'bad', Skipped: 'neutral', Unclear: 'unknown' }[status] || 'neutral';
  return el('span.badge.' + kind, { text: t(status) });
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
