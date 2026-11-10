# FreeCAD 移动建模工作台 0.2.0

独立于 FreeCAD 执行服务的移动网页：用户提交图文需求，NAS 调用兼容 Chat Completions 的云端模型生成脚本，通过 FreeCAD MCP 排队建模，最后将模型独立归档。

## 使用现成镜像

镜像：`ghcr.io/gatanozoa/freecad-mobile:0.2.0`，Linux amd64。app 和 orchestrator 两个容器共用同一个镜像，不需要 NAS 安装依赖或本地构建。镜像本身不包含 FreeCAD 或大模型权重。

使用仓库中的 [Compose](../deploy/mobile/compose.yml) 和 [环境变量示例](../deploy/mobile/.env.example)。首次部署复制示例为 `.env`，填写 NAS 地址、独立移动端 Agent 口令 `CAD_TOKEN`、加密密钥 `MASTER_KEY`。

使用镜像内的 Python 生成 Fernet 密钥：

```sh
docker run --rm --entrypoint python ghcr.io/gatanozoa/freecad-mobile:0.2.0 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

保存生成的值到 `.env` 中的 `MASTER_KEY` 并备份。`PUBLIC_URL` 必须与浏览器访问地址一致。默认端口8089，CAD默认地址 `http://192.168.1.100:8088/mcp`，按实际网络修改。首次登录 admin/admin，在设置中修改密码。

**从原本地构建部署升级：保留原 `.env`、Compose 项目名和 `mobile-data` 数据卷，仅替换 Compose。不要创建新的项目或重新生成 `MASTER_KEY`，否则原有模型配置无法解密。原 `PYTHON_BASE` 变量可以保留，拉取模式不再使用它。**

## 交互

- 创建页作为首页，通过对话输入尺寸、结构和修改要求。
- “＋”添加JPEG、PNG、WebP参考图，最多4张、每张10MB，不支持直接导入PDF/CAD文件。
- 下拉选择模型；API地址、模型名、Key统一在设置页配置。Key在NAS加密保存，不回显到页面。
- 固定零件快捷测试不调用收费模型。
- 展示实际处理阶段、排队、失败及取消状态；成功后显示实际STL缩略图，点击进入三维/三视图弹窗。
- 确认后下载STL、STEP、FCStd，连续修改生成独立版本，刷新后可恢复对话。
- 任务详情保留生成脚本、错误原因、用量和项目删除入口。

三视图是网格正交投影，用于结构核对，不是加工图。尺寸与可打印性需人工复核。模型修改基于上一版本脚本重新执行；已归档模型不受CAD执行服务最近十个产物保留策略影响。

## 数据与运行

两个容器共享 `mobile-data` 持久卷。app 提供网页/API；orchestrator调用模型、提交MCP、独立归档模型。仅单个orchestrator处理任务；与其他Agent共用CAD服务的全局队列。

只公开程序与依赖，不公开任何真实 `.env`、Agent口令、API Key、数据库、图片或模型文件。修改源码会通过GitHub Actions测试、构建并发布镜像。实际发布版本以Actions成功结果为准。首次发布需要包可见性为Public才能匿名拉取。

HTTPS用于保护登录、Key提交、图片和模型传输；Fernet磁盘加密不能代替传输加密。备份必须同时保留数据卷和原 `MASTER_KEY`。

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
