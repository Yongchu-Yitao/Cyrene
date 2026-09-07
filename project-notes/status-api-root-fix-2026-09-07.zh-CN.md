# `/api/status` 根因修复

## 原因

状态投影 `_build_status` 仍用于 UI bootstrap，但 HTTP 适配层没有 `/api/status` 路由。前端状态刷新和 CLI 状态查询因此请求了不存在的接口。此外，CLI 发现后端、聊天连通性检查和 SSE 断线鉴权探测都复用了业务状态查询；CLI 启动还通过完整 UI 数据聚合判断服务就绪。

## 修复后的职责

- `/api/status`：由 HTTP 路由调用 `PresentationQueryService.status()`，复用既有状态投影。保留模型、会话消息数、子代理、记忆统计等原有字段及语义，与 bootstrap 的 `status` 一致。
- `/api/health`：轻量、需鉴权的 API 存活探测，返回 Cyrene 服务标识、状态和实例 ID。不读取会话、模型或 UI 聚合数据。
- `/api/instance-id`：保留公开实例识别用途，不将其视为 token 有效的证明。
- CLI 发现、启动等待、纯连通性检查与前端 SSE 断线探测使用 health；业务状态刷新、CLI status 与交互聊天头部仍读取 status。
- CLI 发现同时验证 HTTP 状态与 Cyrene JSON 标识，避免接受任意本地 HTTP 200。保留受保护 Electron 实例检测，避免启动竞争后端。
- CLI 静默启动不再读取 UI 数据；普通启动就绪后读取一次 UI 数据以保留会话列表输出。启动请求使用发现流程更新后的鉴权信息。

没有加入虚假状态、404 吞错或接口降级回退；没有修改原有业务状态算法和复杂度预算。

## 验证

- 集中 Python 回归：468 passed，覆盖真实路由注册、鉴权、状态投影、CLI、页面生命周期及相关前端行为。
- 前端 Node 测试：88 passed。
- Ruff、前端构建、`git diff --check` 通过。
- Python 复杂度：9726 scopes，无预算增长；JavaScript：11163 scopes，无预算增长。
- 隔离临时目录、正式运行时初始化、真实 `create_app` 和本地 HTTP 端口冒烟：health/status 无 token 返回 401，instance-id 返回 200；正确 token 下 health/status/ui-data 均为 200，status 与 bootstrap 状态一致；实际 CLI 凭据发现和状态查询通过。服务退出并清理临时目录。
- 首次 HTTP 冒烟遗漏运行时数据库初始化，导致 ui-data 500；补齐正式启动步骤后通过。该问题属于验证脚本设置，不是接口修复中的业务变更。

验证范围是本次接口及调用链修复。未重新运行全项目性能基准，不能将此前性能测量中的退步宣称为已解决。
