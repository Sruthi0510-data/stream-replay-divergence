# Runbook: somebody has said "just reprocess from Kafka"

This is the operational half of the repository. It assumes an incident is in
progress, a table looks wrong, and the topic is still inside its retention
window.

## Before you reprocess anything

Run the classification. It reads the log and nothing else, takes one pass, and
does not need the job:

```
srd bounds --log topic.json --spec specs/pipeline.toml
```

It tells you two things and refuses to tell you a third.

It tells you how many records are **contingent**: records whose fate depends on
where the batch boundaries happened to fall. On the shipped fixture that is
8.8% of the log.

It tells you which output rows two reprocessings can disagree about, and over
what stretch of event time. Outside that stretch, two reprocessings will agree
with each other.

It refuses to tell you how far a reprocessing will land from the table you
already have. That number is not computable from the log, because the original
run processed records in the order they arrived and that order was never written
down. The bound understates the real divergence by several times on the shipped
fixture, so treating it as a safety margin is worse than having no number.

## Decide what "reprocess" is for

Three different intentions get the same words, and only one of them is safe.

**Backfilling a window that was never computed.** Safe, if the window is outside
the reachable span the classification reported and no downstream consumer joins
it against rows from inside that span. This is the case reprocessing is actually
good at.

**Recomputing a window because the code changed.** Safe in the sense that you
want the new answer, and you should expect the unchanged parts of the output to
move as well. They will. Say so in the change description, because somebody will
otherwise diff old against new and attribute the whole difference to the code
change.

**Rebuilding a window because the output looks wrong.** Not safe, and this is
the one that is usually meant. You will get a different table, it will not be
the old table with the bug removed, and you will have destroyed the evidence
needed to work out what the original run did. Snapshot the existing table first.

## Do the cheap check first

```
srd replay --log topic.json --output yesterdays-table.json
```

Exit code 3 means the replay does not reproduce the table you have. That is the
expected answer for any job with a processing-time timer in it, and it is worth
knowing before you overwrite anything rather than after.

The output splits the differing rows by mechanism. If the count under "changed
by tie ordering" is not zero, widening the allowed lateness will not fix those
rows and no reprocessing will produce a stable answer for them; the query needs
a deterministic tie-break.

## What the alerts mean and what they do not

`RecordsDropped` firing means records were discarded. It does **not** mean
reprocessing will recover them identically, and on the shipped fixture the
replay's own drop count is 8 records against thirty thousand while the session
table loses almost everything. The counter is nearly silent during the failure
it is supposed to cover.

`PartitionFallingBehind` firing means the partition driving the global watermark
has run further ahead of the slowest partition than the allowed lateness. Fresh
records from the slow partition are arriving already condemned. This is the
alert to act on, because it fires before the loss rather than after.

`OutputNoLongerReplayable` firing means the last hour's output contains rows
that a second run would disagree with. It does not mean the output is wrong.

## The three fixes, in order of how much they buy

**Give the session operator an event-time gap instead of a processing-time one.**
This is the largest single improvement available and it is a code change, not a
setting. It moves the session table from unreplayable to as replayable as the
aggregate, which is not the same as replayable but is a different order of
problem.

**Widen the allowed lateness to cover the observed out-of-orderness.** Measure
it first; `srd bounds` reports it. On the shipped fixture, going from the
configured 30 seconds to five minutes takes the replay-stable share from 62.0%
to 99.0%, and the residue is the tie-order floor that no setting touches. The
cost is state retention and therefore memory, which is why the setting was tight
in the first place.

**Switch the watermark from global to per partition.** One boolean. It is worth
501 records of the original run's drops and lifts the stable share to 79.6% and
no further. Do it, and do not expect it to be the answer.

## What to write in the incident review

The counter you have measures loss. The property you needed was reproducibility,
and no counter in a standard streaming deployment measures it. If the review
produces one action, make it the addition of a contingent-record count to the
job's metrics, because that is the number that answers the question the incident
actually asked.
