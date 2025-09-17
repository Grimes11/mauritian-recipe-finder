# backend/app.py
from __future__ import annotations

import os
from typing import Any, Dict, Set, List, Tuple
from inspect import signature  # keep

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

# utils
from utils import data_loader as dl
from utils.normalizer import Normalizer
from utils.units_service import UnitsService
from utils.validators import validate_all_recipes

# services
from services.retrieval_service import RetrievalService

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
app = Flask(__name__)
# Open CORS for dev (tighten in prod if needed)
CORS(app)

# Instantiate shared services
normalizer = Normalizer()
units = UnitsService()
retrieval = RetrievalService()

# Known allergen tags our data may use
KNOWN_ALLERGEN_TAGS: Set[str] = {
    "contains-egg",
    "contains-milk",
    "fish",
    "shellfish",
    "peanut",
    "tree-nut",
    "soy",
    "sesame",
    "gluten",
}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _to_id_if_obj(x: Any) -> str | None:
    """Return str id if x is a dict with id, else None."""
    if isinstance(x, dict) and x.get("id"):
        return str(x["id"])
    return None

def _to_label_if_obj(x: Any) -> str | None:
    """Return str label if x is a dict with label, else None."""
    if isinstance(x, dict) and x.get("label"):
        return str(x["label"])
    return None

def _resolve_to_id(term: str) -> str | None:
    """
    Resolve a free-text term via Normalizer, coercing result to a string id.
    Normalizer may return a string id or a dict; handle both.
    """
    rid = normalizer.resolve(term)
    if isinstance(rid, str):
        return rid
    if isinstance(rid, dict):
        return rid.get("id")
    return None

def _parse_search_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes incoming JSON into RetrievalService.search() params.

    Body fields:
      have:  list[str | {id,label}]
      avoid: list[str | {id,label}]  (can include FoodOn IDs, labels, or allergen tags)
      diet:  list[str]
      avoid_allergens: list[str]
      limit: int
      hard_exclude_unavoidable: bool
      attach_labels: bool
    """
    # ---------- HAVE ----------
    raw_have: List[Any] = data.get("have") or []
    have_terms_or_objs: List[Any] = []
    for x in raw_have:
        # Keep {id} as-is (stringified id)
        xid = _to_id_if_obj(x)
        if xid:
            have_terms_or_objs.append({"id": xid})
            continue
        # Or keep {label} as-is
        xlbl = _to_label_if_obj(x)
        if xlbl:
            have_terms_or_objs.append({"label": xlbl})
            continue
        # Or a plain string
        have_terms_or_objs.append(str(x))

    # ---------- AVOID ----------
    raw_avoid: List[Any] = data.get("avoid") or data.get("avoid_ids") or []
    avoid_ids: Set[str] = set()
    avoid_allergens: Set[str] = {str(x).strip().lower() for x in (data.get("avoid_allergens") or [])}

    for x in raw_avoid:
        # If object with id, trust it
        xid = _to_id_if_obj(x)
        if xid:
            avoid_ids.add(xid)
            continue

        # If object with label or plain string, resolve
        s = _to_label_if_obj(x) if isinstance(x, dict) else str(x)
        s = (s or "").strip()
        if not s:
            continue
        low = s.lower()

        # FoodOn id pattern?
        if low.startswith("foodon:"):
            avoid_ids.add(s)
            continue

        # Looks like allergen tag?
        if low in KNOWN_ALLERGEN_TAGS:
            avoid_allergens.add(low)
            continue

        # Try resolving label/synonym/local name
        rid = _resolve_to_id(s)
        if rid:
            avoid_ids.add(rid)
        # else: unresolved term silently ignored

    # ---------- Other flags ----------
    diet = {str(x).strip().lower() for x in (data.get("diet") or [])}
    limit = int(data.get("limit") or 10)
    hard_exclude_unavoidable = bool(data.get("hard_exclude_unavoidable", False))
    attach_labels = bool(data.get("attach_labels", False))

    return {
        "have_terms_or_objs": have_terms_or_objs,
        "avoid_ids": avoid_ids,
        "diet": diet,
        "avoid_allergens": avoid_allergens,
        "limit": limit,
        "hard_exclude_unavoidable": hard_exclude_unavoidable,
        "attach_labels": attach_labels,
    }

def _parse_query_to_payload(args: Dict[str, str]) -> Dict[str, Any]:
    """
    Allow server-rendered results via GET /results?have=a,b,c&avoid=x,y
    Recognized query params:
      have, avoid, diet, avoid_allergens  (comma-separated)
      limit (int), attach_labels (bool), hard_exclude_unavoidable (bool)
    """
    def _split_csv(key: str) -> List[str]:
        raw = (args.get(key) or "").strip()
        if not raw:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

    def _to_bool(key: str, default: bool = False) -> bool:
        raw = (args.get(key) or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def _to_int(key: str, default: int = 10) -> int:
        try:
            return int(args.get(key, default))
        except Exception:
            return default

    data_like: Dict[str, Any] = {
        "have": _split_csv("have"),
        "avoid": _split_csv("avoid"),
        "diet": _split_csv("diet"),
        "avoid_allergens": _split_csv("avoid_allergens"),
        "limit": _to_int("limit", 10),
        "attach_labels": _to_bool("attach_labels", True),
        "hard_exclude_unavoidable": _to_bool("hard_exclude_unavoidable", False),
    }
    return data_like

# --- normalize 'have' for service compatibility ------------------------------
def _normalize_have_for_service(have_terms_or_objs: List[Any]) -> List[Dict[str, str]]:
    """
    Convert mixed strings/objects into a list of dicts with either {id} or {label}.
    """
    have_normalized: List[Dict[str, str]] = []
    for x in have_terms_or_objs:
        if isinstance(x, dict):
            if x.get("id"):
                have_normalized.append({"id": str(x["id"])})
            elif x.get("label"):
                have_normalized.append({"label": str(x["label"])})
        elif isinstance(x, str):
            s = x.strip()
            if s:
                if s.lower().startswith("foodon:"):
                    have_normalized.append({"id": s})
                else:
                    have_normalized.append({"label": s})
    return have_normalized

# --- utilities used by the enhanced fallback ---------------------------------
def _build_label_map() -> Dict[str, str]:
    """
    Build a lowercase id -> friendly label map from /typeahead.
    Falls back to using id as label if none available.
    """
    label_map: Dict[str, str] = {}
    try:
        for row in dl.get_typeahead() or []:
            rid = (row.get("id") or "").lower()
            term = (row.get("term") or row.get("q") or rid or "").strip()
            if rid:
                label_map.setdefault(rid, term)
    except Exception:
        pass
    return label_map

def _extract_ing_id_and_label(ing: Any, label_map: Dict[str, str]) -> Tuple[str, str]:
    """
    From a recipe ingredient dict, extract:
      - canonical id (lowercased string; may be empty)
      - a reasonable label (from label_map, or 'label'/'name' fields, or id)
    """
    iid = ""
    lbl = ""
    if isinstance(ing, dict):
        iid = str(ing.get("id") or "").strip()
        lbl = str(
            ing.get("label")
            or ing.get("name")
            or ing.get("q")
            or ing.get("term")
            or ""
        ).strip()
    iid_low = iid.lower() if iid else ""

    # prefer label_map if we have a canonical id
    if iid_low and label_map.get(iid_low):
        lbl = label_map[iid_low]

    if not lbl:
        lbl = iid or ""  # at worst, show the id

    return iid_low, lbl.lower()

def _normalize_terms_to_ids_and_labels(
    have_norm: List[Dict[str, str]],
    avoid_ids_in: List[str],
    avoid_allergens_in: List[str],
) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Produce three lowercase sets:
      - have_ids (resolved FoodOn IDs)
      - have_labels (raw label terms from the user)
      - avoid_ids (FoodOn IDs from avoid list, plus any resolved from labels)
    """
    have_ids: Set[str] = set()
    have_labels: Set[str] = set()
    avoid_ids: Set[str] = {str(x).lower() for x in (avoid_ids_in or []) if x}

    # Resolve have labels to IDs where possible, and keep labels for fuzzy match
    for h in have_norm:
        if "id" in h and h["id"]:
            have_ids.add(str(h["id"]).lower())
        elif "label" in h and h["label"]:
            lab = str(h["label"]).strip()
            if lab:
                have_labels.add(lab.lower())
                rid = _resolve_to_id(lab)
                if rid:
                    have_ids.add(str(rid).lower())

    # If any avoid entries were actually labels (not provided as IDs upstream),
    # they should have been resolved earlier in _parse_search_payload; we keep
    # the already-collected avoid_ids, and also consider allergens separately.
    _ = avoid_allergens_in  # (kept for future; not used in fallback scoring here)

    return have_ids, have_labels, avoid_ids

# --- enhanced fallback search -------------------------------------------------
def _fallback_basic_search(
    *, have_norm: List[Dict[str, str]],
    avoid_ids: List[str],
    diet: List[str],
    limit: int,
    attach_labels: bool
) -> Dict[str, Any]:
    """
    Enhanced local ranking when the underlying service can't consume filters.
    Reacts to user inputs by:
      - Matching ingredient IDs against resolved 'have' IDs
      - Fuzzy matching ingredient labels against 'have' labels
      - Penalizing avoid matches (by ID or label substring)
    """
    label_map = _build_label_map()
    have_ids, have_labels, avoid_ids_low = _normalize_terms_to_ids_and_labels(
        have_norm, avoid_ids_in=avoid_ids, avoid_allergens_in=[]
    )

    # also keep avoid labels for substring checks if user typed any as labels
    avoid_labels: Set[str] = set()
    # (We don’t have original avoid labels here; they were normalized upstream.
    # If you want, you can push raw avoid labels into the params and include them.)

    recipes = dl.get_recipes() or []
    results: List[Dict[str, Any]] = []

    for idx, r in enumerate(recipes):
        ings = r.get("ingredients") or []

        ing_ids_low: List[str] = []
        ing_labels_low: List[str] = []
        adapted_ings = []

        for ing in ings:
            iid_low, lbl_low = _extract_ing_id_and_label(ing, label_map)
            if iid_low:
                ing_ids_low.append(iid_low)
            if lbl_low:
                ing_labels_low.append(lbl_low)

            if attach_labels and isinstance(ing, dict):
                # attach label if we have a nicer one
                pretty = label_map.get(iid_low) if iid_low else None
                adapted_ings.append({
                    "id": ing.get("id"),
                    "qty": ing.get("qty"),
                    **({"label": pretty} if pretty else {})
                })

        # counts
        have_by_id = sum(1 for iid in ing_ids_low if iid in have_ids) if have_ids else 0
        # label match: if any have label appears as a substring in ingredient label
        have_by_label = 0
        if have_labels:
            for lbl in ing_labels_low:
                if any(h in lbl for h in have_labels):
                    have_by_label += 1

        # avoid penalties: by ID, and by label substring (best effort)
        avoid_by_id = sum(1 for iid in ing_ids_low if iid in avoid_ids_low) if avoid_ids_low else 0
        avoid_by_label = 0
        if avoid_labels:
            for lbl in ing_labels_low:
                if any(a in lbl for a in avoid_labels):
                    avoid_by_label += 1

        have_count = have_by_id + max(have_by_label - have_by_id, 0)  # don't double count exact ID matches
        avoid_count = avoid_by_id + avoid_by_label
        total_ing = len(ing_ids_low) if ing_ids_low else len(ing_labels_low)
        missing_count = max(total_ing - have_count, 0)

        # Scoring: reward matches, penalize avoids and missing
        score = (have_count * 2.0) - (avoid_count * 3.0) - (missing_count * 0.25)

        results.append({
            "recipe_index": idx,
            "title": r.get("title") or "Recipe",
            "score": round(score, 3),
            "have_count": int(have_count),
            "missing_count": int(missing_count),
            "avoid_count": int(avoid_count),
            "ingredients_adapted": adapted_ings if attach_labels else None,
            "change_log": [],  # this fallback doesn't synthesize specific swaps
        })

    # sort & trim
    results.sort(key=lambda x: x["score"], reverse=True)
    if isinstance(limit, int) and limit > 0:
        results = results[:limit]

    print("[app] Using basic fallback search (enhanced).")
    return {"results": results}

# --- trimming helper around RetrievalService ---------------------------------
def _call_retrieval_with_clean_kwargs(**candidate_kwargs):
    """
    Introspect RetrievalService.search and pass only accepted kwargs.
    If the service exposes no filter knobs, use the local fallback.
    If it *does* expose knobs but returns an output that looks unfiltered,
    also use the fallback (self-heals an ignoring implementation).
    """
    from inspect import signature

    def _looks_unfiltered(result: Dict[str, Any],
                          have_norm: List[Dict[str, str]],
                          avoid_ids: List[str]) -> bool:
        """Heuristic: if inputs are non-empty but all metrics are missing/zero and
        scores are identical (or absent), assume the service ignored filters."""
        # Only care when user actually provided any constraints:
        if not (have_norm or avoid_ids):
            return False

        try:
            items = (result or {}).get("results") or []
            if not items:
                # empty is a valid, filtered outcome
                return False

            # If *none* of the typical metrics are present on any item, likely unfiltered
            metrics_absent = all(
                ("have_count" not in it and
                 "missing_count" not in it and
                 "avoid_count" not in it and
                 "score" not in it)
                for it in items
            )
            if metrics_absent:
                return True

            # If metrics exist but they are all zeros/None and scores are all equal/None
            all_have_zero   = all((it.get("have_count") in (0, None)) for it in items)
            all_avoid_zero  = all((it.get("avoid_count") in (0, None)) for it in items)
            scores          = [it.get("score") for it in items]
            all_scores_same = len(set(scores)) <= 1  # all equal or all None

            if all_have_zero and all_avoid_zero and all_scores_same:
                return True
        except Exception:
            # On any unexpected structure, don't second-guess the service
            return False

        return False

    sig = signature(retrieval.search)
    accepted = set(sig.parameters.keys())
    filter_keys = {"have_terms_or_objs", "have", "avoid_ids", "avoid_allergens", "diet"}
    has_filter_knobs = len(accepted & filter_keys) > 0

    have_norm  = candidate_kwargs.get("have_terms_or_objs") or candidate_kwargs.get("have") or []
    avoid_ids  = candidate_kwargs.get("avoid_ids") or []
    diet       = candidate_kwargs.get("diet") or []
    limit      = candidate_kwargs.get("limit") or 10
    attach_lbl = bool(candidate_kwargs.get("attach_labels"))

    # If the service exposes no knobs, go straight to fallback.
    if not has_filter_knobs:
        print("[app] RetrievalService exposes no filter kwargs; using fallback.")
        return _fallback_basic_search(
            have_norm=have_norm,
            avoid_ids=avoid_ids,
            diet=diet,
            limit=limit,
            attach_labels=attach_lbl,
        )

    # Normal path: trim kwargs to what the service accepts
    clean_kwargs = {k: v for k, v in candidate_kwargs.items() if k in accepted}
    if "attach_labels" in accepted and "attach_labels" not in clean_kwargs:
        clean_kwargs["attach_labels"] = True

    try:
        result = retrieval.search(**clean_kwargs)
    except TypeError as e:
        # Extra safety: if the implementation is stricter than its signature,
        # drop unknowns and retry once.
        print(f"[app] search kwargs TypeError {e}; retrying with minimal args.")
        minimal = {k: v for k, v in clean_kwargs.items() if k in {"have", "have_terms_or_objs", "limit"}}
        result = retrieval.search(**minimal)

    # Post-call sanity check: does the output look unfiltered?
    if _looks_unfiltered(result, have_norm, avoid_ids):
        print("[app] RetrievalService output looks unfiltered; using fallback.")
        return _fallback_basic_search(
            have_norm=have_norm,
            avoid_ids=avoid_ids,
            diet=diet,
            limit=limit,
            attach_labels=attach_lbl,
        )

    return result

# -----------------------------------------------------------------------------
# JSON API Routes (unchanged outwardly)
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "name": "Mauritian Recipe Finder API",
            "endpoints": [
                "GET  /health",
                "POST /reload",
                "GET  /typeahead",
                "GET  /recipes",
                "GET  /recipe/<index>",
                "GET  /validate",
                "POST /search",
            ],
        }
    )

@app.post("/reload")
def reload():
    """
    Clear in-memory caches after you modify JSON files under data/.
    """
    dl.reload_all()
    # Recreate services that cache indexes internally
    global normalizer, units, retrieval
    normalizer = Normalizer()
    units = UnitsService()
    retrieval = RetrievalService()
    return jsonify({"ok": True, "message": "Reloaded caches and services"}), 200

@app.get("/typeahead")
def typeahead():
    """Return the flattened typeahead dataset (labels + synonyms)."""
    return jsonify(dl.get_typeahead())

@app.get("/recipes")
def list_recipes():
    """Return all recipes (MVP). In production you might paginate."""
    return jsonify(dl.get_recipes())

@app.get("/recipe/<int:index>")
def get_recipe(index: int):
    """Return a single recipe by its index in data/recipes.json (JSON API)."""
    recipes = dl.get_recipes()
    if index < 0 or index >= len(recipes):
        return jsonify({"error": "recipe index out of range"}), 404
    return jsonify(recipes[index])

@app.get("/validate")
def validate():
    """Basic dataset validation; helpful during development."""
    issues = validate_all_recipes()
    ok = len(issues) == 0
    return jsonify({"ok": ok, "issues": issues})

@app.post("/search")
def search():
    """
    Retrieve + adapt recipes based on user pantry and constraints (JSON API).
    Accepts either:
      - have:  ["chicken","onion"] or [{"id":"FOODON:..."}]
      - avoid: ["gluten","butter","FOODON:..."]  (labels, allergens or IDs)
    """
    data = request.get_json(force=True, silent=True) or {}
    params = _parse_search_payload(data)

    # Build 'have' in a compatibility-friendly format
    have_normalized = _normalize_have_for_service(params.get("have_terms_or_objs", []))

    # Convert sets to lists and pull flags
    avoid_ids        = list(params.get("avoid_ids", []) or [])
    avoid_allergens  = list(params.get("avoid_allergens", []) or [])
    diet             = list(params.get("diet", []) or [])
    limit            = int(params.get("limit", 10))
    hard_excl        = bool(params.get("hard_exclude_unavoidable", False))
    attach_labels    = bool(params.get("attach_labels", False))

    # Superset of kwargs – _call_retrieval_with_clean_kwargs will trim or fallback
    candidate_kwargs = {
        "have_terms_or_objs": have_normalized,
        "have": have_normalized,  # some implementations use this name
        "avoid_ids": avoid_ids,
        "avoid_allergens": avoid_allergens,
        "diet": diet,
        "limit": limit,
        "hard_exclude_unavoidable": hard_excl,
        "attach_labels": attach_labels,
    }

    result = _call_retrieval_with_clean_kwargs(**candidate_kwargs)
    return jsonify(result), 200


# -----------------------------------------------------------------------------
# UI Page Routes (HTML)
# -----------------------------------------------------------------------------
@app.get("/")
def home_page():
    return render_template("home.html")

@app.get("/search")
def search_page():
    return render_template("search.html")

@app.get("/about")
def about_page():
    return render_template("about.html")

@app.get("/how_to")
def how_to_page():
    return render_template("how_to.html")

@app.get("/recipes/<int:index>")
def recipe_detail_page(index: int):
    """
    HTML Recipe detail page (plural '/recipes/...').
    We keep the JSON API at '/recipe/<index>' (singular) untouched.
    """
    recipes = dl.get_recipes()
    if index < 0 or index >= len(recipes):
        return render_template("recipe_detail.html", recipe=None, index=index, not_found=True), 404
    recipe = recipes[index]
    return render_template("recipe_detail.html", recipe=recipe, index=index, not_found=False)

@app.get("/results")
def results_page():
    """
    Optional server-rendered results page (for quick manual testing):
    /results?have=chicken,tomato&avoid=dairy&limit=5&attach_labels=1
    """
    data_like = _parse_query_to_payload(request.args)  # build a "data" dict from querystring
    params = _parse_search_payload(data_like)         # reuse main normalization

    # Build compatibility 'have'
    have_normalized = _normalize_have_for_service(params.get("have_terms_or_objs", []))

    candidate_kwargs = {
        "have_terms_or_objs": have_normalized,
        "have": have_normalized,
        "avoid_ids": list(params.get("avoid_ids", []) or []),
        "avoid_allergens": list(params.get("avoid_allergens", []) or []),
        "diet": list(params.get("diet", []) or []),
        "limit": int(params.get("limit", 10)),
        "hard_exclude_unavoidable": bool(params.get("hard_exclude_unavoidable", False)),
        "attach_labels": bool(params.get("attach_labels", True)),
    }

    result = _call_retrieval_with_clean_kwargs(**candidate_kwargs)

    return render_template(
        "results.html",
        query=data_like,
        result=result,
    )

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Respect PORT env var if present; default 5001 (to avoid 5000 collisions)
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=True)
