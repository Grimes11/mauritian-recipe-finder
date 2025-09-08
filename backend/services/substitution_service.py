from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# We reuse all loaders from utils
from utils import data_loader as dl


class SubstitutionService:
    """
    Suggests ingredient substitutions using:
      1) Explicit rules from data/subs_foodon.json
      2) Fallback: items sharing a parent class with the source (if role-compatible)
    It also enforces optional constraints: avoid IDs, avoid allergens, and diet tags.

    Return shape (each suggestion):
        {
          "target_id": str,
          "score": float,              # 0..1
          "reason": str,               # short human-friendly explanation
          "source": "rule"|"fallback", # where the suggestion came from
          "notes": str|None            # optional notes from rule
        }
    """

    # --- Tunables for scoring / fallback ------------------------------------ #
    _FALLBACK_BASE = 0.55           # base score for same-parent fallback
    _BONUS_ROLE_MATCH = 0.10        # when candidate role matches requested
    _BONUS_SAME_PARENT = 0.05       # explicit same parent (even in rule)
    _BONUS_DIET_OK = 0.05           # candidate meets diet constraint
    _PENALTY_ROLE_UNKNOWN = 0.05    # role of candidate unknown but required

    def __init__(self) -> None:
        # Snapshots of all data; all of these are memoized in data_loader.
        self._foodon_idx: Dict[str, Dict[str, Any]] = dl.get_foodon_index()
        self._subs: Dict[str, List[Dict[str, Any]]] = dl.get_subs()
        self._roles: Dict[str, Dict[str, Any]] = dl.get_roles()

        # Build quick reverse index: parent_id -> {children_ids}
        self._by_parent: Dict[str, Set[str]] = self._build_parent_index()

    # ----------------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------------- #
    def suggest_for(
        self,
        source_id: str,
        *,
        role: Optional[str] = None,
        avoid_ids: Optional[Iterable[str]] = None,
        diet_must_include: Optional[Iterable[str]] = None,
        avoid_allergens: Optional[Iterable[str]] = None,
        limit: int = 5,
        include_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Compute best substitutions for `source_id`.

        Args:
            source_id: FoodOn ID you want to replace.
            role: Optional functional role for this ingredient (e.g. "protein", "fat", "aroma").
            avoid_ids: Any FoodOn IDs the user wants to avoid (or already has; you can filter either way).
            diet_must_include: Ex: {"vegan"} or {"vegetarian"} — candidate must have these diet tags.
            avoid_allergens: Ex: {"contains-milk", "shellfish"} — candidate must NOT include any of these.
            limit: Max number of suggestions to return.
            include_fallback: Whether to use same-parent fallback when there are few/no rules.

        Returns:
            A sorted list (best first) of suggestion dicts.
        """
        avoid_ids_set = set(avoid_ids or [])
        diet_req = set(_norm_set(diet_must_include))
        allergen_avoid = set(_norm_set(avoid_allergens))

        # Gather rule-based suggestions
        ruled: List[Dict[str, Any]] = self._suggest_from_rules(
            source_id, role, avoid_ids_set, diet_req, allergen_avoid
        )

        # If needed, add fallback suggestions
        fallbacks: List[Dict[str, Any]] = []
        if include_fallback:
            fallbacks = self._suggest_from_same_parent(
                source_id, role, avoid_ids_set, diet_req, allergen_avoid
            )

        # Merge, dedupe (by target_id), re-rank
        combined = self._merge_and_rank(ruled, fallbacks)

        # Trim
        return combined[:limit]

    # ----------------------------------------------------------------------- #
    # Internals — rule-based suggestions
    # ----------------------------------------------------------------------- #
    def _suggest_from_rules(
        self,
        source_id: str,
        role: Optional[str],
        avoid_ids: Set[str],
        diet_req: Set[str],
        allergen_avoid: Set[str],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        rules = self._subs.get(source_id) or []

        src_node = self._foodon_idx.get(source_id)
        src_parents = set(src_node.get("parents", [])) if src_node else set()

        for rule in rules:
            target_id: str = rule.get("target_id") or rule.get("id") or ""
            if not target_id or target_id == source_id:
                continue
            if target_id in avoid_ids:
                continue

            cand = self._foodon_idx.get(target_id)
            if not cand:
                continue

            # Constraints
            if not _diet_ok(cand, diet_req):
                continue
            if not _allergen_ok(cand, allergen_avoid):
                continue
            if not self._role_ok(target_id, role, rule.get("role_constraint")):
                continue

            # Score
            score = float(rule.get("weight", 0.6))
            reason_parts = ["explicit rule"]
            if role and rule.get("role_constraint") == role:
                score += self._BONUS_ROLE_MATCH
                reason_parts.append(f"role match: {role}")

            # Tiny bonus if they share a parent
            cand_parents = set(cand.get("parents", []))
            if src_parents and cand_parents.intersection(src_parents):
                score += self._BONUS_SAME_PARENT
                reason_parts.append("same parent class")

            # Diet bonus
            if diet_req:
                score += self._BONUS_DIET_OK
                reason_parts.append("meets diet")

            out.append(
                {
                    "target_id": target_id,
                    "score": _clamp01(score),
                    "reason": "; ".join(reason_parts),
                    "source": "rule",
                    "notes": rule.get("notes"),
                }
            )

        return out

    # ----------------------------------------------------------------------- #
    # Internals — fallback (same-parent) suggestions
    # ----------------------------------------------------------------------- #
    def _suggest_from_same_parent(
        self,
        source_id: str,
        role: Optional[str],
        avoid_ids: Set[str],
        diet_req: Set[str],
        allergen_avoid: Set[str],
    ) -> List[Dict[str, Any]]:
        src = self._foodon_idx.get(source_id)
        if not src:
            return []

        candidates: Set[str] = set()
        for parent in src.get("parents", []) or []:
            for sib in self._by_parent.get(parent, set()):
                if sib != source_id:
                    candidates.add(sib)

        out: List[Dict[str, Any]] = []
        for target_id in candidates:
            if target_id in avoid_ids:
                continue
            cand = self._foodon_idx.get(target_id)
            if not cand:
                continue

            if not _diet_ok(cand, diet_req):
                continue
            if not _allergen_ok(cand, allergen_avoid):
                continue

            # Role handling
            role_ok = self._role_ok(target_id, role, rule_role=None)
            if not role_ok:
                continue

            # Score starts from fallback base
            score = self._FALLBACK_BASE
            reason_parts = ["same parent fallback"]

            # If we can positively assert role match, add bonus;
            # if role required but unknown for candidate, apply small penalty.
            if role:
                cand_roles = self._roles_for_id(target_id)
                if role in cand_roles:
                    score += self._BONUS_ROLE_MATCH
                    reason_parts.append(f"role match: {role}")
                elif len(cand_roles) == 0:
                    score -= self._PENALTY_ROLE_UNKNOWN
                    reason_parts.append("role unknown")

            if diet_req:
                score += self._BONUS_DIET_OK
                reason_parts.append("meets diet")

            out.append(
                {
                    "target_id": target_id,
                    "score": _clamp01(score),
                    "reason": "; ".join(reason_parts),
                    "source": "fallback",
                    "notes": None,
                }
            )

        # Prefer higher-similarity siblings by crude heuristic:
        #   more shared parents -> slightly higher score
        src_parents = set(src.get("parents", []))
        for item in out:
            tgt = self._foodon_idx.get(item["target_id"])
            shared = src_parents.intersection(set(tgt.get("parents", [])))
            item["score"] = _clamp01(item["score"] + 0.01 * len(shared))  # tiny nudge

        return out

    # ----------------------------------------------------------------------- #
    # Role & constraints helpers
    # ----------------------------------------------------------------------- #
    def _role_ok(
        self,
        target_id: str,
        requested_role: Optional[str],
        rule_role: Optional[str],
    ) -> bool:
        """
        role resolution:
          - if rule states a role_constraint, candidate must match it AND requested_role if provided.
          - else if requested_role provided: candidate should support it; if unknown, allow but penalize later.
          - else: no role constraint.
        """
        if rule_role:
            # Rule pins the role for this substitution
            return (requested_role is None) or (requested_role == rule_role)

        if requested_role:
            return requested_role in self._roles_for_id(target_id) or True  # allow unknown; score penalty later

        return True

    @lru_cache(maxsize=None)
    def _roles_for_id(self, foodon_id: str) -> Set[str]:
        """
        Flexible role resolution from roles.json.
        Expected (any/all may exist):
            {
              "by_id": {"FOODON:...": ["protein", ...]},
              "by_parent": {"FOODON:parentID": ["leafy_green", ...]},
              "by_label_substring": {"milk": ["dairy"], "yogurt": ["dairy"]}
            }
        """
        roles_cfg = self._roles or {}
        out: Set[str] = set()

        node = self._foodon_idx.get(foodon_id) or {}
        label = (node.get("label") or "").lower()
        parents = set(node.get("parents", []))

        # exact id
        for r in roles_cfg.get("by_id", {}).get(foodon_id, []):
            out.add(r)

        # parent hits
        by_parent = roles_cfg.get("by_parent", {})
        for p in parents:
            for r in by_parent.get(p, []):
                out.add(r)

        # label substring
        by_label = roles_cfg.get("by_label_substring", {})
        for needle, rs in by_label.items():
            if needle.lower() in label:
                for r in rs:
                    out.add(r)

        return out

    # ----------------------------------------------------------------------- #
    # Index builders & merging
    # ----------------------------------------------------------------------- #
    def _build_parent_index(self) -> Dict[str, Set[str]]:
        by_parent: Dict[str, Set[str]] = {}
        for _id, node in self._foodon_idx.items():
            for p in node.get("parents", []) or []:
                by_parent.setdefault(p, set()).add(_id)
        return by_parent

    def _merge_and_rank(
        self,
        a: List[Dict[str, Any]],
        b: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge two suggestion lists, prefer higher score per target_id.
        """
        best: Dict[str, Dict[str, Any]] = {}
        for item in list(a) + list(b):
            tid = item["target_id"]
            if tid not in best or item["score"] > best[tid]["score"]:
                best[tid] = item

        merged = list(best.values())
        merged.sort(key=lambda x: (-x["score"], x["target_id"]))
        return merged


# --------------------------------------------------------------------------- #
# Helper predicates (diet / allergen)
# --------------------------------------------------------------------------- #
def _diet_ok(node: Dict[str, Any], required: Set[str]) -> bool:
    if not required:
        return True
    node_tags = set(x.lower() for x in (node.get("diet_tags") or []))
    # require all requested diet tags to be present
    return required.issubset(node_tags)


def _allergen_ok(node: Dict[str, Any], avoid: Set[str]) -> bool:
    if not avoid:
        return True
    node_all = set(x.lower() for x in (node.get("allergen_tags") or []))
    return node_all.isdisjoint(avoid)


def _norm_set(vals: Optional[Iterable[str]]) -> Set[str]:
    return {v.lower() for v in (vals or []) if v}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# --------------------------------------------------------------------------- #
# Example manual test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    svc = SubstitutionService()

    # Example: replace cow milk with vegan options, avoid allergens "contains-milk"
    src = "FOODON:03302116"  # cow whole milk (or close ID; adjust to your cache)
    suggestions = svc.suggest_for(
        src,
        role="dairy",
        avoid_ids={"FOODON:00005478"},            # avoid exact whole milk
        diet_must_include={"vegan"},              # want vegan substitutes
        avoid_allergens={"contains-milk"},        # no milk allergens
        limit=5,
    )
    print("Suggestions:")
    for s in suggestions:
        print(f"- {s['target_id']}  score={s['score']:.2f}  [{s['source']}] {s['reason']}  notes={s['notes']}")
