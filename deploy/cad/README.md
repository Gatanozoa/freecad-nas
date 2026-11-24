# 部署 FreeCAD MCP 服务

Gateway 提供管理页面和 MCP 接口，Worker 在关闭网络访问的容器中执行 FreeCAD 脚本。两个服务通过命名卷共享任务状态和模型产物。

## 启动

将 [compose.yml](compose.yml) 和 [.env.example](.env.example) 放入同一目录。在该目录运行 `cp .env.example .env`，然后填写三个网络参数。

| 变量 | 用途 |
| --- | --- |
| `BIND_ADDRESS` | Docker 主机用于监听请求的局域网 IP |
| `HTTP_PORT` | 对外提供服务的端口，默认为 `8088` |
| `PUBLIC_URL` | 浏览器和 Agent 实际访问的服务地址，包含协议和端口，例如 `http://nas.example.com:8088` |

```sh
docker compose up -d
docker compose ps
```

Worker 首次启动时会运行 FreeCAD 自检。两个容器均显示 healthy 后，通过 `PUBLIC_URL` 下的 `/admin` 登录，初始用户名和密码均为 `admin`。修改密码后，在管理页为每个 Agent 创建独立的 Bearer Token。MCP 客户端连接 `/mcp`，健康检查地址为 `/healthz`。

也可以单独下载 [compose.standalone.yml](compose.standalone.yml)，在其中填写 `PUBLIC_URL` 后运行 `docker compose -f compose.standalone.yml up -d`。该文件自带环境配置。

## 任务和数据

`state` 卷持久化账号、队列和任务记录，`artifacts` 卷保存导出的模型。Worker 每次执行一个任务，默认超时为 180 秒。服务默认保存最近 10 个成功任务的产物，可在管理页调整。仅向可信 Agent 发放执行模型脚本的权限。
