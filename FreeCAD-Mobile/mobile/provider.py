"""Explicit Chat Completions adapter; never guesses vision support from model name."""
import json
import httpx
from urllib.parse import urlsplit

def normalize_base_url(value):
    if not isinstance(value,str): raise ValueError('请填写 API 地址')
    value=value.strip().rstrip('/')
    parsed=urlsplit(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('API 地址需以 http:// 或 https:// 开头，且不能包含口令或查询参数')
    if parsed.path.endswith('/chat/completions'):
        value=value[:-len('/chat/completions')]
    elif parsed.path.endswith(('/responses','/messages')):
        raise ValueError('当前支持 Chat Completions 协议，请填写其基础地址或完整 /chat/completions 地址')
    return value

def endpoint_url(config):
    return normalize_base_url(config['url'])+'/chat/completions'

def provider_error(status):
    return {401:'Key 无效或已过期',403:'服务商拒绝访问，请检查 Key 权限',404:'接口路径或模型名不存在，请检查 API 地址和模型名',429:'触发额度或速率限制，请查看服务商账户'}.get(status,'请检查服务商状态及模型输入能力')

SYSTEM = '''你是机械零件建模助手。长度单位毫米。优先保持原脚本，只修改用户要求的部分。
只返回一个 JSON 对象，字段：summary(中文概述), assumptions(假设字符串数组),
dimensions(关键尺寸说明字符串数组), questions(必须补充的问题字符串数组), script(完整FreeCAD Python代码)。
图片标注看不清时不要擅自确定关键尺寸，questions写出问题，此时script为空。
不输出思维链。手办、有机角色精雕不属于本服务能力，应通过questions说明需要其他建模工具。
有足够信息时questions为空。允许import FreeCAD as App, Part, Mesh, MeshPart, Draft, math。
禁止GUI、文件IO、网络、子进程、导入其他模块、自行导出、openDocument。
必须创建文档，result为最终含有效正体积实体Shape的文档对象，doc.recompute()。
采用明确的参数变量，注释关键结构和假设。修改后仍输出可以从头执行的完整脚本。
必须遵循这个输出模板（其中shape替换为实际完成所有切孔等操作的最终几何体）：
doc = App.newDocument("Model")
shape = Part.makeBox(10, 10, 5)
result = doc.addObject("Part::Feature", "Result")
result.Shape = shape
doc.recompute()
result 必须在模块顶层赋值，不是JSON字段，不是仅存在于函数内部的局部变量。
如果用函数组织建模，函数应返回最终文档对象，并在模块顶层调用 result = build_model()。
不能将 result 设为 None、字典、文件名、函数或Document本身；不要只给其他变量赋值后遗漏result。'''

async def generate(config, prompt, images=(), base=None, feedback=None):
    content = [{'type':'text','text':prompt}]
    content += [{'type':'image_url','image_url':{'url':image}} for image in images]
    messages = [{'role':'system','content':SYSTEM}]
    if base:
        messages.append({'role':'user','content':'上一版完整脚本：\n' + base})
    messages.append({'role':'user','content':content if images else prompt})
    if feedback:
        messages.append({'role':'user','content':'上次执行失败。只修复错误，保留用户要求：\n'+feedback})
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15), follow_redirects=False) as client:
        response = await client.post(endpoint_url(config),
            headers={'Authorization':'Bearer '+config['key']},
            json={'model':config['model'],'messages':messages})
        if not response.is_success:
            raise ValueError(f'模型服务返回 HTTP {response.status_code}：'+provider_error(response.status_code))
        body = response.json()
    text = body['choices'][0]['message']['content']
    if not isinstance(text, str):
        raise ValueError('模型未返回文本结果')
    start, end = text.find('{'), text.rfind('}')
    plan = json.loads(text[start:end+1])
    if not isinstance(plan, dict) or not isinstance(plan.get('script'),str):
        raise ValueError('模型返回格式不完整：缺少完整脚本')
    for key in ('questions','assumptions','dimensions'):
        if not isinstance(plan.get(key,[]),list) or any(not isinstance(x,str) for x in plan.get(key,[])):
            raise ValueError('模型返回的说明字段格式错误')
    if not plan.get('questions') and not 1 <= len(plan['script']) <= 50000:
        raise ValueError('脚本为空或超过 50000 字符')
    return plan, body.get('usage')

async def probe(config, vision=False):
    from PIL import Image
    from io import BytesIO
    import base64
    text = '只回复 OK'
    if vision:
        image = Image.new('RGB',(96,96),'white')
        from PIL import ImageDraw
        ImageDraw.Draw(image).rectangle((24,24,72,72),fill='red')
        buf = BytesIO(); image.save(buf,format='PNG')
        text = [{'type':'text','text':'请用中文描述图中的形状和颜色。'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()}}]
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        r = await client.post(endpoint_url(config),headers={'Authorization':'Bearer '+config['key']},json={'model':config['model'],'messages':[{'role':'user','content':text}]})
        if not r.is_success:
            raise ValueError(f'测试失败：HTTP {r.status_code}：'+provider_error(r.status_code))
        answer = r.json()['choices'][0]['message']['content']
    return {'answer':str(answer)[:800], 'note':'请人工核对是否识别出红色方形。接受图片不代表能准确识别图纸。' if vision else '文字请求已返回。'}
