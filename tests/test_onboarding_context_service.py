from cyrene.runtime.onboarding_context_service import ProjectResolver, SoulRepository


def test_soul_repository_and_active_project_resolver(tmp_path):
    soul_path = tmp_path / "SOUL.md"
    soul = SoulRepository(soul_path)
    assert soul.read() == ""
    soul.write("# Persona\n")
    assert soul.read() == "# Persona\n"

    resolver = ProjectResolver(
        lambda: {
            "activeProjectId": "project-a",
            "projects": [
                {"id": "project-a", "workspacePath": "/workspace/active"},
                {"id": "project-b", "workspacePath": "/workspace/other"},
            ],
        },
        tmp_path,
    )
    assert resolver.active_workspace() == "/workspace/active"
