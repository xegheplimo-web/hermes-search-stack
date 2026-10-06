"""Tests for ``gateway.protocols`` dataclasses and the backend seam."""

from __future__ import annotations

import pytest

from gateway.backends.stub import StubBackend
from gateway.protocols import EvidenceItem, ExtractItem, SearchItem


def test_search_item_defaults():
    item = SearchItem(title="T", url="https://x.test")
    assert item.title == "T"
    assert item.url == "https://x.test"
    assert item.description == ""
    assert item.position == 0


def test_extract_item_defaults():
    item = ExtractItem(url="https://x.test")
    assert item.title == ""
    assert item.content == ""
    assert item.error is None


def test_evidence_item_shape():
    item = EvidenceItem(id=3, title="T", url="https://x.test", content="body")
    assert item.id == 3
    assert item.content == "body"


def test_dataclasses_are_slotted():
    with pytest.raises(AttributeError):
        SearchItem(title="T", url="u").nonexistent = 1
    with pytest.raises(AttributeError):
        ExtractItem(url="u").nonexistent = 1
    with pytest.raises(AttributeError):
        EvidenceItem(id=1, title="t", url="u").nonexistent = 1


def test_stub_backend_satisfies_protocol():
    """Duck-type check: StubBackend implements the SearchBackend protocol."""
    backend = StubBackend()
    assert backend.name == "stub"
    items = backend.search("anything", max_results=3)
    assert len(items) == 3
    assert all(isinstance(i, SearchItem) for i in items)
    assert [i.position for i in items] == [1, 2, 3]
    extracted = backend.extract([items[0].url])
    assert len(extracted) == 1
    assert isinstance(extracted[0], ExtractItem)
    assert extracted[0].content  # echoes deterministic content
    ping = backend.ping()
    assert ping == {"ok": True, "detail": "stub backend (deterministic, offline)"}


def test_stub_backend_determinism():
    a, b = StubBackend(), StubBackend()
    assert a.search("q") == b.search("q")
    assert a.extract(["https://x.test/1"]) == b.extract(["https://x.test/1"])


def test_stub_backend_extract_errors():
    backend = StubBackend(extract_errors={"https://bad.test": "boom"})
    (item,) = backend.extract(["https://bad.test"])
    assert item.error == "boom"
    assert item.content == ""
