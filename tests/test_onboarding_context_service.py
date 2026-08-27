from agent.plugin.plugin_impl.cyrene_composer_context.application import (
    ComposerContextService,
    ProjectResolver,
)


def test_active_project_resolver(tmp_path):
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
    service = ComposerContextService(
        type("Registry", (), {"list_packs": lambda self: []})(),
        projects=resolver,
        service_resolver=lambda _name: None,
    )
    assert service.context_state()["workspace_dir"] == "/workspace/active"
