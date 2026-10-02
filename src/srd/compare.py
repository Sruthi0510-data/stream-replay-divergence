"""Diffing two runs, and saying which mechanism produced each difference.

A diff that says "4,120 rows differ" starts an argument. A diff that says which
of three mechanisms produced each differing row ends one, because each mechanism
has a different owner and a different fix.

  LATE_DROP       the two runs disagree about which records existed. One of them
                  discarded records the other kept, so counts and sums differ.
                  Owned by whoever chose the allowed lateness.

  TIE_ORDER       the two runs agree on every record and still disagree on the
                  answer, because the aggregate asked "which was last" and two
                  records carried the same stamp. Resolved by processing order,
                  which is arrival order live and offset order on a replay.
                  Owned by whoever wrote the query; a last-value column over a
                  coarse timestamp has no defined answer and the engine is not
                  at fault for picking one.

  SESSION_MERGE   the two runs disagree about how many sessions there were,
                  because a session boundary is a processing-time gap and a
                  replay has no gaps. Owned by whoever decided that a
                  processing-time timer was a reasonable way to segment a
                  business event.

The taxonomy is closed over what this job can produce and no wider. ADR-005
lists the mechanisms a real engine has that this one does not, because a
taxonomy that quietly claims to be complete is worse than one that says where it
stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from srd.job import RunResult, SessionRow, WindowRow

__all__ = [
    "Arm",
    "RowDiff",
    "SessionDiff",
    "Divergence",
    "compare",
]


class Arm(StrEnum):
    """Why one row differs between the two runs."""

    LATE_DROP = "late-drop"
    TIE_ORDER = "tie-order"
    SESSION_MERGE = "session-merge"
    ONLY_ORIGINAL = "only-original"
    ONLY_REPLAY = "only-replay"


@dataclass(frozen=True, slots=True)
class RowDiff:
    """One aggregate row that the two runs do not agree on."""

    window_start_ms: int
    key: str
    arm: Arm
    original: WindowRow | None
    replay: WindowRow | None

    def count_delta(self) -> int:
        """How many records the replay saw that the original did not.

        Signed, and defined as zero when either side is absent, because a row
        that exists on one side only has no delta to speak of and returning the
        whole count would overstate the movement.
        """
        if self.original is None or self.replay is None:
            return 0
        return self.replay.count - self.original.count


@dataclass(frozen=True, slots=True)
class SessionDiff:
    """One key whose session count changed."""

    key: str
    original_sessions: int
    replay_sessions: int

    @property
    def lost(self) -> int:
        return max(0, self.original_sessions - self.replay_sessions)


@dataclass(frozen=True, slots=True)
class Divergence:
    """The whole diff, with the counts the README and the report quote."""

    rows: tuple[RowDiff, ...]
    sessions: tuple[SessionDiff, ...]
    stable_rows: int
    original_rows: int
    replay_rows: int
    union_rows: int
    original_sessions: int
    replay_sessions: int

    @property
    def replay_stable_share(self) -> float:
        """Share of the rows in the union that both runs agree on exactly.

        The union is the denominator rather than the original's row count,
        because a replay that invents rows has not reproduced the table either.
        Using the original as the denominator would let a run score well by
        adding rows, which is the failure mode this fixture actually exhibits.
        """
        if self.union_rows == 0:
            return 0.0
        return self.stable_rows / self.union_rows

    @property
    def session_loss_share(self) -> float:
        if self.original_sessions == 0:
            return 0.0
        return max(0, self.original_sessions - self.replay_sessions) / self.original_sessions

    def by_arm(self) -> dict[Arm, int]:
        counts = dict.fromkeys(Arm, 0)
        for row in self.rows:
            counts[row.arm] += 1
        counts[Arm.SESSION_MERGE] = sum(1 for diff in self.sessions if diff.lost)
        return counts

    def rows_for(self, arm: Arm) -> tuple[RowDiff, ...]:
        return tuple(row for row in self.rows if row.arm is arm)

    @property
    def diverged(self) -> bool:
        return bool(self.rows) or any(diff.lost for diff in self.sessions)


def _sessions_per_key(rows: tuple[SessionRow, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.key] = counts.get(row.key, 0) + 1
    return counts


def compare(original: RunResult, replay: RunResult) -> Divergence:
    """Diff two runs and attribute every difference to exactly one arm.

    The attribution order matters and is not arbitrary. A row is charged to
    LATE_DROP whenever the record sets differ, even if its last-value column
    also disagrees, because the drop is the cause and the tie-break is
    downstream of it. Only rows whose counts and sums match exactly can be
    charged to TIE_ORDER, which makes that arm's count a floor on the number of
    rows that would still differ if the drop problem were fixed tomorrow. That
    floor is the useful number, and an attribution that let LATE_DROP rows leak
    into it would destroy it.
    """
    left = original.window_index()
    right = replay.window_index()
    diffs: list[RowDiff] = []
    stable = 0
    for identity in sorted(set(left) | set(right)):
        start, key = identity
        a = left.get(identity)
        b = right.get(identity)
        if a is None:
            diffs.append(RowDiff(start, key, Arm.ONLY_REPLAY, None, b))
            continue
        if b is None:
            diffs.append(RowDiff(start, key, Arm.ONLY_ORIGINAL, a, None))
            continue
        if a == b:
            stable += 1
            continue
        if a.count != b.count or a.total != b.total:
            diffs.append(RowDiff(start, key, Arm.LATE_DROP, a, b))
        else:
            diffs.append(RowDiff(start, key, Arm.TIE_ORDER, a, b))

    left_sessions = _sessions_per_key(original.sessions)
    right_sessions = _sessions_per_key(replay.sessions)
    session_diffs = tuple(
        SessionDiff(
            key=key,
            original_sessions=left_sessions.get(key, 0),
            replay_sessions=right_sessions.get(key, 0),
        )
        for key in sorted(set(left_sessions) | set(right_sessions))
        if left_sessions.get(key, 0) != right_sessions.get(key, 0)
    )
    return Divergence(
        rows=tuple(diffs),
        sessions=session_diffs,
        stable_rows=stable,
        original_rows=len(left),
        replay_rows=len(right),
        union_rows=len(set(left) | set(right)),
        original_sessions=len(original.sessions),
        replay_sessions=len(replay.sessions),
    )
