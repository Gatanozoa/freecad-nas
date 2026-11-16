# 移动端 0.3.3

黑橙主题、对齐的对话输入栏、桌面任务侧栏，以及同一弹窗内的三维模型 / 三视图切换。

## 更新已有 NAS 项目

1. 保留原项目名称、`.env` 和 `mobile-data` 数据卷。
2. 用本目录 `compose.yml` 替换原 Compose。
3. 如果 `.env` 内有 `MOBILE_VERSION`，改为 `MOBILE_VERSION=0.3.3`；其余配置保持原值，尤其不要重新生成 `MASTER_KEY`。
4. 拉取新镜像并重新部署 app、orchestrator 两个容器，单纯重启不会更新镜像。
5. 浏览器刷新页面；若已安装到桌面，关闭后重新打开。

镜像：`ghcr.io/gatanozoa/freecad-mobile:0.3.3`。无需本地构建或单独拉取 Python 镜像。FreeCAD 服务无需随此次界面升级更新。

新部署才使用 `.env.example` 生成 `.env`。升级时不要用示例覆盖真实配置。

## 同时通过域名和局域网 IP 访问

在已有 `.env` 增加（将域名改成自己的）：

```env
PUBLIC_URL=https://cad.example.com
ALLOWED_ORIGINS=http://192.168.1.100:8089
MOBILE_VERSION=0.3.3
```

PUBLIC_URL 自动加入白名单，ALLOWED_ORIGINS 可用英文逗号追加多个完整来源（协议、主机、非标准端口）。不要填写路径、通配符或结尾斜杠。
重新拉取并部署 app、orchestrator 使环境变量生效。无需改动 CAD_URL、MASTER_KEY 或数据卷。
各地址独立登录；HTTPS 域名登录使用 Secure Cookie，白名单 HTTP 局域网地址使用普通 Cookie，均启用 HttpOnly、SameSite=Strict。
页面和 API 仍由同一来源提供，此设置不是跨域 API 的 CORS 开关。不要通过 Caddy 改写或删除 Origin 请求头。

## 0.3.3 键盘适配

输入栏跟随浏览器实际可见区域调整；键盘弹出时隐藏底部导航及固定测试快捷入口，输入和发送按钮保留可见；收起键盘后恢复。兼容仅缩小 visualViewport 的 Android WebView 行为。更新后关闭桌面入口再重新打开以加载新页面。
