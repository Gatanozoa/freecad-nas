# FreeCAD NAS Agent Service

局域网 Agent 通过 Streamable HTTP MCP 提交 FreeCAD Python 脚本，由 NAS 持久排队、单任务执行并导出 FCStd、STEP 和 STL。提供独立管理页。包含独立的 FreeCAD Gateway/Worker，以及通过 MCP 调用它们的移动建模工作台。两个项目独立部署。

## 镜像

- `ghcr.io/gatanozoa/freecad-agent-gateway:1.3.0`
- `ghcr.io/gatanozoa/freecad-agent-worker:1.3.0`
- `ghcr.io/gatanozoa/freecad-mobile:0.2.0`（app 与 orchestrator 共用）

Linux amd64。镜像发布以 Actions 成功结果为准。另提供 `latest` 和 `sha-<12位提交号>` 标签，正式部署建议使用版本或提交号标签。首次发布后，将 GitHub Packages 中镜像包的可见性设为 Public，NAS 才能免登录拉取。

## 部署

下载 `deploy/cad/compose.yml` 和 `.env.example`，将后者复制为 `.env` 并填写地址和独立 Agent 口令，然后导入 NAS Docker 应用启动。不需要上传源码或在 NAS 构建镜像。

已有部署升级时保持原 Compose 项目名、原 state/artifacts 卷和口令，只替换 image 并移除 build。不要直接用示例环境覆盖原配置。`PUBLIC_URL` 必须与实际浏览器及 MCP 访问地址一致。

管理页 `/admin` 初始账号 admin/admin，支持修改密码。MCP `/mcp`，健康检查 `/healthz`。仅向可信局域网 Agent 发放凭据；远程访问使用 HTTPS 反向代理。

## 移动端

[移动端部署与使用说明](FreeCAD-Mobile/README.md) · [只拉取镜像的 Compose](deploy/mobile/compose.yml) · [环境变量示例](deploy/mobile/.env.example)

现有移动端升级时保留 `.env`、原项目名及 `mobile-data` 卷，仅替换 Compose；不再需要 NAS 本地构建。移动端发布流程独立执行单元/浏览器测试和镜像启动检查，成功后推送到 GHCR。

## 模型脚本

所有长度以毫米计。脚本必须显式赋值 `result` 为含有效实体 Shape 的 FreeCAD 文档对象，而不是 Document 本身。例如：

```python
import FreeCAD as App
import Part
doc = App.newDocument("Model")
result = doc.addObject("Part::Feature", "Result")
result.Shape = Part.makeBox(60, 30, 5)
doc.recompute()
```

禁止 GUI、网络、子进程、任意文件读写或自行导出。全局 FIFO、同时执行一个任务；不同 Agent 仅能读取自己的任务。默认成功产物全局保留最近十个，记录与脚本保留；失败/超时/取消清理产物，中断任务不自动重跑。

Worker 的 Python 审计与容器限制不等同于对恶意原生扩展的完整隔离，因此仅用于可信 Agent。源代码及镜像不包含任何真实 `.env`、Agent 口令、用户 Key 或模型文件。

## 发布

向 main 提交或手动运行 Actions，流水线先检查发布范围并运行队列、鉴权、清理等回归，再分别构建 Gateway/Worker。Worker 必须实际验证 FreeCAD 几何与三种文件导出通过后才能推送到 GHCR。使用 GitHub 自动提供的 GITHUB_TOKEN，无需把个人 Token 写进代码。

FreeCAD 运行环境来自 LinuxServer FreeCAD 镜像；上游项目与许可证：[LinuxServer FreeCAD](https://github.com/linuxserver/docker-freecad)、[FreeCAD](https://github.com/FreeCAD/FreeCAD)。Three.js 等前端依赖的第三方许可证见相关发行文件。
