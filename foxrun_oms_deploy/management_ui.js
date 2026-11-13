'use strict';

views.periods = 'Đợt đối soát';
views.trash = 'Thùng rác';
views.tracking_manual = 'Tracking thủ công';
views.tracking_file = 'Tracking từ file';
const selectedOrders = new Map();
const baseRender = render;
const baseRefresh = refresh;
const baseNavigate = navigate;
const baseOpenOrder = openOrder;
const baseCustomerDetail = customerDetail;

let tmState = { preview: null };
let tfState = { preview: null };

function formatTrackingReason(reason) {
  if (!reason) return '';
  const map = {
    'RETURN_DELIVERY_CONFLICT': 'Xung đột: Đơn đã hoàn về kho không thể chuyển thành đã giao',
    'DUPLICATE_IN_FILE': 'Mã vận đơn bị lặp lại trong file / danh sách',
    'MISSING_TRACKING_OR_INVALID_STATUS': 'Thiếu mã vận đơn hoặc trạng thái không hợp lệ',
    'NOT_FOUND': 'Không tìm thấy đơn hàng tương ứng với mã vận đơn',
    'AMBIGUOUS_TRACKING': 'Mã vận đơn bị trùng giữa nhiều đơn hàng',
    'ALREADY_DELIVERED': 'Đơn hàng đã được giao thành công trước đó'
  };
  return map[reason] || reason;
}

async function renderTrackingManual() {
  const content = $('content');
  content.innerHTML = `
    <div id="view-tracking-manual">
      <div class="heading">
        <div>
          <div class="eyebrow">FOXRUN · HỆ THỐNG ĐIỀU HÀNH</div>
          <h1>Tracking thủ công</h1>
          <p>Cập nhật trạng thái vận đơn hàng loạt theo danh sách mã.</p>
        </div>
        <div class="actions">
          <button class="small" data-go="shipping">← Về trang Vận chuyển</button>
          <button class="small" data-go="tracking_file">Nhập từ file CSV/XLSX →</button>
        </div>
      </div>
      <section id="tmEditSection" class="panel pad">
        <div class="panelhead" style="margin:-20px -20px 20px -20px; padding:15px 20px;">
          <h3>Bước 1: Nhập thông tin tra cứu & trạng thái</h3>
        </div>
        <div class="fieldgrid">
          <div class="field">
            <label for="tmCarrier">Hãng / Nguồn tra cứu</label>
            <select id="tmCarrier">
              <option value="other">Khác / Chưa xác định</option>
              <option value="jt">J&T Express</option>
              <option value="spx">SPX Express</option>
            </select>
          </div>
          <div class="field">
            <label for="tmStatus">Trạng thái vận chuyển mới</label>
            <select id="tmStatus">
              <option value="DELIVERED">DELIVERED · Giao thành công</option>
              <option value="IN_TRANSIT">IN_TRANSIT · Đang trung chuyển</option>
              <option value="OUT_FOR_DELIVERY">OUT_FOR_DELIVERY · Đang giao hàng</option>
              <option value="DELIVERY_FAILED">DELIVERY_FAILED · Giao thất bại</option>
              <option value="RETURNING">RETURNING · Đang chuyển hoàn</option>
              <option value="RETURNED">RETURNED · Đã hoàn về kho</option>
              <option value="EXCEPTION">EXCEPTION · Sự cố vận chuyển</option>
            </select>
          </div>
          <div class="full field">
            <label for="tmCodes">Danh sách mã vận đơn (mỗi dòng một mã, hoặc phân tách bằng dấu phẩy / chấm phẩy)</label>
            <textarea id="tmCodes" rows="5" placeholder="JT123456789&#10;SPX987654321"></textarea>
          </div>
          <div class="full field">
            <label for="tmNote">Ghi chú cập nhật</label>
            <textarea id="tmNote" rows="2" placeholder="Ghi chú đối soát hoặc vận chuyển..."></textarea>
          </div>
        </div>
        <div class="actions" style="margin-top:15px">
          <button class="primary" id="tmPreviewBtn">Kiểm tra & Xem trước</button>
        </div>
      </section>

      <section id="tmReviewSection" class="panel pad" hidden>
        <div class="panelhead" style="margin:-20px -20px 20px -20px; padding:15px 20px;">
          <h3>Bước 2: Xác nhận cập nhật vào cơ sở dữ liệu</h3>
          <span id="tmSummary" class="badge"></span>
        </div>
        <div class="tablewrap" style="margin:15px 0">
          <table>
            <thead>
              <tr>
                <th style="width:40px"><input type="checkbox" id="tmSelectAll" checked title="Chọn tất cả"></th>
                <th>Mã vận đơn</th>
                <th>Mã đơn hàng</th>
                <th>Khách hàng</th>
                <th>Trạng thái hiện tại</th>
                <th>Kết quả kiểm tra</th>
              </tr>
            </thead>
            <tbody id="tmRows"></tbody>
          </table>
        </div>
        <div class="actions" style="margin-top:15px">
          <button type="button" id="tmBackBtn" style="background:#64748b; color:white">Quay lại chỉnh sửa</button>
          <button type="button" class="primary" id="tmConfirmBtn">Xác nhận ghi database</button>
        </div>
      </section>
      <p id="tmMessage" class="callout" style="display:none"></p>
    </div>
  `;

  document.querySelectorAll('[data-go]').forEach(b => b.onclick = () => navigate(b.dataset.go));

  const showMsg = (text, isError = false) => {
    const el = $('tmMessage');
    if (!el) return;
    if (!text) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.className = 'callout ' + (isError ? 'danger' : 'info');
    el.textContent = text;
  };

  $('tmPreviewBtn').onclick = () => run(async () => {
    showMsg('');
    const raw = $('tmCodes').value;
    const numbers = [...new Set(raw.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean))];
    if (!numbers.length) throw Error('Vui lòng nhập ít nhất một mã vận đơn.');

    const targetStatus = $('tmStatus').value;
    const rows = numbers.map(trackingNum => {
      const matches = orders.filter(o => o.tracking && o.tracking.trim().toLowerCase() === trackingNum.toLowerCase());
      if (matches.length === 0) {
        return { tracking_number: trackingNum, eligible: false, reason: 'NOT_FOUND', matches: [] };
      }
      if (matches.length > 1) {
        return {
          tracking_number: trackingNum,
          eligible: false,
          reason: 'AMBIGUOUS_TRACKING',
          matches: matches.map(o => ({ order_id: o.id, order_code: o.code, customer: o.customer, current_status: o.transport, version: o.version }))
        };
      }
      const o = matches[0];
      if (o.stage !== 'SHIPPED') {
        return {
          tracking_number: trackingNum,
          eligible: false,
          reason: 'Đơn chưa bàn giao vận chuyển (SHIPPED)',
          matches: [{ order_id: o.id, order_code: o.code, customer: o.customer, current_status: o.transport, version: o.version }]
        };
      }
      if (o.transport === 'RETURNED' && targetStatus === 'DELIVERED') {
        return {
          tracking_number: trackingNum,
          eligible: false,
          reason: 'RETURN_DELIVERY_CONFLICT',
          matches: [{ order_id: o.id, order_code: o.code, customer: o.customer, current_status: o.transport, version: o.version }]
        };
      }
      return {
        tracking_number: trackingNum,
        eligible: true,
        reason: 'OK',
        matches: [{ order_id: o.id, order_code: o.code, customer: o.customer, current_status: o.transport, version: o.version }]
      };
    });

    tmState.preview = { status: targetStatus, rows };
    const tbody = $('tmRows');
    tbody.innerHTML = rows.map(row => {
      const m = row.matches[0];
      const checkbox = row.eligible
        ? `<input type="checkbox" class="tmCheck" checked data-order-id="${m.order_id}" data-version="${m.version}">`
        : '';
      const orderCodes = row.matches.map(x => x.order_code).join(', ') || '—';
      const customerNames = row.matches.map(x => x.customer || '').filter(Boolean).join(', ') || '—';
      const curStatus = row.matches.map(x => x.current_status || '').join(', ') || '—';
      const reasonBadge = row.eligible
        ? `<span class="badge green">Hợp lệ</span>`
        : `<span class="badge red">${esc(formatTrackingReason(row.reason))}</span>`;
      return `<tr>
        <td>${checkbox}</td>
        <td class="mono font-bold">${esc(row.tracking_number)}</td>
        <td>${esc(orderCodes)}</td>
        <td>${esc(customerNames)}</td>
        <td>${esc(curStatus)}</td>
        <td>${reasonBadge}</td>
      </tr>`;
    }).join('');

    const eligibleCount = rows.filter(r => r.eligible).length;
    $('tmSummary').textContent = `Trạng thái mới: ${targetStatus} · ${eligibleCount}/${rows.length} đơn hợp lệ`;
    $('tmEditSection').hidden = true;
    $('tmReviewSection').hidden = false;

    $('tmSelectAll').onchange = (e) => {
      document.querySelectorAll('.tmCheck').forEach(cb => cb.checked = e.target.checked);
    };
  });

  $('tmBackBtn').onclick = () => {
    $('tmReviewSection').hidden = true;
    $('tmEditSection').hidden = false;
    showMsg('');
  };

  $('tmConfirmBtn').onclick = () => run(async () => {
    const checkboxes = [...document.querySelectorAll('.tmCheck:checked')];
    if (!checkboxes.length) throw Error('Chưa chọn đơn hàng hợp lệ nào để cập nhật.');
    const selected = checkboxes.map(cb => ({
      id: Number(cb.dataset.orderId),
      version: Number(cb.dataset.version)
    }));

    const res = await post('/api/wf/bulk', {
      action: 'tracking',
      status: tmState.preview.status,
      note: $('tmNote').value,
      orders: selected,
      idempotency_key: crypto.randomUUID()
    });

    notice(`Đã cập nhật: ${res.succeeded} đơn; bỏ qua: ${res.total - res.succeeded} đơn.`);
    if ($('tmReviewSection')) $('tmReviewSection').hidden = true;
    if ($('tmEditSection')) $('tmEditSection').hidden = false;
    showMsg(`Đã cập nhật thành công ${res.succeeded} đơn hàng.`);
    await refresh();
  });
}

async function renderTrackingFile() {
  const content = $('content');
  content.innerHTML = `
    <div id="view-tracking-file">
      <div class="heading">
        <div>
          <div class="eyebrow">FOXRUN · HỆ THỐNG ĐIỀU HÀNH</div>
          <h1>Tracking từ file</h1>
          <p>Cập nhật trạng thái vận đơn hàng loạt từ file bảng tính Excel (.xlsx) hoặc CSV.</p>
        </div>
        <div class="actions">
          <button class="small" id="tfSampleBtn">Tải file mẫu tracking</button>
          <button class="small" data-go="tracking_manual">Cập nhật thủ công →</button>
          <button class="small" data-go="shipping">← Về trang Vận chuyển</button>
        </div>
      </div>
      <section id="tfUploadSection" class="panel pad">
        <div class="panelhead" style="margin:-20px -20px 20px -20px; padding:15px 20px;">
          <h3>Tải lên tệp dữ liệu vận chuyển</h3>
        </div>
        <p class="muted">Hỗ trợ cột: <code>tracking_number</code> (hoặc <code>tracking</code>, <code>mã vận đơn</code>) và <code>status</code> (hoặc <code>trạng thái</code>); tùy chọn <code>carrier</code>, <code>note</code>.</p>
        <div class="fieldgrid">
          <div class="field full">
            <label for="tfFile">Chọn file bảng tính (.csv, .xlsx)</label>
            <input type="file" id="tfFile" accept=".csv,.xlsx">
          </div>
        </div>
        <div class="actions" style="margin-top:15px">
          <button class="primary" id="tfPreviewBtn">Kiểm tra & Xem trước file</button>
        </div>
      </section>

      <section id="tfReviewSection" class="panel pad" hidden>
        <div class="panelhead" style="margin:-20px -20px 20px -20px; padding:15px 20px;">
          <h3>Xác nhận thay đổi từ file</h3>
          <span id="tfStats" class="badge"></span>
        </div>
        <div class="tablewrap" style="margin:15px 0">
          <table>
            <thead>
              <tr>
                <th style="width:40px"><input type="checkbox" id="tfSelectAll" checked title="Chọn tất cả"></th>
                <th>Dòng</th>
                <th>Mã vận đơn</th>
                <th>Trạng thái mới</th>
                <th>Trạng thái cũ</th>
                <th>Đơn hàng</th>
                <th>Kết quả đối chiếu</th>
              </tr>
            </thead>
            <tbody id="tfRows"></tbody>
          </table>
        </div>
        <div class="actions" style="margin-top:15px">
          <button type="button" class="primary" id="tfConfirmBtn">Xác nhận ghi database</button>
        </div>
      </section>
      <p id="tfMessage" class="callout" style="display:none"></p>
    </div>
  `;

  document.querySelectorAll('[data-go]').forEach(b => b.onclick = () => navigate(b.dataset.go));

  $('tfSampleBtn').onclick = () => {
    download('foxrun_tracking_sample.csv', 'tracking,status,note\nJT123456789,DELIVERED,Giao thanh cong\nSPX987654321,IN_TRANSIT,Dang chuyen kho');
  };

  const showMsg = (text, isError = false) => {
    const el = $('tfMessage');
    if (!el) return;
    if (!text) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.className = 'callout ' + (isError ? 'danger' : 'info');
    el.textContent = text;
  };

  $('tfPreviewBtn').onclick = () => run(async () => {
    showMsg('');
    const input = $('tfFile');
    const file = input.files[0];
    if (!file) throw Error('Vui lòng chọn file trước.');
    if (file.size > 10 * 1024 * 1024) throw Error('File vượt quá giới hạn 10 MB.');

    let bytes;
    try {
      bytes = await file.arrayBuffer();
    } catch {
      input.value = '';
      throw Error('Không đọc được file đã chọn. Lưu và đóng file Excel, rồi chọn lại bản đã lưu trên máy.');
    }
    const fd = new FormData();
    fd.append('file', new Blob([bytes], {type: file.type}), file.name);
    const res = await api('/api/wf/tracking/preview', {
      method: 'POST',
      body: fd
    });

    tfState.preview = res;
    const tbody = $('tfRows');
    if (!tbody) return;
    const validCount = res.rows.filter(r => !r.error).length;
    tbody.innerHTML = res.rows.map(row => {
      const checkbox = !row.error
        ? `<input type="checkbox" class="tfCheck" checked data-id="${row.order_id || ''}" data-version="${row.version || 0}">`
        : '';
      const oldStatus = row.previous || '—';
      const reasonBadge = !row.error
        ? `<span class="badge green">Hợp lệ</span>`
        : `<span class="badge red">${esc(formatTrackingReason(row.error))}</span>`;
      return `<tr>
        <td>${checkbox}</td>
        <td>${row.line}</td>
        <td class="mono font-bold">${esc(row.tracking)}</td>
        <td><span class="badge blue">${esc(row.status)}</span></td>
        <td>${esc(oldStatus)}</td>
        <td>${row.order_id ? '#' + row.order_id : '—'}</td>
        <td>${reasonBadge}</td>
      </tr>`;
    }).join('');

    if ($('tfStats')) $('tfStats').textContent = `${res.total} dòng · ${validCount} hợp lệ · ${res.errors} cần kiểm tra`;
    if ($('tfReviewSection')) $('tfReviewSection').hidden = false;

    if ($('tfSelectAll')) {
      $('tfSelectAll').onchange = (e) => {
        document.querySelectorAll('.tfCheck').forEach(cb => cb.checked = e.target.checked);
      };
    }
  });

  $('tfConfirmBtn').onclick = () => run(async () => {
    if (!tfState.preview?.preview_id) throw Error('Chưa có bản xem trước.');
    const res = await post('/api/wf/import/confirm', {
      preview_id: tfState.preview.preview_id,
      idempotency_key: crypto.randomUUID()
    });

    notice(`Đã cập nhật: ${res.updated} đơn; bỏ qua: ${res.skipped} đơn.`);
    if ($('tfReviewSection')) $('tfReviewSection').hidden = true;
    showMsg(`Đã cập nhật thành công ${res.updated} đơn hàng từ file.`);
    await refresh();
  });
}

refresh = async function () {
  await baseRefresh();
  const activeIds = new Set(orders.map(order => order.id));
  for (const id of selectedOrders.keys()) {
    if (!activeIds.has(id)) selectedOrders.delete(id);
  }
};

navigate = async function (name, updateHash = true) {
  clearTimeout(searchTimer);
  selectedOrders.clear();
  await baseNavigate(name, updateHash);
};

render = async function () {
  const currentSeq = renderSeq;
  if (view === 'periods') {
    await renderPeriods();
    return;
  }
  if (view === 'trash') {
    await renderTrash();
    return;
  }
  if (view === 'tracking_manual') {
    await renderTrackingManual();
    return;
  }
  if (view === 'tracking_file') {
    await renderTrackingFile();
    return;
  }
  await baseRender();
  if (currentSeq !== renderSeq || view !== 'imports') return;
  if (view === 'imports') {
    const content = $('content');
    if (!content) return;
    const section = document.createElement('div');
    section.id = 'importHistory';
    content.append(section);
    await renderImportHistory();
  }
};

orderTable = function (rows, selectable = false) {
  const selectionHeader = selectable ? '<th><input type="checkbox" id="selectPage" aria-label="Chọn tất cả đơn trên trang" style="width:auto"></th>' : '';
  const html = rows.map(order => {
    const selection = selectable ? '<td><input type="checkbox" class="orderSelection" data-select-id="' + order.id + '" aria-label="Chọn đơn ' + esc(order.code) + '" style="width:auto" ' + (selectedOrders.has(order.id) ? 'checked' : '') + '></td>' : '';
    return '<tr class="click" tabindex="0" data-order="' + order.id + '">' + selection +
      '<td class="code">' + esc(order.code) + '<span class="sub">' + esc(order.source) + ' · ' + date(order.created_at) + '</span></td>' +
      '<td>' + esc(order.customer) + '<span class="sub">' + esc(order.recipient || '') + '</span></td>' +
      '<td>' + badge(order.stage) + (order.issue ? '<span class="sub">' + esc(order.issue) + '</span>' : '') + '</td>' +
      '<td>' + badge(order.transport, 'transport') + '<span class="sub mono">' + esc(order.tracking || 'Chưa có vận đơn') + (order.stale ? ' · Quá 5 ngày' : '') + '</span></td>' +
      '<td>' + money(order.cod, order.currency) + '</td><td>' + badge(order.finance, 'finance') + '</td>' +
      '<td>' + date(order.completed_at) + '</td><td>' + order.items.map(item => esc(item.sku) + ' × ' + item.quantity).join(', ') + '</td></tr>';
  }).join('');
  return '<div class="tablewrap"><table><thead><tr>' + selectionHeader +
    '<th>Mã đơn / Ngày lên đơn</th><th>Khách hàng</th><th>Xử lý đơn</th><th>Vận chuyển</th><th>COD</th><th>Đối soát</th><th>Ngày hoàn thành</th><th>Sản phẩm</th>' +
    '</tr></thead><tbody>' + (html || '<tr><td colspan="' + (selectable ? 9 : 8) + '" class="empty">Chưa có đơn trong danh sách.</td></tr>') + '</tbody></table></div>';
};

bindOrderRows = function () {
  document.querySelectorAll('[data-order]').forEach(row => {
    row.onclick = event => {
      if (event.target.closest('input,button,a,label')) return;
      run(() => openOrder(Number(row.dataset.order)));
    };
    row.onkeydown = event => {
      if (event.key === 'Enter' && event.target === row) row.click();
    };
  });
};

drawList = function () {
  const listArea = $('listArea');
  if (!listArea) return;
  const filtered = orders.filter(order => inView(order, view) && match(order));
  const maxPage = Math.max(1, Math.ceil(filtered.length / 50));
  page = Math.min(page, maxPage);
  const visible = filtered.slice((page - 1) * 50, page * 50);
  listArea.innerHTML =
    '<div class="toolbar"><span id="selectionCount"></span><button class="small" id="selectFiltered">Chọn toàn bộ kết quả lọc</button>' +
    '<button class="small" id="clearSelection">Bỏ chọn</button><button class="primary small" id="bulkAction">Thao tác các đơn đã chọn</button></div>' +
    orderTable(visible, true) +
    '<div class="tablefoot"><span>' + filtered.length + ' đơn</span><div class="actions"><button class="small" id="prevPage" ' + (page === 1 ? 'disabled' : '') + '>←</button>' +
    page + ' / ' + maxPage + '<button class="small" id="nextPage" ' + (page === maxPage ? 'disabled' : '') + '>→</button></div></div>';
  const updateSelection = () => {
    const sc = $('selectionCount');
    const ba = $('bulkAction');
    const sp = $('selectPage');
    if (sc) sc.textContent = 'Đã chọn ' + selectedOrders.size + ' đơn';
    if (ba) ba.disabled = !selectedOrders.size || !can('admin', 'operator', 'warehouse');
    if (sp) {
      const count = visible.filter(order => selectedOrders.has(order.id)).length;
      sp.checked = !!visible.length && count === visible.length;
      sp.indeterminate = count > 0 && count < visible.length;
    }
  };
  document.querySelectorAll('.orderSelection').forEach(checkbox => {
    checkbox.onchange = () => {
      const order = orders.find(row => row.id === Number(checkbox.dataset.selectId));
      if (checkbox.checked) selectedOrders.set(order.id, order.version);
      else selectedOrders.delete(order.id);
      updateSelection();
    };
  });
  $('selectPage').onchange = event => {
    for (const order of visible) {
      if (event.target.checked) selectedOrders.set(order.id, order.version);
      else selectedOrders.delete(order.id);
    }
    drawList();
  };
  if ($('selectFiltered')) {
    $('selectFiltered').onclick = () => {
      if (filtered.length > 500) {
        notice('Mỗi lần chọn tối đa 500 đơn. Hãy lọc theo khách hoặc thu hẹp tìm kiếm.', true);
        return;
      }
      for (const order of filtered) selectedOrders.set(order.id, order.version);
      drawList();
    };
  }
  if ($('clearSelection')) $('clearSelection').onclick = () => { selectedOrders.clear(); drawList(); };
  if ($('bulkAction')) $('bulkAction').onclick = bulkForm;
  if ($('prevPage')) $('prevPage').onclick = () => { page--; drawList(); };
  if ($('nextPage')) $('nextPage').onclick = () => { page++; drawList(); };
  bindOrderRows();
  updateSelection();
};

function bulkForm() {
  const actions = [];
  if (can('admin', 'operator')) actions.push(['approve', 'Duyệt & giữ tồn'], ['cancel', 'Hủy đơn'], ['delete', 'Xóa đơn vào thùng rác']);
  if (can('admin', 'warehouse')) actions.push(['picked', 'Xác nhận lấy hàng'], ['packed', 'Xác nhận đóng gói'], ['ship', 'Bàn giao bằng vận đơn đã có']);
  if (can('admin', 'operator', 'warehouse')) actions.push(['tracking', 'Cập nhật vận chuyển']);
  const selection = [...selectedOrders].map(([id, version]) => ({id, version}));
  openModal('Xử lý ' + selection.length + ' đơn',
    '<form id="bulkForm"><div class="field"><label for="bulkType">Thao tác</label><select id="bulkType" name="action">' +
    actions.map(([key, label]) => '<option value="' + key + '">' + label + '</option>').join('') + '</select></div>' +
    '<div class="field" id="bulkCarrierField" hidden><label for="bulkCarrier">Hãng vận chuyển</label><input id="bulkCarrier" name="carrier"></div>' +
    '<div class="field" id="bulkStatusField" hidden><label for="bulkStatus">Trạng thái vận chuyển</label><select id="bulkStatus" name="status">' +
    Object.entries(meta.tracking).filter(([key]) => !['NOT_SHIPPED', 'UNKNOWN'].includes(key)).map(([key, label]) => '<option value="' + key + '">' + label + '</option>').join('') +
    '</select></div>' + area('Ghi chú / lý do', 'note') +
    '<p class="muted">Hệ thống báo kết quả của từng đơn. Bàn giao hàng loạt sử dụng mã vận đơn đã lưu trên từng đơn. Xóa đơn sẽ đưa vào thùng rác và giải phóng tồn đang giữ.</p>' +
    '<div class="modalfoot"><button class="primary">Thực hiện ' + selection.length + ' đơn</button></div></form>');
  $('bulkType').onchange = () => {
    const type = $('bulkType').value;
    $('bulkCarrierField').hidden = type !== 'ship';
    $('bulkCarrier').required = type === 'ship';
    $('bulkStatusField').hidden = type !== 'tracking';
  };
  $('bulkForm').onsubmit = event => {
    event.preventDefault();
    run(async () => {
      const result = await post('/api/wf/bulk', {...formData('bulkForm'), orders: selection});
      const labels = new Map(orders.map(order => [order.id, order.code]));
      for (const row of result.results) if (row.ok) selectedOrders.delete(row.id);
      await refresh();
      await render();
      openModal('Kết quả xử lý ' + result.total + ' đơn',
        '<p>' + result.succeeded + ' đơn đã xử lý, ' + (result.total - result.succeeded) + ' đơn cần kiểm tra.</p>' +
        '<table><thead><tr><th>Mã đơn</th><th>Kết quả</th></tr></thead><tbody>' +
        result.results.map(row => '<tr><td>' + esc(labels.get(row.id) || row.id) + '</td><td>' +
          (row.ok ? esc(row.stage === 'DELETED' ? 'Đã xóa vào thùng rác' : meta.stages[row.stage] || row.stage) + ' ' + esc(row.issue || '') : '<span class="badge red">' + esc(row.error) + '</span>') +
          '</td></tr>').join('') + '</tbody></table>');
    }, event.submitter);
  };
}

openOrder = async function (id) {
  await baseOpenOrder(id);
  const order = current;
  const section = document.createElement('div');
  section.className = 'callout info';
  section.textContent = 'Ngày hoàn thành giao hàng: ' + date(order.completed_at) +
    (order.returned_at ? ' · Ngày hoàn về: ' + date(order.returned_at) : '');
  if ($('modalBody')) $('modalBody').prepend(section);
};

customerDetail = function (id) {
  baseCustomerDetail(id);
  if (!can('admin', 'operator')) return;
  const button = document.createElement('button');
  button.className = 'danger';
  button.textContent = 'Xóa khách hàng';
  button.onclick = () => {
    const customer = customers.find(row => row.id === id);
    if (!customer) return;
    openModal('Xóa khách ' + customer.name,
      '<p>Khách sẽ được chuyển vào thùng rác. Các đơn hàng, tồn kho và đối soát đã phát sinh vẫn được giữ để tra cứu.</p>' +
      '<button class="danger" id="confirmDeleteCustomer">Xác nhận xóa khách</button>');
    if ($('confirmDeleteCustomer')) $('confirmDeleteCustomer').onclick = event => run(async () => {
      await api('/api/wf/customers/' + id, {method: 'DELETE'});
      await refresh();
      await render();
      if ($('modal')) $('modal').close();
      notice('Đã xóa khách. Có thể khôi phục trong Thùng rác.');
    }, event.target);
  };
  if ($('modalBody')) $('modalBody').append(button);
};

stockHistory = async function () {
  const rows = await api('/api/wf/stock/history');
  openModal('Phiếu nhập / xuất kho',
    '<div class="tablewrap"><table><thead><tr><th>Phiếu</th><th>Thời gian</th><th>Khách / SKU</th><th>Số lượng</th><th>Lý do</th><th></th></tr></thead><tbody>' +
    rows.map(row => '<tr><td>#' + row.id + '</td><td>' + date(row.at) + '</td><td>' + esc(row.customer) + '<span class="sub">' + esc(row.sku) + '</span></td><td>' +
      row.quantity + '</td><td class="wrap">' + esc(row.reason) + '</td><td>' +
      (row.voided ? '<span class="badge">Đã xóa phiếu nhập</span>' : !row.order_id && row.quantity > 0 && can('admin', 'warehouse') ? '<button class="small danger" data-void-receipt="' + row.id + '">Xóa phiếu nhập</button>' : '') +
      '</td></tr>').join('') + '</tbody></table></div>');
  document.querySelectorAll('[data-void-receipt]').forEach(button => {
    button.onclick = () => {
      const id = Number(button.dataset.voidReceipt);
      openModal('Xóa phiếu nhập kho #' + id,
        '<p>Hệ thống sẽ ghi giảm đúng số lượng của phiếu và lưu lịch sử. Tồn sau khi xóa phải đủ cho các đơn đang giữ hàng.</p><button class="danger" id="confirmVoidReceipt">Xóa phiếu nhập</button>');
      $('confirmVoidReceipt').onclick = event => run(async () => {
        await api('/api/wf/stock/receipts/' + id, {method: 'DELETE'});
        await render();
        await stockHistory();
        notice('Đã xóa phiếu nhập và cập nhật tồn kho.');
      }, event.target);
    };
  });
};

async function renderImportHistory() {
  const currentSeq = renderSeq;
  const rows = await api('/api/wf/imports');
  const target = $('importHistory');
  if (currentSeq !== renderSeq || view !== 'imports' || !target) return;
  target.innerHTML = '<div class="panel"><div class="panelhead"><h3>Các phiếu nhập đơn</h3></div>' +
    '<div class="tablewrap"><table><thead><tr><th>File nhập</th><th>Thời gian</th><th>Người nhập</th><th>Số đơn tạo</th><th></th></tr></thead><tbody>' +
    rows.map(row => '<tr><td>' + esc(row.filename) + '</td><td>' + date(row.at) + '</td><td>' + esc(row.actor) + '</td><td>' + row.total + '</td><td>' +
      (row.deleted ? '<span class="badge">Đã xóa</span>' : can('admin', 'operator') ? '<button class="small danger" data-delete-import="' + row.id + '">Xóa phiếu nhập</button>' : '') +
      '</td></tr>').join('') + '</tbody></table></div></div>';
  document.querySelectorAll('[data-delete-import]').forEach(button => {
    button.onclick = () => {
      const row = rows.find(item => item.id === button.dataset.deleteImport);
      if (!row) return;
      openModal('Xóa phiếu nhập đơn',
        '<p>Chuyển ' + row.total + ' đơn được tạo bởi phiếu này vào thùng rác và giải phóng phần tồn đang giữ. Phiếu có đơn đã bàn giao cần xử lý các đơn đó trước.</p>' +
        '<button class="danger" id="confirmDeleteImport">Xóa phiếu nhập</button>');
      if ($('confirmDeleteImport')) {
        $('confirmDeleteImport').onclick = event => run(async () => {
          const result = await api('/api/wf/imports/' + row.id, {method: 'DELETE'});
          await refresh();
          await render();
          if ($('modal')) $('modal').close();
          notice('Đã xóa phiếu nhập và ' + result.deleted + ' đơn.');
        }, event.target);
      }
    };
  });
}

async function renderTrash() {
  const currentSeq = renderSeq;
  const result = await api('/api/wf/trash');
  const content = $('content');
  if (currentSeq !== renderSeq || view !== 'trash' || !content) return;
  content.innerHTML = heading('Thùng rác', 'Khôi phục khách hàng và các đơn đã xóa.') +
    '<div class="panel"><div class="panelhead"><h3>Đơn hàng</h3></div><div class="tablewrap"><table><thead><tr><th>Mã đơn</th><th>Khách</th><th>Ngày xóa</th><th></th></tr></thead><tbody>' +
    result.orders.map(order => '<tr><td>' + esc(order.code) + '</td><td>' + esc(order.customer) + '</td><td>' + date(order.deleted_at) + '</td><td>' +
      (can('admin', 'operator') ? '<button class="small" data-restore-order="' + order.id + '">Khôi phục</button>' : '') + '</td></tr>').join('') +
    '</tbody></table></div></div><div class="panel"><div class="panelhead"><h3>Khách hàng</h3></div><div class="pad">' +
    result.customers.map(customer => '<p>' + esc(customer.name) + ' · ' + esc(customer.code) + ' ' +
      (can('admin', 'operator') ? '<button class="small" data-restore-customer="' + customer.id + '">Khôi phục</button>' : '') + '</p>').join('') + '</div></div>';
  for (const kind of ['order', 'customer']) {
    document.querySelectorAll('[data-restore-' + kind + ']').forEach(button => {
      button.onclick = event => run(async () => {
        const id = button.getAttribute('data-restore-' + kind);
        await post('/api/wf/' + (kind === 'order' ? 'orders' : 'customers') + '/' + id + '/restore', {});
        await refresh();
        await renderTrash();
        notice('Đã khôi phục.');
      }, event.target);
    });
  }
}

async function renderPeriods() {
  const currentSeq = renderSeq;
  const rows = await api('/api/wf/periods');
  const content = $('content');
  if (currentSeq !== renderSeq || view !== 'periods' || !content) return;
  content.innerHTML = heading('Đợt đối soát', 'Lập đợt theo khoảng ngày, kiểm tra số liệu và chốt bảng kê.',
    can('admin', 'finance') ? '<button class="primary" id="newPeriod">+ Tạo đợt đối soát</button>' : '') +
    '<div class="panel"><div class="tablewrap"><table><thead><tr><th>Tên đợt</th><th>Từ ngày</th><th>Đến ngày</th><th>Số đơn</th><th>Trạng thái</th><th></th></tr></thead><tbody>' +
    rows.map(period => '<tr><td>' + esc(period.name) + '</td><td>' + period.date_from + '</td><td>' + period.date_to + '</td><td>' + period.total + '</td><td>' +
      (period.state === 'CLOSED' ? '<span class="badge green">Đã chốt</span>' : '<span class="badge orange">Đang đối soát</span>') +
      '</td><td><button class="small" data-period="' + period.id + '">Mở đợt</button></td></tr>').join('') + '</tbody></table></div></div>';
  if ($('newPeriod')) $('newPeriod').onclick = periodForm;
  document.querySelectorAll('[data-period]').forEach(button => {
    button.onclick = () => run(() => openPeriod(Number(button.dataset.period)));
  });
}

function periodTotals(totals) {
  return '<div class="tablewrap"><table><thead><tr><th>Tiền tệ</th><th>COD</th><th>Phí hãng</th><th>Phí FOXRUN</th><th>Hãng đã chuyển</th><th>Đã trả seller</th><th>Số dư seller</th></tr></thead><tbody>' +
    Object.entries(totals).map(([currency, values]) => '<tr><td>' + esc(currency) + '</td>' +
      ['cod', 'carrier_fee', 'service_fee', 'carrier_received', 'seller_paid', 'seller_balance'].map(key => '<td>' + money(values[key]) + '</td>').join('') + '</tr>').join('') + '</tbody></table></div>';
}

function periodForm() {
  const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  openModal('Tạo đợt đối soát',
    '<form id="periodForm"><div class="fieldgrid">' + field('Tên đợt', 'name', 'Đối soát ' + today, 'text', true, 'required') +
    field('Từ ngày', 'date_from', today.slice(0, 8) + '01', 'date', false, 'required') + field('Đến ngày', 'date_to', today, 'date', false, 'required') +
    '<div class="field"><label for="periodCustomer">Khách hàng</label><select id="periodCustomer" name="customer_id"><option value="">Tất cả khách</option>' + customerOptions() + '</select></div>' +
    '<div class="field"><label for="periodBasis">Lọc theo</label><select id="periodBasis" name="date_basis"><option value="completion">Ngày giao thành công / hoàn về</option><option value="created">Ngày lên đơn</option></select></div></div>' +
    '<p class="muted">Bao gồm cả ngày bắt đầu và ngày kết thúc theo giờ Việt Nam. Đơn chỉ thuộc một đợt đối soát tại một thời điểm.</p>' +
    '<button type="button" id="previewPeriod">Xem đơn trong khoảng ngày</button><div id="periodPreview"></div>' +
    '<div class="modalfoot"><button class="primary">Tạo đợt</button></div></form>');
  const data = () => {
    const values = formData('periodForm');
    values.customer_id = values.customer_id ? Number(values.customer_id) : null;
    return values;
  };
  $('previewPeriod').onclick = event => run(async () => {
    const values = data();
    const params = new URLSearchParams({date_from: values.date_from, date_to: values.date_to, date_basis: values.date_basis});
    if (values.customer_id) params.set('customer_id', values.customer_id);
    const rows = await api('/api/wf/period-candidates?' + params);
    const previewEl = $('periodPreview');
    if (!previewEl) return;
    const available = rows.filter(order => !order.period_id);
    previewEl.innerHTML = '<p>' + available.length + ' đơn có thể thêm, ' + (rows.length - available.length) + ' đơn đã thuộc đợt khác.</p>' + orderTable(available);
    bindOrderRows();
  }, event.target);
  $('periodForm').onsubmit = event => {
    event.preventDefault();
    run(async () => {
      const period = await post('/api/wf/periods', data());
      await refresh();
      await renderPeriods();
      await openPeriod(period.id);
      notice('Đã tạo đợt với ' + period.total + ' đơn.');
    }, event.submitter);
  };
}

async function openPeriod(id) {
  const period = await api('/api/wf/periods/' + id);
  openModal(period.name,
    '<p>' + period.date_from + ' đến ' + period.date_to + ' · ' + period.total + ' đơn · ' + (period.state === 'CLOSED' ? 'Đã chốt' : 'Đang đối soát') + '</p>' +
    periodTotals(period.totals) + '<div class="actions pad"><a href="/api/wf/periods/' + id + '/export-xlsx"><button class="primary small" style="background:#107c41;color:#fff;border-color:#107c41;font-weight:600">📊 Xuất Excel (.xlsx)</button></a><a href="/api/wf/periods/' + id + '/export"><button class="small">Xuất CSV</button></a>' +
    '<button id="openExpenseSheet">Bảng kê chi phí</button>' +
    (can('admin', 'finance') ? period.state === 'DRAFT' ?
      '<button class="primary" data-period-action="close">Chốt đợt đối soát</button><button class="danger" data-period-action="delete">Xóa đợt</button>' :
      '<button data-period-action="reopen">Mở lại đợt</button>' : '') + '</div>' +
    '<p class="muted">Mở từng đơn để cập nhật COD, phí và tiền thực nhận. Chốt đợt sau khi tất cả đơn đã đối soát khớp.</p>' + orderTable(period.orders));
  bindOrderRows();
  $('openExpenseSheet').onclick = event => run(() => openExpenseSheet(period), event.target);
  document.querySelectorAll('[data-period-action]').forEach(button => {
    const action = button.dataset.periodAction;
    button.onclick = event => run(async () => {
      await post('/api/wf/periods/' + id, {action, version: period.version});
      await refresh();
      await renderPeriods();
      if (action === 'delete') $('modal').close();
      else await openPeriod(id);
      notice('Đã cập nhật đợt đối soát.');
    }, event.target);
  });
}

if (user && meta) {
  drawNav();
  run(render);
}

const expenseFields = {carrier_fee: 'Phí hãng', service_fee: 'Phí FOXRUN'};

function expenseTable(rules, rows, totals, preview = false) {
  if (!rows.length) return '<p class="muted">Chưa có khoản phí được áp dụng.</p>';
  const feeHeaders = Object.values(expenseFields).map(label => '<th>' + label + '</th>').join('');
  const headers = rules.map(rule => '<th>' + esc(rule.name) + '</th>').join('');
  const body = rows.map(row => {
    const amounts = rules.map((rule, index) => {
      const amount = row.items[String(index)];
      return '<td>' + (amount === undefined ? '—' : money(amount)) + '</td>';
    }).join('');
    const fees = Object.keys(expenseFields).map(field => {
      const changed = preview && row.before[field] !== row.after[field];
      if (!preview && !row.managed.includes(field)) return '<td>—</td>';
      return '<td>' + (changed ? '<span class="muted">' + money(row.before[field]) + ' → </span>' : '') + money(row.after[field]) + '</td>';
    }).join('');
    return '<tr><td>' + esc(row.code) + '<span class="sub">' + esc(row.customer) + '</span></td><td>' + esc(row.currency) +
      '</td>' + amounts + fees + (preview ? '<td>' + (row.error ? '<span class="badge red">' + esc(row.error) + '</span>' : '—') + '</td>' : '') + '</tr>';
  }).join('');
  const summary = Object.entries(totals).map(([currency, group]) => {
    const amounts = rules.map((rule, index) => group.items[String(index)]);
    const fees = Object.keys(expenseFields).map(field => group.after[field]);
    return '<tr><th scope="row">Tổng</th><td>' + esc(currency) + '</td>' +
      [...amounts, ...fees].map(amount => '<td>' + (amount === undefined ? '—' : money(amount)) + '</td>').join('') +
      (preview ? '<td></td>' : '') + '</tr>';
  }).join('');
  return '<div class="tablewrap"><table><thead><tr><th>Mã đơn</th><th>Tiền tệ</th>' + headers + feeHeaders +
    (preview ? '<th>Lỗi</th>' : '') + '</tr></thead><tbody>' + body + '</tbody><tfoot>' + summary + '</tfoot></table></div>';
}

function expenseOptions(values, selected) {
  return values.map(([key, label]) => '<option value="' + esc(key) + '"' +
    (String(key) === String(selected ?? '') ? ' selected' : '') + '>' + esc(label) + '</option>').join('');
}

function expenseRuleEditor(rule) {
  const input = (label, name, value, max, placeholder = '') =>
    '<label class="field">' + label + '<input data-expense="' + name + '" value="' + esc(value) +
      '" maxlength="' + max + '" placeholder="' + esc(placeholder) + '" required></label>';
  const select = (label, name, options, selected) =>
    '<label class="field">' + label + '<select data-expense="' + name + '">' + expenseOptions(options, selected) + '</select></label>';
  return '<fieldset class="expenseRule panel pad"><div class="fieldgrid">' +
    input('Tên khoản phí', 'name', rule.name, 120, 'Ví dụ Phí đóng gói') +
    input('Công thức', 'formula', rule.formula, 240, 'Ví dụ 2 + so_luong * 0.5') +
    select('Hạch toán vào', 'fee', Object.entries(expenseFields), rule.fee) +
    select('Áp dụng cho', 'status', [['ALL', 'Đã giao và đã hoàn'], ['DELIVERED', 'Đã giao'], ['RETURNED', 'Đã hoàn']], rule.status) +
    select('Tiền tệ', 'currency', [['', 'Tất cả'], ...['MYR', 'THB', 'PHP', 'IDR', 'USD', 'VND'].map(c => [c, c])], rule.currency) +
    select('Khách hàng', 'customer_id', [['', 'Tất cả'], ...customers.map(c => [c.id, c.name]), ...(rule.customer_id && !customers.some(c => c.id === rule.customer_id) ? [[rule.customer_id, 'Khách #' + rule.customer_id]] : [])], rule.customer_id) +
    '<label class="field">Mẫu công thức<select class="expenseTemplate">' +
    expenseOptions([['', 'Chọn mẫu'], ...expenseTemplates.map(([label, formula]) => [formula, label])], '') +
    '</select></label></div><button type="button" class="small danger removeExpense">Bỏ khoản phí</button></fieldset>';
}

const expenseTemplates = [
  ['IF theo số lượng', '=IF(so_luong>=3,5,2)'],
  ['IFS theo bậc COD', '=IFS(cod_thu>=200,5,cod_thu>=100,3,TRUE,1)'],
  ['Phí COD tối thiểu', '=MAX(1,ROUND(cod_thu*2%,2))'],
  ['Tra phí theo khách và tiền tệ', '=VLOOKUP(CONCATENATE(khach,"|",tien_te),BANG_GIA,2,FALSE)'],
  ['Tra phí theo khách, mặc định 0', '=XLOOKUP(khach,MA_TRA,PHI_TRA,0)'],
];

function rateEditor(rate) {
  return '<tr><td><input class="rateKey" aria-label="Mã tra" maxlength="120" value="' +
    esc(rate.key) + '" placeholder="Ví dụ HL|MYR" required></td><td><input class="rateAmount" aria-label="Mức phí" type="number" min="0" step="0.01" value="' +
    esc(rate.amount) + '" required></td><td><button type="button" class="small danger removeRate">Xóa</button></td></tr>';
}

function visibleFeeTotals(rows, byCustomer) {
  const totals = {};
  const accumulate = (target, values) => {
    for (const [key, amount] of Object.entries(values)) {
      const [integer, decimals = ''] = String(amount).split('.');
      const cents = BigInt(integer) * 100n + BigInt(decimals.padEnd(2, '0'));
      target[key] = (target[key] || 0n) + cents;
    }
  };
  for (const row of rows) {
    if (row.error || !row.managed.length) continue;
    const key = byCustomer ? row.currency + ' · ' + row.customer + (row.customer_code ? ' (' + row.customer_code + ')' : '') : row.currency;
    const entry = totals[key] ||= {items: {}, after: {}};
    accumulate(entry.items, row.items);
    accumulate(entry.after, Object.fromEntries(row.managed.map(field => [field, row.after[field]])));
  }
  for (const entry of Object.values(totals)) {
    for (const part of [entry.items, entry.after]) {
      for (const key of Object.keys(part)) part[key] = (part[key] / 100n) + '.' + String(part[key] % 100n).padStart(2, '0');
    }
  }
  return totals;
}

function showExpenseReport(container, rules, rows, preview = false) {
  if (!rows.length) {
    container.innerHTML = expenseTable(rules, rows, {}, preview);
    return;
  }
  const currencies = [...new Set(rows.map(row => row.currency))].sort();
  container.innerHTML = '<div class="fieldgrid"><label class="field">Tìm mã đơn, vận đơn hoặc khách<input data-report="query"></label>' +
    '<label class="field">Tiền tệ<select data-report="currency">' +
    expenseOptions([['', 'Tất cả'], ...currencies.map(currency => [currency, currency])], '') +
    '</select></label><label class="field">Sắp xếp<select data-report="sort"><option value="code">Mã đơn</option>' +
    '<option value="customer">Khách hàng</option><option value="amount">Tổng phí cao nhất</option></select></label>' +
    '<label class="field">Tổng hợp theo<select data-report="group"><option value="currency">Tiền tệ</option>' +
    '<option value="customer">Khách và tiền tệ</option></select></label></div><div data-report="result"></div>';
  const control = name => container.querySelector('[data-report="' + name + '"]');
  const render = () => {
    const query = control('query').value.trim().toLowerCase();
    const currency = control('currency').value;
    const sort = control('sort').value;
    const fee = row => Object.values(row.items).reduce((total, amount) => total + Number(amount), 0);
    const filtered = rows.filter(row => (!currency || row.currency === currency) &&
      [row.code, row.tracking, row.customer, row.customer_code].some(value => String(value || '').toLowerCase().includes(query)));
    filtered.sort((a, b) => sort === 'amount' ?
      a.currency.localeCompare(b.currency) || fee(b) - fee(a) :
      String(a[sort]).localeCompare(String(b[sort]), 'vi'));
    control('result').innerHTML = '<p class="muted">' + filtered.length + ' / ' + rows.length + ' đơn</p>' +
      expenseTable(rules, filtered, visibleFeeTotals(filtered, control('group').value === 'customer'), preview);
  };
  container.oninput = render;
  render();
}

async function openExpenseSheet(period) {
  const url = '/api/wf/periods/' + period.id + '/expenses';
  const sheet = await api(url);
  const editable = sheet.state === 'DRAFT' && can('admin', 'finance');
  openModal('Bảng kê chi phí · ' + period.name,
    '<div class="actions"><button id="expenseBack">Quay lại đợt</button>' +
    (sheet.rows.length ? '<a href="' + url + '/export">Tải CSV</a> <a href="' + url + '/export-xlsx">Tải Excel cho Google Sheets</a>' : '') + '</div>' +
    '<div class="section"><h3>Phí đã áp dụng</h3><div id="appliedExpenseReport"></div></div>' +
    (editable ? '<form id="expenseForm"><div class="callout info"><strong>Biến trong công thức</strong><p><code>cod</code> là COD của đơn, <code>cod_thu</code> là COD thu khi giao thành công, <code>so_luong</code> là tổng số sản phẩm, <code>so_sku</code> là số mã SKU, <code>don</code> bằng 1.</p>' +
      '<p>Dùng IF, IFS, IFERROR, IFNA, AND, OR, NOT, SUM, AVERAGE, COUNT, MIN, MAX, ABS, ROUND, ROUNDUP, ROUNDDOWN, CONCAT, CONCATENATE, VLOOKUP và XLOOKUP.</p>' +
      '<p><code>khach</code> là mã khách, <code>tien_te</code> là tiền tệ, <code>trang_thai</code> là DELIVERED hoặc RETURNED. Hỗ trợ dấu phẩy hoặc chấm phẩy giữa đối số. Dùng dấu chấm cho số thập phân.</p>' +
      '<p>Các khoản cùng nhóm được cộng để thay tổng phí của nhóm trên đơn phù hợp. Bỏ quy tắc và áp dụng lại để trả nhóm phí về số trước khi tự tính. Phí thay đổi cần kiểm tra lại tiền thực nhận.</p></div>' +
      '<div class="actions"><button type="button" id="addExpenseRule">+ Thêm khoản phí</button><button type="button" id="copyExpenseRules">Sao chép từ đợt khác</button></div>' +
      '<div id="expenseCopyPicker"></div><div id="expenseRules"></div>' +
      '<details class="section"><summary>Bảng tra phí và dán dữ liệu</summary><p class="muted">Hai cột mã tra và mức phí. Có thể dùng mã ghép như HL|MYR.</p>' +
      '<div class="tablewrap"><table><thead><tr><th>Mã tra</th><th>Mức phí</th><th></th></tr></thead><tbody id="expenseRates"></tbody></table></div>' +
      '<button type="button" id="addExpenseRate">+ Thêm dòng tra phí</button>' +
      '<label class="field">Dán hai cột từ Sheets hoặc Excel, bỏ hàng tiêu đề<textarea id="pasteExpenseRates" rows="3" placeholder="Mã tra [Tab] Mức phí"></textarea></label>' +
      '<button type="button" id="importExpenseRates">Thêm các dòng đã dán</button></details><div class="actions"><button type="submit" id="previewExpenses">Xem trước</button>' +
      '<button type="button" class="primary" id="applyExpenses" disabled>Áp dụng phí vào các đơn</button></div><div id="expensePreview"></div></form>' :
      '<p class="muted">Có thể chỉnh công thức khi đợt đang mở và tài khoản có quyền đối soát.</p>'));
  $('expenseBack').onclick = () => run(() => openPeriod(period.id));
  showExpenseReport($('appliedExpenseReport'), sheet.rules, sheet.rows);
  if (!editable) return;
  let prepared = null;
  let revision = 0;
  function invalidate() {
    revision++;
    prepared = null;
    $('applyExpenses').disabled = true;
    $('expensePreview').innerHTML = '';
  }
  function addRule(rule) {
    $('expenseRules').insertAdjacentHTML('beforeend', expenseRuleEditor(rule));
  }
  for (const rule of sheet.rules) addRule(rule);
  $('expenseRates').innerHTML = sheet.rates.map(rateEditor).join('');
  $('expenseRates').oninput = invalidate;
  $('expenseRates').onclick = event => {
    if (!event.target.closest('.removeRate')) return;
    event.target.closest('tr').remove();
    invalidate();
  };
  $('addExpenseRate').onclick = () => {
    if ($('expenseRates').children.length >= 500) return notice('Tối đa 500 dòng tra phí.', true);
    $('expenseRates').insertAdjacentHTML('beforeend', rateEditor({key: '', amount: ''}));
    invalidate();
  };
  $('importExpenseRates').onclick = () => run(async () => {
    const lines = $('pasteExpenseRates').value.split(/\r?\n/).filter(line => line.trim());
    if (!lines.length) throw Error('Dán hai cột mã tra và mức phí trước.');
    if (lines.length + $('expenseRates').children.length > 500) throw Error('Tối đa 500 dòng tra phí.');
    const existing = new Set([...$('expenseRates').querySelectorAll('.rateKey')].map(input => input.value.trim().toLowerCase()));
    const rows = lines.map((line, i) => {
      const [key, amount, ...extra] = line.split('\t').map(value => value.trim());
      if (!key || key.length > 120 || !/^\d+(?:[.,]\d{1,2})?$/.test(amount || '') || extra.length) {
        throw Error('Dòng ' + (i + 1) + ' cần mã tra và một mức phí không âm.');
      }
      if (existing.has(key.toLowerCase())) throw Error('Mã tra bị trùng ' + key);
      existing.add(key.toLowerCase());
      return {key, amount: amount.replace(',', '.')};
    });
    $('expenseRates').insertAdjacentHTML('beforeend', rows.map(rateEditor).join(''));
    $('pasteExpenseRates').value = '';
    invalidate();
  });
  $('expenseRules').oninput = event => {
    if (event.target.matches('.expenseTemplate') && event.target.value) {
      event.target.closest('.expenseRule').querySelector('[data-expense="formula"]').value = event.target.value;
      event.target.value = '';
    }
    invalidate();
  };
  $('expenseRules').onclick = event => {
    if (!event.target.closest('.removeExpense')) return;
    event.target.closest('.expenseRule').remove();
    invalidate();
  };
  $('addExpenseRule').onclick = () => {
    if ($('expenseRules').children.length >= 50) {
      notice('Mỗi đợt hỗ trợ tối đa 50 khoản phí.', true);
      return;
    }
    addRule({name: '', formula: '', fee: 'service_fee', status: 'ALL', currency: '', customer_id: null});
    invalidate();
    $('expenseRules').lastElementChild.querySelector('input').focus();
  };
  $('copyExpenseRules').onclick = event => run(async () => {
    const periods = (await api('/api/wf/periods')).filter(other => other.id !== period.id);
    if (!$('expenseCopyPicker')) return;
    $('expenseCopyPicker').innerHTML = '<label class="field">Đợt nguồn<select id="expenseSource">' +
      expenseOptions([['', 'Chọn đợt'], ...periods.map(other => [other.id, other.name + ' · ' + other.date_from])], '') +
      '</select></label><button type="button" id="useExpenseSource">Dùng công thức của đợt này</button>';
    $('useExpenseSource').onclick = click => run(async () => {
      const id = Number($('expenseSource').value);
      if (!id) throw Error('Chọn đợt nguồn.');
      const previous = await api('/api/wf/periods/' + id + '/expenses');
      if (!previous.rules.length) throw Error('Đợt này chưa có công thức đã lưu.');
      $('expenseRules').innerHTML = '';
      for (const rule of previous.rules) addRule(rule);
      $('expenseRates').innerHTML = previous.rates.map(rateEditor).join('');
      $('expenseCopyPicker').innerHTML = '';
      invalidate();
      notice('Đã sao chép công thức vào bản nháp.');
    }, click.target);
  }, event.target);
  $('expenseForm').onsubmit = event => {
    event.preventDefault();
    run(async () => {
      invalidate();
      const currentRevision = revision;
      const rules = [...$('expenseRules').children].map(card => {
        const values = Object.fromEntries([...card.querySelectorAll('[data-expense]')].map(input => [input.dataset.expense, input.value]));
        values.customer_id = values.customer_id ? Number(values.customer_id) : null;
        return values;
      });
      const rates = [...$('expenseRates').rows].map(row => ({
        key: row.querySelector('.rateKey').value, amount: row.querySelector('.rateAmount').value,
      }));
      const data = await post(url + '/preview', {version: sheet.version, rules, rates});
      if (currentRevision !== revision || !$('expensePreview')) return;
      const changed = data.rows.filter(row => Object.keys(expenseFields).some(field => row.before[field] !== row.after[field])).length;
      $('expensePreview').innerHTML = '<p>' + changed + ' đơn thay đổi phí · ' + data.errors +
        ' đơn có lỗi</p><div id="previewExpenseReport"></div>';
      showExpenseReport($('previewExpenseReport'), data.rules, data.rows, true);
      prepared = data;
      $('applyExpenses').disabled = !!data.errors;
    }, event.submitter);
  };
  $('applyExpenses').onclick = event => run(async () => {
    if (!prepared) return;
    const result = await post(url + '/apply', {version: sheet.version, rules: prepared.rules, rates: prepared.rates, preview_key: prepared.preview_key});
    await refresh();
    await renderPeriods();
    await openPeriod(period.id);
    notice('Đã lưu bảng kê. Cập nhật phí cho ' + result.changed + ' đơn.');
  }, event.target);
}
