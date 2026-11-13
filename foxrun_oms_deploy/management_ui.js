'use strict';

views.periods = 'Đợt đối soát';
views.trash = 'Thùng rác';
views.tracking_manual = 'Tracking thủ công';
views.tracking_file = 'Tracking từ file';
views.orders = 'Tất cả đơn';
views.pending = 'Chờ duyệt';
views.reconcile = 'Đối soát';
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
  if (name === 'orders') name = 'all';
  if (name === 'pending') name = 'review';
  if (name === 'reconcile') name = 'finance';
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
  if (currentSeq !== renderSeq) return;

  if (view === 'imports') {
    const content = $('content');
    if (!content) return;
    const headingActions = content.querySelector('.heading .actions');
    if (headingActions && !$('btnSyncAllSources')) {
      const btnSync = document.createElement('button');
      btnSync.id = 'btnSyncAllSources';
      btnSync.className = 'primary small';
      btnSync.style.cssText = 'background:linear-gradient(135deg,#722ed1,#531dab);border:none;color:#fff;font-weight:600';
      btnSync.textContent = '🔄 Đồng bộ tất cả Pancake & Google Sheets';
      btnSync.onclick = () => run(syncAllSources, btnSync);
      headingActions.prepend(btnSync);
    }
    const section = document.createElement('div');
    section.id = 'importHistory';
    content.append(section);
    await renderImportHistory();
  } else if (view === 'stock') {
    const content = $('content');
    if (!content) return;
    const headingActions = content.querySelector('.heading .actions');
    if (headingActions && !$('btnBulkImportStock')) {
      const btnStockImport = document.createElement('button');
      btnStockImport.id = 'btnBulkImportStock';
      btnStockImport.className = 'primary small';
      btnStockImport.style.cssText = 'background:#1890ff;color:#fff;font-weight:600';
      btnStockImport.textContent = '📥 Nhập tồn từ file';
      btnStockImport.onclick = openBulkStockModal;
      headingActions.prepend(btnStockImport);
    }
  } else if (view === 'settings' && can('admin')) {
    const content = $('content');
    if (!content || $('systemFeeSection')) return;
    const section = document.createElement('div');
    section.id = 'systemFeeSection';
    section.className = 'panel';
    section.innerHTML = `
      <div class="panelhead">
        <h3>⚙️ Cấu hình phụ phí hệ thống (System Fee Configs)</h3>
        <button class="primary small" id="btnAddSystemFee">+ Thêm cấu hình phí</button>
      </div>
      <div class="pad" id="systemFeeList">
        <span class="muted">Đang tải cấu hình phí hệ thống...</span>
      </div>
    `;
    content.append(section);
    if ($('btnAddSystemFee')) $('btnAddSystemFee').onclick = () => systemFeeModal();
    await loadSystemFees();
  }
};

orderTable = function (rows, selectable = false) {
  const selectionHeader = selectable ? '<th><input type="checkbox" id="selectPage" aria-label="Chọn tất cả đơn trên trang" style="width:auto"></th>' : '';
  const html = rows.map(order => {
    const selection = selectable ? '<td><input type="checkbox" class="orderSelection" data-select-id="' + order.id + '" aria-label="Chọn đơn ' + esc(order.code) + '" style="width:auto" ' + (selectedOrders.has(order.id) ? 'checked' : '') + '></td>' : '';

    let pancakeBadge = '';
    if (order.pancake_sync_status === 'synced') {
      pancakeBadge = ' <span class="badge green" title="Đã cập nhật Pancake POS">✅ Đã cập nhật Pancake</span>';
    } else if (order.pancake_sync_status === 'error' || order.pancake_sync_status === 'failed') {
      pancakeBadge = ' <span class="badge red" title="' + esc(order.pancake_sync_error || 'Lỗi đồng bộ Pancake') + '">⚠️ Lỗi đồng bộ Pancake <button type="button" class="small retry-pancake" data-retry-order="' + order.id + '" style="padding:1px 5px;font-size:10px;margin-left:4px" title="Thử lại">↻ Thử lại</button></span>';
    } else if (order.pancake_order_id) {
      pancakeBadge = ' <span class="badge blue">Pancake #' + esc(order.pancake_order_id) + '</span>';
    }

    let stageDisplay = badge(order.stage);
    if (view === 'warehouse' && (order.tracking || order.stage === 'READY')) {
      stageDisplay = '<span class="badge green" style="background:#f6ffed;color:#389e0d;border-color:#b7eb8f;font-weight:700">SẴN SÀNG GỬI HÀNG</span>';
    }

    let reconcileFlags = '';
    if (order.is_cod_received) {
      reconcileFlags += ' <span class="badge green">✅ Đã nhận COD</span>';
    }
    if (order.is_returned_to_warehouse || order.return_received) {
      reconcileFlags += ' <span class="badge orange">↩️ Đã hoàn</span>';
    }

    let feeDisplay = money(order.cod, order.currency);
    if (order.special_fee && Number(order.special_fee) > 0) {
      feeDisplay += '<span class="sub" style="color:#d46b08">+ Phụ phí: ' + money(order.special_fee, order.currency) + (order.special_fee_note ? ' (' + esc(order.special_fee_note) + ')' : '') + '</span>';
    }

    return '<tr class="click" tabindex="0" data-order="' + order.id + '">' + selection +
      '<td class="code">' + esc(order.code) + '<span class="sub">' + esc(order.source) + ' · ' + date(order.created_at) + '</span>' + pancakeBadge + '</td>' +
      '<td>' + esc(order.customer) + '<span class="sub">' + esc(order.recipient || '') + '</span></td>' +
      '<td>' + stageDisplay + (order.issue ? '<span class="sub">' + esc(order.issue) + '</span>' : '') + '</td>' +
      '<td>' + badge(order.transport, 'transport') + '<span class="sub mono">' + esc(order.tracking || 'Chưa có vận đơn') + (order.stale ? ' · Quá 5 ngày' : '') + '</span>' + reconcileFlags + '</td>' +
      '<td>' + feeDisplay + '</td><td>' + badge(order.finance, 'finance') + '</td>' +
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
  document.querySelectorAll('.retry-pancake').forEach(btn => {
    btn.onclick = event => {
      event.stopPropagation();
      const oid = Number(btn.dataset.retryOrder);
      run(async () => {
        try {
          const res = await post('/api/wf/orders/' + oid + '/sync-pancake', {});
          notice(res.message || 'Đã đồng bộ lại đơn hàng với Pancake POS thành công.');
        } catch {
          notice('Đang gửi yêu cầu đồng bộ lại đơn sang Pancake POS...');
          await syncAllSources();
        }
        await refresh();
        await render();
      }, btn);
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

  let extraToolbarButtons = '<button class="small" id="btnBulkAiNormalize" style="background:#f9f0ff;color:#722ed1;border:1px solid #d3adf7;font-weight:600">✨ Chuẩn hóa địa chỉ (AI)</button>';
  if (view === 'warehouse') {
    extraToolbarButtons += '<button class="primary small" id="btnBulkHandover" style="background:#fa8c16;border-color:#fa8c16;color:#fff;font-weight:600">🚚 Bàn giao vận chuyển</button>';
  }
  if (view === 'finance') {
    extraToolbarButtons += '<button class="primary small" id="btnUploadCodFile" style="background:#52c41a;border-color:#52c41a;color:#fff;font-weight:600">📤 Upload File trả COD Kho (.xlsx/.csv)</button>';
  }

  listArea.innerHTML =
    '<div class="toolbar"><span id="selectionCount"></span><button class="small" id="selectFiltered">Chọn toàn bộ kết quả lọc</button>' +
    '<button class="small" id="clearSelection">Bỏ chọn</button>' +
    extraToolbarButtons +
    '<button class="primary small" id="bulkAction">Thao tác các đơn đã chọn</button></div>' +
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

  if ($('btnBulkAiNormalize')) {
    $('btnBulkAiNormalize').onclick = () => {
      const selected = [...selectedOrders.keys()];
      if (!selected.length) {
        notice('Vui lòng chọn ít nhất 1 đơn hàng để chuẩn hóa địa chỉ bằng AI.', true);
        return;
      }
      run(async () => {
        try {
          const res = await post('/api/wf/ai/normalize-bulk', {order_ids: selected});
          notice(res.message || `Đã chuẩn hóa địa chỉ AI thành công cho ${res.updated || selected.length} đơn.`);
        } catch {
          const prev = await post('/api/wf/ai/preview', {order_ids: selected});
          if (prev && prev.preview_id) {
            await post('/api/wf/ai/confirm', {preview_id: prev.preview_id});
          }
          notice(`Đã chuẩn hóa địa chỉ AI thành công cho ${selected.length} đơn.`);
        }
        await refresh();
        await render();
      });
    };
  }

  if ($('btnBulkHandover')) {
    $('btnBulkHandover').onclick = () => {
      const selected = [...selectedOrders.keys()];
      if (!selected.length) {
        notice('Vui lòng chọn các đơn SẴN SÀNG GỬI HÀNG để bàn giao.', true);
        return;
      }
      run(async () => {
        try {
          const res = await post('/api/wf/orders/bulk-handover', {order_ids: selected});
          notice(res.message || `Đã bàn giao vận chuyển ${res.succeeded || selected.length} đơn.`);
        } catch {
          const selection = [...selectedOrders].map(([id, version]) => ({id, version}));
          const res = await post('/api/wf/bulk', {action: 'ship', carrier: 'Tự động', orders: selection});
          notice(`Đã bàn giao vận chuyển ${res.succeeded} đơn.`);
        }
        selectedOrders.clear();
        await refresh();
        await render();
      });
    };
  }

  if ($('btnUploadCodFile')) {
    $('btnUploadCodFile').onclick = openUploadCodModal;
  }

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

  let extraOrderDetails = 'Ngày hoàn thành: ' + date(order.completed_at) +
    (order.returned_at ? ' · Ngày hoàn về: ' + date(order.returned_at) : '');
  if (order.special_fee && Number(order.special_fee) > 0) {
    extraOrderDetails += ' · Phụ phí đặc biệt: ' + money(order.special_fee, order.currency) + (order.special_fee_note ? ' (' + esc(order.special_fee_note) + ')' : '');
  }
  if (order.is_cod_received) extraOrderDetails += ' · ✅ Kho đã nhận COD';
  if (order.is_returned_to_warehouse) extraOrderDetails += ' · ↩️ Kho đã nhận hoàn';
  if (order.pancake_sync_status === 'synced') extraOrderDetails += ' · ✅ Đã cập nhật Pancake';
  else if (order.pancake_sync_status === 'error' || order.pancake_sync_status === 'failed') extraOrderDetails += ' · ⚠️ Lỗi Pancake: ' + esc(order.pancake_sync_error || '');

  section.innerHTML = extraOrderDetails;
  if ($('modalBody')) $('modalBody').prepend(section);
};

customerDetail = function (id) {
  baseCustomerDetail(id);
  const customer = customers.find(row => row.id === id);
  if (!customer) return;

  if ($('customerEdit')) {
    const profileCard = document.createElement('div');
    profileCard.className = 'callout info';
    profileCard.style.cssText = 'margin:12px 0;padding:12px;font-size:13px';
    profileCard.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <p style="margin:0 0 6px 0"><strong>Mã chính:</strong> <span class="mono font-bold">${esc(customer.primary_code || customer.code)}</span> · <strong>Từ khóa SKU:</strong> ${esc(customer.sku_keywords || 'Chưa có')}</p>
          <p style="margin:0"><strong>Biểu phí:</strong> Giao ${money(customer.fixed_delivery_fee ?? 25)} · Hoàn ${money(customer.fixed_return_fee ?? 15)} · Phí COD ${((Number(customer.cod_fee_percent ?? 0.01)) * 100).toFixed(1)}%</p>
        </div>
        ${can('admin', 'operator') ? '<button type="button" class="small primary" id="btnEditCustomerProfile">✏️ Sửa thông tin & Biểu phí</button>' : ''}
      </div>
    `;
    $('customerEdit').after(profileCard);
    if ($('btnEditCustomerProfile')) {
      $('btnEditCustomerProfile').onclick = () => openEditCustomerProfile(customer);
    }
  }

  const sourcesSec = document.createElement('div');
  sourcesSec.className = 'section';
  sourcesSec.innerHTML = `
    <div class="panelhead">
      <h3>🔗 Nguồn kết nối đơn (Pancake POS & Google Sheet)</h3>
      ${can('admin', 'operator') ? '<button type="button" class="primary small" id="btnAddSource">+ Thêm nguồn kết nối</button>' : ''}
    </div>
    <div id="customerSourcesArea" class="pad"><span class="muted">Đang tải danh sách nguồn...</span></div>
  `;
  if ($('modalBody')) {
    const aliasSection = $('aliasForm')?.closest('.section');
    if (aliasSection && aliasSection.nextSibling) {
      $('modalBody').insertBefore(sourcesSec, aliasSection.nextSibling);
    } else {
      $('modalBody').append(sourcesSec);
    }
    if ($('btnAddSource')) {
      $('btnAddSource').onclick = () => sourceFormModal(id);
    }
    loadCustomerSources(id);
  }

  if (!can('admin', 'operator')) return;
  const button = document.createElement('button');
  button.className = 'danger';
  button.textContent = 'Xóa khách hàng';
  button.onclick = () => {
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

let periodCustomerFilter = '';
let periodStatusFilter = '';
let periodSearchQuery = '';

async function renderPeriods() {
  const currentSeq = renderSeq;
  const rows = await api('/api/wf/periods');
  const content = $('content');
  if (currentSeq !== renderSeq || view !== 'periods' || !content) return;

  const filtered = rows.filter(period => {
    if (periodCustomerFilter && String(period.customer_id) !== String(periodCustomerFilter)) return false;
    if (periodStatusFilter && period.state !== periodStatusFilter) return false;
    if (periodSearchQuery && !period.name.toLowerCase().includes(periodSearchQuery.toLowerCase())) return false;
    return true;
  });

  content.innerHTML = heading('Đợt đối soát', 'Lập đợt theo khoảng ngày, kiểm tra số liệu và chốt bảng kê.',
    (can('admin', 'finance') ? '<button class="small" id="btnBulkCreatePeriods" style="background:#e6f7ff;color:#096dd9;border:1px solid #91d5ff;font-weight:600;margin-right:8px">⚡ Tạo nhanh đợt cho tất cả khách hàng</button><button class="primary" id="newPeriod">+ Tạo đợt đối soát</button>' : '')) +
    '<div class="panel">' +
    '<div class="toolbar" style="border-bottom:1px solid #f0f0f0">' +
    '<input id="periodSearchInput" placeholder="Tìm kiếm tên đợt đối soát..." value="' + esc(periodSearchQuery) + '">' +
    '<select id="periodCustomerFilter"><option value="">Tất cả khách hàng</option>' + customerOptions(periodCustomerFilter) + '</select>' +
    '<select id="periodStatusFilter"><option value="">Tất cả trạng thái</option><option value="DRAFT" ' + (periodStatusFilter === 'DRAFT' ? 'selected' : '') + '>Đang đối soát (Draft)</option><option value="CLOSED" ' + (periodStatusFilter === 'CLOSED' ? 'selected' : '') + '>Đã chốt (Closed)</option><option value="PAID" ' + (periodStatusFilter === 'PAID' ? 'selected' : '') + '>Đã thanh toán (Paid)</option></select>' +
    '</div>' +
    '<div class="tablewrap"><table><thead><tr><th>Tên đợt</th><th>Từ ngày</th><th>Đến ngày</th><th>Số đơn</th><th>Trạng thái</th><th></th></tr></thead><tbody>' +
    filtered.map(period => '<tr><td>' + esc(period.name) + '</td><td>' + period.date_from + '</td><td>' + period.date_to + '</td><td>' + period.total + '</td><td>' +
      (period.state === 'CLOSED' ? '<span class="badge green">Đã chốt</span>' : period.state === 'PAID' ? '<span class="badge blue">Đã thanh toán</span>' : '<span class="badge orange">Đang đối soát</span>') +
      '</td><td><button class="small" data-period="' + period.id + '">Mở đợt</button></td></tr>').join('') +
      (filtered.length ? '' : '<tr><td colspan="6" class="empty">Chưa có đợt đối soát phù hợp bộ lọc.</td></tr>') +
    '</tbody></table></div></div>';

  if ($('newPeriod')) $('newPeriod').onclick = periodForm;
  if ($('btnBulkCreatePeriods')) $('btnBulkCreatePeriods').onclick = openBulkCreatePeriodsModal;

  if ($('periodSearchInput')) $('periodSearchInput').oninput = e => {
    periodSearchQuery = e.target.value.trim();
    renderPeriods();
  };
  if ($('periodCustomerFilter')) $('periodCustomerFilter').onchange = e => {
    periodCustomerFilter = e.target.value;
    renderPeriods();
  };
  if ($('periodStatusFilter')) $('periodStatusFilter').onchange = e => {
    periodStatusFilter = e.target.value;
    renderPeriods();
  };

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
  const initialFrom = today.slice(0, 8) + '01';
  const initialTo = today;

  openModal('Tạo đợt đối soát',
    '<form id="periodForm"><div class="fieldgrid">' +
    '<div class="field"><label for="periodCustomer">Khách hàng (*)</label><select id="periodCustomer" name="customer_id"><option value="">Tất cả khách</option>' + customerOptions() + '</select></div>' +
    field('Tên đợt', 'name', 'Đối soát ' + today, 'text', false, 'required') +
    field('Từ ngày (completed_at)', 'date_from', initialFrom, 'date', false, 'required') +
    field('Đến ngày (completed_at)', 'date_to', initialTo, 'date', false, 'required') +
    '<div class="field" style="display:none"><label for="periodBasis">Lọc theo</label><select id="periodBasis" name="date_basis"><option value="completion" selected>Ngày giao thành công / hoàn về</option><option value="created">Ngày lên đơn</option></select></div></div>' +
    '<p class="muted">Tự động đề xuất tên theo định dạng: Đối soát {MÃ_KHÁCH} ({dd/mm} - {dd/mm/yyyy}).</p>' +
    '<div class="actions" style="margin-bottom:12px"><button type="button" id="previewPeriod">🔍 Xem trước số liệu đối soát</button></div>' +
    '<div id="periodTotalsCard"></div>' +
    '<div id="periodPreview"></div>' +
    '<div class="modalfoot"><button type="button" id="closePeriodForm">Hủy</button><button class="primary">Tạo đợt</button></div></form>');

  $('closePeriodForm').onclick = () => $('modal').close();

  const updateSuggestedName = () => {
    const custId = $('periodCustomer').value;
    const c = customers.find(x => String(x.id) === String(custId));
    const custCode = c ? (c.primary_code || c.code) : 'CHUNG';
    const dFrom = $('f_date_from')?.value || initialFrom;
    const dTo = $('f_date_to')?.value || initialTo;

    const partsFrom = dFrom.split('-');
    const partsTo = dTo.split('-');
    const fromStr = partsFrom.length === 3 ? `${partsFrom[2]}/${partsFrom[1]}` : dFrom;
    const toStr = partsTo.length === 3 ? `${partsTo[2]}/${partsTo[1]}/${partsTo[0]}` : dTo;

    if ($('f_name')) {
      $('f_name').value = `Đối soát ${custCode} (${fromStr} - ${toStr})`;
    }
  };

  $('periodCustomer').onchange = updateSuggestedName;
  if ($('f_date_from')) $('f_date_from').onchange = updateSuggestedName;
  if ($('f_date_to')) $('f_date_to').onchange = updateSuggestedName;
  if (customers.length > 0 && $('periodCustomer').value) updateSuggestedName();

  const data = () => {
    const values = formData('periodForm');
    values.customer_id = values.customer_id ? Number(values.customer_id) : null;
    values.date_basis = values.date_basis || 'completion';
    return values;
  };

  $('previewPeriod').onclick = event => run(async () => {
    const values = data();
    const params = new URLSearchParams({date_from: values.date_from, date_to: values.date_to, date_basis: values.date_basis});
    if (values.customer_id) params.set('customer_id', values.customer_id);

    try {
      await api('/api/wf/periods/preview?' + params);
    } catch {
      // preview endpoint might be optional / calculated locally
    }

    const rows = await api('/api/wf/period-candidates?' + params);
    const previewEl = $('periodPreview');
    const totalsCardEl = $('periodTotalsCard');
    if (!previewEl) return;
    const available = rows.filter(order => !order.period_id);

    const deliveredCount = available.filter(o => o.transport === 'DELIVERED').length;
    const returnedCount = available.filter(o => o.transport === 'RETURNED').length;
    let standardCod = 0;
    let totalFixedFee = 0;
    let totalSpecialFee = 0;
    const cust = customers.find(c => c.id === values.customer_id);
    const fixedDeliv = cust ? Number(cust.fixed_delivery_fee ?? 25) : 25;
    const fixedRet = cust ? Number(cust.fixed_return_fee ?? 15) : 15;
    const codPercent = cust ? Number(cust.cod_fee_percent ?? 0.01) : 0.01;

    for (const o of available) {
      const isDeliv = o.transport === 'DELIVERED';
      const isRet = o.transport === 'RETURNED';
      const cod = isDeliv ? Number(o.cod || 0) : 0;
      standardCod += cod;
      const sFee = Number(o.special_fee || 0);
      totalSpecialFee += sFee;
      if (isDeliv) {
        totalFixedFee += fixedDeliv;
      } else if (isRet) {
        totalFixedFee += fixedRet;
      }
    }
    const codFeeTotal = standardCod * codPercent;
    const netSeller = standardCod - (totalFixedFee + totalSpecialFee + codFeeTotal);

    if (totalsCardEl) {
      totalsCardEl.innerHTML = `
        <div class="metrics" style="grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px">
          <div class="metric"><div class="label">Đơn giao / Đơn hoàn</div><div class="value" style="font-size:20px;color:#52c41a">${deliveredCount} <small style="color:#d46b08">/ ${returnedCount}</small></div></div>
          <div class="metric"><div class="label">Tổng COD chuẩn</div><div class="value" style="font-size:20px">${money(standardCod)}</div></div>
          <div class="metric"><div class="label">Tổng phí cố định + phụ phí</div><div class="value" style="font-size:20px;color:#d46b08">${money(totalFixedFee + totalSpecialFee)}</div></div>
        </div>
        <div class="callout info" style="font-size:13px;margin-bottom:12px">
          <strong>Net seller (Dự kiến thực nhận):</strong> <span style="font-size:16px;font-weight:700;color:#096dd9">${money(netSeller)}</span>
          <span class="sub muted">Công thức: COD chuẩn (${money(standardCod)}) − Phí cố định (${money(totalFixedFee)}) − Phụ phí đặc biệt (${money(totalSpecialFee)}) − Phí thu hộ (${money(codFeeTotal)})</span>
        </div>
      `;
    }

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

async function syncAllSources() {
  try {
    const res = await post('/api/wf/sources/sync-all', {});
    notice(res.message || `Đã đồng bộ thành công ${res.synced || res.count || 0} đơn từ các nguồn.`);
  } catch (err) {
    try {
      const res = await post('/api/wf/sync/all', {});
      notice(res.message || `Đã đồng bộ thành công ${res.synced || res.count || 0} đơn từ các nguồn.`);
    } catch {
      notice('Lỗi đồng bộ nguồn: ' + err.message, true);
    }
  }
  await refresh();
  await render();
}

function openBulkStockModal() {
  openModal('Nhập tồn kho từ file (.xlsx / .csv)', `
    <form id="bulkStockForm">
      <p class="muted">Cập nhật số lượng tồn kho hàng loạt theo danh sách khách hàng và SKU.</p>
      <div class="field">
        <label for="f_stock_file">Chọn file bảng tính (.xlsx, .csv)</label>
        <input type="file" id="f_stock_file" name="file" accept=".xlsx,.csv" required>
      </div>
      <div class="field">
        <label for="f_stock_mode">Chế độ nhập mặc định (nếu file không chỉ định)</label>
        <select id="f_stock_mode" name="default_mode">
          <option value="ADD">ADD · Cộng dồn vào tồn hiện tại</option>
          <option value="SET">SET · Đặt lại số lượng tồn kho tuyệt đối</option>
        </select>
      </div>
      <p class="muted" style="font-size:11px">Các cột hỗ trợ trong file: <code>customer</code> (mã hoặc tên khách), <code>sku</code> (mã sản phẩm), <code>quantity</code> (số lượng), <code>mode</code> (ADD hoặc SET), <code>note</code> (ghi chú).</p>
      <div class="actions" style="margin-bottom:12px">
        <button type="button" class="small" id="btnDownloadSampleStock">📥 Tải file mẫu nhập tồn (.csv)</button>
      </div>
      <div class="modalfoot">
        <button type="button" id="closeBulkStock">Hủy</button>
        <button class="primary">Xác nhận tải lên & Cập nhật</button>
      </div>
    </form>
  `);
  $('btnDownloadSampleStock').onclick = () => download('foxrun_stock_sample.csv', 'customer,sku,quantity,mode,note\nHL,MASK,50,ADD,Nhap hang dau ky\nHL,SERUM,100,SET,Kiem ke kho dinh ky');
  $('closeBulkStock').onclick = () => $('modal').close();
  $('bulkStockForm').onsubmit = e => {
    e.preventDefault();
    const file = $('f_stock_file').files[0];
    if (!file) return notice('Vui lòng chọn file.', true);
    run(async () => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('default_mode', $('f_stock_mode').value);
      const res = await api('/api/wf/stock/bulk-import', {method: 'POST', body: fd});
      $('modal').close();
      notice(res.message || `Đã cập nhật tồn kho thành công cho ${res.updated || res.total || 0} SKU.`);
      await refresh();
      await render();
    }, e.submitter);
  };
}

async function loadSystemFees() {
  const container = $('systemFeeList');
  if (!container) return;
  try {
    const fees = await api('/api/wf/system-fees');
    if (!fees || !fees.length) {
      container.innerHTML = '<p class="muted">Chưa có cấu hình phụ phí hệ thống. Bấm "+ Thêm cấu hình phí" để tạo.</p>';
      return;
    }
    container.innerHTML = `
      <div class="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Mã cấu hình</th>
              <th>Tên loại phí</th>
              <th>Mức phí</th>
              <th>Postcodes áp dụng</th>
              <th>Khu vực / Tỉnh thành</th>
              <th>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            ${fees.map(f => `
              <tr>
                <td class="mono font-bold">${esc(f.key)}</td>
                <td><strong>${esc(f.name)}</strong></td>
                <td>${money(f.fee)}</td>
                <td class="wrap font-mono" style="font-size:11px">${esc(f.postcodes || '—')}</td>
                <td class="wrap" style="font-size:12px">${esc(f.regions || '—')}</td>
                <td>
                  <button type="button" class="small btn-edit-system-fee" data-fee-key="${esc(f.key)}">Sửa</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
    container.querySelectorAll('.btn-edit-system-fee').forEach(b => {
      b.onclick = () => {
        const item = fees.find(f => f.key === b.dataset.feeKey);
        systemFeeModal(item);
      };
    });
  } catch (err) {
    container.innerHTML = `<p class="muted" style="color:#cf1322">Không tải được cấu hình phụ phí: ${esc(err.message)}</p>`;
  }
}

function systemFeeModal(feeItem = null) {
  const isEdit = !!feeItem;
  openModal(isEdit ? 'Sửa cấu hình phí · ' + feeItem.name : 'Thêm cấu hình phụ phí hệ thống', `
    <form id="systemFeeForm">
      <div class="fieldgrid">
        ${field('Mã cấu hình (key)', 'key', feeItem?.key || '', 'text', false, isEdit ? 'readonly' : 'required placeholder="tourism hoặc remote"')}
        ${field('Tên hiển thị phụ phí', 'name', feeItem?.name || '', 'text', false, 'required placeholder="Phí tuyến du lịch / đảo"')}
        ${field('Mức phụ phí', 'fee', feeItem?.fee ?? '30', 'number', false, 'min="0" step="0.01" required')}
        <div class="field"></div>
        ${area('Danh sách mã bưu chính áp dụng (cách nhau bởi dấu phẩy)', 'postcodes', feeItem?.postcodes || '', 'placeholder="20000, 21000, 35000..." rows="3"')}
        ${area('Danh sách tỉnh / thành / khu vực áp dụng (cách nhau bởi dấu phẩy)', 'regions', feeItem?.regions || '', 'placeholder="Langkawi, Tioman, Sabah, Sarawak..." rows="3"')}
      </div>
      <div class="modalfoot">
        <button type="button" id="closeSystemFee">Hủy</button>
        <button class="primary">${isEdit ? 'Lưu thay đổi' : 'Tạo cấu hình'}</button>
      </div>
    </form>
  `);
  $('closeSystemFee').onclick = () => $('modal').close();
  $('systemFeeForm').onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const data = formData('systemFeeForm');
      data.fee = Number(data.fee);
      await post('/api/wf/system-fees', data);
      $('modal').close();
      notice('Đã lưu cấu hình phụ phí hệ thống thành công.');
      await loadSystemFees();
    }, e.submitter);
  };
}

async function loadCustomerSources(cid) {
  const area = $('customerSourcesArea');
  if (!area) return;
  try {
    const sources = await api('/api/wf/customers/' + cid + '/sources');
    if (!sources || !sources.length) {
      area.innerHTML = '<p class="muted">Chưa cấu hình nguồn đơn Pancake hoặc Google Sheet nào cho khách hàng này.</p>';
      return;
    }
    area.innerHTML = `
      <div class="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Loại nguồn</th>
              <th>Tên nguồn</th>
              <th>Tiền tố mã đơn</th>
              <th>Trạng thái</th>
              <th>Chi tiết kết nối</th>
              <th>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            ${sources.map(s => `
              <tr>
                <td>${s.source_type === 'pancake' ? '<span class="badge blue">Pancake POS</span>' : '<span class="badge green">Google Sheet</span>'}</td>
                <td><strong>${esc(s.source_name || 'Nguồn')}</strong></td>
                <td class="mono font-bold">${esc(s.code_prefix || '—')}</td>
                <td>${s.is_active ? '<span class="badge green">Hoạt động</span>' : '<span class="badge">Tạm dừng</span>'}</td>
                <td style="font-size:12px" class="wrap">
                  ${s.source_type === 'pancake'
                    ? ('Shop: ' + esc(s.pancake_shop_id || '—') + ' · Page: ' + esc(s.pancake_page_id || '—') + (s.pancake_status_filter ? ' · Lọc: ' + esc(s.pancake_status_filter) : ''))
                    : ('<a href="' + esc(s.gsheet_url) + '" target="_blank">Mở Sheet</a> · Tab: ' + esc(s.gsheet_tab_name || 'Sheet1'))}
                </td>
                <td>
                  <div class="actions">
                    <button type="button" class="small btn-test-src" data-src-id="${s.id}">🔍 Kiểm tra kết nối</button>
                    <button type="button" class="small primary btn-sync-src" data-src-id="${s.id}">🔄 Đồng bộ ngay</button>
                    <button type="button" class="small btn-edit-src" data-src-id="${s.id}">Sửa</button>
                    <button type="button" class="small danger btn-del-src" data-src-id="${s.id}">Xóa</button>
                  </div>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
    area.querySelectorAll('.btn-test-src').forEach(b => {
      b.onclick = () => run(async () => {
        const sid = b.dataset.srcId;
        const res = await post('/api/wf/customers/' + cid + '/sources/' + sid + '/test', {});
        notice(res.message || 'Kết nối tới nguồn thành công!');
      }, b);
    });
    area.querySelectorAll('.btn-sync-src').forEach(b => {
      b.onclick = () => run(async () => {
        const sid = b.dataset.srcId;
        const res = await post('/api/wf/customers/' + cid + '/sources/' + sid + '/sync', {});
        notice(res.message || ('Đã đồng bộ ' + (res.synced || res.count || 0) + ' đơn hàng.'));
        await refresh();
        await render();
        customerDetail(cid);
      }, b);
    });
    area.querySelectorAll('.btn-edit-src').forEach(b => {
      b.onclick = () => {
        const sid = Number(b.dataset.srcId);
        const s = sources.find(x => x.id === sid);
        sourceFormModal(cid, s);
      };
    });
    area.querySelectorAll('.btn-del-src').forEach(b => {
      b.onclick = () => run(async () => {
        const sid = b.dataset.srcId;
        await api('/api/wf/customers/' + cid + '/sources/' + sid, {method: 'DELETE'});
        notice('Đã xóa nguồn kết nối.');
        await loadCustomerSources(cid);
      }, b);
    });
  } catch (err) {
    area.innerHTML = '<p class="muted" style="color:#cf1322">Lỗi tải nguồn: ' + esc(err.message) + '</p>';
  }
}

function sourceFormModal(cid, s = null) {
  const isEdit = !!s;
  openModal(isEdit ? 'Sửa nguồn kết nối' : 'Thêm nguồn kết nối (Pancake / Google Sheet)', `
    <form id="sourceEditForm">
      <div class="fieldgrid">
        <div class="field">
          <label for="src_type">Loại nguồn</label>
          <select id="src_type" name="source_type" ${isEdit ? 'disabled' : ''}>
            <option value="pancake" ${s?.source_type === 'pancake' ? 'selected' : ''}>Pancake POS</option>
            <option value="google_sheet" ${s?.source_type === 'google_sheet' ? 'selected' : ''}>Google Sheet</option>
          </select>
        </div>
        ${field('Tên nguồn kết nối', 'source_name', s?.source_name || '', 'text', false, 'required placeholder="Ví dụ Pancake Shop Áo / Sheet Đơn VIP"')}
        ${field('Tiền tố mã đơn (code_prefix)', 'code_prefix', s?.code_prefix || '', 'text', false, 'placeholder="Ví dụ FR-MY, AH, HL"')}
        <div class="field" style="display:flex;align-items:center;margin-top:24px">
          <label style="display:flex;gap:8px;align-items:center;cursor:pointer">
            <input type="checkbox" id="src_active" name="is_active" style="width:auto" ${s ? (s.is_active ? 'checked' : '') : 'checked'}>
            Kích hoạt nguồn đồng bộ
          </label>
        </div>
        <div class="full" id="pancakeConfigGroup" style="border:1px solid #91d5ff;background:#e6f7ff;padding:12px;border-radius:6px">
          <h4 style="margin:0 0 10px 0;color:#096dd9">Cấu hình Pancake POS</h4>
          <div class="fieldgrid">
            ${field('Pancake API Key', 'pancake_api_key', s?.pancake_api_key || '', 'password', false, 'placeholder="API Key từ pos.pages.fm"')}
            ${field('Pancake Shop ID', 'pancake_shop_id', s?.pancake_shop_id || '', 'text', false, 'placeholder="Ví dụ 12345"')}
            ${field('Pancake Page ID (tùy chọn)', 'pancake_page_id', s?.pancake_page_id || '', 'text', false, 'placeholder="Ví dụ 67890"')}
            ${field('Bộ lọc trạng thái Pancake', 'pancake_status_filter', s?.pancake_status_filter || '', 'text', false, 'placeholder="Ví dụ Chờ xác nhận, Đã xác nhận"')}
          </div>
        </div>
        <div class="full" id="gsheetConfigGroup" style="border:1px solid #b7eb8f;background:#f6ffed;padding:12px;border-radius:6px;display:none">
          <h4 style="margin:0 0 10px 0;color:#389e0d">Cấu hình Google Sheet</h4>
          <div class="fieldgrid">
            ${field('Google Sheet URL', 'gsheet_url', s?.gsheet_url || '', 'url', false, 'placeholder="https://docs.google.com/spreadsheets/d/..."')}
            ${field('Tên Sheet / Tab', 'gsheet_tab_name', s?.gsheet_tab_name || 'Sheet1', 'text', false, 'placeholder="Sheet1"')}
          </div>
        </div>
      </div>
      <div class="modalfoot">
        <button type="button" id="closeSourceForm">Hủy</button>
        <button class="primary">${isEdit ? 'Lưu thay đổi' : 'Tạo nguồn kết nối'}</button>
      </div>
    </form>
  `);
  const updateTypeView = () => {
    const val = $('src_type').value;
    $('pancakeConfigGroup').style.display = val === 'pancake' ? 'block' : 'none';
    $('gsheetConfigGroup').style.display = val === 'google_sheet' ? 'block' : 'none';
  };
  $('src_type').onchange = updateTypeView;
  updateTypeView();
  $('closeSourceForm').onclick = () => customerDetail(cid);
  $('sourceEditForm').onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const data = formData('sourceEditForm');
      data.is_active = $('src_active').checked;
      if (isEdit) {
        await api('/api/wf/customers/' + cid + '/sources/' + s.id, {method: 'PATCH', body: JSON.stringify(data)});
        notice('Đã cập nhật nguồn kết nối thành công.');
      } else {
        await post('/api/wf/customers/' + cid + '/sources', data);
        notice('Đã thêm nguồn kết nối thành công.');
      }
      customerDetail(cid);
    }, e.submitter);
  };
}

function openEditCustomerProfile(c) {
  openModal('Sửa thông tin & Biểu phí · ' + c.name, `
    <form id="customerProfileForm">
      <div class="fieldgrid">
        ${field('Tên khách hàng', 'name', c.name || '', 'text', false, 'required')}
        ${field('Mã chính (primary_code)', 'primary_code', c.primary_code || c.code || '', 'text', false, 'required maxlength="50"')}
        ${field('Mã bí danh (cách nhau bởi dấu phẩy)', 'aliases_str', (c.aliases || []).join(', '), 'text', true, 'placeholder="Ví dụ FG, HL2, FOX"')}
        ${field('Từ khóa nhận diện SKU (cách nhau bởi dấu phẩy)', 'sku_keywords', c.sku_keywords || '', 'text', true, 'placeholder="Ví dụ MASK, SERUM, KEM_BODY"')}
        ${field('Phí giao hàng cố định (fixed_delivery_fee)', 'fixed_delivery_fee', c.fixed_delivery_fee ?? '25', 'number', false, 'min="0" step="0.01" required')}
        ${field('Phí hoàn hàng cố định (fixed_return_fee)', 'fixed_return_fee', c.fixed_return_fee ?? '15', 'number', false, 'min="0" step="0.01" required')}
        ${field('% Phí thu hộ COD (cod_fee_percent)', 'cod_fee_percent', c.cod_fee_percent ?? '0.01', 'number', false, 'min="0" step="0.001" placeholder="0.01 cho 1%"')}
      </div>
      <div class="modalfoot">
        <button type="button" id="closeProfileModal">Hủy</button>
        <button class="primary">Lưu thông tin & Biểu phí</button>
      </div>
    </form>
  `);
  $('closeProfileModal').onclick = () => customerDetail(c.id);
  $('customerProfileForm').onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const d = formData('customerProfileForm');
      const payload = {
        name: d.name,
        primary_code: d.primary_code,
        code: d.primary_code,
        sku_keywords: d.sku_keywords,
        fixed_delivery_fee: Number(d.fixed_delivery_fee),
        fixed_return_fee: Number(d.fixed_return_fee),
        cod_fee_percent: Number(d.cod_fee_percent)
      };
      if (d.aliases_str) {
        payload.aliases = d.aliases_str.split(',').map(x => x.trim()).filter(Boolean);
      }
      await api('/api/wf/customers/' + c.id, {method: 'PATCH', body: JSON.stringify(payload)});
      await refresh();
      await render();
      customerDetail(c.id);
      notice('Đã cập nhật thông tin & biểu phí khách hàng.');
    }, e.submitter);
  };
}

function openUploadCodModal() {
  openModal('Tải file trả COD & Hàng hoàn từ Kho', `
    <form id="uploadCodForm">
      <p class="muted">Quét mã vận đơn trả COD từ kho vận hoặc kiểm nhận hoàn. Hỗ trợ file Excel (.xlsx) hoặc CSV.</p>
      <div class="field">
        <label for="f_cod_file">Chọn file bảng tính (.xlsx, .csv)</label>
        <input type="file" id="f_cod_file" name="file" accept=".xlsx,.csv" required>
      </div>
      <p class="muted" style="font-size:11px">Các cột hỗ trợ: <code>tracking</code> (mã vận đơn), <code>cod_received</code> (số tiền COD kho thu được), <code>returned</code> (1 nếu đã nhận hoàn kho, 0 nếu không).</p>
      <div class="actions" style="margin-bottom:12px">
        <button type="button" class="small" id="btnDownloadSampleCod">📥 Tải file mẫu trả COD (.csv)</button>
      </div>
      <div class="modalfoot">
        <button type="button" id="closeUploadCod">Hủy</button>
        <button class="primary">Xác nhận tải lên & Xử lý</button>
      </div>
    </form>
  `);
  $('btnDownloadSampleCod').onclick = () => download('foxrun_cod_sample.csv', 'tracking,cod_received,returned\nJT123456789,150.00,0\nSPX987654321,0.00,1');
  $('closeUploadCod').onclick = () => $('modal').close();
  $('uploadCodForm').onsubmit = e => {
    e.preventDefault();
    const file = $('f_cod_file').files[0];
    if (!file) return notice('Vui lòng chọn file.', true);
    run(async () => {
      const fd = new FormData();
      fd.append('file', file);
      const res = await api('/api/wf/reconcile/upload-cod', {method: 'POST', body: fd});
      $('modal').close();
      notice(res.message || `Đã cập nhật đối soát kho thành công: ${res.updated || res.total || 0} đơn.`);
      await refresh();
      await render();
    }, e.submitter);
  };
}

function openBulkCreatePeriodsModal() {
  const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Ho_Chi_Minh', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  openModal('Tạo nhanh đợt đối soát cho tất cả khách hàng', `
    <form id="bulkPeriodForm">
      <p class="muted">Hệ thống sẽ quét tất cả khách hàng có đơn hoàn thành trong khoảng ngày và tạo đợt đối soát riêng cho từng khách.</p>
      <div class="fieldgrid">
        ${field('Từ ngày hoàn thành', 'date_from', today.slice(0, 8) + '01', 'date', false, 'required')}
        ${field('Đến ngày hoàn thành', 'date_to', today, 'date', false, 'required')}
      </div>
      <div class="modalfoot">
        <button type="button" id="closeBulkPeriod">Hủy</button>
        <button class="primary">Xác nhận tạo đợt tự động</button>
      </div>
    </form>
  `);
  $('closeBulkPeriod').onclick = () => $('modal').close();
  $('bulkPeriodForm').onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const data = formData('bulkPeriodForm');
      const res = await post('/api/wf/periods/bulk-create', data);
      $('modal').close();
      notice(res.message || `Đã tạo thành công ${res.created || res.total || 0} đợt đối soát cho các khách hàng.`);
      await refresh();
      await renderPeriods();
    }, e.submitter);
  };
}
