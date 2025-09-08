"""
Utilities package for Mauritian Recipe Finder.

This package exposes:
- data_loader: loads JSON knowledge base into memory (singleton-style)
- normalizer: resolves local/creole names -> FoodOn IDs
- units_service: parses free-text quantities/units
- validators: basic dataset validations
"""

from . import data_loader, normalizer, units_service, validators  # noqa: F401
