"""Pure domain layer.

Rules for this package:
* No IO, no network, no clock reads, no logging side effects.
* Everything here is deterministic and unit-testable in isolation.
* Adapters and services build on these types; they never leak back in.
"""
