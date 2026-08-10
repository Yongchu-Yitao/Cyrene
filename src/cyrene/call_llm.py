"""Stable module alias for the model runtime client.

The alias preserves module-level caches and monkeypatch behavior for existing
callers while the implementation lives in :mod:`cyrene.model_runtime.client`.
"""

from __future__ import annotations

import sys

from cyrene.model_runtime import client as _client

sys.modules[__name__] = _client
