"""Shared fixtures.

Deliberately thin. Most tests build the two or three records they need inline,
because a test whose setup lives in another file is a test whose failure sends
the reader on a hunt. The helpers here are the ones that would otherwise be
retyped in thirty places.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from srd.events import LoggedEvent, ObservedEvent  # noqa: E402


@pytest.fixture
def make_logged():
    def _make(offset: int, event_time_ms: int, *, key: str = "k", partition: int = 0, payload: int = 1) -> LoggedEvent:
        return LoggedEvent(
            offset=offset,
            partition=partition,
            key=key,
            event_time_ms=event_time_ms,
            payload=payload,
        )

    return _make


@pytest.fixture
def make_observed(make_logged):
    def _make(
        offset: int,
        event_time_ms: int,
        arrival_ms: int,
        *,
        key: str = "k",
        partition: int = 0,
        payload: int = 1,
    ) -> ObservedEvent:
        return ObservedEvent(
            logged=make_logged(offset, event_time_ms, key=key, partition=partition, payload=payload),
            arrival_ms=arrival_ms,
        )

    return _make
