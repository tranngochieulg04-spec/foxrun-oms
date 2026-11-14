# -*- coding: utf-8 -*-
with open('workflow.html', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update settings render logic
target_settings = """if($('aiUsage'))$('aiUsage').onclick=()=>run(async()=>{let d=await api('/api/wf/ai/status');if($('aiUsageArea'))$('aiUsageArea').innerHTML='<p>Mô hình: '+esc(d.model||'Chưa cấu hình')+'</p>'+`<table><thead><tr><th>Thời gian</th><th>Địa chỉ yêu cầu</th><th>Bộ nhớ đệm (Cache)</th><th>Đầu vào (Input)</th><th>Đầu ra (Output)</th><th>Tổng token</th></tr></thead><tbody>${d.usage.map(u=>`<tr><td>${date(u.at)}</td><td>${u.requested}</td><td>${u.cached}</td><td>${u.prompt_tokens}</td><td>${u.output_tokens}</td><td>${u.total_tokens}</td></tr>`).join('')}</tbody></table>`});if($('changePassword'))$('changePassword').onclick=passwordForm;"""

replace_settings = """if($('aiUsage'))$('aiUsage').onclick=()=>run(async()=>{let d=await api('/api/wf/ai/status');if($('aiUsageArea'))$('aiUsageArea').innerHTML='<p>Mô hình: '+esc(d.model||'Chưa cấu hình')+'</p>'+`<table><thead><tr><th>Thời gian</th><th>Địa chỉ yêu cầu</th><th>Bộ nhớ đệm (Cache)</th><th>Đầu vào (Input)</th><th>Đầu ra (Output)</th><th>Tổng token</th></tr></thead><tbody>${d.usage.map(u=>`<tr><td>${date(u.at)}</td><td>${u.requested}</td><td>${u.cached}</td><td>${u.prompt_tokens}</td><td>${u.output_tokens}</td><td>${u.total_tokens}</td></tr>`).join('')}</tbody></table>`});
  if($('btnSaveAiConfig'))$('btnSaveAiConfig').onclick=()=>run(async()=>{let k=$('cfgApiKey').value.trim();let m=$('cfgAiModel').value;await post('/api/wf/ai/config',{api_key:k,model:m});if($('aiConfigMsg'))$('aiConfigMsg').textContent='✅ Đã lưu cấu hình API thành công!';notice('Đã lưu cấu hình API AI.')});
  if($('btnTestAiConfig'))$('btnTestAiConfig').onclick=()=>run(async()=>{if($('aiConfigMsg'))$('aiConfigMsg').textContent='⏳ Đang kết nối thử với Gemini API...';try{let r=await post('/api/wf/ai/test',{});if($('aiConfigMsg'))$('aiConfigMsg').textContent='✅ '+r.message;notice(r.message)}catch(err){if($('aiConfigMsg'))$('aiConfigMsg').textContent='❌ '+(err.message||'Kết nối thất bại')}});
  if($('btnTestPostcode'))$('btnTestPostcode').onclick=()=>run(async()=>{let q=$('testPostcodeQuery')?.value||'';if(!q.trim()){notice('Vui lòng nhập chuỗi địa chỉ để tra cứu thử.',true);return}if($('postcodeTestMsg'))$('postcodeTestMsg').textContent='⏳ Đang tra cứu...';try{let r=await post('/api/wf/postcode/lookup',{address:q});if($('postcodeTestMsg'))$('postcodeTestMsg').textContent=r.postcode?('✅ Tìm thấy Postcode: '+r.postcode+(r.province?' ('+r.province+')':'')+' · Nguồn: '+r.method):'⚠️ Không tìm thấy postcode trong chuỗi.'}catch(err){if($('postcodeTestMsg'))$('postcodeTestMsg').textContent='❌ '+(err.message||'Lỗi')}});
  if($('cfgApiKey')){api('/api/wf/ai/config').then(cfg=>{if($('cfgApiKey'))$('cfgApiKey').value=cfg.api_key||'';if($('cfgAiModel')&&cfg.model)$('cfgAiModel').value=cfg.model}).catch(()=>{})}
  if($('changePassword'))$('changePassword').onclick=passwordForm;"""

assert target_settings in text, 'target_settings not found'
text = text.replace(target_settings, replace_settings)

# 2. Update orderForm with AI and Postcode buttons
target_orderform_html = """${area('Địa chỉ gốc','original_address',o?.original_address||'',isEdit?'readonly':'')}"""
replace_orderform_html = """${area('Địa chỉ gốc','original_address',o?.original_address||'',isEdit?'readonly':'')}<div class="full" style="display:flex;gap:8px;align-items:center;margin:-6px 0 10px 0;flex-wrap:wrap"><button type="button" class="small" id="btnAiParse" style="background:#e6f7ff;color:#096dd9;border:1px solid #91d5ff;font-weight:600">🤖 AI Tự động điền địa chỉ</button><button type="button" class="small" id="btnPostcodeLookup" style="background:#f6ffed;color:#389e0d;border:1px solid #b7eb8f;font-weight:600">📮 Tra cứu Postcode</button><span id="aiParseSpinner" style="font-size:12px;color:#666"></span></div>"""

assert target_orderform_html in text, 'target_orderform_html not found'
text = text.replace(target_orderform_html, replace_orderform_html, 1)

target_orderform_bind = """$('cancelForm').onclick=()=>isEdit?openOrder(o.id):$('modal').close();"""
replace_orderform_bind = """if($('btnAiParse'))$('btnAiParse').onclick=async()=>{let raw=$('f_original_address')?.value||$('f_full_address')?.value||'';let country=document.querySelector('[name="country"]')?.value||'MY';if(!raw.trim()){notice('Vui lòng nhập địa chỉ gốc trước khi dùng AI.',true);return}if($('aiParseSpinner'))$('aiParseSpinner').textContent='⏳ Đang phân tích với AI...';try{let res=await post('/api/wf/ai/parse_address',{text:raw,country});if(res.full_address&&$('f_full_address'))$('f_full_address').value=res.full_address;if(res.province&&$('f_province'))$('f_province').value=res.province;if(res.district&&$('f_district'))$('f_district').value=res.district;if(res.postcode&&$('f_postcode'))$('f_postcode').value=res.postcode;notice('Đã tự động điền địa chỉ bằng AI!');if($('aiParseSpinner'))$('aiParseSpinner').textContent='✅ AI đã điền ('+res.source+')'}catch(err){if($('aiParseSpinner'))$('aiParseSpinner').textContent='❌ '+(err.message||'Lỗi')}};
 if($('btnPostcodeLookup'))$('btnPostcodeLookup').onclick=async()=>{let raw=$('f_full_address')?.value||$('f_original_address')?.value||'';let prov=$('f_province')?.value||'';let dist=$('f_district')?.value||'';let country=document.querySelector('[name="country"]')?.value||'MY';if($('aiParseSpinner'))$('aiParseSpinner').textContent='⏳ Đang tra mã bưu chính...';try{let res=await post('/api/wf/postcode/lookup',{address:raw,province:prov,district:dist,country});if(res.postcode){if($('f_postcode'))$('f_postcode').value=res.postcode;if(res.province&&$('f_province')&&!$('f_province').value)$('f_province').value=res.province;notice('Đã tìm thấy mã bưu chính: '+res.postcode);if($('aiParseSpinner'))$('aiParseSpinner').textContent='✅ Postcode: '+res.postcode}else{notice('Chưa nhận diện được Postcode tự động, hãy nhập thủ công.',true);if($('aiParseSpinner'))$('aiParseSpinner').textContent='⚠️ Không tìm thấy'}}catch(err){if($('aiParseSpinner'))$('aiParseSpinner').textContent='❌ '+(err.message||'Lỗi')}};
 $('cancelForm').onclick=()=>isEdit?openOrder(o.id):$('modal').close();"""

assert target_orderform_bind in text, 'target_orderform_bind not found'
text = text.replace(target_orderform_bind, replace_orderform_bind, 1)

# 3. Update openOrder buttons & handlers
target_openorder_btn = """buttons.push('<button id="editOrder">Sửa thông tin</button>', '<button id="aiAddress">Chuẩn hóa địa chỉ AI</button>',`<button class="primary" data-action="approve">Duyệt & giữ tồn</button>`);"""
replace_openorder_btn = """buttons.push('<button id="editOrder">Sửa thông tin</button>', '<button id="aiAddress">Chuẩn hóa địa chỉ AI</button>', '<button id="postcodeOrder">📮 Tra cứu Postcode</button>', `<button class="primary" data-action="approve">Duyệt & giữ tồn</button>`);"""

assert target_openorder_btn in text, 'target_openorder_btn not found'
text = text.replace(target_openorder_btn, replace_openorder_btn, 1)

target_openorder_bind = """if($('editOrder'))$('editOrder').onclick=()=>orderForm(o);if($('aiAddress'))$('aiAddress').onclick=()=>aiForm(o);"""
replace_openorder_bind = """if($('editOrder'))$('editOrder').onclick=()=>orderForm(o);if($('aiAddress'))$('aiAddress').onclick=()=>aiForm(o);if($('postcodeOrder'))$('postcodeOrder').onclick=()=>run(async()=>{let raw=o.full_address||o.original_address||'';let res=await post('/api/wf/postcode/lookup',{address:raw,province:o.province||'',district:o.district||'',country:o.country||'MY'});if(res.postcode){await api('/api/wf/orders/'+o.id,{method:'PATCH',body:JSON.stringify({version:o.version,postcode:res.postcode,province:o.province||res.province||''})});await afterMutation(o.id,'Đã tìm và cập nhật Postcode: '+res.postcode)}else{notice('Không tìm thấy mã bưu chính trong địa chỉ hiện tại.',true)}});"""

assert target_openorder_bind in text, 'target_openorder_bind not found'
text = text.replace(target_openorder_bind, replace_openorder_bind, 1)

with open('workflow.html', 'w', encoding='utf-8') as f:
    f.write(text)

print('SUCCESSFULLY_UPDATED_WORKFLOW_HTML')
