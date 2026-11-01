'use strict';

async function renderHome() {
  setTopbarTitle(null);
  const view = document.getElementById('view');
  const page = el('div.page');
  view.replaceChildren(el('div.page-scroll', null, page));

  const setup = await api('GET', '/api/setup').catch(() => null);
  if (setup && !setup.ready) {
    location.hash = '#/setup';
    return;
  }
  const { flows, templates } = await api('GET', '/api/flows');

  const list = el('div.flow-list');

  fill(page,
    el('div.page-head', null,
      el('h1', { text: 'Quy trình của bạn' }),
      el('div.spacer'),
      el('button.btn.primary', { id: 'new-flow', text: '+ New flow', onclick: () => openNewFlow(templates) })),
    el('div.demo-card', null,
      el('div', null,
        el('h2', { text: 'Thử ngay với trang demo' }),
        el('p.muted', { text: 'Quy trình đăng nhập, đổi tên hàng loạt từ Excel trên một trang web nhỏ có trong app. Không đụng tới hệ thống thật.' })),
      el('button.btn', { id: 'open-demo', text: 'Open sample flow', onclick: openDemo })),
    list);

  if (!flows.length) {
    list.append(el('div.card.empty', { text: 'Chưa có quy trình nào. Hãy tạo mới hoặc mở quy trình mẫu.' }));
  }
  for (const flow of flows) {
    const open = () => { location.hash = '#/flow/' + flow.id; };
    list.append(el('div.card.flow-item', { 'data-flow': flow.id },
      el('button.name', { text: flow.name, onclick: open }),
      el('span.muted', { text: `${flow.steps} bước` }),
      el('button.btn.small', { text: 'Open', onclick: open }),
      el('button.btn.small', {
        text: 'Duplicate',
        onclick: async () => {
          try {
            await api('POST', `/api/flows/${flow.id}/duplicate`);
            renderHome();
          } catch (error) { showError(error); }
        },
      }),
      el('button.btn.small.danger', {
        text: 'Delete',
        onclick: async () => {
          if (!await confirmBox('Xóa quy trình?', `Xóa "${flow.name}". Không hoàn tác được.`, 'Delete', 'danger')) return;
          try {
            await api('DELETE', `/api/flows/${flow.id}`);
            renderHome();
          } catch (error) { showError(error); }
        },
      })));
  }
}

function openNewFlow(templates) {
  const name = el('input', { type: 'text', id: 'new-flow-name', placeholder: 'Để trống thì đặt tên tự động' });
  let creating = false;
  const create = async template => {
    // One click creates the flow at once, so a double click must not create two.
    if (creating) return;
    creating = true;
    try {
      const created = await api('POST', '/api/flows', { name: name.value.trim(), template });
      box.close();
      location.hash = '#/flow/' + created.id;
    } catch (error) {
      creating = false;
      showError(error);
    }
  };
  const choice = (id, label, hint, template) => el('div.template-item', null,
    el('button.btn', { 'data-template': id, text: label, onclick: () => create(template) }),
    el('span.muted', { text: hint }));
  const box = openModal({
    title: 'Quy trình mới',
    body: el('div', null,
      el('div.field', null, el('label.field-label', { text: 'Tên quy trình', for: 'new-flow-name' }), name),
      el('p.section-label', { text: 'Chọn cách bắt đầu' }),
      el('div.template-list', null,
        choice('blank', 'Blank flow', 'Quy trình trống, tự kéo khối hoặc bấm Record.', null),
        templates.map(item => choice(item.id, item.label, item.hint, item.id)))),
    actions: [{ label: 'Cancel' }],
  });
  name.addEventListener('keydown', event => { if (event.key === 'Enter') create(null); });
}

async function openDemo() {
  try {
    const demo = await api('POST', '/api/demo');
    memory.set('excel.' + demo.id, demo.excel_path);
    location.hash = '#/flow/' + demo.id;
    toast('Đã mở quy trình mẫu. Bấm Run once hoặc Run để xem kết quả.');
  } catch (error) { showError(error); }
}

function openSecrets() {
  const list = el('div');
  const nameInput = el('input', { type: 'text', id: 'secret-name', placeholder: 'vd: mat_khau_cong_ty' });
  const valueInput = el('input', { type: 'password', id: 'secret-value', autocomplete: 'new-password' });
  const reload = async () => {
    const { names } = await api('GET', '/api/secrets');
    list.replaceChildren(names.length ? el('table.data', null, names.map(name => el('tr', null,
      el('td', null, el('code', { text: name })),
      el('td', { style: 'text-align:right' },
        el('button.btn.small', { text: 'Change value', onclick: () => { nameInput.value = name; valueInput.focus(); } }), ' ',
        el('button.btn.small.danger', {
          text: 'Delete',
          onclick: async () => {
            try {
              await api('DELETE', '/api/secrets/' + encodeURIComponent(name));
              reload();
            } catch (error) { showError(error); }
          },
        }))))) : el('p.muted', { text: 'Chưa có bí mật nào.' }));
  };
  const save = async () => {
    try {
      await api('POST', '/api/secrets', { name: nameInput.value.trim(), value: valueInput.value });
      nameInput.value = '';
      valueInput.value = '';
      toast('Đã lưu vào Windows Credential Manager.');
      reload();
      if (window.onSecretsChanged) window.onSecretsChanged();
    } catch (error) { showError(error); }
  };
  openModal({
    title: 'Bí mật (mật khẩu, tài khoản)',
    body: el('div', null,
      el('p.muted', { text: 'Giá trị được giữ trong Windows Credential Manager và không hiển thị lại. File quy trình chỉ chứa tên, ví dụ {{secret:mat_khau}}.', style: 'margin-bottom:12px' }),
      list,
      el('div.divider'),
      el('div.field', null, el('label.field-label', { text: 'Tên', for: 'secret-name' }), nameInput),
      el('div.field', null, el('label.field-label', { text: 'Giá trị', for: 'secret-value' }), valueInput),
      el('button.btn.primary', { id: 'secret-save', text: 'Save secret', onclick: save })),
    actions: [{ label: 'Close', id: 'close' }],
  });
  reload().catch(showError);
}
