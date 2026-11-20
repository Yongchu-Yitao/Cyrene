"""Fingerprint the effective system instructions so preference edits apply next turn."""

from cyrene.core.hook import SESSION_START, with_session_start_cache_fingerprint
from cyrene.core.plugin import PluginSetupContext
from cyrene.platform import settings_store

DETAIL = {
    "concise": "先给出答案或下一步行动。只补充会影响用户判断或执行的信息，包括关键条件和必要步骤。用户要的是成品时，直接给完整成品，压缩随附说明。",
    "balanced": "先给出答案，再解释主要理由。操作类问题给出足够执行的步骤；选择类问题说明最重要的取舍。背景和示例只在有助于理解时加入。",
    "detailed": "先让用户看到结论，再展开依据、步骤和重要取舍。对于复杂或陌生的概念，给一个具体例子；对于需要执行的方案，说明如何判断结果是否符合预期。简单问题按其实际复杂度回答。",
}
TONE = {
    "direct": "开门见山，用明确、平实的措辞表达判断和建议。发现错误时，直接指出具体问题并给出修正。证据不足时，明确说明哪里还不能确定。",
    "natural": "像与熟悉的同事交谈一样回答。接住用户刚说的具体内容，用日常、流畅的措辞继续交流。根据话题轻重调整表达，保留必要的专业术语。",
    "warm": "用耐心、友善的措辞回答。用户明确表达困惑或挫折时，先用一句话回应具体难处，再继续解决问题。纠正错误时说明原因和可行做法。普通事实问题直接作答即可。",
}
COMMON = (
    "以下是面向用户表达时的默认偏好。用户对本次任务明确指定的篇幅、语气、格式或受众优先。"
    "完整交付用户要求的内容，并保留影响结论的重要条件与不确定性。\n"
    "按事实回答。区分已确认的信息和推测，不确定时具体说明哪里不确定。避免过度防御性说明："
    "只有当限制、风险或例外会影响用户的判断或行动时，才简短说明。不要反复免责声明，也不要预先反驳用户没有提出的质疑。"
    "优先使用用户熟悉的词和通行术语；确实需要专业术语时，首次出现用一句通俗的话解释。不要自造概念或使用含义模糊的行话。"
)


def style_prompt():
    lines = ["<response_style>", COMMON]
    for key, label, choices in (("reply_detail", "详略", DETAIL), ("reply_tone", "语气", TONE)):
        instruction = choices.get(str(settings_store.get(key, "inherit")))
        if instruction:
            lines.append(f"{label}：{instruction}")
    return "\n".join([*lines, "</response_style>"])


def setup_response_style(context: PluginSetupContext) -> None:
    if context.agent_id != "main":
        return

    async def mount(_event):
        return {"context": style_prompt(), "context_position": "system",
                "context_kind": "response_style", "context_source": "cyrene_response_style"}

    with_session_start_cache_fingerprint(mount, lambda _event: style_prompt())
    hook_id = "cyrene-response-style-session-start"
    plugin_id = "cyrene_response_style.mount"
    if any(hook.id == hook_id for hook in context.hooks.list()):
        context.hooks.bind_plugin(plugin_id, mount, replace=True)
    else:
        context.hooks.register(SESSION_START, mount, plugin_id=plugin_id,
                               hook_id=hook_id, root_only=True, failure_policy="closed")
