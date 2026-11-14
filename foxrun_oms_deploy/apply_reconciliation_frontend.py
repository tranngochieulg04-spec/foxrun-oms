"""Upgrade Frontend management_ui.js and workflow.html with:
1. Admin Fee Configs Management Modal (Fixed & Formula)
2. Flexible Order Fee Adjustment Modal with Mandatory Note & Light Yellow (#FEF9C3) Row Highlight
3. Excel Live Formulas (.xlsx) Toolbar Buttons and Carrier Excel Reconcile Modal
"""
import re
from pathlib import Path

js_file = Path("management_ui.js")
js_code = js_file.read_text(encoding="utf-8")

# 1. Update order row rendering to include #FEF9C3 yellow highlight & adjusted fee note
old_tr_start = """    return '<tr class="click" tabindex="0" data-order="' + order.id + '">' + selection +"""
new_tr_start = """    const rowBgStyle = order.fee_adjusted ? ' style="background-color: #FEF9C3 !important;"' : '';
    let adjustedFeeBadge = '';
    if (order.fee_adjusted) {
      adjustedFeeBadge = '<div style="margin-top:3px;"><span class="badge warn" style="background:#FEF9C3;color:#854D0E;border:1px solid #FDE047;font-weight:700;padding:2px 6px;border-radius:4px;display:inline-block;white-space:normal;line-height:1.3;" title="Lý do điều chỉnh: ' + esc(order.fee_adjust_reason) + ' | Người sửa: ' + esc(order.fee_adjusted_by) + ' lúc ' + esc(order.fee_adjusted_at) + '">⚠️ Phí đã sửa: ' + esc(order.fee_adjust_reason) + '</span></div>';
    }
    const adjustFeeBtn = (can('admin', 'finance') && ['DELIVERED', 'RETURNED', 'RETURNED_COMPLETED'].includes(order.transport)) ? ' <button type="button" class="btn-adjust-fee small" data-order-id="' + order.id + '" style="background:#fffbe6;color:#d46b08;border:1px solid #ffe58f;padding:1px 5px;font-size:11px;font-weight:600;margin-top:3px;border-radius:3px;">✏️ Sửa phí</button>' : '';
    return '<tr class="click' + (order.fee_adjusted ? ' fee-adjusted-highlight' : '') + '" tabindex="0" data-order="' + order.id + '"' + rowBgStyle + '>' + selection +"""

if old_tr_start in js_code:
    js_code = js_code.replace(old_tr_start, new_tr_start, 1)

# Add adjustedFeeBadge and adjustFeeBtn to the fee / finance display column
old_fee_col = """'<td>' + feeDisplay + '</td><td>' + badge(order.finance, 'finance') + '</td>' +"""
new_fee_col = """'<td>' + feeDisplay + adjustedFeeBadge + adjustFeeBtn + '</td><td>' + badge(order.finance, 'finance') + '</td>' +"""

if old_fee_col in js_code:
    js_code = js_code.replace(old_fee_col, new_fee_col, 1)

# 2. Update extraToolbarButtons in drawList
old_tb_check = """  if (view === 'finance') {
    extraToolbarButtons += '<button class="primary small" id="btnUploadCodFile" style="background:#52c41a;border-color:#52c41a;color:#fff;font-weight:600">📤 Upload File trả COD Kho (.xlsx/.csv)</button>';
  }"""

new_tb_check = """  if (view === 'finance' || view === 'reconcile') {
    extraToolbarButtons += '<button class="small" id="btnAdminFeeConfigs" style="background:#e6f7ff;color:#096dd9;border:1px solid #91d5ff;font-weight:600">⚙️ Cấu hình chi phí (Admin)</button>';
    extraToolbarButtons += '<a href="/api/wf/reconcile/export-xlsx" download style="text-decoration:none;"><button type="button" class="small" style="background:#f6ffed;color:#107c41;border:1px solid #b7eb8f;font-weight:600">📊 Xuất Excel Live Formulas (.xlsx)</button></a>';
    extraToolbarButtons += '<button class="primary small" id="btnCarrierExcelReconcile" style="background:#107c41;border-color:#107c41;color:#fff;font-weight:600">📥 Đối soát file hãng vận chuyển (.xlsx)</button>';
    extraToolbarButtons += '<button class="small" id="btnUploadCodFile" style="background:#fff;border-color:#d9d9d9;color:#595959;font-weight:500">📤 File COD Kho</button>';
  }"""

if old_tb_check in js_code:
    js_code = js_code.replace(old_tb_check, new_tb_check, 1)

# 3. Wire button events in drawList
old_btn_wire = """  if ($('btnUploadCodFile')) {
    $('btnUploadCodFile').onclick = openUploadCodModal;
  }"""

new_btn_wire = """  if ($('btnUploadCodFile')) {
    $('btnUploadCodFile').onclick = openUploadCodModal;
  }
  if ($('btnAdminFeeConfigs')) {
    $('btnAdminFeeConfigs').onclick = openAdminFeeConfigsModal;
  }
  if ($('btnCarrierExcelReconcile')) {
    $('btnCarrierExcelReconcile').onclick = openCarrierExcelReconcileModal;
  }
  document.querySelectorAll('.btn-adjust-fee').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const oid = Number(btn.dataset.orderId);
      const targetOrd = orders.find(x => x.id === oid);
      if (targetOrd) openAdjustOrderFeesModal(targetOrd);
    };
  });"""

if old_btn_wire in js_code:
    js_code = js_code.replace(old_btn_wire, new_btn_wire, 1)

# 4. Append reconciliation modal functions at the end of management_ui.js
reconciliation_js_modal_code = '''
// =========================================================================
// RECONCILIATION UPGRADE: ADMIN FEE CONFIGS, ORDER FEE ADJUSTMENT & CARRIER EXCEL
// =========================================================================

async function openAdminFeeConfigsModal() {
  notice('Đang tải danh sách cấu hình chi phí Admin...');
  try {
    const configs = await api('/api/wf/admin/fee-configs');
    renderFeeConfigsModalContent(configs);
  } catch (err) {
    notice(err.message || 'Lỗi khi tải cấu hình chi phí.', true);
  }
}

function renderFeeConfigsModalContent(configs) {
  const tableRows = configs.map(c => `
    <tr>
      <td><strong>${esc(c.name)}</strong><br><span class="sub">${c.customer_code ? 'Khách: ' + esc(c.customer_code) : 'Toàn hệ thống'} · ${esc(c.target_type)}</span></td>
      <td><span class="badge ${c.fee_type === 'FIXED' ? 'blue' : 'orange'}">${c.fee_type === 'FIXED' ? 'Cố định' : 'Công thức'}</span></td>
      <td><strong>${c.fee_type === 'FIXED' ? money(c.amount) : `<code style="background:#f5f5f5;padding:2px 4px;border-radius:3px;color:#d4380d;">${esc(c.formula)}</code>`}</strong></td>
      <td><span class="badge ${c.fee_category === 'service_fee' ? 'green' : 'blue'}">${c.fee_category === 'service_fee' ? 'Phí dịch vụ' : 'Cước vận chuyển'}</span></td>
      <td><button type="button" class="small btn-toggle-fee" data-id="${c.id}" style="background:${c.is_active ? '#f6ffed' : '#fff1f0'};color:${c.is_active ? '#52c41a' : '#f5222d'};border:1px solid ${c.is_active ? '#b7eb8f' : '#ffa39e'};font-weight:600;">${c.is_active ? '✅ Đang bật' : '⏸️ Đã tắt'}</button></td>
      <td>
        <button type="button" class="small btn-delete-fee" data-id="${c.id}" style="color:#f5222d;background:#fff;border:1px solid #ffa39e;">Xóa</button>
      </td>
    </tr>
  `).join('');

  const modalHtml = `
    <div style="max-height:80vh;overflow-y:auto;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <p class="muted" style="margin:0;">Cấu hình các khoản chi phí cố định và biến đổi (lưu vĩnh viễn vào CSDL PostgreSQL).</p>
        <button type="button" class="primary small" id="btnAddNewFeeConfig">+ Thêm khoản chi phí</button>
      </div>
      <div id="addFeeConfigContainer" style="display:none;background:#fafafa;padding:12px;border:1px solid #d9d9d9;border-radius:6px;margin-bottom:16px;"></div>
      <div class="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Tên khoản phí / Đối tác</th>
              <th>Loại phí</th>
              <th>Định mức / Biểu thức</th>
              <th>Nhóm phí</th>
              <th>Trạng thái</th>
              <th>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            ${tableRows || '<tr><td colspan="6" class="empty">Chưa có cấu hình chi phí nào. Bấm Thêm để tạo mới.</td></tr>'}
          </tbody>
        </table>
      </div>
      <div class="modalfoot" style="margin-top:16px;">
        <button type="button" id="btnCloseFeeConfigs">Đóng</button>
      </div>
    </div>
  `;

  openModal('⚙️ Quản lý Cấu hình Chi phí Admin', modalHtml);
  $('btnCloseFeeConfigs').onclick = () => $('modal').close();

  // Wire toggles
  document.querySelectorAll('.btn-toggle-fee').forEach(btn => {
    btn.onclick = async () => {
      const id = Number(btn.dataset.id);
      try {
        await api('/api/wf/admin/fee-configs/' + id + '/toggle', {method: 'PATCH'});
        const refreshed = await api('/api/wf/admin/fee-configs');
        renderFeeConfigsModalContent(refreshed);
        notice('Đã chuyển đổi trạng thái khoản phí.');
      } catch (e) {
        notice(e.message || 'Lỗi khi cập nhật trạng thái.', true);
      }
    };
  });

  // Wire deletes
  document.querySelectorAll('.btn-delete-fee').forEach(btn => {
    btn.onclick = async () => {
      if (!confirm('Bạn có chắc muốn xóa khoản chi phí này?')) return;
      const id = Number(btn.dataset.id);
      try {
        await api('/api/wf/admin/fee-configs/' + id, {method: 'DELETE'});
        const refreshed = await api('/api/wf/admin/fee-configs');
        renderFeeConfigsModalContent(refreshed);
        notice('Đã xóa khoản phí thành công.');
      } catch (e) {
        notice(e.message || 'Lỗi khi xóa khoản phí.', true);
      }
    };
  });

  // Wire add new
  $('btnAddNewFeeConfig').onclick = () => {
    const container = $('addFeeConfigContainer');
    if (container.style.display !== 'none') {
      container.style.display = 'none';
      return;
    }
    container.style.display = 'block';
    container.innerHTML = `
      <form id="newFeeConfigForm">
        <h4 style="margin:0 0 10px 0;color:#001529;">Thêm mới Khoản chi phí</h4>
        <div class="fieldgrid">
          <div class="field">
            <label>Loại chi phí</label>
            <select id="cfgFeeType" name="fee_type">
              <option value="FIXED">Chi phí cố định (Fixed Cost)</option>
              <option value="FORMULA">Chi phí theo công thức (Formula Cost)</option>
            </select>
          </div>
          ${field('Tên khoản phí', 'name', '', 'text', false, 'required placeholder="VD: Phí bảo hiểm COD 1.5%"')}
          <div class="field">
            <label>Nhóm phí</label>
            <select name="fee_category">
              <option value="service_fee">Phí dịch vụ FOXRUN (OMS Service Fee)</option>
              <option value="carrier_fee">Cước vận chuyển hãng (Carrier Fee)</option>
            </select>
          </div>
          <div class="field">
            <label>Đối tượng áp dụng</label>
            <select name="target_type">
              <option value="PER_ORDER">Theo từng đơn hàng (PER_ORDER)</option>
              <option value="PER_CUSTOMER">Theo khách hàng (PER_CUSTOMER)</option>
              <option value="PER_PERIOD">Theo đợt đối soát (PER_PERIOD)</option>
            </select>
          </div>
          <div class="field" id="cfgAmountBox">
            <label>Số tiền cố định (THB/MYR/VND)</label>
            <input type="number" step="0.01" min="0" name="amount" value="0">
          </div>
          <div class="field" id="cfgFormulaBox" style="display:none;">
            <label>Biểu thức toán học</label>
            <input type="text" name="formula" placeholder="VD: COD * 0.015 hoặc QTY * 2 hoặc COD * 0.03 + 5">
            <span class="sub" style="font-size:11px;color:#8c8c8c;">Hỗ trợ: COD, QTY, SHIPPING_FEE, SERVICE_FEE và các phép tính +, -, *, /, %</span>
          </div>
          ${field('Mã đối tác (Bỏ trống = Toàn hệ thống)', 'customer_code', '', 'text', false, 'placeholder="VD: HC, FG, DG, AH..."')}
          ${field('Ghi chú điều khoản', 'note', '', 'text', false, 'placeholder="Mô tả chi tiết cách tính phí"')}
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
          <button type="button" id="btnCancelAddFee">Hủy</button>
          <button class="primary" type="submit">Lưu cấu hình chi phí</button>
        </div>
      </form>
    `;

    $('btnCancelAddFee').onclick = () => { container.style.display = 'none'; };
    $('cfgFeeType').onchange = () => {
      const isFormula = $('cfgFeeType').value === 'FORMULA';
      $('cfgFormulaBox').style.display = isFormula ? 'block' : 'none';
      $('cfgAmountBox').style.display = isFormula ? 'none' : 'block';
    };

    $('newFeeConfigForm').onsubmit = (e) => {
      e.preventDefault();
      run(async () => {
        const d = formData('newFeeConfigForm');
        d.amount = Number(d.amount || 0);
        await post('/api/wf/admin/fee-configs', d);
        notice('Đã lưu cấu hình chi phí thành công!');
        const refreshed = await api('/api/wf/admin/fee-configs');
        renderFeeConfigsModalContent(refreshed);
      }, e.submitter);
    };
  };
}

function openAdjustOrderFeesModal(order) {
  const currentCarrierFee = order.carrier_fee || '0';
  const currentServiceFee = order.service_fee || '0';
  const currentSpecFee = order.special_fee || '0';

  const modalHtml = `
    <form id="orderFeeAdjustForm">
      <div class="callout info" style="margin-bottom:12px;">
        Điều chỉnh chi phí cho đơn hàng <strong class="mono">${esc(order.code)}</strong> (${esc(order.customer)}).
        <br><strong>Lưu ý:</strong> Bắt buộc phải nhập Lý do điều chỉnh. Sau khi lưu, đơn hàng sẽ được <strong>tô màu nền vàng nhạt (#FEF9C3)</strong> kèm ghi chú lý do.
      </div>
      <div class="fieldgrid">
        ${field('Cước vận chuyển (Hãng)', 'carrier_fee', currentCarrierFee, 'number', false, 'step="0.01" min="0" required')}
        ${field('Phí dịch vụ FOXRUN', 'service_fee', currentServiceFee, 'number', false, 'step="0.01" min="0" required')}
        ${field('Phụ phí vùng đặc biệt', 'special_fee', currentSpecFee, 'number', false, 'step="0.01" min="0"')}
        <div class="field full">
          <label style="color:#d4380d;font-weight:700;">Lý do điều chỉnh (Bắt buộc) *</label>
          <textarea id="adjustFeeReason" name="reason" required placeholder="Nhập rõ lý do sửa chi phí (VD: Thỏa thuận riêng với seller, hàng cồng kềnh quá khổ, hãng hoàn phí cước...)" style="width:100%;min-height:70px;border:1px solid #ffa39e;border-radius:4px;padding:8px;"></textarea>
        </div>
      </div>
      <div id="feeHistoryContainer" style="margin-top:10px;font-size:12px;"></div>
      <div class="modalfoot" style="margin-top:14px;">
        <button type="button" id="btnCancelOrderFeeAdjust">Đóng</button>
        <button class="primary" style="background:#faad14;border-color:#faad14;color:#fff;font-weight:600;">Lưu điều chỉnh & Tô vàng nền</button>
      </div>
    </form>
  `;

  openModal('✏️ Sửa chi phí đơn hàng · ' + esc(order.code), modalHtml);
  $('btnCancelOrderFeeAdjust').onclick = () => $('modal').close();

  // Load adjustment history
  api('/api/wf/orders/' + order.id + '/fee-adjustments').then(history => {
    if (history && history.length && $('feeHistoryContainer')) {
      const histRows = history.map(h => `
        <div style="background:#fafafa;padding:6px 10px;border-left:3px solid #faad14;margin-bottom:4px;border-radius:2px;">
          <strong>${esc(h.actor)}</strong> lúc ${esc(h.created_at ? h.created_at.slice(0, 16).replace('T', ' ') : '')}:
          <em>${esc(h.reason)}</em>
          <span class="sub">(Cước: ${h.carrier_fee_before} &rarr; ${h.carrier_fee_after} | Phí DV: ${h.service_fee_before} &rarr; ${h.service_fee_after})</span>
        </div>
      `).join('');
      $('feeHistoryContainer').innerHTML = '<h5 style="margin:6px 0;">Lịch sử điều chỉnh trước đó:</h5>' + histRows;
    }
  }).catch(() => {});

  $('orderFeeAdjustForm').onsubmit = (e) => {
    e.preventDefault();
    const reasonVal = ($('adjustFeeReason').value || '').trim();
    if (!reasonVal) {
      notice('Bắt buộc phải nhập Lý do điều chỉnh chi phí!', true);
      $('adjustFeeReason').focus();
      return;
    }

    run(async () => {
      const d = formData('orderFeeAdjustForm');
      d.carrier_fee = Number(d.carrier_fee);
      d.service_fee = Number(d.service_fee);
      d.special_fee = Number(d.special_fee || 0);
      d.version = order.version;
      d.reason = reasonVal;

      const updated = await post('/api/wf/orders/' + order.id + '/adjust-fees', d);
      $('modal').close();
      notice('Đã điều chỉnh chi phí đơn hàng và cập nhật màu nền vàng nhạt (#FEF9C3) thành công!');
      
      // Update in local cache
      const idx = orders.findIndex(x => x.id === order.id);
      if (idx !== -1) {
        orders[idx] = updated;
      }
      drawList();
    }, e.submitter);
  };
}

function openCarrierExcelReconcileModal() {
  const modalHtml = `
    <form id="carrierReconcileForm">
      <div class="callout info" style="margin-bottom:12px;">
        Tải lên file Excel đối soát từ hãng vận chuyển (J&T Express, Flash, Kerry...) để tự động so khớp Mã vận đơn, tiền thu hộ COD và cước phí.
      </div>
      <div class="field">
        <label>File Excel Đối Soát (.xlsx / .xlsm)</label>
        <input type="file" id="carrierExcelInput" accept=".xlsx,.xlsm" required>
      </div>
      <div id="carrierMatchReport" style="display:none;margin-top:12px;"></div>
      <div class="modalfoot" style="margin-top:14px;">
        <button type="button" id="btnCloseCarrierReconcile">Đóng</button>
        <button class="primary" id="btnPreviewCarrierReconcile">Xem trước đối soát</button>
      </div>
    </form>
  `;

  openModal('📥 Đối soát file hãng vận chuyển (.xlsx)', modalHtml);
  $('btnCloseCarrierReconcile').onclick = () => $('modal').close();

  $('carrierReconcileForm').onsubmit = (e) => {
    e.preventDefault();
    const file = $('carrierExcelInput').files[0];
    if (!file) {
      notice('Vui lòng chọn file Excel!', true);
      return;
    }

    run(async () => {
      notice('Đang phân tích và so khớp dữ liệu từ hãng...');
      const fd = new FormData();
      fd.append('file', file);
      
      const rep = await api('/api/wf/reconcile/upload-carrier-excel?apply=false', {
        method: 'POST',
        body: fd
      });

      renderCarrierMatchReport(rep, file);
    }, e.submitter);
  };
}

function renderCarrierMatchReport(report, originalFile) {
  const repDiv = $('carrierMatchReport');
  if (!repDiv) return;
  repDiv.style.display = 'block';

  let mismatchHtml = '';
  if (report.mismatched_count > 0) {
    mismatchHtml = `
      <h5 style="color:#cf1322;margin:12px 0 6px 0;">⚠️ Danh sách đơn lệch số liệu (${report.mismatched_count} đơn):</h5>
      <div class="tablewrap" style="max-height:220px;overflow-y:auto;">
        <table>
          <thead>
            <tr>
              <th>Mã đơn / Vận đơn</th>
              <th>Khách</th>
              <th>COD Hệ thống</th>
              <th>COD Hãng</th>
              <th>Lệch COD</th>
              <th>Cước Hệ thống</th>
              <th>Cước Hãng</th>
              <th>Lệch Cước</th>
            </tr>
          </thead>
          <tbody>
            ${report.mismatched.map(m => `
              <tr style="background:#fff1f0;">
                <td><strong>${esc(m.code)}</strong><br><span class="mono sub">${esc(m.tracking)}</span></td>
                <td>${esc(m.customer)}</td>
                <td>${money(m.sys_cod)}</td>
                <td>${money(m.carrier_cod)}</td>
                <td style="color:${Number(m.cod_diff) !== 0 ? '#cf1322' : '#52c41a'};font-weight:700;">${money(m.cod_diff)}</td>
                <td>${money(m.sys_carrier_fee)}</td>
                <td>${money(m.carrier_fee)}</td>
                <td style="color:${Number(m.fee_diff) !== 0 ? '#cf1322' : '#52c41a'};font-weight:700;">${money(m.fee_diff)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  repDiv.innerHTML = `
    <div style="background:#f5f5f5;padding:12px;border-radius:6px;border:1px solid #d9d9d9;">
      <h4 style="margin:0 0 8px 0;">Kết quả so khớp file: ${esc(report.filename)}</h4>
      <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px;">
        <span style="color:#52c41a;font-weight:700;">✅ Khớp hoàn toàn: ${report.matched_count} đơn</span>
        <span style="color:#cf1322;font-weight:700;">⚠️ Lệch số liệu: ${report.mismatched_count} đơn</span>
        <span style="color:#fa8c16;font-weight:700;">❓ Không tìm thấy: ${report.not_found_count} đơn</span>
      </div>
      ${mismatchHtml}
      <div style="margin-top:14px;text-align:right;">
        <button type="button" class="primary" id="btnConfirmApplyCarrierExcel" style="background:#107c41;border-color:#107c41;">
          Xác nhận áp dụng & Cập nhật cước vào hệ thống
        </button>
      </div>
    </div>
  `;

  if ($('btnConfirmApplyCarrierExcel')) {
    $('btnConfirmApplyCarrierExcel').onclick = () => {
      run(async () => {
        notice('Đang cập nhật số liệu đối soát hãng vào CSDL...');
        const fd = new FormData();
        fd.append('file', originalFile);
        const applyRes = await api('/api/wf/reconcile/upload-carrier-excel?apply=true', {
          method: 'POST',
          body: fd
        });
        $('modal').close();
        notice(`Đã cập nhật đối soát từ file hãng thành công: ${applyRes.matched_count + applyRes.mismatched_count} đơn!`);
        await refresh();
        await render();
      });
    };
  }
}
'''

js_file.write_text(js_code + "\n" + reconciliation_js_modal_code, encoding="utf-8")
print("management_ui.js updated successfully!")
