"""Keep the backend suite network-free even when a developer has provider credentials."""

import pytest


@pytest.fixture(autouse=True)
def disable_live_official_source_provider(monkeypatch):
    from backend.main import app

    monkeypatch.setattr(app.state, "official_source_retriever", None)
