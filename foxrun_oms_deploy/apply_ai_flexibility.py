# -*- coding: utf-8 -*-
import json
import re

# Update address_ai.py
with open('address_ai.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Helper functions for saved models
models_helper = '''
    DEFAULT_MODELS = [
        'gemini-3.5-flash-lite',
        'gemini-3.1-flash-lite',
        'gemini-2.0-flash',
        'gemini-1.5-flash',
        'gemini-1.5-pro'
    ]

    def get_saved_models(db):
        if not db: return list(DEFAULT_MODELS)
        row = db.get(AIConfig, 'SAVED_MODELS')
        if row and row.value:
            try:
                stored = json.loads(row.value)
                if isinstance(stored, list):
                    res = []
                    for m in stored + DEFAULT_MODELS:
                        m = str(m).strip()
                        if m and m not in res:
                            res.append(m)
                    return res
            except Exception:
                pass
        return list(DEFAULT_MODELS)

    def save_model_to_list(db, model_name):
        model_name = model_name.strip()
        if not model_name or not db: return
        models = get_saved_models(db)
        if model_name not in models:
            models.insert(0, model_name)
            db.merge(AIConfig(key='SAVED_MODELS', value=json.dumps(models, ensure_ascii=False)))
'''

# Check if DEFAULT_MODELS already in code
if 'DEFAULT_MODELS = [' not in code:
    code = code.replace("    def config(db=None):", models_helper + "\n    def config(db=None):")

# Update get_ai_config, save_ai_config, switch_model
old_config_endpoints = '''    @app.get('/api/wf/ai/config')
    def get_ai_config(db=Depends(w.get_db),user=Depends(w.current_user)):
        w.require(user,'admin','operator')
        key,model=config(db)
        masked=(key[:4]+'...'+key[-4:]) if len(key)>=8 else ('***' if key else '')
        return {'configured':bool(key),'masked_key':masked,'model':model}

    @app.post('/api/wf/ai/config')
    def save_ai_config(data:AIConfigIn,db=Depends(w.get_db),user=Depends(w.current_user)):
        w.require(user,'admin')
        if data.api_key.strip():
            db.merge(AIConfig(key='GEMINI_API_KEY',value=data.api_key.strip()))
            os.environ['GEMINI_API_KEY']=data.api_key.strip()
        if data.model.strip():
            db.merge(AIConfig(key='GEMINI_MODEL',value=data.model.strip()))
            os.environ['GEMINI_MODEL']=data.model.strip()
        db.commit()
        return {'ok':True,'model':data.model.strip()}'''

new_config_endpoints = '''    @app.get('/api/wf/ai/config')
    def get_ai_config(db=Depends(w.get_db),user=Depends(w.current_user)):
        w.require(user,'admin','operator')
        key,model=config(db)
        masked=(key[:4]+'...'+key[-4:]) if len(key)>=8 else ('***' if key else '')
        models = get_saved_models(db)
        if model not in models:
            models.insert(0, model)
        return {
            'configured': bool(key),
            'has_key': bool(key),
            'masked_key': masked,
            'model': model,
            'models': models
        }

    @app.post('/api/wf/ai/config')
    def save_ai_config(data:AIConfigIn,db=Depends(w.get_db),user=Depends(w.current_user)):
        w.require(user,'admin')
        if data.api_key and data.api_key.strip():
            db.merge(AIConfig(key='GEMINI_API_KEY',value=data.api_key.strip()))
            os.environ['GEMINI_API_KEY']=data.api_key.strip()
        new_model = data.model.strip() if data.model else ''
        if new_model:
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+', new_model):
                raise HTTPException(422, 'Tên mô hình không hợp lệ.')
            db.merge(AIConfig(key='GEMINI_MODEL',value=new_model))
            os.environ['GEMINI_MODEL']=new_model
            save_model_to_list(db, new_model)
        db.commit()
        key, current_model = config(db)
        return {
            'ok': True,
            'model': current_model,
            'models': get_saved_models(db),
            'configured': bool(key)
        }

    class SwitchModelIn(BaseModel):
        model: str = Field(min_length=1, max_length=150)

    @app.post('/api/wf/ai/switch-model')
    def switch_ai_model(data: SwitchModelIn, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin', 'operator')
        new_model = data.model.strip()
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', new_model):
            raise HTTPException(422, 'Tên mô hình không hợp lệ.')
        db.merge(AIConfig(key='GEMINI_MODEL', value=new_model))
        os.environ['GEMINI_MODEL'] = new_model
        save_model_to_list(db, new_model)
        db.commit()
        return {'ok': True, 'model': new_model, 'models': get_saved_models(db)}

    @app.delete('/api/wf/ai/models/{model_name}')
    def delete_saved_model(model_name: str, db=Depends(w.get_db), user=Depends(w.current_user)):
        w.require(user, 'admin')
        model_name = model_name.strip()
        key, current_model = config(db)
        if model_name == current_model:
            raise HTTPException(400, 'Không thể xóa mô hình đang được kích hoạt.')
        models = [m for m in get_saved_models(db) if m != model_name]
        db.merge(AIConfig(key='SAVED_MODELS', value=json.dumps(models, ensure_ascii=False)))
        db.commit()
        return {'ok': True, 'models': models}'''

assert old_config_endpoints in code, "old_config_endpoints not found in address_ai.py"
code = code.replace(old_config_endpoints, new_config_endpoints, 1)

with open('address_ai.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Updated address_ai.py successfully!')
