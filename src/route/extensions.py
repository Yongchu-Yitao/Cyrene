"""Composition of thin Extension Center HTTP adapters."""

from fastapi import APIRouter

from cyrene.extensions.application_service import ExtensionApplicationService
from route.extension_routes.admin import register_admin_routes
from route.extension_routes.catalog import register_catalog_routes
from route.extension_routes.lifecycle import register_lifecycle_routes
from route.extension_routes.skills import register_skill_routes


def register_extension_routes(
    router: APIRouter, application_service: ExtensionApplicationService
) -> None:
    register_skill_routes(router, application_service)
    register_catalog_routes(router, application_service)
    register_lifecycle_routes(router, application_service)
    register_admin_routes(router, application_service)


__all__ = ["register_extension_routes"]
