# 调研 AI 个人助理的交互工作台，比较实现方式

围绕“调研 AI 个人助理的交互工作台，比较实现方式”，整理了 2 份选定资料。本次重点：改成对比表

## 主要发现

### 1. Plugin Extensions：持续存在的工作空间

复杂任务可以拥有持续存在的工作区，输入框与工作区共同维护任务上下文。

来源：https://developers.openai.com/plugins/build/extensions

### 2. ChatKit：交互组件与 Actions

明确的按钮操作可以直接调用业务逻辑；需要理解和规划的动作再交给 Agent。

来源：https://developers.openai.com/api/docs/guides/custom-chatkit

## 建议

采用固定应用外壳与动态工作区。优先复用稳定组件，通过结构化动作连接任务执行；复杂场景再使用隔离的专用视图。



说明：演示运行器根据预置或用户提供的资料组装，未调用语言模型或实时检索。