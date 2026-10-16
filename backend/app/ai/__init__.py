"""Controlled AI CFO layer. The model never receives SQL, database handles, posting rights or payment rights.

This module holds the registry of read-only tools, argument validation, and deterministic question routing.
Tool bodies live in app/services/cfo.py and return validated figures; the language layer only explains them.
"""
