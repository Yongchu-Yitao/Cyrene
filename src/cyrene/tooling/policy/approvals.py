"""Approval-policy helpers."""

from cyrene.tooling import runtime_support as _implementation

__all__ = [
    "_request_destructive_confirmation",
    "_request_external_delivery_confirmation",
    "_request_external_upload_confirmation",
    "_request_read_elevation",
    "_request_scope_elevation",
    "_request_write_elevation",
    "request_destructive_confirmation",
    "request_external_delivery_confirmation",
    "request_external_upload_confirmation",
    "request_read_elevation",
    "request_self_configuration_confirmation",
    "request_host_lifecycle_confirmation",
    "request_scope_elevation",
    "request_write_elevation",
]

request_destructive_confirmation = _implementation._request_destructive_confirmation
request_external_delivery_confirmation = _implementation._request_external_delivery_confirmation
request_external_upload_confirmation = _implementation._request_external_upload_confirmation
request_read_elevation = _implementation._request_read_elevation
request_self_configuration_confirmation = _implementation._request_self_configuration_confirmation
request_host_lifecycle_confirmation = _implementation._request_host_lifecycle_confirmation
request_scope_elevation = _implementation._request_scope_elevation
request_write_elevation = _implementation._request_write_elevation

_request_destructive_confirmation = request_destructive_confirmation
_request_external_delivery_confirmation = request_external_delivery_confirmation
_request_external_upload_confirmation = request_external_upload_confirmation
_request_read_elevation = request_read_elevation
_request_self_configuration_confirmation = request_self_configuration_confirmation
_request_host_lifecycle_confirmation = request_host_lifecycle_confirmation
_request_scope_elevation = request_scope_elevation
_request_write_elevation = request_write_elevation
