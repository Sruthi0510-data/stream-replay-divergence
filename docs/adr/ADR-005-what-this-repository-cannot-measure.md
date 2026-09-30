# ADR-005: what this repository cannot measure

## Status

Accepted. This file exists so that nobody has to guess whether an omission was
an oversight.

## Context

The model is deliberately small. Every simplification is a place where a real
engine does something this code does not, and a reader comparing their own job
against these numbers needs the list.

## The model has one drop reason

`DropReason` has a single member, `LATE_BEYOND_WATERMARK`. A real engine also
discards records for a schema that does not parse, a key that hashes outside the
expected range, a state store that has been evicted under memory pressure, and a
deserialiser that threw. Each of those interacts with replay differently and
none of them is modelled.

The enum has one member rather than being a boolean so that the shape of the
answer is honest: this is one reason out of several, not the reason.

## Exactly-once delivery is not modelled

The log here has unique offsets and every record is processed exactly once. Real
pipelines have duplicate delivery, transactional sinks with their own commit
semantics, and idempotent writes that turn a duplicate into a no-op. A replay
into a non-idempotent sink has a failure mode this repository says nothing
about, and it is usually the first one people hit.

## State store checkpoints are not modelled

A real stateful job checkpoints. A replay that starts from a checkpoint rather
than from the beginning of the topic inherits state the log does not contain,
which makes it neither the original run nor a clean replay. The bounds argument
assumes a replay starts empty; from a checkpoint the reachable set is different
and this code does not compute it.

## One consumer, no rebalancing

Partitions here are a label on a record. A real consumer group rebalances,
reassigns partitions mid-run, and replays from the last committed offset on the
new owner. Rebalancing changes the batching in ways this model does not
reproduce, and it changes it *during* a run rather than between runs.

## Windows do not overlap and do not slide

Tumbling windows only. Sliding and session windows over event time have
different state-retention behaviour under a watermark, and a sliding window's
row can be rewritten by a record that arrives after an earlier instance of the
same window closed.

## The bound is about replays, not about the original run

The single most important limitation, and it has its own section in the README.
`bounds.classify` bounds what two replays can disagree about. It says nothing
about how far either lands from the run that produced the table an operator
already has, because that run processed in arrival order and the ordering is not
in the log.

## The fixture is one topic

Every number in the README is measured on one seeded log with one traffic shape.
`exp03` sweeps the two settings that matter most and shows where the result stops
being robust, which is the honest version of a sensitivity analysis, but it is
not the same as having measured a second workload. A reader should treat the
mechanism as general and the magnitudes as specific to this fixture.
