"""Deterministic rules that operate only on validated contract objects."""

from .consensus import RULES_VERSION, evaluate_consensus

__all__ = ["RULES_VERSION", "evaluate_consensus"]
