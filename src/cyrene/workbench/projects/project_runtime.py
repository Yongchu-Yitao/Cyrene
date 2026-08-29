"""Project and task-session domain constructors."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from cyrene.config import WORKSPACE_DIR
from cyrene.localization import app_language, localized

def _safe_workbench_data_key(value: Any) -> str:
    raw = str(value or '').strip()
    cleaned = re.sub('[^A-Za-z0-9._-]+', '_', raw).strip('._')
    return cleaned or 'project'

def _workbench_default_project_name() -> str:
    if WORKSPACE_DIR.name == 'workspace' and WORKSPACE_DIR.parent.name:
        return WORKSPACE_DIR.parent.name
    return WORKSPACE_DIR.name or 'Cyrene'

def _workbench_project_data_key(project: dict[str, Any] | None) -> str:
    if not project:
        return ''
    return _safe_workbench_data_key(project.get('dataKey') or project.get('id'))

def _workbench_project_resource_key(project: dict[str, Any] | None) -> str:
    """Return the stable project identity used by Plugin-owned resources."""
    if not project:
        return 'default'
    return _safe_workbench_data_key(project.get('id'))

def _ndjson_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + '\n'

def _primary_candidate() -> dict[str, Any]:
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    candidates = service.candidates_for_route("primary") if service is not None else []
    return dict(candidates[0]) if candidates else {}

def _live_llm_config() -> tuple[str, str]:
    candidate = _primary_candidate()
    return (
        str(candidate.get('model') or ''),
        str(candidate.get('base_url') or ''),
    )

def _get_model() -> str:
    return str(_primary_candidate().get('model') or '')

def _get_base_url() -> str:
    return str(_primary_candidate().get('base_url') or '')

def _parse_ctx_limit(ctx_str: str) -> int:
    """Parse human-readable context limit like '128K', '1M', '200K' to int."""
    ctx_str = (ctx_str or '').strip().upper()
    if not ctx_str:
        return 0
    try:
        if ctx_str.endswith('M'):
            return int(float(ctx_str[:-1]) * 1000000)
        if ctx_str.endswith('K'):
            return int(float(ctx_str[:-1]) * 1000)
        return int(ctx_str)
    except (ValueError, TypeError):
        return 0

def _ctx_limit_for_model(model_name: str) -> int:
    """Resolve a model window from canonical profiles, then known families."""

    from cyrene.core.plugin import application_plugin_service

    target = str(model_name or '').strip()
    ctx_limit = 0
    service = application_plugin_service("model_configuration")
    configuration = service.get_model_configuration() if service is not None else {}
    for profile in configuration.get('profiles') or []:
        if target not in {
            str(profile.get('id') or '').strip(),
            str(profile.get('model') or '').strip(),
            str(profile.get('name') or '').strip(),
        }:
            continue
        ctx_limit = int(profile.get('context_limit') or 0) or _parse_ctx_limit(
            str(profile.get('ctx') or '')
        )
        break
    if not ctx_limit:
        model_lower = target.lower()
        if any((x in model_lower for x in ('claude-opus-4', 'opus-4'))):
            ctx_limit = 200000
        elif any((x in model_lower for x in ('claude-sonnet-4', 'sonnet-4'))):
            ctx_limit = 200000
        elif any((x in model_lower for x in ('claude-haiku-4', 'haiku-4'))):
            ctx_limit = 200000
        elif 'gpt-4' in model_lower or 'gpt-4o' in model_lower:
            ctx_limit = 128000
        elif 'gpt-3.5' in model_lower:
            ctx_limit = 16000
        elif 'deepseek' in model_lower:
            ctx_limit = 128000
        elif 'qwen' in model_lower:
            ctx_limit = 128000
        elif 'gemini' in model_lower:
            ctx_limit = 1000000
    return ctx_limit

def _get_current_model_ctx_limit() -> int:
    """Look up the canonical primary model's context window limit."""
    return _ctx_limit_for_model(_get_model())

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _short_id(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:10]}'

def _workbench_default_project() -> dict[str, Any]:
    now = _utc_now_iso()
    project_id = _short_id('project')
    workspace_name = _workbench_default_project_name()
    initial_session = _workbench_new_session(
        project_id, localized('New task', '新任务'), '', now
    )
    workspace_summary = localized(
        f'Workspace at {WORKSPACE_DIR}',
        f'工作区位于 {WORKSPACE_DIR}',
    )
    return {'projects': [{'id': project_id, 'name': workspace_name, 'dataKey': _safe_workbench_data_key(project_id), 'workspacePath': str(WORKSPACE_DIR), 'workspacePathSource': 'user', 'status': 'active', 'model': _get_model(), 'accountTier': 'Pro', 'context': {'summary': workspace_summary, 'stack': [], 'decisions': [], 'knowledgeDocumentIds': []}, 'createdAt': now, 'updatedAt': now, 'sessions': [initial_session], 'sharedArtifacts': []}], 'activeProjectId': project_id, 'activeSessionId': initial_session['id']}
_WORKBENCH_PLACEHOLDER_GOALS = frozenset({
    'Clarify the current task goal through conversation.',
    '通过对话明确当前任务目标。',
})
_WORKBENCH_PLACEHOLDER_GOAL = localized(
    'Clarify the current task goal through conversation.',
    '通过对话明确当前任务目标。',
)

def _workbench_is_blank_goal(goal: Any) -> bool:
    g = str(goal or '').strip()
    return not g or g in _WORKBENCH_PLACEHOLDER_GOALS

def _workbench_is_default_title(title: Any) -> bool:
    return not str(title or '').strip() or str(title or '').strip() in {
        'New task', '新任务'
    }

def _workbench_derive_title(text: str) -> str:
    """A short task title from free text — its first line/sentence, trimmed."""
    raw = re.sub('\\s+', ' ', str(text or '').strip())
    if not raw:
        return localized('New task', '新任务')
    head = re.split('[。\\n？?！!；;]', raw, maxsplit=1)[0].strip() or raw
    return head[:24] or localized('New task', '新任务')


def _workbench_new_session(project_id: str, title: str, goal: str='', now: str | None=None, *, kind: str='task', status: str='idle') -> dict[str, Any]:
    now = now or _utc_now_iso()
    session_id = _short_id('session')
    default_title = localized('New task', '新任务')
    return {'id': session_id, 'projectId': project_id, 'kind': kind, 'title': str(title or default_title).strip()[:80] or default_title, 'goal': str(goal or '').strip(), 'constraints': [], 'status': status, 'priority': 'medium', 'createdAt': now, 'updatedAt': now, 'agentReply': '', 'plan': [], 'planRevision': 0, 'planDefinitionRevision': 0, 'approvedPlanDefinitionRevision': None, 'events': [], 'runs': [], 'artifacts': [], 'acceptanceCriteria': [], 'summary': None, 'titleLocked': False}

def _workbench_acceptance_fully_passed(criteria: Any) -> bool:
    """Return whether a task has a non-empty, completely passed acceptance set."""
    if not isinstance(criteria, list) or not criteria:
        return False
    return all((isinstance(item, dict) and str(item.get('status') or '').strip().lower() in {'passed', 'done', 'completed'} for item in criteria))

def _workbench_mark_completed_if_acceptance_passed(session: dict[str, Any], *, now: str | None=None, event_body: str='') -> bool:
    """Promote a task to completed once every acceptance criterion is passed.

    This is deliberately server-side so manual criterion updates, independent
    verification, and background goal-loop verification share the same rule.
    The event is emitted only on the transition, keeping retries idempotent.
    """
    if not _workbench_acceptance_fully_passed(session.get('acceptanceCriteria')):
        return False
    if str(session.get('status') or '').strip().lower() in {'completed', 'done'}:
        return True
    session['status'] = 'completed'
    timestamp = now or _utc_now_iso()
    if not event_body:
        event_body = localized(
            'All acceptance criteria passed, and the task was marked complete automatically.',
            '所有验收标准均已通过，任务自动标记为已完成。',
        )
    session['events'] = list(session.get('events') or []) + [{'id': _short_id('event'), 'type': 'TaskCompleted', 'createdAt': timestamp, 'body': event_body}]
    return True


def _workbench_english_init_sections(template: str) -> list[dict[str, Any]]:
    def question(identifier: str, kind: str, label: str, placeholder: str) -> dict[str, str]:
        return {
            'id': identifier,
            'type': kind,
            'label': label,
            'placeholder': placeholder,
        }

    def section(identifier: str, title: str, *questions: dict[str, str]) -> dict[str, Any]:
        return {'id': identifier, 'title': title, 'questions': list(questions)}

    forms: dict[str, list[dict[str, Any]]] = {
        'blank': [
            section(
                'basics', 'Project overview',
                question('goal', 'textarea', 'What do you want to create or accomplish?', 'For example: write a market analysis, build a blog, or complete a course project'),
                question('description', 'textarea', 'Describe the work, its context, and the expected outcome.', 'For example: analyze Q3 sales data and deliver a PDF report with charts'),
            ),
            section(
                'scope', 'Scope and requirements',
                question('requirements', 'textarea', 'What specific requirements or content must be included?', 'For example: analytical charts, a Python backend, and bilingual output'),
                question('out_of_scope', 'textarea', 'What should explicitly be excluded?', 'For example: no user interface and no real-time updates'),
            ),
            section(
                'resources', 'Resources and constraints',
                question('resource', 'text', 'What resources or source materials are available?', 'For example: a code repository, dataset, reference documents, or designs'),
                question('tech', 'text', 'Do you prefer any tools, technology stack, or platform?', 'For example: Python, LaTeX, Figma, or GitHub Pages'),
            ),
            section(
                'timeline', 'Timeline',
                question('deadline', 'text', 'When should this be complete? Are there key dates?', 'For example: before next Friday'),
                question('milestones', 'textarea', 'What interim delivery milestones are needed?', 'For example: first draft by Wednesday and final version by Friday'),
            ),
        ],
        'product': [
            section(
                'basics', 'Product overview',
                question('goal', 'textarea', 'What is the product’s core objective?', 'For example: build a collaboration tool that improves cross-team task management'),
                question('problem', 'textarea', 'What user problem should it solve?', 'For example: scattered tasks, unclear progress, and high communication overhead'),
                question('users', 'text', 'Who are the target users?', 'For example: product managers and developers in small teams'),
            ),
            section(
                'scope', 'Feature planning',
                question('features', 'textarea', 'What are the core features and their priorities?', 'For example: task board (P0), progress reports (P1), notifications (P2)'),
                question('mvp', 'textarea', 'Which features must the MVP include?', 'For example: sign-in, task creation and assignment, and a board view'),
            ),
            section(
                'resources', 'Resources and timing',
                question('team', 'text', 'What is the team size and role mix?', 'For example: 2 frontend engineers, 2 backend engineers, and 1 designer'),
                question('tech', 'text', 'What technology stack has been selected?', 'For example: React, Node.js, and PostgreSQL'),
                question('deadline', 'text', 'When is the planned launch?', 'For example: deliver the MVP within 8 weeks'),
            ),
            section(
                'quality', 'Quality and acceptance',
                question('standard', 'textarea', 'What quality requirements or acceptance standards apply?', 'For example: page loads under 2 seconds, tested core flows, and WCAG accessibility'),
            ),
        ],
        'pm': [
            section(
                'basics', 'Project overview',
                question('goal', 'textarea', 'What is this project’s objective?', 'For example: redesign the company website to improve brand perception and conversion'),
                question('stakeholders', 'text', 'Who are the key stakeholders or partners?', 'For example: marketing, the design team, and an external development vendor'),
            ),
            section(
                'scope', 'Scope and tasks',
                question('deliverables', 'textarea', 'What are the main deliverables?', 'For example: redesigned website pages, a CMS, and deployment documentation'),
                question('deps', 'textarea', 'What external dependencies or prerequisites exist?', 'For example: final designs and third-party API credentials'),
            ),
            section(
                'team', 'Team and collaboration',
                question('team', 'text', 'How is the team organized, and how will it collaborate?', 'For example: five internal contributors plus a consultant, with daily stand-ups and weekly reports'),
                question('tools', 'text', 'Which collaboration tools and platforms will be used?', 'For example: Jira, Confluence, Slack, and GitHub'),
            ),
            section(
                'timeline', 'Timeline and risks',
                question('deadline', 'text', 'What are the key milestones and deadlines?', 'For example: design approval in week 4 and launch in week 8'),
                question('risks', 'textarea', 'What known risks or blockers exist?', 'For example: limited design capacity or uncertain third-party API reliability'),
            ),
        ],
        'knowledge': [
            section(
                'direction', 'Research direction',
                question('goal', 'textarea', 'What specific direction do you want to research?', 'For example: improving LLM-based methods for molecular-dynamics simulation'),
                question('scenario', 'textarea', 'Which tasks, scenarios, or applications does this direction target?', 'For example: more efficient conformational sampling in drug discovery'),
            ),
            section(
                'problem', 'Problem definition',
                question('problem', 'textarea', 'Which problem should be addressed first?', 'For example: insufficient sampling efficiency for long-timescale conformational changes'),
                question('gap', 'textarea', 'What is the clearest limitation of existing approaches?', 'For example: high compute cost, weak rare-event sampling, or limited interpretability'),
            ),
            section(
                'conditions', 'Current foundation',
                question('basis', 'textarea', 'What information or foundation do you already have?', 'For example: papers, ideas, data, code, or experimental results'),
                question('resources', 'text', 'What resources or constraints apply?', 'For example: data, compute, time, tools, or publication targets'),
            ),
            section(
                'output', 'Expected outcome',
                question('outcome', 'textarea', 'What final outcome do you want?', 'For example: a research plan, experimental results, a paper draft, or a code prototype'),
                question('min_requirement', 'textarea', 'What is the minimum acceptable result?', 'For example: measurable improvement, reproducible experiments, or a submission-ready result'),
            ),
        ],
        'ai': [
            section(
                'basics', 'Project overview',
                question('goal', 'textarea', 'What do you want to build, and what is its core capability?', 'For example: a code-review assistant that checks pull requests and suggests improvements'),
                question('users', 'text', 'Who will use it, and in what situation?', 'For example: a development team when a pull request is opened'),
            ),
            section(
                'capability', 'Capability design',
                question('tools', 'textarea', 'Which capabilities or tool calls are required?', 'For example: read source files, run linting, consult documentation, and comment on pull requests'),
                question('knowledge', 'textarea', 'Which knowledge or context should it use?', 'For example: coding standards, API documentation, and historical pull-request patterns'),
            ),
            section(
                'resources', 'Development resources',
                question('model', 'text', 'Which model or inference service will be used?', 'For example: Claude API, a local open-source model, or Azure OpenAI'),
                question('tech', 'text', 'What are the technology stack and runtime environment?', 'For example: Python, Docker, and GitHub Actions'),
            ),
            section(
                'timeline', 'Plan and delivery',
                question('deadline', 'text', 'When should it become usable?', 'For example: prototype in 2 weeks and production launch in 6 weeks'),
                question('milestones', 'textarea', 'What are the key delivery milestones?', 'For example: core logic in week 2, integration testing in week 4, and launch in week 6'),
            ),
        ],
        'import': [
            section(
                'basics', 'Import overview',
                question('goal', 'textarea', 'What project or content should be imported?', 'For example: import an open-source blog system from GitHub'),
                question('source', 'text', 'What is the source, and what is its current state?', 'For example: a GitHub repository, local folder, or exported archive'),
            ),
            section(
                'scope', 'Import scope',
                question('parts', 'textarea', 'Should all content or only selected parts be imported?', 'For example: import source code and documentation but not commit history'),
                question('adapt', 'textarea', 'What adaptation or refactoring is needed after import?', 'For example: adjust local configuration and update dependency versions'),
            ),
            section(
                'resources', 'Environment and tools',
                question('tech', 'text', 'Which technology stack does the project use?', 'For example: React, Express, and MongoDB'),
                question('env', 'textarea', 'What environment or configuration is required to run it?', 'For example: Node 18+, Docker, and MySQL 8.0'),
            ),
            section(
                'timeline', 'Next steps',
                question('next', 'textarea', 'What should happen after the import?', 'For example: fix known bugs, add tests, and deploy'),
                question('deadline', 'text', 'When should import and adaptation be complete?', 'For example: import this week and finish adaptation next week'),
            ),
        ],
    }
    return forms.get(template, forms['blank'])

def _workbench_default_init_form(project: dict[str, Any] | None=None) -> dict[str, Any]:
    """Return a deterministic onboarding form for empty-workspace projects.

    The section structure and questions are chosen by template so users get
    scoping questions that fit their project type. ``project`` is optional so
    callers that don't have it yet get the generic blank form.
    """
    template = str(project.get('template') or 'blank').strip() if project else 'blank'
    if app_language() == 'en':
        return {
            'generated': False,
            'completed': False,
            'greeting': "Hi! I’m your project setup assistant. I’ll start with a few key questions so I can understand what you need.",
            'sections': _workbench_english_init_sections(template),
            'answers': {},
        }
    greeting = '你好！我是你的项目初始化助理。我先从几个关键问题开始，以便更好地理解你的需求。'
    FORMS: dict[str, dict] = {'blank': {'sections': [{'id': 'basics', 'title': '项目概览', 'questions': [{'id': 'goal', 'type': 'textarea', 'label': '你想做什么？期望达成什么目标？', 'placeholder': '例如：写一份市场分析报告、开发一个博客网站、完成期末作业'}, {'id': 'description', 'type': 'textarea', 'label': '具体描述一下要做的事情，包括背景和期望的结果', 'placeholder': '例如：分析 Q3 的销售数据，输出一份包含图表的 PDF 报告'}]}, {'id': 'scope', 'title': '范围与要求', 'questions': [{'id': 'requirements', 'type': 'textarea', 'label': '有哪些具体要求或内容需要包含？', 'placeholder': '例如：数据分析图表、Python 后端、中英文双语输出'}, {'id': 'out_of_scope', 'type': 'textarea', 'label': '有哪些明确不需要的或排除在外的？', 'placeholder': '例如：不需要用户界面、不需要实时更新'}]}, {'id': 'resources', 'title': '资源与约束', 'questions': [{'id': 'resource', 'type': 'text', 'label': '有哪些可用的资源或输入材料？', 'placeholder': '例如：项目代码仓库、数据集、参考文档、设计稿'}, {'id': 'tech', 'type': 'text', 'label': '是否有偏好的工具、技术栈或平台？', 'placeholder': '例如：Python、LaTeX、Figma、GitHub Pages'}]}, {'id': 'timeline', 'title': '时间计划', 'questions': [{'id': 'deadline', 'type': 'text', 'label': '期望什么时候完成？有没有关键时间点？', 'placeholder': '例如：下周五之前'}, {'id': 'milestones', 'type': 'textarea', 'label': '有哪些阶段性的交付节点？', 'placeholder': '例如：周三前出初稿、周五前完成终版'}]}]}, 'product': {'sections': [{'id': 'basics', 'title': '产品概览', 'questions': [{'id': 'goal', 'type': 'textarea', 'label': '这个产品的核心目标是什么？', 'placeholder': '例如：打造一个团队协作工具，提高跨部门任务管理效率'}, {'id': 'problem', 'type': 'textarea', 'label': '要解决用户的什么痛点？', 'placeholder': '例如：任务分散、进度不透明、沟通成本高'}, {'id': 'users', 'type': 'text', 'label': '目标用户是谁？', 'placeholder': '例如：中小团队的 PM 和开发者'}]}, {'id': 'scope', 'title': '功能规划', 'questions': [{'id': 'features', 'type': 'textarea', 'label': '核心功能有哪些？优先级如何？', 'placeholder': '例如：任务看板（P0）、进度报表（P1）、消息通知（P2）'}, {'id': 'mvp', 'type': 'textarea', 'label': 'MVP 需要包含哪些功能？', 'placeholder': '例如：用户登录、任务创建与指派、看板视图'}]}, {'id': 'resources', 'title': '资源与时间', 'questions': [{'id': 'team', 'type': 'text', 'label': '团队规模和角色是怎样的？', 'placeholder': '例如：2 前端 + 2 后端 + 1 设计'}, {'id': 'tech', 'type': 'text', 'label': '确定的技术栈是什么？', 'placeholder': '例如：React、Node.js、PostgreSQL'}, {'id': 'deadline', 'type': 'text', 'label': '计划什么时候上线？', 'placeholder': '例如：8 周内交付 MVP'}]}, {'id': 'quality', 'title': '质量与验收', 'questions': [{'id': 'standard', 'type': 'textarea', 'label': '有哪些质量要求或验收标准？', 'placeholder': '例如：页面加载 < 2s、核心流程覆盖测试、WCAG 无障碍'}]}]}, 'pm': {'sections': [{'id': 'basics', 'title': '项目概览', 'questions': [{'id': 'goal', 'type': 'textarea', 'label': '这个项目的目标是什么？', 'placeholder': '例如：完成公司官网改版，提升品牌形象和转化率'}, {'id': 'stakeholders', 'type': 'text', 'label': '关键干系人或合作方有哪些？', 'placeholder': '例如：市场部、设计团队、外包开发'}]}, {'id': 'scope', 'title': '范围与任务', 'questions': [{'id': 'deliverables', 'type': 'textarea', 'label': '主要交付物或产出有哪些？', 'placeholder': '例如：新版官网页面、CMS 后台、部署文档'}, {'id': 'deps', 'type': 'textarea', 'label': '有哪些外部依赖或前置条件？', 'placeholder': '例如：需要设计团队先输出视觉稿、第三方 API 密钥'}]}, {'id': 'team', 'title': '团队与协作', 'questions': [{'id': 'team', 'type': 'text', 'label': '团队如何组成？协作方式是什么？', 'placeholder': '例如：5 人内部团队 + 外部顾问，每日站会 + 周报'}, {'id': 'tools', 'type': 'text', 'label': '使用的协作工具和平台有哪些？', 'placeholder': '例如：Jira、Confluence、Slack、GitHub'}]}, {'id': 'timeline', 'title': '时间与风险', 'questions': [{'id': 'deadline', 'type': 'text', 'label': '关键里程碑和截止日期是什么？', 'placeholder': '例如：第 4 周设计定稿、第 8 周上线'}, {'id': 'risks', 'type': 'textarea', 'label': '已知的风险或阻塞项有哪些？', 'placeholder': '例如：设计资源紧张、第三方 API 稳定性未知'}]}]}, 'knowledge': {'sections': [{'id': 'direction', 'title': '研究方向', 'questions': [{'id': 'goal', 'type': 'textarea', 'label': '你当前想研究的具体方向是什么？', 'placeholder': '例如：基于大语言模型的分子动力学模拟方法优化'}, {'id': 'scenario', 'type': 'textarea', 'label': '这个方向主要面向什么任务、场景或应用？', 'placeholder': '例如：药物分子筛选中的构象采样效率提升'}]}, {'id': 'problem', 'title': '问题定位', 'questions': [{'id': 'problem', 'type': 'textarea', 'label': '你希望优先解决什么问题？', 'placeholder': '例如：现有 MD 模拟方法在长时程构象变化上的采样效率不足'}, {'id': 'gap', 'type': 'textarea', 'label': '你认为现有方法最明显的不足是什么？', 'placeholder': '例如：计算成本高、对稀有事件的采样不足、缺乏可解释性'}]}, {'id': 'conditions', 'title': '现有条件', 'questions': [{'id': 'basis', 'type': 'textarea', 'label': '你目前已有的信息或基础是什么？', 'placeholder': '例如：论文、想法、数据、代码、实验结果'}, {'id': 'resources', 'type': 'text', 'label': '你有哪些可用资源或限制？', 'placeholder': '例如：数据、算力、时间、工具、投稿目标'}]}, {'id': 'output', 'title': '最终产出', 'questions': [{'id': 'outcome', 'type': 'textarea', 'label': '你希望最终形成什么成果？', 'placeholder': '例如：研究方案、实验结果、论文初稿、代码原型'}, {'id': 'min_requirement', 'type': 'textarea', 'label': '你对结果有什么最低要求？', 'placeholder': '例如：指标提升、可复现实验、能投稿、能开题'}]}]}, 'ai': {'sections': [{'id': 'basics', 'title': '项目概览', 'questions': [{'id': 'goal', 'type': 'textarea', 'label': '你想构建什么？它的核心能力是什么？', 'placeholder': '例如：一个代码审查助手，能自动检查 PR 并给出改进建议'}, {'id': 'users', 'type': 'text', 'label': '谁会用？在什么场景下使用？', 'placeholder': '例如：开发团队，在提 PR 时自动触发'}]}, {'id': 'capability', 'title': '能力设计', 'questions': [{'id': 'tools', 'type': 'textarea', 'label': '需要具备哪些能力或工具调用？', 'placeholder': '例如：读取代码文件、调用 Lint 工具、查询文档、评论 PR'}, {'id': 'knowledge', 'type': 'textarea', 'label': '需要参考哪些知识或上下文？', 'placeholder': '例如：项目编码规范、API 文档、历史 PR 模式'}]}, {'id': 'resources', 'title': '开发资源', 'questions': [{'id': 'model', 'type': 'text', 'label': '使用什么模型或推理服务？', 'placeholder': '例如：Claude API、本地开源模型、Azure OpenAI'}, {'id': 'tech', 'type': 'text', 'label': '技术栈和运行环境是什么？', 'placeholder': '例如：Python、Docker、GitHub Actions'}]}, {'id': 'timeline', 'title': '计划与交付', 'questions': [{'id': 'deadline', 'type': 'text', 'label': '期望什么时候可用？', 'placeholder': '例如：2 周出原型、6 周正式上线'}, {'id': 'milestones', 'type': 'textarea', 'label': '有哪些重要的交付节点？', 'placeholder': '例如：第 2 周核心逻辑完成、第 4 周集成测试、第 6 周上线'}]}]}, 'import': {'sections': [{'id': 'basics', 'title': '导入概览', 'questions': [{'id': 'goal', 'type': 'textarea', 'label': '导入的项目或内容是什么？', 'placeholder': '例如：从 GitHub 导入一个开源博客系统'}, {'id': 'source', 'type': 'text', 'label': '来源是什么？目前的状态如何？', 'placeholder': '例如：GitHub 仓库、本地文件夹、导出文件'}]}, {'id': 'scope', 'title': '导入范围', 'questions': [{'id': 'parts', 'type': 'textarea', 'label': '需要导入全部内容还是部分内容？', 'placeholder': '例如：只导入源码和文档，不需要导入历史提交'}, {'id': 'adapt', 'type': 'textarea', 'label': '导入后需要做哪些适配或改造？', 'placeholder': '例如：修改配置为本地环境、更新依赖版本'}]}, {'id': 'resources', 'title': '环境与工具', 'questions': [{'id': 'tech', 'type': 'text', 'label': '项目使用的技术栈是什么？', 'placeholder': '例如：React、Express、MongoDB'}, {'id': 'env', 'type': 'textarea', 'label': '运行需要哪些环境或配置？', 'placeholder': '例如：Node 18+、Docker、MySQL 8.0'}]}, {'id': 'timeline', 'title': '后续计划', 'questions': [{'id': 'next', 'type': 'textarea', 'label': '导入完成后的下一步计划是什么？', 'placeholder': '例如：修复已知 bug、补充测试、部署上线'}, {'id': 'deadline', 'type': 'text', 'label': '期望什么时候完成导入和适配？', 'placeholder': '例如：本周内完成导入，下周完成适配'}]}]}}
    form = FORMS.get(template, FORMS['blank'])
    return {'generated': False, 'completed': False, 'greeting': greeting, 'sections': form['sections'], 'answers': {}}

def _workbench_new_init_session(project_id: str, project: dict[str, Any], now: str | None=None) -> dict[str, Any]:
    now = now or _utc_now_iso()
    session = _workbench_new_session(
        project_id,
        localized('Set up project', '初始化项目'),
        localized(
            'Complete the project’s basic setup and initial planning.',
            '完成项目的基础设置与初始规划。',
        ),
        now,
        kind='init',
        status='initializing',
    )
    form = _workbench_default_init_form(project)
    session['init'] = form
    session['agentReply'] = form['greeting']
    return session

workbench_project_data_key = _workbench_project_data_key


__all__ = ['workbench_project_data_key']
