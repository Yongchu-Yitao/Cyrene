"""Composition of thin behavior-learning HTTP adapters."""

from fastapi import APIRouter

from cyrene.learning.application_service import LearningApplicationService
from route.learning_routes.process import register_learning_process_routes
from route.learning_routes.queries import register_learning_query_routes
from route.learning_routes.skill_commands import register_skill_command_routes
from route.learning_routes.skill_queries import register_skill_query_routes


def register_learning_routes(
    router: APIRouter, application_service: LearningApplicationService
) -> None:
    register_learning_query_routes(router, application_service)
    register_skill_query_routes(router, application_service)
    register_skill_command_routes(router, application_service)
    register_learning_process_routes(router, application_service)


__all__ = ["register_learning_routes"]
