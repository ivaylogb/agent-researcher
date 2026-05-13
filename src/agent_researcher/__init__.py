"""agent-researcher: a structured failure-diagnosis agent for production agents.

Phase 1: hypothesis generator. Reads a target agent's code and a failed eval
scenario, produces structured hypotheses about the root cause.

This is NOT auto-research in the Karpathy sense. It is structured diagnosis
against the four-layer agent engineering model. See README.md for the
distinction.
"""

__version__ = "0.1.0"
