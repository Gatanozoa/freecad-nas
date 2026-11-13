# 移动端 0.3.1

黑橙主题、对齐的对话输入栏、桌面任务侧栏，以及同一弹窗内的三维模型 / 三视图切换。

## 更新已有 NAS 项目

1. 保留原项目名称、`.env` 和 `mobile-data` 数据卷。
2. 用本目录 `compose.yml` 替换原 Compose。
3. 如果 `.env` 内有 `MOBILE_VERSION`，改为 `MOBILE_VERSION=0.3.1`；其余配置保持原值，尤其不要重新生成 `MASTER_KEY`。
4. 拉取新镜像并重新部署 app、orchestrator 两个容器，单纯重启不会更新镜像。
5. 浏览器刷新页面；若已安装到桌面，关闭后重新打开。

镜像：`ghcr.io/gatanozoa/freecad-mobile:0.3.1`。无需本地构建或单独拉取 Python 镜像。FreeCAD 服务无需随此次界面升级更新。

新部署才使用 `.env.example` 生成 `.env`。升级时不要用示例覆盖真实配置。
