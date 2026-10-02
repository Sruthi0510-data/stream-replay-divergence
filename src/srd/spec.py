"""The pipeline declaration, as a file rather than a pile of flags.

Every setting in this file is one somebody chose, and the choices that matter
most are the ones nobody remembers making. Allowed lateness gets picked during
the first week and never revisited; the trigger interval gets picked to make a
demo look responsive; the session gap gets copied from a blog post. Each of them
decides what the output means, and none of them is visible in the output.

Putting them in a reviewed file changes what a code review is about. A diff that
moves `allowed_lateness_ms` from 30 seconds to 5 seconds is a diff that silently
discards a quarter of one partition's traffic, and it should be as hard to merge
as a schema change, because it is one.

Unknown keys are refused rather than ignored. A typo in a settings file that is
silently accepted is how a job ends up running with a default nobody chose, and
the failure arrives months later as a number that does not add up.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from srd.job import JobConfig
from srd.watermark import WatermarkPolicy

__all__ = ["PipelineSpec", "SpecError"]

_JOB_KEYS = {"window_ms", "session_gap_ms", "allowed_lateness_ms", "per_partition"}
_RUN_KEYS = {"trigger_interval_ms", "replay_batch_records"}
_SECTIONS = {"job", "runtime"}


class SpecError(ValueError):
    """Raised when a declaration cannot be read or does not make sense."""


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    """One reviewed declaration of the whole pipeline.

    The split between `job` and `runtime` is the argument of the repository laid
    out in a file. Everything under `job` changes what the output means and
    belongs in review. Everything under `runtime` is supposed to be an operational
    detail, the kind of thing an on-call engineer changes at three in the morning
    to clear a backlog, and this repository exists to show that it is not.
    """

    window_ms: int
    session_gap_ms: int
    allowed_lateness_ms: int
    per_partition: bool
    trigger_interval_ms: int
    replay_batch_records: int

    def __post_init__(self) -> None:
        if self.trigger_interval_ms <= 0:
            raise SpecError("trigger interval must be positive")
        if self.replay_batch_records <= 0:
            raise SpecError("replay batch size must be positive")

    @property
    def job(self) -> JobConfig:
        return JobConfig(
            window_ms=self.window_ms,
            session_gap_ms=self.session_gap_ms,
            watermark=WatermarkPolicy(
                allowed_lateness_ms=self.allowed_lateness_ms,
                per_partition=self.per_partition,
            ),
        )

    @classmethod
    def from_file(cls, path: Path) -> PipelineSpec:
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SpecError(f"no such spec: {path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise SpecError(f"{path} is not valid TOML: {exc}") from exc
        unknown_sections = sorted(set(raw) - _SECTIONS)
        if unknown_sections:
            raise SpecError(f"{path} has unknown sections: {', '.join(unknown_sections)}")
        job = raw.get("job", {})
        runtime = raw.get("runtime", {})
        unknown = sorted((set(job) - _JOB_KEYS) | (set(runtime) - _RUN_KEYS))
        if unknown:
            raise SpecError(f"{path} has unknown keys: {', '.join(unknown)}")
        missing = sorted((_JOB_KEYS - set(job)) | (_RUN_KEYS - set(runtime)))
        if missing:
            raise SpecError(f"{path} is missing: {', '.join(missing)}")
        try:
            return cls(
                window_ms=int(job["window_ms"]),
                session_gap_ms=int(job["session_gap_ms"]),
                allowed_lateness_ms=int(job["allowed_lateness_ms"]),
                per_partition=bool(job["per_partition"]),
                trigger_interval_ms=int(runtime["trigger_interval_ms"]),
                replay_batch_records=int(runtime["replay_batch_records"]),
            )
        except (TypeError, ValueError) as exc:
            raise SpecError(f"{path} has a value of the wrong type: {exc}") from exc
