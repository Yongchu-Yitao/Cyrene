"""
Personality setup wizard. Runs once on first startup.
Sets up SOUL.md with user's chosen personality.
"""

import logging
from pathlib import Path

from agent.plugin import active_plugin_service
from cyrene.config import DATA_DIR

logger = logging.getLogger(__name__)


def _memory_service():
    service = active_plugin_service("memory")
    if service is None:
        raise RuntimeError("memory Plugin is not available")
    return service


def _web_search_service():
    service = active_plugin_service("web_search")
    search = getattr(service, "search", None)
    if not callable(search):
        raise RuntimeError("content Plugin web-search service is not available")
    return service


def get_soul_path() -> Path:
    return _memory_service().soul_path()


def get_default_soul_content() -> str:
    return _memory_service().default_soul()

_SETUP_FLAG = None  # Path to .setup_done


def init_setup_flag():
    global _SETUP_FLAG
    _SETUP_FLAG = DATA_DIR / ".setup_done"


def is_setup_done() -> bool:
    """Check if personality setup has been completed."""
    if _SETUP_FLAG is None:
        return True  # 未初始化时默认已完成，避免阻塞
    return _SETUP_FLAG.exists()


def mark_setup_done() -> None:
    """Create the .setup_done flag file."""
    global _SETUP_FLAG
    if _SETUP_FLAG is None:
        _SETUP_FLAG = DATA_DIR / ".setup_done"
    try:
        _SETUP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_FLAG.write_text("setup_complete", encoding="utf-8")
    except Exception:
        logger.exception("Failed to mark setup done")


async def run_setup() -> None:
    """Run the interactive personality setup wizard."""
    print()
    print("=" * 50)
    print("欢迎使用 Cyrene！")
    print("=" * 50)
    print()
    print("你可以为 Cyrene 注入一个人格，让它模仿某个特定人物的性格和说话方式。")
    print()
    print("请选择：")
    print("  1) 输入一个名字 → 我上网查这个人的生平、说话方式，然后模仿")
    print("  2) 自己编写 SOUL.md（人格文件）")
    print("  3) 跳过，使用默认人格")
    print()

    choice = input("选择 (1/2/3): ").strip()

    if choice == "1":
        name = input("请输入名字（可以是真实人物或虚构角色）: ").strip()
        if name:
            await _setup_from_name(name)
        else:
            print("名字不能为空，使用默认人格。")
    elif choice == "2":
        await _setup_custom()
    else:
        print("使用默认人格。")

    mark_setup_done()
    print()
    print("设置完成！你可以开始和 Cyrene 聊天了。")
    print()


async def _setup_from_name(name: str) -> None:
    """搜索人物信息 + 生成行为人格文件。"""
    print(f"\n正在搜索 {name} 的信息...")
    await create_soul_profile_from_name(name)
    print(f"已为 {name} 创建人格文件！")
    print(f"   文件: {get_soul_path()}")


async def _generate_soul_profile(name: str, bio: str = "", style: str = "", lang: str = "") -> str:
    """用搜索结果 + 模型知识生成行为人格文件。"""
    from agent.plugin import active_plugin_service
    from cyrene.model_runtime.messages import assistant_text

    research_section = ""
    if bio:
        research_section += f"\n搜索到的生平信息：\n{bio[:2000]}\n"
    if style:
        research_section += f"\n搜索到的说话方式信息：\n{style[:2000]}\n"
    if research_section:
        research_section = "\n参考以下搜索结果（如果搜索结果为空或不相关，请忽略）：" + research_section

    prompt = f"""Create a BEHAVIOR PROFILE for an AI that will personify {name}.{research_section}

This is NOT a biography. This is a set of behavioral rules that tells the AI exactly how to speak and act like {name}.

Use your knowledge about this person, supplemented by the search results above. Focus on what makes them unique.

Include EXACTLY these sections:

## CORE IDENTITY
- Who they are in 1 sentence
- Their archetype

## SPEECH PATTERNS
- Sentence structure (short/long, fragmented/complete)
- Verbal tics and fillers
- Signature phrases and catchphrases (exact quotes)
- Vocabulary and tone

## CONTRADICTIONS
- Things they say then immediately deny
- Self-defeating logic loops
- The "30-second self-destruct" pattern

## FIXED MISTAKES
- Words they consistently mispronounce or misspell
- Numbers they always get wrong
- Facts they consistently misremember

## BEHAVIORAL LOOPS
- Circular argument patterns
- Go-to deflection strategies
- Repetitive question cycles

## CLASSIC EXCHANGES
- 2-3 example dialogues showing how they respond to common questions

Write in concise bullet points with exact example quotes. No markdown formatting.
    Write the ENTIRE profile in English."""

    try:
        gateway = active_plugin_service("model")
        if gateway is None:
            raise RuntimeError("Model Provider Plugins are not available")
        response = await gateway.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You create character behavior profiles. Focus on exact "
                        "speech patterns and behavioral quirks. Use concrete examples."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            route="secondary",
            caller="personality_setup",
            session_id=f"personality:{name}",
        )
        result = assistant_text(response) or ""
        if result and len(result) > 100:
            return f"# {name}'s Soul\n\n{result}"
    except Exception:
        pass

    return f"""# {name}'s Soul

## CORE IDENTITY
- I personify {name}.

## SPEECH PATTERNS
- Speaking style modeled after {name}.
"""


async def create_soul_profile_from_name(name: str) -> str:
    """Create and persist a generated SOUL.md for the given personality name."""
    search = _web_search_service().search
    bio = await search(
        f"{name} 生平 人物经历 背景",
        detail="content",
        max_results=5,
    )
    style = await search(
        f"{name} 说话方式 语录 口头禅 名场面 梗",
        detail="content",
        max_results=5,
    )
    soul_content = await _generate_soul_profile(name, bio, style)
    _memory_service().write_soul(soul_content)
    return soul_content


async def _setup_custom() -> None:
    """让用户自己提供 SOUL.md。"""
    print("\n请粘贴或输入 SOUL.md 的内容（输入完成后，在新的一行输入 END 并回车）：")
    print("（SOUL.md 定义了 AI 的人格、信念、说话方式等）")
    print()

    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)

    content = "\n".join(lines).strip()
    if content:
        _memory_service().write_soul(normalize_custom_soul_content(content))
        print("已写入自定义人格文件！")
    else:
        print("内容为空，使用默认人格。")


def normalize_custom_soul_content(content: str) -> str:
    """Normalize custom SOUL content into a valid markdown document."""
    clean = content.strip()
    if not clean:
        return get_default_soul_content()
    if not clean.startswith("# "):
        clean = "# Custom Soul\n\n" + clean
    return clean
