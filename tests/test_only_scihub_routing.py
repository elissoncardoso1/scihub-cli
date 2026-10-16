"""Regression: --only-scihub routing must reach Sci-Hub even for year>=threshold DOIs."""

import logging

from scihub_cli.core.source_manager import SourceManager


class _StubSource:
    name = "Sci-Hub"

    def can_handle(self, identifier: str) -> bool:
        return True


def test_only_scihub_routes_recent_dois_to_scihub(caplog):
    manager = SourceManager(sources=[_StubSource()], year_threshold=2021)
    with caplog.at_level(logging.INFO):
        chain = manager.get_source_chain("10.1002/jaba.908")
    assert [s.name for s in chain] == ["Sci-Hub"]


def test_only_scihub_routes_old_dois_to_scihub(caplog):
    manager = SourceManager(sources=[_StubSource()], year_threshold=2021)
    chain = manager.get_source_chain("10.1016/j.lmot.2020.101694")
    assert [s.name for s in chain] == ["Sci-Hub"]
