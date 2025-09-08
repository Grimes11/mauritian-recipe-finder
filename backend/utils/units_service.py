# backend/utils/units_service.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import re

from . import data_loader as dl


_UNICODE_FRACTIONS = {
    "½": 0.5,
    "¼": 0.25,
    "¾": 0.75,
    "⅓": 1/3,
    "⅔": 2/3,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}


class UnitsService:
    """
    Parses and normalizes ingredient quantities using units.json.
    Supports both schema styles:
      1) canonical objects:  "gram": { "aliases": ["g", "grams"] }
      2) alias redirects:    "g": "gram"
    """

    def __init__(self):
        self.units_raw: Dict[str, Any] = dl.get_units()

        # Build canonical unit definitions and alias map.
        # self.unit_defs: canonical -> {aliases: [...]}
        # self.alias_to_unit: alias -> canonical
        self.unit_defs: Dict[str, Dict[str, Any]] = {}
        self.alias_to_unit: Dict[str, str] = {}

        # Pass 1: collect canonical definitions (object values)
        for key, spec in self.units_raw.items():
            if isinstance(spec, dict):
                aliases = [a.lower() for a in (spec.get("aliases") or [])]
                canon = key.lower()
                if key.lower() not in aliases:
                    aliases.append(key.lower())
                self.unit_defs[canon] = {"aliases": aliases, **spec}

        # Pass 2: wire up redirects (string values)
        for key, spec in self.units_raw.items():
            if isinstance(spec, str):
                alias = key.lower()
                target = spec.lower()
                # if target exists as canonical, map alias to it
                if target in self.unit_defs:
                    self.alias_to_unit[alias] = target
                else:
                    # If target not yet a canonical object, create a minimal one
                    self.unit_defs.setdefault(target, {"aliases": [target]})
                    self.alias_to_unit[alias] = target

        # Pass 3: map all aliases from canonical objects too
        for canon, obj in self.unit_defs.items():
            for alias in obj.get("aliases", []):
                self.alias_to_unit[alias.lower()] = canon

        # A few pragmatic extras if your units.json is minimal
        self._ensure_common_defaults()

        # Precompile a permissive pattern:
        #  - Optional leading amount (decimal, vulgar fraction, or ascii fraction)
        #  - Optional range like "1-2"
        #  - Optional unit word (letters/µ)
        # Examples it handles: "2 tbsp", "1/2 cup", "½ tsp", "1-2 tsp", "3g", "pinch"
        self._qty_re = re.compile(
            r"""
            ^\s*
            (?P<a1>(?:\d+(?:\.\d+)?|\d+/\d+|[½¼¾⅓⅔⅛⅜⅝⅞]))?   # first amount (optional)
            (?:\s*-\s*
               (?P<a2>(?:\d+(?:\.\d+)?|\d+/\d+|[½¼¾⅓⅔⅛⅜⅝⅞]))
            )?
            \s*
            (?P<unit>[a-zA-Zµ]+)?                             # unit (optional)
            """,
            re.VERBOSE,
        )

    # --------------------- public API --------------------- #

    def parse_quantity(self, qty: str) -> Dict[str, Any]:
        """
        Parse a free-text qty like "2 tbsp", "500 g", "1/2 cup", "½ tsp", "1-2 tsp".
        Returns { amount_min, amount_max, unit }.
        If cannot parse, returns all None.
        """
        if not qty:
            return {"amount_min": None, "amount_max": None, "unit": None}

        s = qty.strip().lower()
        m = self._qty_re.match(s)
        if not m:
            return {"amount_min": None, "amount_max": None, "unit": None}

        a1 = self._parse_amount(m.group("a1"))
        a2 = self._parse_amount(m.group("a2"))
        unit_raw = (m.group("unit") or "").lower() or None

        # Resolve unit via alias map
        unit: Optional[str] = None
        if unit_raw:
            unit = self.alias_to_unit.get(unit_raw)

        # If only one amount, min==max
        if a1 is not None and a2 is None:
            a2 = a1

        return {
            "amount_min": a1,
            "amount_max": a2,
            "unit": unit,
        }

    # --------------------- helpers --------------------- #

    def _parse_amount(self, token: Optional[str]) -> Optional[float]:
        if not token:
            return None

        # Unicode vulgar fractions
        if token in _UNICODE_FRACTIONS:
            return float(_UNICODE_FRACTIONS[token])

        # ASCII fraction "n/d"
        if "/" in token and self._looks_fraction(token):
            try:
                n, d = token.split("/")
                return float(n) / float(d)
            except Exception:
                return None

        # Decimal or integer
        try:
            return float(token)
        except Exception:
            return None

    @staticmethod
    def _looks_fraction(s: str) -> bool:
        parts = s.split("/")
        return len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit()

    def _ensure_common_defaults(self) -> None:
        """
        Ensure some very common units work even if units.json is minimal.
        Does not override existing definitions.
        """
        defaults: Dict[str, Dict[str, Any]] = {
            "gram": {"aliases": ["g", "gram", "grams"]},
            "kilogram": {"aliases": ["kg", "kilogram", "kilograms"]},
            "milliliter": {"aliases": ["ml", "milliliter", "milliliters"]},
            "liter": {"aliases": ["l", "liter", "liters"]},
            "teaspoon": {"aliases": ["tsp", "teaspoon", "teaspoons"]},
            "tablespoon": {"aliases": ["tbsp", "tablespoon", "tablespoons"]},
            "cup": {"aliases": ["cup", "cups"]},
            "pinch": {"aliases": ["pinch"]},
            "dash": {"aliases": ["dash"]},
            "piece": {"aliases": ["pc", "pcs", "piece", "pieces"]},
            "clove": {"aliases": ["clove", "cloves"]},
        }

        # Merge missing canonical defs
        for canon, spec in defaults.items():
            if canon not in self.unit_defs:
                self.unit_defs[canon] = {"aliases": list(spec["aliases"])}
            else:
                # extend aliases if needed
                existing = set(a.lower() for a in self.unit_defs[canon].get("aliases", []))
                for a in spec["aliases"]:
                    if a.lower() not in existing:
                        self.unit_defs[canon]["aliases"].append(a.lower())

        # Rebuild alias map to include these
        for canon, spec in self.unit_defs.items():
            for a in spec.get("aliases", []):
                self.alias_to_unit.setdefault(a.lower(), canon)


if __name__ == "__main__":
    svc = UnitsService()
    tests = [
        "2 tbsp",
        "500 g",
        "1/2 cup",
        "½ tsp",
        "1-2 tsp",
        "3g",
        "pinch",
        "2 cloves",
        "200ml",
        "  ",
        None,
    ]
    for t in tests:
        print(repr(t), "->", svc.parse_quantity(t or ""))
