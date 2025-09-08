# backend/utils/data_loader.py
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Paths & filenames
# --------------------------------------------------------------------------- #

THIS_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = (THIS_DIR / ".." / "data").resolve()

FILE_FOODON = "foodon_cache.json"
FILE_RECIPE = "recipes.json"
FILE_TYPEAHEAD = "typeahead_full.json"
FILE_MX = "mx_mauritius.json"
FILE_SUBS = "subs_foodon.json"
FILE_ROLES = "roles.json"
FILE_UNITS = "units.json"


def _abspath(filename: str) -> Path:
    return DATA_DIR / filename


# --------------------------------------------------------------------------- #
# Low-level JSON I/O
# --------------------------------------------------------------------------- #

def _load_json(filename: str) -> Any:
    path = _abspath(filename)
    if not path.exists():
        raise FileNotFoundError(
            f"Missing data file: {path}\n"
            f"Expected under DATA_DIR={DATA_DIR}. "
            "Verify your folder structure and filenames."
        )
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e


def _save_json(filename: str, payload: Any) -> None:
    """
    Utility writer (rarely used at runtime, but handy for scripts).
    Writes pretty JSON with UTF-8.
    """
    path = _abspath(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Public functional accessors (memoized)
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=None)
def get_foodon_cache() -> List[Dict[str, Any]]:
    return _load_json(FILE_FOODON)


@lru_cache(maxsize=None)
def get_recipes() -> List[Dict[str, Any]]:
    return _load_json(FILE_RECIPE)


@lru_cache(maxsize=None)
def get_typeahead() -> List[Dict[str, Any]]:
    """
    Expected shape (each row):
      {
        "id": "FOODON:xxxxx",
        "name": "display label or synonym",
        "kind": "label" | "synonym" | "mx",
      }
    """
    return _load_json(FILE_TYPEAHEAD)


@lru_cache(maxsize=None)
def get_mx() -> Dict[str, str]:
    """
    Local/Creole/French → FoodOn ID
    Keys are expected to be lowercase in the source file.
    """
    return _load_json(FILE_MX)


@lru_cache(maxsize=None)
def get_subs() -> Dict[str, List[Dict[str, Any]]]:
    """
    Substitution rules keyed by FoodOn ID.
    """
    return _load_json(FILE_SUBS)


@lru_cache(maxsize=None)
def get_roles() -> Dict[str, Dict[str, Any]]:
    return _load_json(FILE_ROLES)


@lru_cache(maxsize=None)
def get_units() -> Dict[str, Any]:
    return _load_json(FILE_UNITS)


# --------------------------------------------------------------------------- #
# Derived indexes
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=None)
def get_foodon_index() -> Dict[str, Dict[str, Any]]:
    """FoodOn ID -> node"""
    return {row["id"]: row for row in get_foodon_cache()}


@lru_cache(maxsize=None)
def get_label_index() -> Dict[str, str]:
    """lower(label) -> FoodOn ID"""
    idx: Dict[str, str] = {}
    for row in get_foodon_cache():
        lbl = (row.get("label") or "").strip().lower()
        if lbl:
            idx[lbl] = row["id"]
    return idx


@lru_cache(maxsize=None)
def get_synonym_index() -> Dict[str, str]:
    """lower(synonym) -> FoodOn ID"""
    idx: Dict[str, str] = {}
    for row in get_foodon_cache():
        for syn in (row.get("synonyms") or []):
            s = (syn or "").strip().lower()
            if s:
                idx[s] = row["id"]
    return idx


@lru_cache(maxsize=None)
def get_all_names_index() -> Dict[str, str]:
    """Combined labels + synonyms (lowercased) -> FoodOn ID"""
    merged = dict(get_label_index())
    merged.update(get_synonym_index())
    return merged


# --------------------------------------------------------------------------- #
# Helpers / Lookups
# --------------------------------------------------------------------------- #

def id_exists(foodon_id: str) -> bool:
    return foodon_id in get_foodon_index()


def get_label(foodon_id: str) -> str:
    node = get_foodon_index().get(foodon_id) or {}
    return node.get("label") or foodon_id


def resolve_name(q: str) -> Optional[str]:
    """
    Resolve a free-text name to a FoodOn ID using:
    1) MX local map (exact, lowercase)
    2) FoodOn labels/synonyms (exact, case-insensitive)
    Returns FoodOn ID or None.
    """
    if not q:
        return None
    s = q.strip().lower()
    if not s:
        return None

    # 1) MX map
    mx = get_mx()
    if s in mx:
        return mx[s]

    # 2) labels/synonyms
    idx = get_all_names_index()
    return idx.get(s)


def prefix_typeahead(q: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Return up to `limit` rows from typeahead_full.json whose "name" starts with `q` (case-insensitive).
    """
    q = (q or "").strip().lower()
    if not q:
        return []
    out: List[Dict[str, Any]] = []
    for row in get_typeahead():
        name = (row.get("name") or "").lower()
        if name.startswith(q):
            out.append(row)
            if len(out) >= limit:
                break
    return out


def search_contains(q: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Fallback contains search over typeahead (case-insensitive).
    """
    q = (q or "").strip().lower()
    if not q:
        return []
    out: List[Dict[str, Any]] = []
    for row in get_typeahead():
        name = (row.get("name") or "").lower()
        if q in name:
            out.append(row)
            if len(out) >= limit:
                break
    return out


def reload_all() -> None:
    """
    Clear all memoized caches. Call this if you edit JSON files on disk
    during a running dev server.
    """
    get_foodon_cache.cache_clear()
    get_recipes.cache_clear()
    get_typeahead.cache_clear()
    get_mx.cache_clear()
    get_subs.cache_clear()
    get_roles.cache_clear()
    get_units.cache_clear()
    get_foodon_index.cache_clear()
    get_label_index.cache_clear()
    get_synonym_index.cache_clear()
    get_all_names_index.cache_clear()


# --------------------------------------------------------------------------- #
# Optional OO-style wrapper (adapter around the functional API)
# --------------------------------------------------------------------------- #

class DataLoader:
    """
    Thin adapter around the functional API so you can write:

        dl = DataLoader()
        items = dl.foodon_cache()
        dl.reload()

    It does not copy data; it just delegates to the memoized functions above.
    """

    def data_dir(self) -> Path:
        return DATA_DIR

    # Loads
    def foodon_cache(self) -> List[Dict[str, Any]]:
        return get_foodon_cache()

    def recipes(self) -> List[Dict[str, Any]]:
        return get_recipes()

    def typeahead(self) -> List[Dict[str, Any]]:
        return get_typeahead()

    def mx(self) -> Dict[str, str]:
        return get_mx()

    def subs(self) -> Dict[str, List[Dict[str, Any]]]:
        return get_subs()

    def roles(self) -> Dict[str, Dict[str, Any]]:
        return get_roles()

    def units(self) -> Dict[str, Any]:
        return get_units()

    # Indexes
    def foodon_index(self) -> Dict[str, Dict[str, Any]]:
        return get_foodon_index()

    def all_names_index(self) -> Dict[str, str]:
        return get_all_names_index()

    # Helpers
    def label(self, foodon_id: str) -> str:
        return get_label(foodon_id)

    def exists(self, foodon_id: str) -> bool:
        return id_exists(foodon_id)

    def resolve(self, q: str) -> Optional[str]:
        return resolve_name(q)

    def typeahead_prefix(self, q: str, limit: int = 50) -> List[Dict[str, Any]]:
        return prefix_typeahead(q, limit=limit)

    def typeahead_contains(self, q: str, limit: int = 50) -> List[Dict[str, Any]]:
        return search_contains(q, limit=limit)

    def reload(self) -> None:
        reload_all()


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    print(f"DATA_DIR = {DATA_DIR}")
    try:
        print(f"FoodOn items: {len(get_foodon_cache())}")
        print(f"Recipes: {len(get_recipes())}")
        print(f"Typeahead: {len(get_typeahead())}")
        print(f"MX terms: {len(get_mx())}")
        print(f"Subs rules: {len(get_subs())}")
        print(f"Roles: {len(get_roles())}")
        print(f"Units: {len(get_units())}")

        # Quick resolution checks
        sample_terms = ["dholl", "garlic", "tomato", "coriander", "safran"]
        for t in sample_terms:
            print(f"resolve_name('{t}') -> {resolve_name(t)}")

        # Prefix demo
        print("prefix_typeahead('to', 5) ->", [r.get("name") for r in prefix_typeahead("to", 5)])

        print("OK ✅")
    except Exception as e:
        print("Smoke test failed ❌:", e)
