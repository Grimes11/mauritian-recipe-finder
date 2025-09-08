# backend/app.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Set

from flask import Flask, jsonify, request
from flask_cors import CORS

# Utils / services
from utils.data_loader import (
    reload_all,
    get_typeahead,
    get_recipes,
    get_foodon_index,
)
from utils.validators import validate_all_recipes
from services.retrieval_service import RetrievalService

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)  # allow local frontend to call API during dev

# Singletons (created once, reused)
retrieval = RetrievalService()


# -----------------------------------------------------------------------------
# Basic routes
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    """Simple landing to prove the server is up."""
    return jsonify({
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
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/reload", methods=["POST"])
def reload_data():
    """
    Clear all memoized caches and re-init services.
    Useful after editing JSON in /data without restarting the server.
    """
    global retrieval
    reload_all()
    retrieval = RetrievalService()  # refresh internal indexes
    return jsonify({"ok": True, "message": "Caches reloaded and services reinitialized."})


# -----------------------------------------------------------------------------
# Data helpers
# -----------------------------------------------------------------------------
@app.route("/typeahead", methods=["GET"])
def typeahead():
    """Return flattened typeahead (labels + synonyms) for the UI."""
    return jsonify(get_typeahead())


@app.route("/recipes", methods=["GET"])
def recipes():
    """Lightweight recipe listing (index + title)."""
    data = get_recipes()
    out = [{"index": i, "title": r.get("title")} for i, r in enumerate(data)]
    return jsonify({"count": len(out), "items": out})


@app.route("/recipe/<int:index>", methods=["GET"])
def recipe_detail(index: int):
    data = get_recipes()
    if index < 0 or index >= len(data):
        return jsonify({"error": "recipe index out of range"}), 404
    # Enrich with labels
    idx_map = get_foodon_index()
    recipe = data[index]
    enriched = []
    for ing in recipe.get("ingredients", []):
        fid = ing.get("id")
        node = idx_map.get(fid, {})
        enriched.append({
            "id": fid,
            "label": node.get("label", fid),
            "qty": ing.get("qty"),
        })
    return jsonify({
        "index": index,
        "title": recipe.get("title"),
        "ingredients": enriched,
    })


@app.route("/validate", methods=["GET"])
def validate():
    """
    Run the basic validators against data/recipes.json.
    Returns empty list when all good.
    """
    issues = validate_all_recipes()
    if not issues:
        return jsonify({"ok": True, "issues": []})
    # Normalize for JSON
    normalized = [{"recipe_index": idx, "errors": errs} for idx, errs in issues]
    return jsonify({"ok": False, "issues": normalized})


# -----------------------------------------------------------------------------
# Search (MVP retrieval + adaptation)
# -----------------------------------------------------------------------------
@app.route("/search", methods=["POST"])
def search():
    """
    Body (JSON):
    {
      "have": ["tomato", {"id":"FOODON:..."} , {"label":"onion"}],
      "avoid_ids": ["FOODON:..."],
      "diet": ["vegan", "vegetarian"],
      "avoid_allergens": ["contains-milk", "fish"],
      "limit": 10
    }
    """
    payload: Dict[str, Any] = request.get_json(force=True) or {}

    have: List[Any] = payload.get("have", [])
    avoid_ids: Set[str] = set(payload.get("avoid_ids", []))
    diet: Set[str] = set([str(d).lower() for d in payload.get("diet", [])])
    avoid_allergens: Set[str] = set([str(a).lower() for a in payload.get("avoid_allergens", [])])
    limit: int = int(payload.get("limit", 10))

    results = retrieval.search(
        have_terms_or_objs=have,
        avoid_ids=avoid_ids,
        diet=diet,
        avoid_allergens=avoid_allergens,
        limit=limit,
    )
    return jsonify(results)


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # You can set PORT via env if you like; default to 5001 for dev
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=True)
