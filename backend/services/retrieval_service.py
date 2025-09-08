# backend/services/retrieval_service.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from utils.data_loader import get_recipes, get_foodon_index
from utils.normalizer import Normalizer
from services.substitution_service import SubstitutionService


class RetrievalService:
    """
    End-to-end retrieval + adaptation:
      - normalize “have” strings to FoodOn IDs
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
        avoid_ids: Optional[Set[str]] = None,
        diet: Optional[Set[str]] = None,
        avoid_allergens: Optional[Set[str]] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Returns {count, results[]} sorted by score desc.
        """
        avoid_ids = avoid_ids or set()
        diet = set([d.lower() for d in (diet or set())])
        avoid_allergens = set([a.lower() for a in (avoid_allergens or set())])
        have_ids = self._normalize_have(have_terms_or_objs)
        have_set = set(have_ids)

        out: List[Dict[str, Any]] = []
        for idx, r in enumerate(get_recipes()):
            adapted = self._adapt_recipe(
                recipe=r,
                have_set=have_set,
                avoid_ids=avoid_ids,
                diet=diet,
                avoid_allergens=avoid_allergens,
            )
            score = self._score_recipe(adapted["have_count"], adapted["avoid_count"], adapted["missing_count"])

            out.append({
                "recipe_index": idx,
                "title": r.get("title"),
                "score": score,
                "have_count": adapted["have_count"],
                "missing_count": adapted["missing_count"],
                "avoid_count": adapted["avoid_count"],
                "ingredients_original": r.get("ingredients", []),
                "ingredients_adapted": adapted["ingredients_adapted"],
                "change_log": adapted["change_log"],
            })

        out.sort(key=lambda x: (-x["score"], x["missing_count"], x["avoid_count"], x["title"] or ""))
        return {"count": len(out[:limit]), "results": out[:limit]}

    # ---------- internal helpers ----------

    def _normalize_have(self, items: List[Any]) -> List[str]:
        ids: List[str] = []
        for x in items or []:
            if isinstance(x, dict) and x.get("id"):
                ids.append(x["id"])
                continue
            # accept {label:"..."} or plain string
            if isinstance(x, dict) and x.get("label"):
                term = str(x["label"])
            else:
                term = str(x)
            rid = self.normalizer.resolve(term)
            if rid:
                ids.append(rid)
        # dedupe preserving order
        seen: Set[str] = set()
        uniq: List[str] = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        return uniq

    def _score_recipe(self, have: int, avoid: int, missing: int) -> int:
        # MVP scoring function
        return 3 * have - 2 * avoid - 1 * missing

    def _adapt_recipe(
        self,
        recipe: Dict[str, Any],
        have_set: Set[str],
        avoid_ids: Set[str],
        diet: Set[str],
        avoid_allergens: Set[str],
    ) -> Dict[str, Any]:
        ings = recipe.get("ingredients", [])
        r_ids = [ing["id"] for ing in ings if "id" in ing]

        have = [i for i in r_ids if i in have_set]
        missing = [i for i in r_ids if i not in have_set]
        contains_avoid = [i for i in r_ids if i in avoid_ids]

        adapted_ings: List[Dict[str, Any]] = []
        change_log: List[Dict[str, Any]] = []

        # We also avoid substituting with something the user already has (to reduce duplicates)
        dont_use_ids = set(avoid_ids).union(have_set)

        for ing in ings:
            fid = ing["id"]
            qty = ing.get("qty")

            # 1) Avoided item → try to replace
            if fid in avoid_ids:
                sug = self.subs.suggest_for(
                    source_id=fid,
                    role=None,  # could pass a role later
                    avoid_ids=dont_use_ids,
                    diet_must_include=diet,
                    avoid_allergens=avoid_allergens,
                    limit=1,
                )
                if sug:
                    best = sug[0]
                    adapted_ings.append({"id": best["target_id"], "qty": qty})
                    change_log.append({
                        "type": "avoid_sub",
                        "from_id": fid,
                        "to_id": best["target_id"],
                        "reason": best["reason"],
                        "score": best["score"],
                        "notes": best.get("notes"),
                    })
                else:
                    # Remove ingredient if we truly must avoid and found nothing safe
                    change_log.append({"type": "avoid_remove", "from_id": fid, "reason": "no suitable substitute found"})
                continue

            # 2) If user already has it, keep
            if fid in have_set:
                adapted_ings.append(ing)
                continue

            # 3) Missing → try substitution
            sug = self.subs.suggest_for(
                source_id=fid,
                role=None,
                avoid_ids=dont_use_ids,
                diet_must_include=diet,
                avoid_allergens=avoid_allergens,
                limit=1,
            )
            if sug:
                best = sug[0]
                adapted_ings.append({"id": best["target_id"], "qty": qty})
                change_log.append({
                    "type": "missing_sub",
                    "from_id": fid,
                    "to_id": best["target_id"],
                    "reason": best["reason"],
                    "score": best["score"],
                    "notes": best.get("notes"),
                })
            else:
                # Keep original if we can (MVP policy)
                adapted_ings.append(ing)

        return {
            "have_count": len(have),
            "missing_count": len(missing),
            "avoid_count": len(contains_avoid),
            "ingredients_adapted": adapted_ings,
            "change_log": change_log,
        }
