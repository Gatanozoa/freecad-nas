# 部署 FreeCAD 移动建模工作台

工作台使用 `ghcr.io/gatanozoa/freecad-mobile:0.6.0`。同一镜像启动两个服务，`app` 提供网页，`orchestrator` 调用模型并将建模任务提交给 FreeCAD MCP。

1. [启动 CAD 服务](../cad/README.md)，在其管理页为工作台创建独立的 Agent 口令。
2. 将 [compose.yml](compose.yml) 和 [.env.example](.env.example) 放入同一目录，运行 `cp .env.example .env`。
3. 在 `.env` 中填写 `PUBLIC_URL`（浏览器访问工作台的完整地址）、`CAD_URL`（容器能访问的 CAD 服务地址，含 `/mcp`）和 `CAD_TOKEN`（上一步创建的口令）。设置 `INITIAL_PASSWORD` 作为 admin 的初始登录密码。
4. 用下面的命令生成 Fernet 密钥，填入 `MASTER_KEY`。备份时同时保存密钥和 `mobile-data` 数据卷，恢复加密的模型配置需要两者。

```sh
docker run --rm --entrypoint python ghcr.io/gatanozoa/freecad-mobile:0.6.0 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

在 Compose 目录运行 `docker compose up -d`，然后打开 `PUBLIC_URL`（默认端口 `8089`）。以 admin 登录，在设置页填写兼容 Chat Completions 的模型地址、名称和 API Key。

## 同时通过域名和局域网 IP 访问

将主要访问地址填入 `PUBLIC_URL`，将其他允许的来源填入 `ALLOWED_ORIGINS`。例如：

```env
PUBLIC_URL=https://cad.example.com
ALLOWED_ORIGINS=http://192.0.2.10:8089
```

多个来源用英文逗号分隔，每项包含协议、主机和非标准端口，不含路径。修改后运行 `docker compose up -d` 使配置生效。每个来源需要独立登录，使用反向代理时应传递浏览器发送的 Origin 请求头。

## 使用工作台

手机端使用带图标的悬浮标签栏。模型预览从底部打开，可切换三维预览与三视图；系统开启减少透明度时，支持该偏好的浏览器会使用实色导航。

会话页默认打开最近会话。使用「新建对话」可以打开独立草稿。任务记录保留各阶段及发生时间，点击展开查看。服务端断线后可以在会话中重试，已受理的建模任务会使用同一任务编号。模型库每个项目显示一张卡片，打开后可切换版本。

移动端编辑时打开独立输入栏。可见区域变化时，编辑栏紧贴屏幕键盘上方。关闭输入栏后，草稿和会话位置会保留。

整表确认会显示建议尺寸和复核说明。必要尺寸缺失、确有冲突或结构不明确时才进入追问。已有的待补充任务可以在原会话重新提交一次，生成新版的尺寸确认卡。

模型归档后，可在预览里直接下载 STL、STEP 和 FCStd。系统返回会先关闭模型预览。需要调整时，使用预览底部的「返回会话修改」。

尺寸确认、模型下载和版本管理见[工作台使用说明](../../FreeCAD-Mobile/README.md)。
