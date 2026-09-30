# ADR-003: three arms, charged in a fixed order, and no more

## Status

Accepted.

## Context

A diff that reports a count of differing rows starts an argument about whose
fault it is. Three mechanisms can make a row differ in this job, and they have
three different owners and three different fixes, so a diff that does not
separate them is a diff that cannot be acted on.

The mechanisms:

- **late-drop**: the two runs disagree about which records existed. Owned by
  whoever chose the allowed lateness. Fixable by configuration.
- **tie-order**: the two runs agree on every record and still disagree, because
  the aggregate asked which record was last and two records carried the same
  stamp. Owned by whoever wrote the query. Not fixable by configuration at all.
- **session-merge**: the two runs disagree about how many sessions there were,
  because a session boundary is a processing-time gap and a replay has no gaps.
  Owned by whoever chose a processing-time timer. See ADR-004.

A row can exhibit more than one at once, so the attribution needs an order.

## Decision

A row whose record sets differ is charged to **late-drop**, even when its
last-value column also disagrees. Only rows whose counts and sums match exactly
can be charged to **tie-order**.

The reason is that the tie-order count is then a floor: it is the number of rows
that would still be wrong the day after the allowed lateness was fixed. That
number is the useful one, and an attribution that let late-drop rows leak into
it would destroy it. On the shipped fixture the floor is small and it is not
zero, and the most generous allowed lateness in the sweep leaves exactly it
behind.

Rows present on only one side get their own two arms rather than being folded
into late-drop, because "the replay produced a row the original never had" and
"the replay changed a row that exists in both" are different conversations with
a data consumer.

## Consequences

The taxonomy is closed over what this job can produce. It is not closed over
what a real engine can produce, and ADR-005 says so explicitly rather than
leaving a reader to assume completeness.

The fixed order means a run where the allowed lateness is badly wrong reports a
tie-order count that undercounts the true tie problem, because rows with both
problems are hidden inside late-drop. That is the intended trade: the number is
a floor, and a floor that is sometimes loose is more useful than an estimate
that is sometimes high.

## Alternatives considered

**Charging a row to every arm that applies.** Rejected: the arm counts then sum
to more than the number of differing rows, and the first question anyone asks of
such a table is which number to trust.

**Deciding ties by a deterministic rule so the arm disappears.** Sorting by
offset inside each window would make both runs agree. It would also be a change
to the job's semantics made in order to improve a measurement of the job, and it
would hide a real defect in the query rather than reporting it.
