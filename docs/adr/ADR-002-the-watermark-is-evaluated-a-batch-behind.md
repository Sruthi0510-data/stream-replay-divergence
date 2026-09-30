# ADR-002: the watermark is evaluated a batch behind

## Status

Accepted.

## Context

There are two places to put the watermark update inside the batch loop, and they
differ in one line.

Advance the watermark from the current batch and then test the batch against it,
and a record can raise the line past itself. The highest-stamped record in a
batch condemns everything below it in that same batch, regardless of where the
batch boundaries fell.

Advance the watermark after testing, and the line in force is the one computed
from batches that have already finished. A record survives if it shares a batch
with the record that would have condemned it.

The first ordering makes the job look deterministic under replay, because the
outcome depends only on the multiset of records in each batch and, in the limit,
only on the records. It is also wrong: it is not what Spark structured streaming
does and it is not what Flink does. Both compute a watermark from completed
input and apply it forward.

## Decision

Test first, advance second. The loop in `srd.job.run` does it in five numbered
steps with the reason in the docstring, and
`test_a_record_shares_a_batch_with_the_record_that_would_condemn_it` pins it:
two records, the second far below the first, dropped when split across batches
and kept when together.

## Consequences

This is what makes the divergence structural rather than incidental. Under the
other ordering, `bounds.classify` would return `STABLE_KEEP` for every record
and there would be nothing to measure.

It also means the model has a property that surprises people, which is that
processing a log in one enormous batch drops nothing at all. That is correct and
it is what a single-batch reprocessing of a real topic does.

The cost is that every reader who has written this loop the other way will
believe the implementation is buggy until they read the test. The test is
therefore named for the behaviour rather than the mechanism.

## Alternatives considered

**Per-record watermark evaluation**, advancing the line after each record rather
than each batch. This is the limit of the first ordering with batches of one,
and it is the most aggressive dropper. It is available as a batching
(`ThroughputBatcher(max_records=1)`) rather than as a semantics, because it is a
configuration a real engine can be driven into, not a different rule.
