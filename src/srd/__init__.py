"""Measures the part of a stateful streaming job's output that a replay cannot reproduce.

The claim this package exists to make precise: a commit log records what arrived.
It structurally cannot record what the job decided to forget, because the decision
to forget belongs to the consumer and is made from state the producer never sees.
Replaying the log therefore re-derives the decisions rather than reproducing them.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
