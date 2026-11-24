# FreeCAD NAS Agent Service

在局域网中运行 FreeCAD 建模服务。Agent 通过 Streamable HTTP MCP 提交 Python 脚本，Gateway 管理任务队列，Worker 执行建模并导出 FCStd、STEP 和 STL。需要从手机提交图文需求时，可另行部署移动建模工作台。

## 镜像与部署

以下镜像支持 `linux/amd64`。

| 服务 | 镜像 | 部署说明 |
| --- | --- | --- |
| CAD 网关 | `ghcr.io/gatanozoa/freecad-agent-gateway:1.3.0` | [部署 Gateway 和 Worker](deploy/cad/README.md) |
| CAD 执行器 | `ghcr.io/gatanozoa/freecad-agent-worker:1.3.0` | 与 Gateway 在同一个 Compose 项目中启动 |
| 移动工作台 | `ghcr.io/gatanozoa/freecad-mobile:0.6.0` | [连接 CAD 服务并启动工作台](deploy/mobile/README.md) |

先启动 CAD 服务，在 `/admin` 登录并为每个客户端创建独立的 Agent 口令。客户端通过 `/mcp` 提交任务，可通过 `/healthz` 检查服务状态。远程访问请使用 HTTPS。

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

Worker 的 Python 审计与容器限制不等同于对恶意原生扩展的完整隔离，因此仅用于可信 Agent。

FreeCAD 运行环境来自 LinuxServer FreeCAD 镜像；上游项目与许可证：[LinuxServer FreeCAD](https://github.com/linuxserver/docker-freecad)、[FreeCAD](https://github.com/FreeCAD/FreeCAD)。Three.js 等前端依赖的第三方许可证见相关发行文件。
