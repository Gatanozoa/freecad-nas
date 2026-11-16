"""Fail-closed measurement gate; model suggestions never count as user approval."""
import hashlib
import json

SOURCES = {'user', 'drawing', 'estimate', 'unknown'}

def review(plan):
    raw = plan.get('measurement')
    questions = list(plan.get('questions') or [])
    if not isinstance(raw, dict) or not isinstance(raw.get('parameters'), list) or not raw['parameters']:
        return {'parameters': [], 'issues': [], 'missing': [], 'questions': questions or
                ['请补充零件的用途、外形尺寸和厚度，并说明孔径、孔位或配合尺寸；图片没有标注时请实测后提供。'], 'ready': False}
    if len(raw['parameters']) > 80:
        raise ValueError('尺寸参数过多，请分解零件后重试')
    parameters=[]; missing=[]
    issues=raw.get('issues', [])
    if not isinstance(issues,list) or any(not isinstance(x,str) for x in issues):
        raise ValueError('尺寸冲突字段格式错误')
    names=set()
    for item in raw['parameters']:
        if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'].strip():
            raise ValueError('尺寸参数名称不完整')
        name=item['name'].strip()[:120]
        if name in names: raise ValueError('尺寸参数名称重复')
        names.add(name)
        value=item.get('value','')
        if not isinstance(value,str) or len(value)>300: raise ValueError('尺寸值需为带单位的简短文本')
        source=item.get('source','unknown')
        if source not in SOURCES: raise ValueError('尺寸来源无效')
        required=item.get('required',True)
        if not isinstance(required,bool): raise ValueError('尺寸必要性字段无效')
        hint=item.get('measurement_hint','');question=item.get('question','')
        if not isinstance(hint,str) or not isinstance(question,str): raise ValueError('测量提示格式错误')
        parameter={'name':name,'value':value.strip(),'source':source,'required':required,
                   'measurement_hint':hint[:1000], 'question':question[:500]}
        parameters.append(parameter)
        if required and (not value.strip() or source=='unknown'):
            missing.append(parameter)
    if not any(p['required'] for p in parameters):
        issues=[*issues,'请明确本零件建模必需的外形与结构参数。']
    if missing or issues:
        questions=questions or [p['question'] or '请提供'+p['name'] for p in missing] + issues
    return {'parameters':parameters,'issues':issues,'missing':missing,
            'questions':list(dict.fromkeys(questions))[:3],
            'ready':not (missing or issues or questions)}

def approval_digest(plan, script):
    payload=[script, plan.get('measurement'),plan.get('assumptions',[]),plan.get('dimensions',[])]
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def approved(plan, script):
    return bool(plan and plan.get('measurement_approval')==approval_digest(plan,script))

def history_context(store, row):
    history=[];parent=row.get('parent')
    while parent and len(history)<8:
        previous=store.job(parent);plan=previous.get('plan') or {}
        history.append({'version':previous['version'],
                        'reply':previous['prompt'].split('\n本次补充或修改：\n')[-1][-4000:],
                        'measurement':plan.get('measurement'),'questions':plan.get('questions',[])})
        parent=previous.get('parent')
    return ('\n此前的尺寸记录与追问（当前用户回复优先，不要把估算当成实测）：\n'+
            json.dumps(history[::-1],ensure_ascii=False)) if history else ''
