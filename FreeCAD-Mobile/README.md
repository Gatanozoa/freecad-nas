# FreeCAD 移动建模工作台

独立于 FreeCAD 执行服务的移动网页：用户提交图文需求，NAS 调用兼容 Chat Completions 的云端模型生成脚本，通过 FreeCAD MCP 排队建模，最后将模型独立归档。

## 部署

app 和 orchestrator 共用 `ghcr.io/gatanozoa/freecad-mobile:0.6.0` 镜像，支持 Linux amd64。按[部署指南](../deploy/mobile/README.md)配置 FreeCAD MCP 地址、Agent 口令及用于加密模型配置的 Fernet 密钥，再启动工作台。

## 交互

- 创建页作为首页，通过对话输入尺寸、结构和修改要求。
- “＋”添加JPEG、PNG、WebP参考图，最多4张、每张10MB，不支持直接导入PDF/CAD文件。
- 下拉选择模型；API地址、模型名、Key统一在设置页配置。Key在NAS加密保存，不回显到页面。
- 固定零件快捷测试不调用收费模型。
- 展示实际处理阶段、排队、失败及取消状态；成功后显示实际STL缩略图，点击进入三维/三视图弹窗。
- 确认后下载STL、STEP、FCStd，连续修改生成独立版本，刷新后可恢复对话。
- 任务详情保留生成脚本、错误原因、用量和项目删除入口。

三视图是网格正交投影，用于结构核对，不是加工图。尺寸与可打印性需人工复核。模型修改基于上一版本脚本重新执行；已归档模型不受CAD执行服务最近十个产物保留策略影响。

## 尺寸确认与界面诊断

模型根据照片和文字整理尺寸、单位及数据来源。缺少必要参数时，工作台会提示补充测量值。参数齐全后，检查尺寸确认卡并确认，服务随后执行建模脚本。刷新页面后可继续处理待确认清单。

在设置的「连接与存储」中可查看客户端和服务端版本，或检查界面更新。移动键盘遮挡输入栏时，可在会话页复现后打开「键盘与窗口诊断」查看窗口变化记录。

## 数据与运行

两个容器共享 `mobile-data` 持久卷。app 提供网页/API；orchestrator调用模型、提交MCP、独立归档模型。仅单个orchestrator处理任务；与其他Agent共用CAD服务的全局队列。

HTTPS用于保护登录、Key提交、图片和模型传输；Fernet磁盘加密不能代替传输加密。备份时应同时保存 `mobile-data` 卷及其对应的 `MASTER_KEY`。

## 开发测试

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
python -m pip install playwright==1.64.1
python -m playwright install chromium
python tests/browser_chat.py
python tests/browser_provider_settings.py
python tests/browser_failed_details.py
```

浏览器测试使用隔离临时数据和本地网格，不调用NAS或收费模型。设置 `BROWSER_CHANNEL=msedge` 可使用已安装的Edge。

前端离线随镜像提供Three.js 0.180.0，许可证在 `mobile/web/vendor/LICENSE`；`tests/assets.py` 从官方npm发行包校验SHA-512后更新资源。
