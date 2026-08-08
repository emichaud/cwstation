"""SmallStack search — full-text search over opted-in CRUDViews.

Public helpers for keeping the index correct after bulk writes and for
indexing opaque identifiers. See :mod:`apps.search.maintenance`.
"""

from .maintenance import digits_search, reindex_instances

__all__ = ["reindex_instances", "digits_search"]
