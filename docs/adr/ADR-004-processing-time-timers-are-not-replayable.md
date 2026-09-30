# ADR-004: a processing-time timer is a decision not to be replayable

## Status

Accepted, and the session operator is kept in the job precisely because it is
the wrong design.

## Context

The sessioniser closes a session when a key has been quiet for
`session_gap_ms` of **wall clock on the consumer**. This is how a great many
real sessionisers are written, because it is the obvious thing to write and
because the streaming frameworks make it easy.

It has a property its authors rarely notice: its output is a function of how
fast the job was running. Two runs of the same job over the same records, one
keeping up and one catching up from a backlog, produce different session tables,
and neither is wrong.

The alternative is an event-time gap: close the session when the watermark has
advanced past the last record's event time by the gap. That is replayable,
because the watermark is a function of the records.

## Decision

Keep the processing-time version, document it as the defect it is, and measure
what it costs.

The measurement is in `exp05`. Thirty replay settings, batch sizes crossed with
time per batch, and not one of them reproduces the original session table. The
closest recovers a fifth of the sessions, and matching the original's wall-clock
span would require running several times slower still. Reprocessing six hours of
traffic faithfully takes six hours.

## Consequences

The session table is unreplayable by construction, and this is the clearest
result in the repository because it involves no probability at all. It is also
the one an operator is least likely to have anticipated, because the counter
they watch, records dropped, reads nearly zero throughout.

Keeping the defect means the job is not a model of a well-built pipeline. That
is the intent: a repository that only modelled correct designs would have
nothing to say about the pipelines people actually operate.

## Alternatives considered

**Switching to an event-time gap.** This is the right fix for a production job
and it is the recommendation in the runbook. It is not the right fix here,
because the whole point is to show what the wrong choice costs, and a repository
that quietly fixed it would be arguing from an example it had removed.

**Modelling both and measuring the difference.** Tempting, and rejected on
scope. The event-time version's divergence is a function of drops, which makes
it a second copy of the aggregate's story rather than a new one. The honest
version of that comparison is one sentence in the runbook: an event-time gap
moves the session table from unreplayable to as-replayable-as-the-aggregate,
which is not the same as replayable.
