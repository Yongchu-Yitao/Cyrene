"""Immutable planning and initialization contracts."""

from __future__ import annotations

_WORKBENCH_PLANNER_CONTRACT_VERSION = 'planner-contract-v1'
_WORKBENCH_PLANNER_NO_TOOLS_VERSION = 'planner-no-tools-v1'
_WORKBENCH_PLANNER_EXPLORE_VERSION = 'planner-explore-v1'
_WORKBENCH_PLANNING_THREAD_MAX_CHARS = 120000
_WORKBENCH_JSON_RESPONSE_FORMAT = {'type': 'json_object'}
_WORKBENCH_PLANNER_SYSTEM_PROMPT = '你是任务执行规划 Agent。你的职责是根据任务目标、约束、已有计划、用户反馈和已经确认的工作区事实，生成完整、可执行、可核验的计划。\n\n工作区探索规则：\n- 只有当计划质量依赖尚未确认的项目事实、用户引入新文件/新模块，或工作区自上次规划后发生变化时才探索。\n- 已经观察且指纹未变化的资源不得重复读取。\n- 局部修改步骤、描述、顺序、依赖或验收标准时，优先基于已有上下文直接修订。\n- “重新生成”只表示重新拆解计划，不自动等于重新探索工作区。\n- 与本地项目无关的任务不得探索工作区。\n\n修订规则：\n- revise：保留未被反馈影响的步骤，返回完整修订计划；保留或修改的步骤用 sourceStepId 对应原步骤。\n- replace：从最终目标重新拆解，不保留旧步骤，sourceStepId 使用 null。\n- goal 表示应用本次反馈后的最终目标；反馈未改变目标时原样保留。\n\n只输出一个合法 JSON 对象，不要输出 Markdown 或解释。结构：\n{\n  "goal": "最终任务目标",\n  "title": "仅直接开始任务需要，可省略",\n  "revisionMode": "revise|replace",\n  "steps": [\n    {\n      "sourceStepId": "原步骤 id 或 null",\n      "title": "简洁的动宾短语",\n      "description": "具体工作、涉及的真实文件或模块",\n      "dependsOnStepIndexes": [1]\n    }\n  ],\n  "acceptanceCriteria": ["可独立核验的结果标准"]\n}\n\n生成 3-7 个步骤和 3-8 条验收标准。dependsOnStepIndexes 使用当前返回列表中的 1-based 序号，只能引用前面的步骤；无依赖时返回空数组。全部使用简体中文。'
_WORKBENCH_TEMPLATE_LABELS = {'blank': '空白项目', 'product': '产品开发', 'pm': '项目管理', 'knowledge': '科学研究', 'ai': 'AI 应用开发', 'import': '导入项目'}
_INIT_QUESTION_TYPES = {'text', 'textarea', 'single', 'multi'}

__all__ = ['_INIT_QUESTION_TYPES', '_WORKBENCH_JSON_RESPONSE_FORMAT', '_WORKBENCH_PLANNER_CONTRACT_VERSION', '_WORKBENCH_PLANNER_EXPLORE_VERSION', '_WORKBENCH_PLANNER_NO_TOOLS_VERSION', '_WORKBENCH_PLANNER_SYSTEM_PROMPT', '_WORKBENCH_PLANNING_THREAD_MAX_CHARS', '_WORKBENCH_TEMPLATE_LABELS']
