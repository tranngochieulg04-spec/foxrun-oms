# -*- coding: utf-8 -*-

with open('workflow.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Replace the AI configuration panel HTML
old_ai_panel = """<div class="panel"><div class="panelhead"><h3>🔌 Cấu hình API (AI & Postcode)</h3></div><div class="pad"><div class="grid2"><div><p><strong>Gemini AI API</strong></p><div class="field"><label>Gemini API Key</label><input id="cfgApiKey" type="password" placeholder="Dán Gemini API Key (AIzaSy...)"></div><div class="field"><label>Mô hình AI</label><select id="cfgAiModel"><option value="gemini-3.5-flash-lite" selected>⚡ Gemini 3.5 Flash Lite (Siêu nhanh, tối ưu chi phí)</option><option value="gemini-3.1-flash-lite">⚡ Gemini 3.1 Flash Lite (Nhẹ, tốc độ cao)</option><option value="gemini-2.0-flash">Gemini 2.0 Flash (Thế hệ mới)</option><option value="gemini-1.5-flash">Gemini 1.5 Flash (Bản ổn định)</option><option value="gemini-1.5-pro">Gemini 1.5 Pro (Độ chính xác cao)</option></select></div><div class="field" style="margin-top:-6px"><input id="cfgCustomModel" placeholder="Hoặc nhập model tùy chỉnh khác (nếu có)..." style="font-size:12px"></div><div class="actions"><button class="primary small" id="btnSaveAiConfig">Lưu cấu hình AI</button><button class="small" id="btnTestAiConfig">Kiểm tra kết nối API</button></div><p id="aiConfigMsg" style="margin-top:10px;font-size:12px"></p></div><div><p><strong>Postcode API (Mã bưu chính)</strong></p><p>Trạng thái: <span class="badge green">Sẵn sàng hoạt động</span></p><p class="muted">Tự động nhận diện bưu chính theo cú pháp & địa danh cho Malaysia (5 số), Việt Nam (5 số), Thái Lan (5 số), Philippines (4 số)...</p><div class="field"><label>Thử tra cứu nhanh Postcode</label><input id="testPostcodeQuery" placeholder="Ví dụ: 106 Manir 21200 Terengganu hoặc Hà Nội..."></div><button class="small" id="btnTestPostcode">Tra cứu thử Postcode</button><p id="postcodeTestMsg" style="margin-top:10px;font-size:12px"></p></div></div></div></div>"""

new_ai_panel = """<div class="panel"><div class="panelhead"><h3>🔌 Cấu hình AI Gemini & Postcode</h3></div><div class="pad"><div class="grid2"><div><p><strong>Gemini AI API & Chuyển đổi mô hình linh hoạt</strong></p><div class="field"><label>Gemini API Key <span id="cfgKeyStatus" style="margin-left:8px"></span></label><input id="cfgApiKey" type="password" placeholder="Dán Gemini API Key mới (để trống nếu giữ nguyên key cũ)"><small class="muted" style="display:block;margin-top:3px;font-size:11px">Key lưu an toàn trong CSDL PostgreSQL, không cần nhập lại khi đổi mô hình.</small></div><div class="field" style="margin-top:10px"><label>Mô hình AI đang kích hoạt</label><div style="display:flex;gap:8px;align-items:center"><select id="cfgAiModel" style="flex:1"></select><button class="small" id="btnQuickSwitch" type="button" style="white-space:nowrap;background:#e6f7ff;color:#096dd9;border-color:#91d5ff;font-weight:600">⚡ Đổi sang model này</button></div></div><div style="margin:8px 0"><label style="font-size:12px;font-weight:600;color:#555">Chuyển nhanh mô hình (1 click):</label><div id="quickModelPills" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px"></div></div><div class="field" style="margin-top:10px"><label>Thêm mô hình tùy chỉnh mới</label><div style="display:flex;gap:8px"><input id="cfgCustomModel" placeholder="Nhập tên model mới (VD: gemini-3.5-flash-lite, gemini-3.1-flash-lite...)" style="font-size:12px;flex:1"><button class="small" id="btnAddModel" type="button" style="white-space:nowrap;background:#f6ffed;color:#389e0d;border-color:#b7eb8f;font-weight:600">+ Thêm model</button></div></div><div class="actions" style="margin-top:14px"><button class="primary small" id="btnSaveAiConfig">💾 Lưu tất cả cấu hình</button><button class="small" id="btnTestAiConfig">🧪 Kiểm tra kết nối API</button></div><p id="aiConfigMsg" style="margin-top:10px;font-size:12px"></p></div><div><p><strong>Postcode API (Mã bưu chính)</strong></p><p>Trạng thái: <span class="badge green">Sẵn sàng hoạt động</span></p><p class="muted">Tự động nhận diện bưu chính theo cú pháp & địa danh cho Malaysia (5 số), Việt Nam (5 số), Thái Lan (5 số), Philippines (4 số)...</p><div class="field"><label>Thử tra cứu nhanh Postcode</label><input id="testPostcodeQuery" placeholder="Ví dụ: 106 Manir 21200 Terengganu hoặc Hà Nội..."></div><button class="small" id="btnTestPostcode">Tra cứu thử Postcode</button><p id="postcodeTestMsg" style="margin-top:10px;font-size:12px"></p></div></div></div></div>"""

assert old_ai_panel in html, 'old_ai_panel not found'
html = html.replace(old_ai_panel, new_ai_panel, 1)

# Replace the event wiring in view === 'settings'
old_wiring = """  if($('btnSaveAiConfig'))$('btnSaveAiConfig').onclick=()=>run(async()=>{let k=$('cfgApiKey').value.trim();let custom=$('cfgCustomModel')?.value.trim();let m=custom||$('cfgAiModel').value;await post('/api/wf/ai/config',{api_key:k,model:m});if($('aiConfigMsg'))$('aiConfigMsg').textContent='✅ Đã lưu cấu hình mô hình '+m+' thành công!';notice('Đã lưu cấu hình AI: '+m)});
  if($('btnTestAiConfig'))$('btnTestAiConfig').onclick=()=>run(async()=>{if($('aiConfigMsg'))$('aiConfigMsg').textContent='⏳ Đang kết nối thử với Gemini API...';try{let r=await post('/api/wf/ai/test',{});if($('aiConfigMsg'))$('aiConfigMsg').textContent='✅ '+r.message;notice(r.message)}catch(err){if($('aiConfigMsg'))$('aiConfigMsg').textContent='❌ '+(err.message||'Kết nối thất bại')}});
  if($('btnTestPostcode'))$('btnTestPostcode').onclick=()=>run(async()=>{let q=$('testPostcodeQuery')?.value||'';if(!q.trim()){notice('Vui lòng nhập chuỗi địa chỉ để tra cứu thử.',true);return}if($('postcodeTestMsg'))$('postcodeTestMsg').textContent='⏳ Đang tra cứu...';try{let r=await post('/api/wf/postcode/lookup',{address:q});if($('postcodeTestMsg'))$('postcodeTestMsg').textContent=r.postcode?('✅ Tìm thấy Postcode: '+r.postcode+(r.province?' ('+r.province+')':'')+' · Nguồn: '+r.method):'⚠️ Không tìm thấy postcode trong chuỗi.'}catch(err){if($('postcodeTestMsg'))$('postcodeTestMsg').textContent='❌ '+(err.message||'Lỗi')}});
  if($('cfgApiKey')){api('/api/wf/ai/config').then(cfg=>{if($('cfgApiKey'))$('cfgApiKey').value=cfg.api_key||'';if($('cfgAiModel')&&cfg.model){let opts=Array.from($('cfgAiModel').options).map(o=>o.value);if(opts.includes(cfg.model)){$('cfgAiModel').value=cfg.model}else{if($('cfgCustomModel'))$('cfgCustomModel').value=cfg.model}}}).catch(()=>{})}"""

new_wiring = """  async function loadAiSettingsUI(){
    try{
      let cfg=await api('/api/wf/ai/config');
      if($('cfgKeyStatus')) $('cfgKeyStatus').innerHTML=cfg.configured?`<span class=\"badge green\" style=\"font-size:11px\">✓ Đã lưu: ${esc(cfg.masked_key)}</span>`:`<span class=\"badge orange\" style=\"font-size:11px\">⚠️ Chưa cấu hình Key</span>`;
      let sel=$('cfgAiModel');
      if(sel&&cfg.models){
        sel.innerHTML=cfg.models.map(m=>`<option value=\"${esc(m)}\" ${m===cfg.model?'selected':''}>${m.startsWith('gemini-3.')?'⚡ ':''}${esc(m)}</option>`).join('');
      }
      let pills=$('quickModelPills');
      if(pills&&cfg.models){
        pills.innerHTML=cfg.models.map(m=>{let isAct=(m===cfg.model);return `<button type=\"button\" class=\"small\" data-switch-model=\"${esc(m)}\" style=\"font-size:11px;padding:3px 9px;border-radius:12px;background:${isAct?'#1890ff':'#f0f2f5'};color:${isAct?'#fff':'#333'};border:1px solid ${isAct?'#1890ff':'#d9d9d9'};font-weight:${isAct?'bold':'normal'};cursor:pointer\">${isAct?'✓ ':''}${esc(m)}</button>`}).join('');
        pills.querySelectorAll('[data-switch-model]').forEach(b=>{
          b.onclick=()=>run(async()=>{
            let m=b.dataset.switchModel;
            await post('/api/wf/ai/switch-model',{model:m});
            notice('Đã chuyển sang mô hình: '+m);
            await loadAiSettingsUI();
          });
        });
      }
    }catch(e){}
  }
  if($('cfgApiKey')) loadAiSettingsUI();
  if($('btnQuickSwitch')) $('btnQuickSwitch').onclick=()=>run(async()=>{
    let m=$('cfgAiModel').value;
    await post('/api/wf/ai/switch-model',{model:m});
    notice('Đã kích hoạt mô hình: '+m);
    await loadAiSettingsUI();
  });
  if($('btnAddModel')) $('btnAddModel').onclick=()=>run(async()=>{
    let m=$('cfgCustomModel')?.value.trim();
    if(!m){notice('Vui lòng nhập tên mô hình.',true);return}
    await post('/api/wf/ai/switch-model',{model:m});
    $('cfgCustomModel').value='';
    notice('Đã thêm và kích hoạt mô hình: '+m);
    await loadAiSettingsUI();
  });
  if($('btnSaveAiConfig')) $('btnSaveAiConfig').onclick=()=>run(async()=>{
    let k=$('cfgApiKey')?.value.trim()||'';
    let m=$('cfgAiModel')?.value||'';
    await post('/api/wf/ai/config',{api_key:k,model:m});
    if($('cfgApiKey')) $('cfgApiKey').value='';
    if($('aiConfigMsg')) $('aiConfigMsg').textContent='✅ Đã lưu cấu hình AI thành công!';
    notice('Đã lưu cấu hình AI.');
    await loadAiSettingsUI();
  });
  if($('btnTestAiConfig')) $('btnTestAiConfig').onclick=()=>run(async()=>{
    if($('aiConfigMsg')) $('aiConfigMsg').textContent='⏳ Đang kết nối thử với Gemini API...';
    try{
      let r=await post('/api/wf/ai/test',{});
      if($('aiConfigMsg')) $('aiConfigMsg').textContent='✅ Kết nối thành công tới model '+r.model+'!';
      notice('Kết nối Gemini API thành công ('+r.model+')');
    }catch(err){
      if($('aiConfigMsg')) $('aiConfigMsg').textContent='❌ '+(err.message||'Kết nối thất bại');
    }
  });
  if($('btnTestPostcode')) $('btnTestPostcode').onclick=()=>run(async()=>{
    let q=$('testPostcodeQuery')?.value||'';
    if(!q.trim()){notice('Vui lòng nhập chuỗi địa chỉ để tra cứu thử.',true);return}
    if($('postcodeTestMsg')) $('postcodeTestMsg').textContent='⏳ Đang tra cứu...';
    try{
      let r=await post('/api/wf/postcode/lookup',{address:q});
      if($('postcodeTestMsg')) $('postcodeTestMsg').textContent=r.postcode?('✅ Tìm thấy Postcode: '+r.postcode+(r.province?' ('+r.province+')':'')+' · Nguồn: '+r.method):'⚠️ Không tìm thấy postcode trong chuỗi.';
    }catch(err){
      if($('postcodeTestMsg')) $('postcodeTestMsg').textContent='❌ '+(err.message||'Lỗi');
    }
  });"""

assert old_wiring in html, 'old_wiring not found'
html = html.replace(old_wiring, new_wiring, 1)

with open('workflow.html', 'w', encoding='utf-8') as f:
    f.write(html)

print('Updated workflow.html with dynamic AI switcher successfully!')
