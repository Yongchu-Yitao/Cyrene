"""Errors raised by the context tree component."""


class ContextError(RuntimeError):
    """Base class for context tree errors."""


class TreeNotFoundError(ContextError):
    """Raised when a context tree does not exist."""


class NodeNotFoundError(ContextError):
    """Raised when a node does not exist in the requested tree."""


class NodeHasChildrenError(ContextError):
    """Raised when a non-recursive delete targets a non-leaf node."""


class RootDeletionError(ContextError):
    """Raised when ``delete_node`` is used on a tree root."""


class ContextValueError(ContextError, ValueError):
    """Raised when a node value cannot be stored losslessly as JSON."""
