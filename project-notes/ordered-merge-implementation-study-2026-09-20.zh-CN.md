# 前端时间线合并快路径：实现研究

约束：保持所有功能，本轮只研究如何实现。已经生成可审阅但未应用的补丁；没有修改产品源码。它修复了上一版实验的稀疏数组缺口，不代表已经完成上线所需的全部验证。

## 最小接入范围

只修改 `runtime-timeline.jsx` 中 `wbcProjectTranscript` 的**最后一次有序消息合并**。去重活动卡片、优先使用更新 checkpoint、处理 removedMessageIds、用户消息协调和 continuation 判定全部保留原位置和原逻辑。

保留 `wbcMergeChronologicalMessages` 完整实现及其所有其他调用者。问答确认、分屏事件、用户消息回显、旧式投影、保存回调都不会因这次接入改走新算法。

补丁新增两个私有函数，放在 `wbcProjectTranscript` 前：

```js
function wbcMergeProjectedMessages(messages, additions) {
  var ordered = wbcTryMergeOrderedMessages(messages, additions);
  return ordered === null
    ? wbcMergeChronologicalMessages(messages, additions)
    : ordered;
}
```

仅将该投影末尾的合并调用改为 `wbcMergeProjectedMessages`。`null` 是明确的“不适用”结果，空数组仍是有效结果。不捕获或吞掉原算法的异常。

保持助手函数位于同一模块，避免增加运行时依赖。现有 Node 测试用空依赖 stub，若另拆模块导入，需要同步调整 harness；部分 Python 测试还会截取 `wbcMergeChronologicalMessages` 到 `wbcRuntimeSegmentMessages` 之间的函数。本草案的位置兼容这种截取方式。

## 快路径的条件

全部满足才启用，任何一项不满足都返回 null，由调用者走完整旧算法：

1. 两个输入是当前 realm 的普通数组，没有自己的 slice、forEach、constructor 或 iterator 覆盖。
2. 每个数组索引都有自己的数据属性，不是空槽或 getter。记录是普通对象或 null-prototype 对象。
3. id、createdAt、created_at 仅为字符串或缺失；相关字段不能是 getter、继承字段或需要自定义类型转换的值。
4. 按原来的 `createdAt || created_at` 优先级解析时间，所有时间有限，且每个序列内部非递减。绝不预排序输入。
5. 新增记录全部为 assistant，没有有效的 clientRequestId、answerToQuestionId 或 optimistic 标记。特殊关联逻辑全部交给旧算法。
6. 实验默认新增数至少 8，且 `M × (N + M) ≥ 4096`。这是降低小请求额外开销的初始性能门槛，不是正确性前提，也不是已在所有设备调优的参数。

先检查新增侧，再检查历史侧，尽量让问答、特殊消息等提前回退。属性描述符检查不会主动执行普通 getter；已验证数组索引 getter、记录 getter 和自定义 forEach 的回退调用次数与旧实现一致。

边界：任意 JavaScript Proxy 可以对 getPrototypeOf/getOwnPropertyDescriptor/has 自身产生副作用，JavaScript 没有通用、无副作用的 Proxy 检测办法。因此不能声称此 guard 对任意恶意/有副作用的 JS 对象完全等价。正式接入还须确认目标投影输入合同为 JSON 数据及普通不可变对象更新、内建对象未被改写；如果支持把任意 Proxy 直接作为消息输入，就不能按当前方案无条件接入。选择私有投影接入而不改公共合并函数，正是为了缩小这一输入合同的审查范围。

## 合并过程及等价依据

- 每个时间只解析一次，存入两个 Float64Array。
- 用历史中所有非空 id 建立 known，保留历史中原有的重复记录。
- 顺序读取新增消息。id 已知则跳过；空 id 永不作为去重键。
- 对每条待插入消息，先输出历史中时间小于或等于它的记录，再输出它本身；最后输出历史余项。
- 返回新数组，复用原消息引用，不克隆、截断、改写任何消息，不引入跨调用缓存。

对满足条件的输入，旧算法与新算法的 known 可逐步对应；每次新增项都插在第一个严格晚于它的已有项之前。同时间时历史项先于新增项，新增项之间保持原先顺序。两个序列有序，历史指针无需回退。因此其时间复杂度为 O(N+M)，替代原逐项扫描与插入的 O(NM+M²) 最坏情况。

额外时间缓存占约 `8 × (N+M)` 字节数值存储，不包括数组对象、输出数组和 Set 的开销。N=M=1000 时约 16 KB 数值存储；没有测得完整页面峰值内存，不能据此断言整体内存不变。

## 实验验证

实验直接读取当前产品源码，在内存中仅替换上述一个调用点，比较旧、新结果。草案源文件另存于实验目录，不被产品导入。

- **20,336 组合并对比**，含小输入枚举、确定性随机样例、明确边界用例。
- **4,000 组完整现代时间线投影对比**，含问答、重连、revision、活动卡片与 removedMessageIds。
- **311,752 次引用检查**；普通数据输入递归冻结，比较值、顺序、空槽及对象身份。
- **240 次投影回放对比**，覆盖 snapshot、增量文本、删除、重复旧 revision 和终结状态；断言真实接入位置确实命中快路径。
- 将未应用的候选完整源码注入现有 `runtime-timeline.test.mjs`：**8/8 通过**。这些已有短样例主要验证回退与原行为，不能代替快路径差分实验。

明确覆盖上轮稀疏数组反例，以及 null 元素、数字 id、null-prototype 记录、乐观消息确认、等时间稳定性、访问器与数组方法覆盖。对不适用输入完整回退。

研究中发现一个测试装置问题：向 Node VM 注入宿主 Object 后，VM 创建的对象字面量原型不匹配，导致真实投影一直回退。已废弃该轮读数，改成同 realm 执行，并断言投影命中情况。最终报告只含修正后的测量。

## 性能结果与代价

同 realm Node 纯投影，预热 2 次、采样 7 次，以下为中位数 ms。没有浏览器 DOM、布局、GC pause 或真实会话负载。采样较少、顺序非交替，数字用于确认数量级，不是正式回归结论。

| 历史 N／新增 M | 旧投影 | 条件快路径投影 | 路径 |
|---|---:|---:|---|
| 1000／1 | 0.185 | 0.209 | 原算法 |
| 1000／8 | 0.857 | 0.295 | 快路径 |
| 1000／32 | 3.237 | 0.323 | 快路径 |
| 1000／1000 | 144.821 | 0.668 | 快路径 |
| 1000／8，历史最后一个时间无效 | 0.845 | 1.053 | 验证失败后回退 |

普通短请求维持旧算法，但仍有少量分支和函数调用成本；无效输入直到历史末尾才被识别时，有约 0.21 ms 的额外开销样例。不能声称所有路径都更快或绝无退化。正式测试应交替运行 A/B，测量有序输入占比、实际命中率及回退成本，再确定门槛。

与上轮 449 ms 等数字使用的 VM 计时环境不同，不能横向解释为再次加速；只能在本表同一轮内比较旧、新投影。

## 正式实施前还需完成

1. 冻结待修改代码基线，确认目标入口的数据来源和输入合同；当前工作区有其他修改，本研究保留并包含了新增 removedMessageIds 逻辑。
2. 将差分、输入不变、引用身份、回放和普通 getter 回退测试纳入正式测试；与现有 UI／架构检查一起执行。
3. 用实际 Electron／浏览器运行长会话、分屏、重连和恢复场景，核对显示、查找、复制、导航、无障碍及取消/失败。未更改 DOM 策略不等于已经验证这些功能。
4. 测首次回复显示、页面长任务、CPU、峰值 RSS 和 GC；特别检查无效时间、频繁回退、小设备及大历史。
5. 如果不能确认输入合同或出现输出差异，就维持原实现。回滚只需恢复该投影的一处调用；原算法始终保留。

## 可审阅产物

均在 `project-notes/experiments/performance-2026-09-20/`：

- `guarded_merge.mjs`：带前置条件的纯算法实验实现。
- `guarded_merge_study.mjs`、`guarded-merge-study.json`：差分、回放、命中断言与性能结果。
- `guarded-merge-proposal.patch`：约 95 行的未应用草案，包含助手函数和一个调用点替换。
- `proposed-runtime-timeline.jsx`：完整候选源码，位于实验目录。
- `guarded_candidate_contracts.mjs`、`guarded-candidate-contracts.log`：在候选源码上执行现有 8 项合同测试。
- `guarded-merge-source.json`：生成草案时的原源码与候选源码 SHA-256。

复现：`node project-notes/experiments/performance-2026-09-20/guarded_merge_study.mjs`；候选合同测试同目录运行 `guarded_candidate_contracts.mjs`。源码变化后应重新生成候选并复核，不应盲目应用过期 patch。
