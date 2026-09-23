from __future__ import annotations


class GraphRepositoryError(RuntimeError):
    """Base class for safe internal graph repository failures."""


class GraphEntityNotFound(GraphRepositoryError):
    """No entity matched an owner-scoped graph lookup."""


class GraphEntityAmbiguous(GraphRepositoryError):
    """An exact graph name matched more than one owner-scoped entity."""


class GraphOwnershipError(GraphRepositoryError):
    """A graph row or its evidence is not owned by the supplied user."""


class GraphInvalidTraversal(GraphRepositoryError, ValueError):
    """A graph traversal request exceeds policy or has invalid parameters."""


class GraphWriteConflict(GraphRepositoryError):
    """An idempotent graph write replay conflicts with previously stored values."""


class GraphQueryTimeout(GraphRepositoryError):
    """A bounded graph read exceeded its configured deadline."""


class GraphQueryCancelled(GraphRepositoryError):
    """A graph read was cancelled before its result could be published."""
