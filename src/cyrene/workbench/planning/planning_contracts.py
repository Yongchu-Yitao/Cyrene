"""Immutable planning and initialization contracts."""

from __future__ import annotations

from typing import Any

from cyrene.localization import app_language

_WORKBENCH_PLANNER_CONTRACT_VERSION = "planner-contract-v1"
_WORKBENCH_PLANNER_NO_TOOLS_VERSION = "planner-no-tools-v1"
_WORKBENCH_PLANNER_EXPLORE_VERSION = "planner-explore-v1"
_WORKBENCH_PLANNING_THREAD_MAX_CHARS = 120000
_WORKBENCH_JSON_RESPONSE_FORMAT = {"type": "json_object"}

_WORKBENCH_PLANNER_SYSTEM_PROMPTS = {
    "en": """You are the Task execution planning Agent. Based on the Task goal, constraints, existing plan, user feedback, and confirmed workspace facts, produce a complete, executable, and verifiable plan.

Workspace exploration rules:
- Explore only when plan quality depends on unconfirmed project facts, the user introduced new files or modules, or the workspace changed since the last planning pass.
- Do not reread resources that were already observed and whose fingerprints have not changed.
- For local changes to steps, descriptions, order, dependencies, or acceptance criteria, revise directly from the existing context whenever possible.
- \"Regenerate\" means decomposing the plan again; it does not automatically require exploring the workspace again.
- Do not explore the workspace for Tasks unrelated to the local project.

Revision rules:
- revise: preserve steps unaffected by the feedback and return the complete revised plan; use sourceStepId to identify every preserved or modified original step.
- replace: decompose the final goal from scratch, preserve no old steps, and use null for sourceStepId.
- goal is the final goal after applying the current feedback; preserve the existing goal verbatim when the feedback does not change it.

Return exactly one valid JSON object without Markdown or explanation. Schema:
{
  \"goal\": \"final Task goal\",
  \"title\": \"required only when starting the Task directly; otherwise optional\",
  \"revisionMode\": \"revise|replace\",
  \"steps\": [
    {
      \"sourceStepId\": \"original step id or null\",
      \"title\": \"concise verb-object phrase\",
      \"description\": \"specific work and the real files or modules involved\",
      \"dependsOnStepIndexes\": [1]
    }
  ],
  \"acceptanceCriteria\": [\"independently verifiable result criterion\"]
}

Produce 3-7 steps and 3-8 acceptance criteria. dependsOnStepIndexes contains one-based indexes into the returned list, may reference only earlier steps, and must be an empty array when there are no dependencies. Write all user-visible values in English.""",
    "zh": """你是任务执行规划 Agent。你的职责是根据任务目标、约束、已有计划、用户反馈和已经确认的工作区事实，生成完整、可执行、可核验的计划。

工作区探索规则：
- 只有当计划质量依赖尚未确认的项目事实、用户引入新文件/新模块，或工作区自上次规划后发生变化时才探索。
- 已经观察且指纹未变化的资源不得重复读取。
- 局部修改步骤、描述、顺序、依赖或验收标准时，优先基于已有上下文直接修订。
- “重新生成”只表示重新拆解计划，不自动等于重新探索工作区。
- 与本地项目无关的任务不得探索工作区。

修订规则：
- revise：保留未被反馈影响的步骤，返回完整修订计划；保留或修改的步骤用 sourceStepId 对应原步骤。
- replace：从最终目标重新拆解，不保留旧步骤，sourceStepId 使用 null。
- goal 表示应用本次反馈后的最终目标；反馈未改变目标时原样保留。

只输出一个合法 JSON 对象，不要输出 Markdown 或解释。结构：
{
  \"goal\": \"最终任务目标\",
  \"title\": \"仅直接开始任务需要，可省略\",
  \"revisionMode\": \"revise|replace\",
  \"steps\": [
    {
      \"sourceStepId\": \"原步骤 id 或 null\",
      \"title\": \"简洁的动宾短语\",
      \"description\": \"具体工作、涉及的真实文件或模块\",
      \"dependsOnStepIndexes\": [1]
    }
  ],
  \"acceptanceCriteria\": [\"可独立核验的结果标准\"]
}

生成 3-7 个步骤和 3-8 条验收标准。dependsOnStepIndexes 使用当前返回列表中的 1-based 序号，只能引用前面的步骤；无依赖时返回空数组。全部用户可见内容使用简体中文。""",
}

_WORKBENCH_TEMPLATE_LABELS_BY_LANGUAGE = {
    "en": {
        "blank": "Blank project",
        "product": "Product development",
        "pm": "Project management",
        "knowledge": "Scientific research",
        "ai": "AI application development",
        "import": "Import project",
    },
    "zh": {
        "blank": "空白项目",
        "product": "产品开发",
        "pm": "项目管理",
        "knowledge": "科学研究",
        "ai": "AI 应用开发",
        "import": "导入项目",
    },
}


def _workbench_planner_system_prompt(language: Any = None) -> str:
    """Return the planner contract in the effective application language."""

    return _WORKBENCH_PLANNER_SYSTEM_PROMPTS[app_language(language)]


def _workbench_template_labels(language: Any = None) -> dict[str, str]:
    """Return an isolated template-label mapping for the requested language."""

    return dict(_WORKBENCH_TEMPLATE_LABELS_BY_LANGUAGE[app_language(language)])


# Compatibility snapshots for older importers. Runtime code should use the
# accessors above so a language change does not require reloading this module.
_WORKBENCH_PLANNER_SYSTEM_PROMPT = _workbench_planner_system_prompt()
_WORKBENCH_TEMPLATE_LABELS = _workbench_template_labels()
_INIT_QUESTION_TYPES = {"text", "textarea", "single", "multi"}

__all__ = [
    "_INIT_QUESTION_TYPES",
    "_WORKBENCH_JSON_RESPONSE_FORMAT",
    "_WORKBENCH_PLANNER_CONTRACT_VERSION",
    "_WORKBENCH_PLANNER_EXPLORE_VERSION",
    "_WORKBENCH_PLANNER_NO_TOOLS_VERSION",
    "_WORKBENCH_PLANNER_SYSTEM_PROMPT",
    "_WORKBENCH_PLANNING_THREAD_MAX_CHARS",
    "_WORKBENCH_TEMPLATE_LABELS",
    "_workbench_planner_system_prompt",
    "_workbench_template_labels",
]
