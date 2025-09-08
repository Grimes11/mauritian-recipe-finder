from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Import utils modules whether run as a package or directly as a script
try:
    from . import data_loader as dl
    from . import units_service as us
except ImportError:  # pragma: no cover - fallback for direct script execution
    import os
    import sys

    THIS_DIR = os.path.dirname(__file__)
    BACKEND_DIR = os.path.dirname(THIS_DIR)
    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)
    import utils.data_loader as dl  # type: ignore
    import utils.units_service as us  # type: ignore


def validate_recipe_structure(recipe: Dict[str, Any]) -> List[str]:
    """
    Validate the shape of a single recipe dict.
    Returns a list of human-readable error strings (empty if valid).

    Required top-level keys:
      - title (str)
      - ingredients (non-empty list)

    Each ingredient:
      - must have an 'id' present in FoodOn cache
      - 'qty' is optional; if present, we try to parse it via UnitsService
    """
    errs: List[str] = []

    # Required top-level keys
    for key in ("title", "ingredients"):
        if key not in recipe:
            errs.append(f"missing key: {key}")

    # Must be a non-empty list to proceed
    if not isinstance(recipe.get("ingredients", None), list) or not recipe.get("ingredients"):
        errs.append("ingredients must be a non-empty list")
        return errs  # can't go deeper

    # Check each ingredient entry
    for i, ing in enumerate(recipe["ingredients"]):
        if "id" not in ing:
            errs.append(f"ingredient[{i}] missing 'id'")
            continue

        foodon_id = ing["id"]
        if not dl.id_exists(foodon_id):
            errs.append(f"ingredient[{i}] unknown FoodOn id: {foodon_id}")

        # qty may be optional (MVP), but if present, try to parse
        qty = ing.get("qty")
        if qty:
            parsed = us.parse_quantity(qty)
            # If nothing could be parsed, warn (not hard fail for MVP)
            if parsed.get("amount_min") is None and parsed.get("unit") is None:
                errs.append(f"ingredient[{i}] qty not understood: {qty}")

    return errs


def validate_recipes(recipes: List[Dict[str, Any]] | None = None) -> List[Tuple[int, List[str]]]:
    """
    Public API expected by app.py.

    Validate a list of recipes (or load from data if None).
    Returns a list of (index, [errors...]) for any recipes with problems.
    """
    if recipes is None:
        recipes = dl.get_recipes()

    problems: List[Tuple[int, List[str]]] = []
    for idx, r in enumerate(recipes):
        errs = validate_recipe_structure(r)
        if errs:
            problems.append((idx, errs))
    return problems


# (Kept for convenience; used by earlier steps/tests)
def validate_all_recipes() -> List[Tuple[int, List[str]]]:
    """
    Validate every recipe in data/recipes.json.
    Returns list of (index, errors[]) for any recipe that has errors.
    """
    return validate_recipes(dl.get_recipes())


def spot_test_ids_exist(ids: List[str]) -> List[str]:
    """
    Quick helper used in Day-1/Day-2 DoD checks.
    Returns list of IDs that DO NOT exist in foodon_cache.json.
    """
    return [x for x in ids if not dl.id_exists(x)]


if __name__ == "__main__":
    # Run full validation when this file is executed directly
    issues = validate_all_recipes()
    if not issues:
        print("✅ All recipes passed basic validation.")
    else:
        print("⚠️ Issues found:")
        for idx, errs in issues:
            print(f"  - Recipe #{idx}:")
            for e in errs:
                print(f"      • {e}")
