# -*- coding: utf-8 -*-

# 1. Update address_ai.py
with open('address_ai.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace("model:str=Field(default='gemini-1.5-flash',max_length=150)", "model:str=Field(default='gemini-3.5-flash-lite',max_length=150)")
code = code.replace("os.environ.get('GEMINI_MODEL','gemini-1.5-flash').strip()", "os.environ.get('GEMINI_MODEL','gemini-3.5-flash-lite').strip()")

with open('address_ai.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Updated address_ai.py default model to gemini-3.5-flash-lite')

# 2. Update workflow.html
with open('workflow.html', 'r', encoding='utf-8') as f:
    html = f.read()

old_select = """<div class="field"><label>Mô hình AI</label><select id="cfgAiModel"><option value="gemini-1.5-flash">gemini-1.5-flash (Nhanh, tối ưu)</option><option value="gemini-2.0-flash">gemini-2.0-flash (Siêu tốc mới)</option><option value="gemini-1.5-pro">gemini-1.5-pro (Độ chính xác cao)</option></select></div>"""

new_select = """<div class="field"><label>Mô hình AI</label><select id="cfgAiModel"><option value="gemini-3.5-flash-lite" selected>⚡ Gemini 3.5 Flash Lite (Siêu nhanh, tối ưu chi phí)</option><option value="gemini-3.1-flash-lite">⚡ Gemini 3.1 Flash Lite (Nhẹ, tốc độ cao)</option><option value="gemini-2.0-flash">Gemini 2.0 Flash (Thế hệ mới)</option><option value="gemini-1.5-flash">Gemini 1.5 Flash (Bản ổn định)</option><option value="gemini-1.5-pro">Gemini 1.5 Pro (Độ chính xác cao)</option></select></div><div class="field" style="margin-top:-6px"><input id="cfgCustomModel" placeholder="Hoặc nhập model tùy chỉnh khác (nếu có)..." style="font-size:12px"></div>"""

assert old_select in html, 'old_select not found in workflow.html'
html = html.replace(old_select, new_select, 1)

old_save = """if($('btnSaveAiConfig'))$('btnSaveAiConfig').onclick=()=>run(async()=>{let k=$('cfgApiKey').value.trim();let m=$('cfgAiModel').value;await post('/api/wf/ai/config',{api_key:k,model:m});if($('aiConfigMsg'))$('aiConfigMsg').textContent='✅ Đã lưu cấu hình API thành công!';notice('Đã lưu cấu hình API AI.')});"""

new_save = """if($('btnSaveAiConfig'))$('btnSaveAiConfig').onclick=()=>run(async()=>{let k=$('cfgApiKey').value.trim();let custom=$('cfgCustomModel')?.value.trim();let m=custom||$('cfgAiModel').value;await post('/api/wf/ai/config',{api_key:k,model:m});if($('aiConfigMsg'))$('aiConfigMsg').textContent='✅ Đã lưu cấu hình mô hình '+m+' thành công!';notice('Đã lưu cấu hình AI: '+m)});"""

assert old_save in html, 'old_save not found in workflow.html'
html = html.replace(old_save, new_save, 1)

old_load = """if($('cfgApiKey')){api('/api/wf/ai/config').then(cfg=>{if($('cfgApiKey'))$('cfgApiKey').value=cfg.api_key||'';if($('cfgAiModel')&&cfg.model)$('cfgAiModel').value=cfg.model}).catch(()=>{})}"""

new_load = """if($('cfgApiKey')){api('/api/wf/ai/config').then(cfg=>{if($('cfgApiKey'))$('cfgApiKey').value=cfg.api_key||'';if($('cfgAiModel')&&cfg.model){let opts=Array.from($('cfgAiModel').options).map(o=>o.value);if(opts.includes(cfg.model)){$('cfgAiModel').value=cfg.model}else{if($('cfgCustomModel'))$('cfgCustomModel').value=cfg.model}}}).catch(()=>{})}"""

assert old_load in html, 'old_load not found in workflow.html'
html = html.replace(old_load, new_load, 1)

with open('workflow.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Updated workflow.html with Gemini 3.5 & 3.1 Flash Lite options!')
