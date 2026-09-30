# ADR-001: the log is not the run, and the types say so

## Status

Accepted. Every other decision in this repository depends on it.

## Context

The natural way to model a streaming record is one class with every field on it:
offset, partition, key, event time, payload, and the time it turned up at the
consumer. It reads well and it is wrong, because the last field is of a
different kind from the others.

The first five are written by the producer and stored by the broker. They are
durable, they are the same on every read, and a replay gets them back exactly.
The sixth is measured by a consumer process that did not exist when the record
was written, is not written back anywhere, and is different for every consumer
that ever reads the record.

Modelling them together makes the difference invisible at exactly the moment it
matters, which is when a function is deciding a record's fate.

## Decision

Two frozen types. `LoggedEvent` holds what the broker stores. `ObservedEvent`
holds a `LoggedEvent` plus `arrival_ms`, and forwards the logged fields as
properties so that call sites stay readable.

A function that needs arrival time says so in its signature. A function that
takes a `LoggedEvent` cannot reach for arrival time because there is nothing to
reach for.

The bridge between them is `as_observed`, which stamps every record with the
same arrival time and is documented as the lie it is. That is what a replay
genuinely knows, and any other choice would be an invention.

## Consequences

The good one: the structural claim is enforced rather than argued. When
`bounds.classify` takes a log and no batching, the reader can see from the
signature that the answer cannot depend on anything a replay lacks.

The cost: two types for one concept, and a property forwarder on the second.
`ObservedEvent` carries six forwarding properties that exist only to keep call
sites from writing `event.logged.key`. That is real boilerplate and it is paid
once.

A second cost, less obvious: the fixture cannot ship arrival times. The
generator produces them, feeds them to the original run and discards them, so
regenerating the fixture is the only way to get a live run back. That is
deliberate. A fixture carrying arrival times would be a fixture unlike any log
an incident responder has been handed, and every result measured against it
would be about a system nobody operates.

## Alternatives considered

**One class with an optional arrival time.** Rejected: `None` would have to be
checked at every use, and the check would be forgotten in the one place that
mattered. The failure would be a silently wrong result rather than a type error.

**Storing arrival time in the log.** This is what a broker's ingestion timestamp
looks like, and it does not solve the problem. The broker's ingestion time is a
different clock and a different queue from the consumer's, so it bounds the
consumer's arrival time without determining it, and the batching depends on the
value rather than the bound.
