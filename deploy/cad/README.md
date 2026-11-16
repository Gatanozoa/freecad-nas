# FreeCAD MCP 镜像部署

镜像已公开，无需 GitHub 登录、源码、本地构建或 .env。仅 gateway、worker 两个容器。

NAS IP 192.168.1.100，端口 8088；更换地址时同步修改 PUBLIC_URL。
管理页：http://192.168.1.100:8088/admin
MCP：http://192.168.1.100:8088/mcp
健康检查：http://192.168.1.100:8088/healthz

新部署使用 admin/admin 登录，在管理页修改密码并新增 Agent；各客户端使用独立 Bearer Token。移动端设置的 CAD_URL 也指向此 MCP 地址。

升级必须保留原项目名称以及 state、artifacts 卷，否则会初始化新的队列与账号。原来通过 CAD_AGENTS 环境变量配置 Agent 的部署，请保留原值；管理页持久化的 Agent 继续从 state 卷读取。不要删除数据卷。

worker 单线程排队执行，默认每任务 180 秒，成功产物默认保留最近 10 个，可在管理页调整。失败/取消/超时产物由执行端清理。Worker 禁止网络访问；模型脚本仅应提交可信任务。

本配置使用命名卷，不需要 storage-init 或宿主目录权限调整。worker 首次启动执行 FreeCAD 自检；等待健康检查通过后再提交任务。
