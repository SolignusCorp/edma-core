"""Core test helpers: fake port objects + registry registration (auto-undo)."""

import pytest


def reg_fake_search(mp, A, FSE):
    """FSE (fake search engine: .search(q,n)) → web.search plugin registration.
    mp.setitem — test tugagach registry avtomatik tiklanadi."""
    from edma_core import contrib
    mp.setitem(A.ACTION_REGISTRY, "web.search",
               contrib.make_web_search_spec(FSE.search, spend_fn=lambda *a, **k: None))


def fake_dataset_spec(handler):
    """dataset.create spec (human-approval gate bilan) test uchun."""
    from edma_core.actions import ActionSpec
    return ActionSpec(
        name="dataset.create", description="test stub",
        schema={"topic": (str, 1, 80, True), "size": (int, 10, 2000, False),
                "language": (str, 2, 2, False)},
        risk="high", requires_human=True, cost_estimate=0.02, handler=handler)
