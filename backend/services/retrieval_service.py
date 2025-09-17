# backend/services/retrieval_service.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from utils.data_loader import get_recipes, get_foodon_index, get_label
from utils.normalizer import Normalizer
from services.substitution_service import SubstitutionService


class RetrievalService:
    """
    End-to-end retrieval + adaptation:
      - normalize inputs to FoodOn IDs
      - score recipes
      - substitute missing/avoid ingredients
    """

    def __init__(self) -> None:
        self.normalizer = Normalizer()
        self.subs = SubstitutionService()
        self.fo_idx = get_foodon_index()

    # ---------- public API ----------

    def search(
        self,
        have_terms_or_objs: List[Any],
        avoid_ids: Optional[Set[str]] = None,   # accept this…
        avoid: Optional[Set[str]] = None,       # …and this (legacy payloads)
        diet: Optional[Set[str]] = None,
        avoid_allergens: Optional[Set[str]] = None,
        limit: int = 10,
        hard_exclude_unavoidable: bool = False,
        attach_labels: bool = False,
    ) -> Dict[str, Any]:
        """
        Returns {count, results[]} sorted by score desc.
        Accepts either `avoid_ids` or `avoid` (both sets of FoodOn IDs).
        """
        # Merge avoid parameters into a single set of IDs
        merged_avoid_ids: Set[str] = set()
        for s in (avoid_ids or set()):
            merged_avoid_ids.add(str(s))
        for s in (avoid or set()):
            merged_avoid_ids.add(str(s))

        diet = set([str(d).lower() for d in (diet or set())])
        avoid_allergens = set([str(a).lower() for a in (avoid_allergens or set())])

        have_ids = self._normalize_have(have_terms_or_objs)
        have_set = set(have_ids)

        results: List[Dict[str, Any]] = []

        for idx, recipe in enumerate(get_recipes()):
            adapted = self._adapt_recipe(
                recipe=recipe,
                have_set=have_set,
                avoid_ids=merged_avoid_ids,
                diet=diet,
                avoid_allergens=avoid_allergens,
                hard_exclude_unavoidable=hard_exclude_unavoidable,
            )
            # If adaptation failed due to hard exclusion, skip this recipe
            if adapted is None:
                continue

            score = self._score_recipe(
                adapted["have_count"], adapted["avoid_count"], adapted["missing_count"]
            )

            ingredients_adapted = adapted["ingredients_adapted"]
            if attach_labels:
                ingredients_adapted = self._with_labels(ingredients_adapted)

            results.append(
                {
                    "recipe_index": idx,
                    "title": recipe.get("title"),
                    "score": score,
                    "have_count": adapted["have_count"],
                    "missing_count": adapted["missing_count"],
                    "avoid_count": adapted["avoid_count"],
                    "ingredients_original": recipe.get("ingredients", []),
                    "ingredients_adapted": ingredients_adapted,
                    "change_log": adapted["change_log"],
                }
            )

        # Sort: highest score first, then fewest missing, fewest avoid, then title
        results.sort(
            key=lambda x: (
                -x["score"],
                x["missing_count"],
                x["avoid_count"],
                x["title"] or "",
            )
        )
        trimmed = results[: max(1, int(limit))]
        return {"count": len(trimmed), "results": trimmed}

    # ---------- internal helpers ----------

    def _normalize_have(self, items: List[Any]) -> List[str]:
        """
        Accepts:
          - strings (IDs, labels, synonyms, local names)
          - dicts with {id} or {label}
        Returns a de-duplicated list of FoodOn IDs (strings).
        """
        ids: List[str] = []

        for x in items or []:
            # { "id": "FOODON:..." } shortcut
            if isinstance(x, dict) and x.get("id"):
                ids.append(str(x["id"]))
                continue

            # { "label": "tomato" } or plain "tomato"
            term = str(x.get("label") if isinstance(x, dict) and x.get("label") else x)
            rid = self.normalizer.resolve(term)
            if isinstance(rid, dict):
                rid = rid.get("id")
            if isinstance(rid, str):
                ids.append(rid)

        # Dedupe while preserving order
        seen: Set[str] = set()
        uniq: List[str] = []
        for fid in ids:
            if fid not in seen:
                seen.add(fid)
                uniq.append(fid)
        return uniq

    def _score_recipe(self, have: int, avoid: int, missing: int) -> int:
        # MVP scoring
        return 3 * have - 2 * avoid - 1 * missing

    def _adapt_recipe(
        self,
        recipe: Dict[str, Any],
        have_set: Set[str],
        avoid_ids: Set[str],
        diet: Set[str],
        avoid_allergens: Set[str],
        hard_exclude_unavoidable: bool,
    ) -> Optional[Dict[str, Any]]:
        """
        Returns adapted recipe dict or None if hard_exclude_unavoidable is True and
        we find an unavoidable avoided ingredient without a substitute.
        """
        ings = recipe.get("ingredients", []) or []
        r_ids = [ing["id"] for ing in ings if "id" in ing]

        have = [i for i in r_ids if i in have_set]
        missing = [i for i in r_ids if i not in have_set]
        contains_avoid = [i for i in r_ids if i in avoid_ids]

        adapted_ings: List[Dict[str, Any]] = []
        change_log: List[Dict[str, Any]] = []

        # Avoid substituting with items the user avoids or already has (reduce dupes)
        block_ids = set(avoid_ids).union(have_set)

        for ing in ings:
            fid = ing.get("id")
            if not fid:
                continue
            qty = ing.get("qty")

            # 1) Avoided item → try replacement
            if fid in avoid_ids:
                suggestions = self.subs.suggest_for(
                    source_id=fid,
                    role=None,  # TODO: hook up roles when available
                    avoid_ids=block_ids,
                    diet_must_include=diet,
                    avoid_allergens=avoid_allergens,
                    limit=1,
                )
                if suggestions:
                    best = suggestions[0]
                    adapted_ings.append({"id": best["target_id"], "qty": qty})
                    change_log.append(
                        {
                            "type": "avoid_sub",
                            "from_id": fid,
                            "to_id": best["target_id"],
                            "reason": best["reason"],
                            "score": best["score"],
                            "notes": best.get("notes"),
                        }
                    )
                else:
                    if hard_exclude_unavoidable:
                        # Signal the caller to discard this recipe entirely
                        return None
                    # else: drop the ingredient as a last resort
                    change_log.append(
                        {
                            "type": "avoid_remove",
                            "from_id": fid,
                            "reason": "no suitable substitute found",
                        }
                    )
                continue

            # 2) If user has it, keep as-is
            if fid in have_set:
                adapted_ings.append(ing)
                continue

            # 3) Missing → try substitution
            suggestions = self.subs.suggest_for(
                source_id=fid,
                role=None,
                avoid_ids=block_ids,
                diet_must_include=diet,
                avoid_allergens=avoid_allergens,
                limit=1,
            )
            if suggestions:
                best = suggestions[0]
                adapted_ings.append({"id": best["target_id"], "qty": qty})
                change_log.append(
                    {
                        "type": "missing_sub",
                        "from_id": fid,
                        "to_id": best["target_id"],
                        "reason": best["reason"],
                        "score": best["score"],
                        "notes": best.get("notes"),
                    }
                )
            else:
                # Keep original if we couldn't find a reasonable substitute
                adapted_ings.append(ing)

        return {
            "have_count": len(have),
            "missing_count": len(missing),
            "avoid_count": len(contains_avoid),
            "ingredients_adapted": adapted_ings,
            "change_log": change_log,
        }

    def _with_labels(self, ings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Attach human labels to ingredient objects (useful for UI/debug)."""
        out: List[Dict[str, Any]] = []
        for ing in ings:
            fid = ing.get("id")
            out.append({**ing, "label": get_label(fid) if fid else None})
        return out
