'use strict';

async function startRunFlow(editor, mode, options) {
  await editor.save.flush();
  runWithConfirmation(editor.id, editor.excelPath, mode, options || {});
}

async function runWithConfirmation(flowId, excelPath, mode, options) {
  let check;
  try {
    check = await api('POST', '/api/preflight', {
      flow_id: flowId, excel_path: excelPath, mode,
      previous_run: options.previousRun, include_unknown: options.includeUnknown,
    });
  } catch (error) {
    showError(error);
    return;
  }
  const blocked = check.errors.length > 0;
  const issues = el('ul.issue-list', { id: 'issues' },
    check.errors.map(item => el('li.error', { text: item.message })),
    check.warnings.map(item => el('li.warning', { text: item.message })),
    check.changed ? el('li.warning', { text: check.changed }) : null);
  const trial = mode === 'trial';
  const facts = blocked ? null : el('dl.facts', null,
    el('dt', { text: 'Số lần chạy' }), el('dd', { id: 'confirm-total', text: String(check.total) + (check.skipped ? ` (bỏ qua ${check.skipped} hàng thiếu dữ liệu)` : '') }),
    check.stop_column !== undefined ? el('dt', { text: 'Dừng khi' }) : null,
    check.stop_column !== undefined ? el('dd', { id: 'confirm-stop', text: runsUntilText(check.stop_column) }) : null,
    el('dt', { text: 'Tên miền truy cập' }), el('dd', { id: 'confirm-hosts', text: check.hosts.join(', ') || 'Không rõ' }),
    el('dt', { text: 'Cột sẽ ghi' }), el('dd', { text: check.columns.length ? check.columns.join(', ') + ' (trong file kết quả)' : 'Không có' }));
  const title = blocked ? 'Chưa chạy được' : (trial ? 'Chạy thử 1 lần' : 'Xác nhận chạy');
  openModal({
    title,
    body: el('div', null,
      blocked ? el('p', { text: 'Hãy sửa các lỗi sau trước khi chạy:', style: 'margin-bottom:6px' }) : null,
      !blocked && trial ? el('div.banner.warn', { text: 'Đây là thao tác ghi thật: các bước sẽ chạy thật trên trang web cho một hàng.' }) : null,
      !blocked && !trial ? el('p', { text: 'Tool sẽ ghi thật lên trang web. File Excel gốc giữ nguyên.', style: 'margin-bottom:6px' }) : null,
      facts,
      check.errors.length || check.warnings.length || check.changed ? issues : null),
    actions: blocked ? [{ label: 'Close', id: 'close' }] : [
      { label: 'Cancel', id: 'cancel' },
      {
        label: trial ? 'Run once' : 'Run', kind: 'primary', id: 'confirm-run',
        onClick: async () => {
          try {
            const started = await api('POST', '/api/runs', {
              flow_id: flowId, excel_path: excelPath, mode,
              previous_run: options.previousRun, include_unknown: options.includeUnknown,
            });
            location.hash = '#/run/' + started.run_id;
          } catch (error) { showError(error); }
        },
      },
    ],
  });
}

const OUTCOME_TEXT = {
  finished: 'Đã chạy xong.',
  stopped: 'Đã dừng theo yêu cầu.',
  failed: 'Đã dừng vì lỗi.',
  browser_closed: 'Đã dừng vì cửa sổ Chrome bị đóng.',
  after_loop_failed: 'Các hàng đã chạy xong, nhưng bước sau vùng lặp bị lỗi.',
  crashed: 'Tiến trình chạy dừng đột ngột.',
};

const FAILED_BLOCK_HOLD_MS = 1500;

async function renderRun(runId) {
  const view = document.getElementById('view');
  setTopbarTitle(null);
  const state = {
    total: 0, rows: new Map(), counts: { 'OK': 0, 'Lỗi': 0, 'Bỏ qua': 0, 'Chưa rõ': 0 },
    finished: null, paused: null, current: '', logs: [], summary: null,
    lit: new Set(), activeStep: null, failedStep: null, flash: null, wait: null,
  };
  const page = el('div.page', { id: 'run-page' });
  view.replaceChildren(el('div.page-scroll', null, page));

  const loadSummary = async () => {
    state.summary = await api('GET', '/api/runs/' + runId);
    return state.summary;
  };
  const summary = await loadSummary();
  const flow = await api('GET', '/api/flows/' + summary.flow_id).then(data => data.flow).catch(() => null);
  const loop = flow && findLoop(flow);
  const loopSteps = loop ? loop.steps.map(step => step.id) : [];

  const stepState = id => {
    if (state.failedStep === id || (state.flash && state.flash.id === id && Date.now() < state.flash.until)) return 'failed';
    if (state.activeStep === id) return 'running';
    return state.lit.has(id) ? 'lit' : null;
  };

  const command = async cmd => {
    try { await api('POST', `/api/runs/${runId}/command`, { cmd }); } catch (error) { showError(error); }
  };

  // Only these two texts change: redrawing every second would close an open screenshot.
  const showCountdown = () => {
    if (!state.wait) return;
    const left = Math.max(0, Math.ceil((state.wait.until - Date.now()) / 1000));
    const current = document.getElementById('run-current');
    if (current) current.textContent = `${state.current}, còn ${left} giây`;
    const summaryNode = page.querySelector(`#run-flow .node[data-step="${state.wait.step}"] span`);
    if (summaryNode) summaryNode.textContent = `còn ${left} giây`;
  };

  const draw = () => {
    const done = state.counts['OK'] + state.counts['Lỗi'] + state.counts['Bỏ qua'] + state.counts['Chưa rõ'];
    const percent = state.total ? Math.min(100, Math.round((done / state.total) * 100)) : (state.finished ? 100 : 0);
    const finished = state.finished;
    const rows = [...state.rows.values()].sort((a, b) => a.row - b.row);
    const headline = finished ? (OUTCOME_TEXT[finished.outcome] || finished.outcome) : (state.paused ? 'Đang chờ bạn quyết định' : 'Đang chạy...');
    fill(page,
      el('div.page-head', null,
        el('h1', { id: 'run-headline', text: headline }),
        el('span.spacer'),
        !finished ? el('button.btn.danger', { id: 'run-stop', text: 'Stop after this row', onclick: () => command('stop') }) : null,
        el('a.btn', { href: '#/flow/' + summary.flow_id, text: 'Back to flow' })),
      el('p.muted', { text: summary.flow_name, style: 'margin:-10px 0 14px' }),
      state.paused ? el('div.pause-box', { id: 'pause-box' },
        el('p', { text: state.paused, style: 'margin-bottom:8px' }),
        el('div.row', null,
          el('button.btn.primary', { id: 'run-continue', text: 'Continue', onclick: () => command('continue') }),
          el('button.btn', { id: 'run-abort', text: 'Stop', onclick: () => command('stop') }))) : null,
      finished && finished.message ? el('div.banner.' + (finished.outcome === 'finished' ? 'ok' : 'bad'), { id: 'run-message', text: finished.message }) : null,
      el('div.card.card-pad', { style: 'margin-bottom:14px' },
        el('div.stat-row', { id: 'run-counts' }, ['OK', 'Lỗi', 'Bỏ qua', 'Chưa rõ'].map(status => el('span.row', { style: 'gap:5px' },
          statusBadge(status), el('b', { 'data-count': status, text: String(state.counts[status]) })))),
        el('div.progress', null, el('div', { style: `width:${percent}%` })),
        el('div.muted', { id: 'run-current', text: finished ? `${done}/${state.total || done} lượt` : (state.current || 'Đang mở trình duyệt...') }),
        finished ? finishedActions(runId, summary, state, finished) : null),
      flow ? el('div.card.card-pad', { id: 'run-flow', style: 'margin-bottom:14px' }, renderRunDiagram(flow, stepState)) : null,
      rows.length ? el('div.card', { style: 'margin-bottom:14px;overflow:auto' }, el('table.data', { id: 'run-rows' },
        el('tr', null, el('th', { text: summary.excel_path ? 'Hàng' : 'Lần' }), el('th', { text: 'Trạng thái' }), el('th', { text: 'Chi tiết' })),
        rows.map(row => el('tr', { 'data-row': row.row },
          el('td', { text: String(row.row) }),
          el('td', null, statusBadge(row.status)),
          el('td', null,
            el('span', { text: row.message || '', style: 'overflow-wrap:anywhere' }),
            row.screenshot ? el('details', null,
              el('summary', { text: 'View screenshot', style: 'cursor:pointer;color:var(--accent-text)' }),
              el('img.shot', { src: `/api/runs/${runId}/files/${row.screenshot}`, alt: 'Ảnh chụp màn hình lúc lỗi', loading: 'lazy' })) : null))))) : null,
      state.logs.length ? el('div.log', { id: 'run-log' }, state.logs.map(item => el('div.' + item.level, { text: item.text }))) : null);
    showCountdown();
  };

  const resetLoopBlocks = () => {
    loopSteps.forEach(id => state.lit.delete(id));
    state.activeStep = null;
  };

  const apply = event => {
    if (['step', 'row_done', 'finished'].includes(event.type)) state.wait = null;
    switch (event.type) {
      case 'started': state.total = event.total; break;
      case 'waiting': state.wait = { step: event.step, until: Date.now() + event.seconds * 1000 }; break;
      case 'step':
        state.current = (event.row ? `Hàng ${event.row}: ` : '') + event.label;
        state.lit.add(event.step);
        state.activeStep = event.step;
        break;
      case 'row_start':
        state.current = `Hàng ${event.row}`;
        // The next row must not start before anyone could see which block just failed.
        if (state.failedStep) {
          state.flash = { id: state.failedStep, until: Date.now() + FAILED_BLOCK_HOLD_MS };
          state.failedStep = null;
          setTimeout(schedule, FAILED_BLOCK_HOLD_MS + 20);
        }
        resetLoopBlocks();
        break;
      case 'row_done':
        state.rows.set(event.row, event);
        state.counts[event.status] = (state.counts[event.status] || 0) + 1;
        if (event.status === 'Lỗi') state.failedStep = state.activeStep;
        else if (event.status === 'OK') resetLoopBlocks();
        break;
      case 'paused': state.paused = event.reason; break;
      case 'resumed': state.paused = null; break;
      case 'log': state.logs.push({ level: event.level, text: event.text }); break;
      case 'finished':
        state.finished = event;
        state.paused = null;
        if (event.outcome === 'finished') state.lit.clear();
        else if (event.outcome !== 'stopped' && state.activeStep) state.failedStep = state.activeStep;
        state.activeStep = null;
        break;
      default: break;
    }
  };

  if (!summary.running && summary.outcome) {
    // Opened after the run ended: everything needed is in the saved summary.
    for (const row of summary.rows) {
      state.rows.set(row.row, row);
      state.counts[row.status] = (state.counts[row.status] || 0) + 1;
    }
    state.total = summary.rows.length;
    state.finished = { outcome: summary.outcome, message: summary.message, result_file: summary.result_file };
    draw();
    return;
  }

  draw();
  const source = new EventSource(`/api/runs/${runId}/events`);
  let timer = null;
  const schedule = () => {
    if (timer) return;
    timer = setTimeout(() => { timer = null; draw(); }, 80);
  };
  const countdownTimer = setInterval(showCountdown, 200);
  source.onmessage = message => {
    apply(JSON.parse(message.data));
    schedule();
  };
  source.addEventListener('end', () => {
    source.close();
    clearInterval(countdownTimer);
    loadSummary().then(() => { state.summaryLoaded = true; schedule(); }).catch(() => {});
  });
  source.onerror = () => {
    // The browser reconnects by itself. When the run is gone from memory it cannot.
    if (source.readyState === EventSource.CLOSED) {
      loadSummary().then(() => renderRun(runId)).catch(showError);
    }
  };
  window.leaveView = () => {
    source.close();
    clearInterval(countdownTimer);
    window.leaveView = null;
  };
}

function finishedActions(runId, summary, state, finished) {
  const anyLeft = state.counts['Lỗi'] > 0;
  const unknown = state.counts['Chưa rõ'] > 0;
  const incomplete = ['stopped', 'crashed', 'browser_closed'].includes(finished.outcome);
  const again = (mode, includeUnknown) => () => runWithConfirmation(summary.flow_id, summary.excel_path, mode, {
    previousRun: runId, includeUnknown,
  });
  return el('div.row.wrap', { style: 'margin-top:12px' },
    finished.result_file ? el('button.btn.primary', {
      id: 'open-result',
      text: 'Open result file',
      onclick: async () => {
        try { await api('POST', `/api/runs/${runId}/open`, {}); } catch (error) { showError(error); }
      },
    }) : null,
    finished.result_file ? el('span.muted.mono', { id: 'result-file', text: finished.result_file, style: 'overflow-wrap:anywhere' }) : null,
    incomplete && summary.excel_path ? el('button.btn', { id: 'run-resume', text: 'Resume', onclick: again('resume', false) }) : null,
    anyLeft && summary.excel_path ? el('button.btn', { id: 'run-retry', text: 'Retry failed rows', onclick: again('retry', false) }) : null,
    unknown && summary.excel_path ? el('button.btn', { id: 'run-unknown', text: 'Retry failed and unclear rows', title: 'Chạy lại cả hàng Lỗi và hàng Chưa rõ', onclick: again('retry', true) }) : null);
}
