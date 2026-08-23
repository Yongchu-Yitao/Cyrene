# Cyrene 实时控制 PowerPoint

Cyrene 的 Office 工具包可以控制 PowerPoint 当前打开的演示文稿，也可以在没有实时会话时直接处理 `.pptx` 文件。加载项在 PowerPoint 的 Office.js 运行时中执行对象模型操作；Cyrene 通过仅监听回环地址的 HTTPS/WSS Gateway 发送类型化指令。纯 Office.js 形状批次使用一次最终 `context.sync()` 追求速度；图片按原始操作顺序使用 `ImageCoercion` 所要求的同步，但整个批次仍只产生一个 Cyrene revision 和 undo token。声明式建页还可切换为逐阶段或逐元素同步，让用户在 PowerPoint 里看到组件依次出现。

## 首次安装

1. 启动 Cyrene 桌面应用。桌面后端会自动启动 `https://localhost:4243` 的 Office Gateway，并在 Cyrene 数据目录生成证书和加载项清单。
2. 打开“设置 → 服务集成 → Microsoft PowerPoint”，点击“安装到 PowerPoint”。按钮会把 `localhost` 证书加入当前用户的信任存储；macOS 使用登录钥匙串，Windows 使用当前用户 Root 证书存储，不会修改系统级证书存储。
3. macOS 会自动把清单安装到 PowerPoint 的用户加载项目录。Windows 会准备好清单并打开其位置；Windows 桌面版按照 Microsoft 的机制，需要把清单所在目录配置为 PowerPoint 的受信任共享文件夹目录。
4. 完全退出并重新打开 PowerPoint，打开任意演示文稿，选择“主页 → 加载项 → Cyrene Live PowerPoint”。首次从加载项列表点开后会出现 Cyrene 任务窗格；任务窗格显示“已连接”后，Agent 就能操作当前演示文稿。

只有检测到加载项已经安装（Windows 上也可以由一次真实连接证明）时，Cyrene 才会把 PowerPoint 工具放入 Agent 上下文。未安装时，服务集成页只保留安装入口，不显示 Gateway、证书或连接参数等高级设置，也不会给 Agent 暴露空壳工具；安装完成后的新任务会自动获得工具，不需要运行命令。命令行入口只供开发和故障排查使用。

如果安装后列表中暂时没有出现 Cyrene，先确认 PowerPoint 已用 `⌘Q` 完全退出而不是只关闭窗口，再重新打开。开发期间更新过 manifest 时，PowerPoint 仍可能保留旧的 Wef 缓存；此时按 Microsoft 的 Office 缓存清理流程清理后重新安装。

面向开发者的命令行安装入口仍然保留：`uv run python -m cyrene.office.install --trust`。

手动 Web 模式默认不占用固定端口。需要使用 Office Gateway 时以 `CYRENE_OFFICE_FORCE_START=1` 启动 Cyrene。端口可通过 `CYRENE_OFFICE_PORT` 修改；修改后要重新生成并重新加载清单。

## 渐进式 PPT Agent Kit

安装后，模型默认只看到五个核心工具：`ppt.get_context`、`ppt.inspect`、`ppt.apply_batch`、`ppt.render`、`ppt.tool_search`。其中 `ppt.tool_search` 支持搜索、描述、调用三个阶段，只把当前任务需要的参数 schema 加载进上下文。

| 层级 | 按需能力 |
| --- | --- |
| L1 Inspect | `list_slides`、`get_slide`、`list_shapes`、`get_shape`、`read_text`、`get_master`、`get_theme`、`get_selection` |
| L2 Edit | 新增、移动、缩放、文字、样式、删除、分组、图层，以及宿主支持时的图片；全部编译成一个 `apply_batch` |
| L3 Compose | 创建、复制、替换、重排、移动、删除页面，以及声明式 `SlideSpec` |
| L4 Review | 渲染、溢出、重叠、对比度、前后对比和撤销 |
| L5 Advanced | 表格、图表、母版、布局、备注、持久绑定、OOXML、导入页面 |
| L6 Escape | 受开发模式和确认保护的 Office.js 命令白名单与 OOXML 页面替换 |

完整工作流固定为：读取上下文 → 一次读取所需结构 → 最小修改计划 → 一页一个批次 → PowerPoint 渲染并验证 → 只局部修正 → 返回实际修改摘要。实时多页任务逐页提交。实时模式下 `ppt.create_slide` 与 `ppt.apply_slide_spec` 默认使用 `commitMode=progressive`，通过 `progressiveGranularity=stage|element` 选择按“结构/标题/内容/媒体”或按单个组件同步；明确要求最高速度时可选择 `atomic`。文件模式固定按原子方式写入，不伪造实时搭建阶段。

写操作接受 `expectedRevision` 和 `idempotencyKey`（兼容旧的 snake_case 字段）。返回值统一包含 `revision`、`changed`、`created`、`deleted`、`warnings`、`undoToken`、`renderId` 和审计摘要。修订锁会拒绝基于旧 Cyrene 修订继续写入；PowerPoint 的选择变化单独作为实时状态事件，不再伪装成内容修订并制造冲突。同一幂等键只会重放同一结果。

## 两种执行后端

`ppt.get_context` 会明确返回 `mode`：

- `live_office`：通过加载项和 Office.js 修改当前打开的演示文稿，用户实时可见。
- `file`：传入 `filePath` 后直接编辑 PPTX/OOXML 包，支持结构检查、元素编辑、原生表格、可编辑原生图表、备注、版式/母版 typed operations、跨文稿导入、页面操作、快照撤销和本机可用时的 LibreOffice 渲染。

两种模式使用同一套工具语义。能力协商会明确报告宿主差异：实时模式支持服务端生成后导入的可编辑原生图表、原生表格、版式应用和跨文稿导入；图片及可视图表使用稳定的 Office Common API `Document.setSelectedDataAsync` 与 `ImageCoercion 1.1`，按指定的 left/top/width/height 插入当前目标页。当前生产版 Office.js 不直接开放备注写入和母版图形修改，因此这两项在文件后端完成，实时上下文会明确返回 `notesOperations.edit=false`、`masterOperations.editShapes=false`，不会假装成功。

`ppt.edit_chart` 提供 `chartMode=visual|native`，并要求 Agent 根据上下文能力显式选择：`visual` 把柱状图/折线图确定性渲染为图片，通过 `ImageCoercion` 在当前页实时新增或替换；`native` 由 Cyrene 在隔离的单页 PPTX 中生成带嵌入 Excel 工作簿的 Office 图表，再替换回当前页，因此仍可在 PowerPoint 中编辑图表数据。返回值用 `nativeEditable` 明确区分两种结果。

## 批量操作

`ppt.apply_batch` 支持：

- `add_textbox`、`add_shape`、`add_line`、`insert_image`
- `update_shape`、`move_shape`、`resize_shape`、`update_text`、`apply_style`、`delete_shape`
- `set_z_order`、`group_shapes`、`ungroup_shapes`

每个新元素可设置 `ref`。加载项把它保存成 `cyrene:<ref>` 的 PowerPoint 元素名称，后续批次可以直接用 `shapeRef=<ref>` 引用，不依赖易变化的索引。

核心编辑需要 PowerPointApi 1.5。图片插入需要 Office Common API 的 ImageCoercion 1.1；真实渲染、可恢复快照、原生表格、分组和图层调整需要 PowerPointApi 1.8；精确页面尺寸检查需要 1.10。不支持的操作会返回明确的 `capability_unavailable`，不会再尝试图片填充等替代路径。

## 安全边界

- Gateway 只绑定 `127.0.0.1`，使用独立的本地 TLS 证书和 288 位随机桥接密钥。
- 密钥只写入用户数据目录中的加载项清单，不复用 Cyrene 主 API 令牌。
- Agent 只调用白名单类型化操作；加载项没有 `eval` 或任意 JavaScript 执行入口。L6 的 `execute_officejs` 只接受固定命令枚举。
- 修改发生在 PowerPoint 当前文档内，沿用 PowerPoint 自身的自动保存、协作和文件权限模型。

当前完整纵向切片面向 PowerPoint。会话协议已经允许 `word` 宿主，Word 的具体 typed capabilities 可以在同一个 `office_tools` Gateway 下继续增加，无需改变 Agent 或本地传输架构。
