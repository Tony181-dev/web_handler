'use strict';

// The flow diagram: blocks arrange and connect themselves; people only choose order.
// `editor` provides: flow, selection (Set of ids), select(), changed(), addStep(), issuesByStep.

const DRAG_THRESHOLD_PX = 5;

// Where a whole value comes from: ['excel', 'C'], ['secret', 'name'], ['variable', 'name'] or ['text'].
function sourceOf(value) {
  const match = /^\{\{\s*(.+?)\s*\}\}$/.exec(value || '');
  const token = match && !match[1].includes('{') ? match[1] : '';
  if (/^[A-Z]{1,3}$/.test(token)) return ['excel', token];
  if (token.startsWith('secret:')) return ['secret', token.slice(7).trim()];
  if (token.startsWith('var:')) return ['variable', token.slice(4).trim()];
  return ['text'];
}

// The lists of blocks a block holds: a loop has one, an "If" block its Yes and No branches.
function branchesOf(step) {
  if (step.do === 'loop') return [step.steps || []];
  if (step.do === 'if') return [step.then || [], step.else || []];
  return [];
}

// Every block in these lists and the blocks they hold, in running order.
function flattenSteps(steps) {
  const all = [];
  const visit = list => list.forEach(step => {
    all.push(step);
    branchesOf(step).forEach(visit);
  });
  visit(steps);
  return all;
}

function containsStep(steps, id) {
  return flattenSteps(steps).some(step => step.id === id);
}

// Blocks drawn as a frame around other blocks, on a row of their own.
function isFrame(step) {
  return step.do === 'loop' || step.do === 'if';
}

// Same rule as stop_column() in flow.py: the first column the loop's blocks use decides where rows end.
function stopColumn(flow) {
  const loop = findLoop(flow);
  if (!loop) return '';
  for (const step of flattenSteps(loop.steps)) {
    for (const key of ['url', 'account', 'value', 'equals', 'contains', 'url_contains', 'url_not_contains']) {
      const match = /\{\{\s*([A-Z]{1,3})\s*\}\}/.exec(step[key] || '');
      if (match) return match[1];
    }
  }
  const required = (flow.excel && flow.excel.required) || [];
  return required[0] || '';
}

function runsUntilText(column) {
  return column ? t('Runs until column {column} ends (first empty cell)', { column }) : t('Runs until the column ends (first empty cell)');
}

function stepSummary(step) {
  const target = step.target && step.target.primary;
  switch (step.do) {
    case 'goto': {
      const [source, name] = sourceOf(step.url);
      if (source === 'excel') return t('Excel, column {name}', { name });
      return step.url || step.note || t('no link yet');
    }
    case 'login': {
      const [source, name] = sourceOf(step.account);
      if (source === 'excel') return t('Excel, column {name}', { name });
      if (source === 'text') return step.account ? (step.hide_account ? '••••••' : step.account) : t('no account yet');
      return name;
    }
    case 'verify': return step.url_not_contains ? t('URL without {text}', { text: step.url_not_contains }) : step.url_contains ? t('URL with {text}', { text: step.url_contains }) : (target || t('no condition yet'));
    case 'if': return conditionText(step);
    case 'manual_pause': return step.message || t('click Continue');
    case 'wait': return t('{seconds} s', { seconds: step.seconds });
    // A template's blank block says what it is for.
    default: return target || step.note || t('no element yet');
  }
}

// What an "If" block asks, in pieces: the words as strings, and what it compares as { part: text },
// so the diagram can set the element and the value apart from the words between them.
function conditionPieces(step) {
  const element = step.target && step.target.primary;
  const value = step.value || '…';
  // The sentence is translated with its placeholders still in, then the placeholders become pieces.
  const fill = (template, values) => template.split(/(\{\w+\})/).filter(Boolean).map(piece => {
    const name = /^\{(\w+)\}$/.exec(piece);
    return name && name[1] in values ? { part: values[name[1]] } : piece;
  });
  if (!element && step.test !== 'url_contains') return [t('no element yet')];
  switch (step.test) {
    case 'exists': return fill(step.negate ? t('{element} is not on the page') : t('{element} is on the page'), { element });
    case 'equals': return fill(step.negate ? t('{element} does not equal {value}') : t('{element} equals {value}'), { element, value });
    case 'contains': return fill(step.negate ? t('{element} does not contain {value}') : t('{element} contains {value}'), { element, value });
    case 'url_contains': return fill(step.negate ? t('URL without {text}') : t('URL with {text}'), { text: value });
    default: return [t('no condition yet')];
  }
}

// The same, as a sentence.
function conditionText(step) {
  return conditionPieces(step).map(piece => (typeof piece === 'string' ? piece : piece.part)).join('');
}

// The same, for the diagram: what is compared stands in a box of its own.
function conditionNodes(step) {
  return conditionPieces(step).map(piece => (typeof piece === 'string' ? piece : el('span.cond-part', { text: piece.part })));
}

function stepIsIncomplete(step) {
  const targetSteps = ['fill', 'click', 'select', 'check', 'read', 'ocr', 'wait_for'];
  if (step.do === 'goto' && !step.url) return true;
  if (step.do === 'login' && !(step.account && step.password)) return true;
  if (targetSteps.includes(step.do) && !(step.target && step.target.primary)) return true;
  if ((step.do === 'fill' || step.do === 'select') && !step.value) return true;
  if ((step.do === 'read' || step.do === 'ocr') && !step.save_to_var && !step.save_to_column) return true;
  if (step.do === 'if') {
    if (!['exists', 'equals', 'contains', 'url_contains'].includes(step.test)) return true;
    if (step.test !== 'url_contains' && !(step.target && step.target.primary)) return true;
    if (step.test !== 'exists' && !step.value) return true;
    if (!(step.then || []).length && !(step.else || []).length) return true;
  }
  if (step.do === 'verify') {
    const hasUrl = step.url_contains || step.url_not_contains;
    const hasElement = (step.equals || step.contains) && step.target && step.target.primary;
    if (!hasUrl && !hasElement) return true;
  }
  return false;
}

function nextStepId(flow) {
  let highest = 0;
  const visit = steps => steps.forEach(step => {
    const number = parseInt(String(step.id || '').replace(/\D/g, ''), 10);
    if (number > highest) highest = number;
    branchesOf(step).forEach(visit);
  });
  visit(flow.steps);
  return 's' + (highest + 1);
}

function findLoop(flow) {
  return flow.steps.find(step => step.do === 'loop') || null;
}

function findStep(flow, id) {
  return flattenSteps(flow.steps).find(step => step.id === id) || null;
}

// The blocks a run goes through before this one, with the loop's blocks taken once.
// A block in a branch is reached through that branch, whatever the condition would say.
function stepsBefore(flow, id) {
  const before = [];
  const visit = steps => {
    for (const step of steps) {
      if (step.id === id) return true;
      if (step.do === 'loop') {
        if (visit(step.steps)) return true;
      } else if (step.do === 'if' && containsStep(branchesOf(step).flat(), id)) {
        return visit(branchesOf(step).find(branch => containsStep(branch, id)));
      } else {
        before.push(step);
      }
    }
    return false;
  };
  visit(flow.steps);
  return before;
}

function listContaining(flow, id) {
  const search = steps => {
    if (steps.some(step => step.id === id)) return steps;
    for (const step of steps) {
      for (const branch of branchesOf(step)) {
        const found = search(branch);
        if (found) return found;
      }
    }
    return null;
  };
  return search(flow.steps);
}

// The list a lane of the diagram shows: top, loop, or if:<id>:then and if:<id>:else for the branches.
function laneList(flow, name) {
  if (name === 'top') return flow.steps;
  if (name === 'loop') return findLoop(flow).steps;
  const [, id, branch] = name.split(':');
  return findStep(flow, id)[branch];
}

function renderCanvas(editor) {
  const canvas = editor.canvasNode;
  const scrollTop = canvas.scrollTop;
  const scrollLeft = canvas.scrollLeft;
  const flow = editor.flow;
  canvas.replaceChildren();
  canvas.classList.toggle('vertical', editor.view === 'vertical');

  // Always there, so that the bar appearing does not push the blocks down.
  const loop = findLoop(flow);
  const showBar = editor.selection.size > 0 && !(loop && editor.selection.has(loop.id));
  canvas.append(el('div.selection-slot', null, showBar ? renderSelectionBar(editor) : null));

  const content = el('div.canvas-content', { id: 'canvas-content', style: `zoom:${editor.zoom}` });
  if (!flow.steps.length) {
    content.append(el('div.canvas-empty', null,
      el('h2', { text: t('Drag blocks here from the left') }),
      el('p', { text: t('Or click Record, and the tool creates the blocks as you do the work once by hand.'), style: 'margin-top:6px' })));
  }
  content.append(renderLane(editor, flow.steps, 'top'));
  canvas.append(content);
  canvas.scrollTop = scrollTop;
  canvas.scrollLeft = scrollLeft;
}

function renderLane(editor, steps, name, emptyText) {
  const lane = el('div.lane', { 'data-lane': name });
  steps.forEach((step, index) => {
    if (step.do === 'loop') lane.append(el('div.item.block', { 'data-index': index }, renderLoop(editor, step)));
    else if (step.do === 'if') lane.append(el('div.item.block', { 'data-index': index }, renderIf(editor, step)));
    else lane.append(renderItem(editor, step, index, index < steps.length - 1 && !isFrame(steps[index + 1])));
  });
  if (!steps.length && emptyText) lane.append(el('div.loop-empty', { text: emptyText }));
  return lane;
}

function arrowNode() {
  return el('div.arrow', { text: '→', 'aria-hidden': 'true' });
}

function renderItem(editor, step, index, withArrow) {
  const classes = ['node'];
  if (editor.selection.has(step.id)) classes.push(editor.selection.size > 1 ? 'multi' : 'selected');
  if (stepIsIncomplete(step)) classes.push('invalid');
  if (step.hide_value || step.hide_account) classes.push('concealed');
  if (editor.litSteps.has(step.id)) classes.push('lit');
  if (editor.failedStep === step.id) classes.push('failed');
  const node = el('div.' + classes.join('.'), {
    'data-step': step.id,
    tabindex: '0',
    role: 'button',
    'aria-label': `${STEP_NAMES[step.do]}: ${stepSummary(step)}`,
    onkeydown: event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        pick(editor, step.id, event);
      }
    },
  }, el('b', { text: STEP_NAMES[step.do] }), el('span', { text: stepSummary(step), title: stepSummary(step) }));
  node.addEventListener('pointerdown', event => startNodeDrag(event, editor, step, node.parentElement.classList.contains('node-pair') ? node.parentElement : node));
  const chip = variableChip(step);
  if (!chip) return el('div.item', { 'data-index': index }, node, withArrow ? arrowNode() : null);
  // The block and its variable are one piece: dragging either moves both.
  const pair = el('div.node-pair', null, node, chip);
  chip.addEventListener('pointerdown', event => startNodeDrag(event, editor, step, pair));
  return el('div.item', { 'data-index': index }, pair, withArrow ? arrowNode() : null);
}

// The variable a reading block fills, as a square beside it, so later blocks can be seen to use it.
function variableChip(step) {
  if (!['read', 'ocr'].includes(step.do) || (step.save_to_column !== undefined && step.save_to_column !== null)) return null;
  const name = step.save_to_var;
  return el('div.var-chip', { title: name || '' },
    el('b', { text: t('Variable') }),
    el('span', { text: name || t('no name yet') }));
}

function renderLoop(editor, loop) {
  const selected = editor.selection.has(loop.id);
  const lane = renderLane(editor, loop.steps, 'loop', t('Drop blocks here to repeat them.'));
  const description = loop.source === 'count' ? t('{count} time(s)', { count: loop.count || 1 }) : t('each Excel row');
  const frame = el('div.loop-frame' + (selected ? '.selected' : ''), { 'data-loop': loop.id },
    el('div.loop-head', null,
      el('span', { text: `↻ ${STEP_NAMES.loop} · ${description}` }),
      el('span.spacer'),
      el('button.btn.small', {
        id: 'remove-loop',
        text: t('Remove loop'),
        onclick: event => { event.stopPropagation(); removeLoop(editor); },
        onpointerdown: event => event.stopPropagation(),
      })),
    lane);
  frame.addEventListener('click', event => {
    if (!editor.ignoreClick && !event.target.closest('.node, .if-frame')) editor.select(loop.id);
  });
  return frame;
}

// An "If" block: its condition on top, then the Yes and No branches, each a lane of its own.
function renderIf(editor, step) {
  const classes = ['if-frame'];
  if (editor.selection.has(step.id)) classes.push(editor.selection.size > 1 ? 'multi' : 'selected');
  if (stepIsIncomplete(step)) classes.push('invalid');
  if (editor.litSteps.has(step.id)) classes.push('lit');
  if (editor.failedStep === step.id) classes.push('failed');
  const head = el('div.if-head', {
    'data-step': step.id, tabindex: '0', role: 'button',
    'aria-label': `${STEP_NAMES.if}: ${conditionText(step)}`,
    onkeydown: event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        pick(editor, step.id, event);
      }
    },
  }, el('b', { text: `◇ ${STEP_NAMES.if}` }), el('span', { title: conditionText(step) }, conditionNodes(step)));
  const branch = (key, label) => el('div.branch', { 'data-branch': key },
    el('div.branch-label', { text: label }),
    renderLane(editor, step[key] || [], `if:${step.id}:${key}`, t('Drop blocks here.')));
  const frame = el('div.' + classes.join('.'), { 'data-if': step.id }, head,
    el('div.branches', null, branch('then', t('✓ Yes')), branch('else', t('✗ No'))));
  head.addEventListener('pointerdown', event => startNodeDrag(event, editor, step, frame));
  frame.addEventListener('click', event => {
    event.stopPropagation();
    // A click on the condition was already taken when it was pressed.
    if (!editor.ignoreClick && !event.target.closest('.node, .if-head')) pick(editor, step.id, event);
  });
  return frame;
}

// A click selects the block; with Shift or Ctrl held it adds the block to the selection or takes it out.
function pick(editor, id, event) {
  if (event.shiftKey || event.ctrlKey || event.metaKey) editor.toggleSelect(id);
  else editor.select(id);
}

function renderSelectionBar(editor) {
  const problem = loopProblem(editor);
  return el('div.selection-bar', { onpointerdown: event => event.stopPropagation() },
    el('span', { text: t('Selected blocks: {count}', { count: editor.selection.size }) }),
    el('button.btn.small.primary', {
      id: 'make-loop',
      text: t('↻ Create loop'),
      disabled: Boolean(problem),
      title: problem || '',
      onclick: () => makeLoop(editor),
    }),
    el('button.btn.small', { id: 'duplicate-selection', text: t('Duplicate'), onclick: () => editor.duplicateSelection() }),
    el('button.btn.small.danger', { text: t('Delete'), onclick: () => editor.deleteSelection() }),
    problem && editor.selection.size > 1 ? el('span.muted', { id: 'loop-problem', text: problem, title: problem }) : null);
}

// The same diagram, read-only, showing how far a run has got. stateOf(id) gives a class or null,
// branchOf(id) the branch an "If" block took, if it has run.
function renderRunDiagram(flow, stateOf, branchOf, vertical) {
  const item = (step, withArrow) => {
    const node = el('div.' + ['node', 'readonly', stateOf(step.id)].filter(Boolean).join('.'), { 'data-step': step.id },
      el('b', { text: STEP_NAMES[step.do] }), el('span', { text: stepSummary(step), title: stepSummary(step) }));
    const chip = variableChip(step);
    return el('div.item', null, chip ? el('div.node-pair', null, node, chip) : node, withArrow ? arrowNode() : null);
  };
  const lane = steps => el('div.lane', null, steps.map((step, index) => {
    if (step.do === 'loop') {
      const description = step.source === 'count' ? t('{count} time(s)', { count: step.count || 1 }) : t('each Excel row');
      return el('div.item.block', null, el('div.loop-frame.readonly', null,
        el('div.loop-head', { text: `↻ ${STEP_NAMES.loop} · ${description}` }), lane(step.steps)));
    }
    if (step.do === 'if') {
      const taken = branchOf ? branchOf(step.id) : null;
      const branch = (key, label) => el('div.' + ['branch', taken === key ? 'taken' : '', taken && taken !== key ? 'skipped' : ''].filter(Boolean).join('.'), { 'data-branch': key },
        el('div.branch-label', { text: label }), lane(step[key] || []));
      return el('div.item.block', null, el('div.' + ['if-frame', 'readonly', stateOf(step.id)].filter(Boolean).join('.'), { 'data-if': step.id },
        el('div.if-head', { 'data-step': step.id }, el('b', { text: `◇ ${STEP_NAMES.if}` }), el('span', { title: conditionText(step) }, conditionNodes(step))),
        el('div.branches', null, branch('then', t('✓ Yes')), branch('else', t('✗ No')))));
    }
    return item(step, index < steps.length - 1 && !isFrame(steps[index + 1]));
  }));
  return el('div.run-diagram' + (vertical ? '.vertical' : ''), null, lane(flow.steps));
}

function selectionIndexes(editor) {
  return editor.flow.steps
    .map((step, index) => (editor.selection.has(step.id) ? index : -1))
    .filter(index => index >= 0);
}

function loopProblem(editor) {
  if (findLoop(editor.flow)) return t('A flow can have only one loop.');
  const indexes = selectionIndexes(editor);
  if (indexes.length !== editor.selection.size) return t('Select only blocks outside the loop.');
  if (indexes[indexes.length - 1] - indexes[0] + 1 !== indexes.length) return t('The blocks must be next to each other.');
  return '';
}

function makeLoop(editor) {
  const problem = loopProblem(editor);
  if (problem) { toast(problem, 'bad'); return; }
  const indexes = selectionIndexes(editor);
  const flow = editor.flow;
  const children = flow.steps.slice(indexes[0], indexes[indexes.length - 1] + 1);
  const usesColumn = /\{\{\s*[A-Z]{1,3}\s*\}\}/.test(JSON.stringify(children));
  const loop = {
    id: nextStepId(flow), do: 'loop',
    source: editor.excelPath || flow.excel || usesColumn ? 'excel' : 'count',
    count: 1, steps: children,
  };
  flow.steps.splice(indexes[0], children.length, loop);
  if (loop.source === 'excel') editor.ensureExcelSettings();
  editor.select(loop.id);
  editor.changed();
}

function removeLoop(editor) {
  const flow = editor.flow;
  const loop = findLoop(flow);
  if (!loop) return;
  const index = flow.steps.indexOf(loop);
  flow.steps.splice(index, 1, ...loop.steps);
  editor.select(null);
  editor.changed();
}

// Dragging

// depth says where a block may go: 'top' only in the main lane (a loop), 'loop' also in the loop
// (an "If" block), 'any' also in the branches of an "If" block.
function dropPosition(editor, x, y, depth) {
  // The innermost lane under the pointer that may take the block, else the main lane.
  const canvasBox = editor.canvasNode.getBoundingClientRect();
  if (x < canvasBox.left || x > canvasBox.right || y < canvasBox.top || y > canvasBox.bottom) return null;
  const hovered = document.elementFromPoint(x, y);
  const allowed = zone => (zone.classList.contains('branch') ? depth === 'any' : depth !== 'top');
  let zone = hovered && hovered.closest ? hovered.closest('.branch, .loop-frame') : null;
  while (zone && !allowed(zone)) zone = zone.parentElement.closest('.branch, .loop-frame');
  const lane = zone ? zone.querySelector(':scope > .lane') : editor.canvasNode.querySelector('[data-lane=top]');
  if (!lane) return null;
  const vertical = editor.view === 'vertical';
  const items = Array.from(lane.children).filter(child => child.classList.contains('item'));
  let index = items.length;
  for (let position = 0; position < items.length; position++) {
    const box = items[position].getBoundingClientRect();
    const before = vertical
      ? y < box.top + box.height / 2
      : y < box.top || (y <= box.bottom && x < box.left + box.width / 2);
    if (before) { index = position; break; }
  }
  return { lane: lane.dataset.lane, index, items, vertical };
}

function placeMarker(marker, position, pointerY) {
  const { items, index } = position;
  if (!items.length) {
    marker.style.display = 'none';
    return;
  }
  marker.style.display = 'block';
  const anchor = items[Math.min(index, items.length - 1)];
  const box = anchor.getBoundingClientRect();
  if (position.vertical) {
    // Blocks stand in a column, so the place between two of them is a line across.
    const node = anchor.querySelector('.node-pair, .node, .loop-frame, .if-frame') || anchor;
    marker.style.left = box.left + 'px';
    marker.style.top = (index >= items.length ? box.bottom + 2 : box.top - 3) + 'px';
    marker.style.width = Math.max(node.getBoundingClientRect().width, 40) + 'px';
    marker.style.height = '3px';
    return;
  }
  marker.style.width = '';
  // The next block is on a lower row, but the pointer is still at the end of this one.
  const previous = index > 0 && index < items.length ? items[index - 1] : null;
  if (previous && pointerY < box.top && !previous.classList.contains('block')) {
    const node = (previous.querySelector('.node-pair') || previous.querySelector('.node')).getBoundingClientRect();
    marker.style.left = node.right + 4 + 'px';
    marker.style.top = node.top + 'px';
    marker.style.height = node.height + 'px';
    return;
  }
  marker.style.left = (index >= items.length ? box.right + 2 : box.left - 3) + 'px';
  marker.style.top = box.top + 'px';
  marker.style.height = Math.max(box.height, 40) + 'px';
}

function runDrag(event, label, onDrop, onStart, onEnd, depth) {
  const editor = runDrag.editor;
  const startX = event.clientX;
  const startY = event.clientY;
  let started = false;
  let ghost = null;
  let marker = null;

  const move = moveEvent => {
    if (!started) {
      if (Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) < DRAG_THRESHOLD_PX) return;
      started = true;
      ghost = el('div.drag-ghost', { text: label });
      marker = el('div.drop-marker');
      document.body.append(ghost, marker);
      if (onStart) onStart();
    }
    ghost.style.left = moveEvent.clientX + 10 + 'px';
    ghost.style.top = moveEvent.clientY + 8 + 'px';
    const position = dropPosition(editor, moveEvent.clientX, moveEvent.clientY, depth);
    if (position) placeMarker(marker, position, moveEvent.clientY);
    else marker.style.display = 'none';
  };
  const finish = upEvent => {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', finish);
    document.removeEventListener('pointercancel', cancel);
    if (ghost) ghost.remove();
    if (marker) marker.remove();
    if (onEnd) onEnd();
    if (!started) { if (onDrop.click) onDrop.click(); return; }
    const position = dropPosition(editor, upEvent.clientX, upEvent.clientY, depth);
    if (position) onDrop(position);
  };
  const cancel = () => {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', finish);
    document.removeEventListener('pointercancel', cancel);
    if (ghost) ghost.remove();
    if (marker) marker.remove();
    if (onEnd) onEnd();
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', finish);
  document.addEventListener('pointercancel', cancel);
}

// Where a block of this kind may be dropped, as dropPosition() takes it.
function dropDepth(kind) {
  if (kind === 'loop') return 'top';
  return kind === 'if' ? 'loop' : 'any';
}

function startPaletteDrag(event, editor, kind) {
  event.preventDefault();
  runDrag.editor = editor;
  const drop = position => {
    if (kind === 'loop') { editor.addLoop(position.index); return; }
    const step = editor.makeStep(kind);
    laneList(editor.flow, position.lane).splice(position.index, 0, step);
    editor.select(step.id);
    editor.changed();
  };
  // A plain click appends the block, for people who prefer not to drag.
  drop.click = () => editor.addStep(kind);
  runDrag(event, STEP_NAMES[kind], drop, null, null, dropDepth(kind));
}

function startNodeDrag(event, editor, step, node) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  node.focus({ preventScroll: true });
  runDrag.editor = editor;
  const drop = position => {
    const source = listContaining(editor.flow, step.id);
    if (!source) return;
    const from = source.indexOf(step);
    const target = laneList(editor.flow, position.lane);
    let index = position.index;
    source.splice(from, 1);
    if (source === target && from < index) index -= 1;
    target.splice(index, 0, step);
    editor.select(step.id);
    editor.changed();
  };
  drop.click = () => pick(editor, step.id, event);
  runDrag(event, STEP_NAMES[step.do], drop, () => node.classList.add('dragging'), () => node.classList.remove('dragging'), dropDepth(step.do));
}

// Drawing a box over blocks selects them, ready to become the loop.
function startLasso(event, editor) {
  if (event.button !== 0 || event.target.closest('.node, button, .selection-bar, .loop-head, .if-head')) return;
  const startX = event.clientX;
  const startY = event.clientY;
  let box = null;
  const move = moveEvent => {
    if (!box && Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) < DRAG_THRESHOLD_PX) return;
    if (!box) {
      box = el('div.lasso');
      document.body.append(box);
    }
    box.style.left = Math.min(startX, moveEvent.clientX) + 'px';
    box.style.top = Math.min(startY, moveEvent.clientY) + 'px';
    box.style.width = Math.abs(moveEvent.clientX - startX) + 'px';
    box.style.height = Math.abs(moveEvent.clientY - startY) + 'px';
  };
  const up = upEvent => {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    const adding = event.shiftKey || event.ctrlKey || event.metaKey;
    if (!box) {
      if (!adding && !event.target.closest('.loop-frame, .if-frame')) editor.select(null);
      return;
    }
    box.remove();
    // The click that ends the box is not a click on the loop or the "If" block it was drawn in.
    editor.ignoreClick = true;
    setTimeout(() => { editor.ignoreClick = false; }, 0);
    const left = Math.min(startX, upEvent.clientX);
    const right = Math.max(startX, upEvent.clientX);
    const top = Math.min(startY, upEvent.clientY);
    const bottom = Math.max(startY, upEvent.clientY);
    const hits = Array.from(editor.canvasNode.querySelectorAll('.node, .if-head')).filter(node => {
      const rect = node.getBoundingClientRect();
      return rect.left < right && rect.right > left && rect.top < bottom && rect.bottom > top;
    });
    // An "If" block taken whole brings its branches with it, so they are not chosen on their own.
    const frames = new Set(hits.filter(node => node.classList.contains('if-head')).map(node => node.dataset.step));
    const chosen = hits.filter(node => {
      const frame = node.classList.contains('if-head') ? null : node.closest('.if-frame');
      return !(frame && frames.has(frame.dataset.if));
    }).map(node => node.dataset.step);
    // With Shift or Ctrl held the box adds to what is already selected, as long as that is not the loop.
    const loop = findLoop(editor.flow);
    const kept = adding ? [...editor.selection].filter(id => !(loop && id === loop.id)) : [];
    editor.selectMany([...new Set([...kept, ...chosen])]);
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', up);
}
