from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_existing_item_action_menus_are_available_from_right_click():
    shell = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")
    chat = (ROOT / "src/webui/frontend/workbench-chat.jsx").read_text(
        encoding="utf-8"
    )
    knowledge = (ROOT / "src/webui/frontend/workbench-knowledge.jsx").read_text(
        encoding="utf-8"
    )

    project_card = shell.split('className={"workbench-project-card"', 1)[1].split(
        "</div>", 1
    )[0]
    task_board_card = shell.split('className={"wb-board-card is-"', 1)[1].split(
        "</article>", 1
    )[0]
    task_rail_card = shell.split('className={"workbench-task-card"', 1)[1].split(
        "</div>", 1
    )[0]
    chat_card = chat.split('className={"wbc-chat-card"', 1)[1].split("</div>", 1)[0]
    knowledge_card = knowledge.split('className: "wb-kb-card"', 1)[1].split(
        'role: "button"', 1
    )[0]

    for item in (project_card, task_board_card, task_rail_card, chat_card):
        assert "onContextMenu=" in item
        assert "event.preventDefault();" in item
        assert "event.stopPropagation();" in item

    assert "onContextMenu:" in knowledge_card
    assert "e.preventDefault();" in knowledge_card
    assert "e.stopPropagation();" in knowledge_card
    assert 'e.type === "contextmenu"' in knowledge
    assert 'setOpenMenu("card:" + d.id)' in knowledge
