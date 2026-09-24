"""Compatibility package for flat checkouts stored outside a `cutclips` folder.

The application modules live in the repository root and import each other as
``cutclips.*``. Point the package search path there regardless of the checkout
directory's name.
"""
from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent)]
