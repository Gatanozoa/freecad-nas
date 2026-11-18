"""Fail-closed measurement gate; model suggestions never count as user approval."""
import hashlib
import json

SOURCES = {'user', 'drawing', 'estimate', 'unknown'}
ISSUE_KINDS = {'conflict', 'ambiguity', 'unsupported', 'advisory'}
DEFAULT_QUESTION = '请补充零件的用途、外形尺寸和厚度，并说明孔径、孔位或配合尺寸；图片没有标注时请实测后提供。'

def separate_issues(raw_issues, *, model_response):
    if not isinstance(raw_issues, list):
        raise ValueError('尺寸问题必须以数组返回')
    blocking, advisories = [], []
    for issue in raw_issues:
        if isinstance(issue, str) and not model_response:
            # Stored plans from earlier versions use plain strings for blocking issues.
            if issue.strip():
                blocking.append(issue.strip())
            continue
        if not isinstance(issue, dict) or issue.get('kind') not in ISSUE_KINDS:
            raise ValueError('请为每项 measurement.issues 指定 kind 和 message。'
                             '冲突用 conflict，结构或单位无法确定用 ambiguity，能力不支持用 unsupported。'
                             '估算依据、已选方案、草稿复核建议用 advisory 或放入 assumptions。')
        message = issue.get('message')
        if not isinstance(message, str) or not message.strip():
            raise ValueError('尺寸问题缺少简短说明')
        target = advisories if issue['kind'] == 'advisory' else blocking
        target.append(message.strip())
    return blocking, advisories

def review(plan, *, model_response=False):
    raw = plan.get('measurement')
    questions = [item.strip() for item in (plan.get('questions') or []) if item.strip()]
    if not isinstance(raw, dict):
        raw = {}
    issues, advisories = separate_issues(raw.get('issues', []), model_response=model_response)
    raw_parameters = raw.get('parameters')
    if not isinstance(raw_parameters, list) or not raw_parameters:
        return {'parameters': [], 'issues': issues, 'advisories': advisories, 'missing': [],
                'questions': list(dict.fromkeys(questions or issues or [DEFAULT_QUESTION]))[:3], 'ready': False}
    if len(raw_parameters) > 80:
        raise ValueError('尺寸参数过多，请分解零件后重试')
    parameters=[]; missing=[]
    names=set()
    for item in raw_parameters:
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
    if model_response:
        notes = set(advisories) | {note.strip() for note in plan.get('assumptions', [])}
        questions = [question for question in questions if question not in notes]
        if questions and not (missing or issues):
            raise ValueError('必要尺寸均已填写，但 questions 中仍有未归类内容：'
                             + '；'.join(questions)[:500]
                             + '。逐项判断：真正缺失的必要参数标为 unknown，未解决的冲突或结构问题填入带类型的 issues。'
                               '已给出具体值的估算、可选细节和复核建议移入 assumptions。'
                               '没有阻塞项时 questions 必须为空，并返回完整脚本供整表确认。')
    if missing or issues:
        questions = questions or [p['question'] or '请提供'+p['name'] for p in missing] + issues
    return {'parameters': parameters, 'issues': issues, 'advisories': advisories, 'missing': missing,
            'questions': list(dict.fromkeys(questions))[:3],
            'ready': not (missing or issues or questions)}

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
