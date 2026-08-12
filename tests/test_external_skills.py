def test_external_skill_prompt_discloses_only_names_and_ids(monkeypatch):
    from cyrene.learning import skills

    first = "A" * 25_000
    second = "B" * 25_000
    monkeypatch.setattr(
        skills,
        "build_skills",
        lambda: [
            {
                "id": "first",
                "name": "First",
                "desc": "first skill",
                "enabled": True,
                "entrypoint_name": "SKILL.md",
                "preview": first,
            },
            {
                "id": "second",
                "name": "Second",
                "desc": "second skill",
                "enabled": True,
                "entrypoint_name": "SKILL.md",
                "preview": second,
            },
        ],
    )

    prompt = skills.build_skill_prompt_block()

    assert "First (ID: first)" in prompt
    assert "Second (ID: second)" in prompt
    assert first not in prompt
    assert second not in prompt
    assert "first skill" not in prompt
    assert "LoadSkill" in prompt


def test_read_skill_text_defaults_to_the_complete_file(tmp_path):
    from cyrene.learning.skills import read_skill_text

    content = "技能说明" * 10_000
    path = tmp_path / "SKILL.md"
    path.write_text(content, encoding="utf-8")

    assert read_skill_text(path) == content
