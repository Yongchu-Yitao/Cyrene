from cyrene.runtime.onboarding_context_service import (
    OnboardingContextApplicationService,
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
    service = OnboardingContextApplicationService(resolver)
    assert service.context_state()["workspace_dir"] == "/workspace/active"
