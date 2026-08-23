"""Live Microsoft Office bridge used by Cyrene's progressive tools."""

from cyrene.office.service import (
    OfficeBridgeError,
    OfficeBridgeService,
    get_office_bridge,
)

__all__ = ["OfficeBridgeError", "OfficeBridgeService", "get_office_bridge"]
