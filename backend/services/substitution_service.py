# backend/services/substitution_service.py
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set

from utils import data_loader as dl


def _norm_set(vals: Optional[Iterable[str]]) -> Set[str]:
    return {str(v).strip().lower() for v in (vals or []) if v is not None}


def _clamp01(x: float) -> float:
    try:
        f = float(x)
    except Exception:
        f = 0.0
    return max(0.0, min(1.0, f))


def _as_list(x) -> List[Any]:
    return x if isinstance(x, list) else []


def _as_dict(x) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


class SubstitutionService:
    """
    Suggests ingredient substitutions using:
      1) Explicit rules from data/subs_foodon.json (list or dict)
      2) Fallback: items sharing a parent class with the source (if role-compatible)

    Each suggestion item:
        {
          "target_id": str,
          "score": float,              # 0..1
          "reason": str,               # human-friendly short explanation
          "source": "rule"|"fallback",
          "notes": str|None
        }
    """

    # Tunables
    _FALLBACK_BASE = 0.55
    _BONUS_ROLE_MATCH = 0.10
    _BONUS_SAME_PARENT = 0.05
    _BONUS_DIET_OK = 0.05
    _PENALTY_ROLE_UNKNOWN = 0.05

    def __init__(self) -> None:
        self._foodon_idx: Dict[str, Dict[str, Any]] = dl.get_foodon_index()
        # Accept dict OR list and normalize to {source_id: [rules...]}
        self._subs: Dict[str, List[Dict[str, Any]]] = self._coerce_to_rule_map(dl.get_subs())
        self._roles: Dict[str, Dict[str, Any]] = dl.get_roles()
        self._by_parent: Dict[str, Set[str]] = self._build_parent_index()

    # ------------------------- Public API ---------------------------------- #
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
        avoid_ids_set = set(avoid_ids or [])
        diet_req = _norm_set(diet_must_include)
        allergen_avoid = _norm_set(avoid_allergens)

        ruled = self._suggest_from_rules(source_id, role, avoid_ids_set, diet_req, allergen_avoid)

        fallbacks: List[Dict[str, Any]] = []
        if include_fallback and len(ruled) < limit:
            fallbacks = self._suggest_from_same_parent(
                source_id, role, avoid_ids_set, diet_req, allergen_avoid, needed=limit - len(ruled)
            )

        combined = self._merge_and_rank(ruled, fallbacks)
        return combined[:limit]

    # ---------------------- Input coercion / indexing ---------------------- #
    def _coerce_to_rule_map(self, raw: Any) -> Dict[str, List[Dict[str, Any]]]:
        """
        Accept two shapes and return:
           { source_id: [ {target_id, weight, reason?, notes?, role_constraint?}, ... ] }
        """
        # Case A: mapping form
        if isinstance(raw, dict):
            out: Dict[str, List[Dict[str, Any]]] = {}
            for src, rules in raw.items():
                bucket: List[Dict[str, Any]] = []
                for r in _as_list(rules):
                    tgt = r.get("target_id") or r.get("id")
                    if not tgt:
                        continue
                    bucket.append(
                        {
                            "target_id": str(tgt),
                            "weight": float(r.get("weight", r.get("similarity", 0.6)) or 0.6),
                            "reason": r.get("reason") or r.get("notes") or "rule-based substitute",
                            "notes": r.get("notes"),
                            "role_constraint": r.get("role_constraint"),
                        }
                    )
                if bucket:
                    out[src] = bucket
            return out

        # Case B: flat list form
        if isinstance(raw, list):
            out: Dict[str, List[Dict[str, Any]]] = {}
            for entry in raw:
                e = _as_dict(entry)
                src = e.get("source_id")
                if not src:
                    continue
                bucket = out.setdefault(src, [])
                for t in _as_list(e.get("targets")):
                    tgt = t.get("target_id") or t.get("id")
                    if not tgt:
                        continue
                    bucket.append(
                        {
                            "target_id": str(tgt),
                            "weight": float(t.get("weight", t.get("similarity", 0.6)) or 0.6),
                            "reason": t.get("reason") or t.get("notes") or "rule-based substitute",
                            "notes": t.get("notes"),
                            "role_constraint": t.get("role_constraint"),
                        }
                    )
            return out

        # Unknown shape
        return {}

    def _build_parent_index(self) -> Dict[str, Set[str]]:
        by_parent: Dict[str, Set[str]] = {}
        for fid, node in self._foodon_idx.items():
            for p in node.get("parents", []) or []:
                by_parent.setdefault(p, set()).add(fid)
        return by_parent

    # ---------------------- Rule-based suggestions ------------------------- #
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
            target_id: str = rule.get("target_id") or ""
            if not target_id or target_id == source_id or target_id in avoid_ids:
                continue

            cand = self._foodon_idx.get(target_id)
            if not cand:
                continue

            if not _diet_ok(cand, diet_req):
                continue
            if not _allergen_ok(cand, allergen_avoid):
                continue
            if not self._role_ok(target_id, role, rule.get("role_constraint")):
                continue

            score = float(rule.get("weight", 0.6))
            reason_parts = ["explicit rule"]

            if role and rule.get("role_constraint") == role:
                score += self._BONUS_ROLE_MATCH
                reason_parts.append(f"role match: {role}")

            cand_parents = set(cand.get("parents", []))
            if src_parents and cand_parents.intersection(src_parents):
                score += self._BONUS_SAME_PARENT
                reason_parts.append("same parent class")

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

    # ---------------------- Fallback suggestions --------------------------- #
    def _suggest_from_same_parent(
        self,
        source_id: str,
        role: Optional[str],
        avoid_ids: Set[str],
        diet_req: Set[str],
        allergen_avoid: Set[str],
        needed: int,
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
        src_parents = set(src.get("parents", []))

        for target_id in candidates:
            if len(out) >= needed:
                break

            if target_id in avoid_ids:
                continue

            cand = self._foodon_idx.get(target_id)
            if not cand:
                continue

            if not _diet_ok(cand, diet_req):
                continue
            if not _allergen_ok(cand, allergen_avoid):
                continue

            role_ok = self._role_ok(target_id, role, rule_role=None)
            if not role_ok:
                continue

            score = self._FALLBACK_BASE
            reason_parts = ["same parent fallback"]

            if role:
                cand_roles = self._roles_for_id(target_id)
                if role in cand_roles:
                    score += self._BONUS_ROLE_MATCH
                    reason_parts.append(f"role match: {role}")
                elif len(cand_roles) == 0:
                    score -= self._PENALTY_ROLE_UNKNOWN
                    reason_parts.append("role unknown")

            # tiny nudge for # of shared parents
            shared = src_parents.intersection(set(cand.get("parents", [])))
            score = _clamp01(score + 0.01 * len(shared))

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

        return out

    # ---------------------- Role helpers ----------------------------------- #
    def _role_ok(self, target_id: str, requested_role: Optional[str], rule_role: Optional[str]) -> bool:
        """
        - If a rule pins a role_constraint, require it (and match requested_role if provided).
        - If requested_role provided but no constraint, allow candidates that have it or unknown (penalized later).
        - If no roles involved, allow.
        """
        if rule_role:
            return (requested_role is None) or (requested_role == rule_role)

        if requested_role:
            return (requested_role in self._roles_for_id(target_id)) or True  # allow unknown; penalize later

        return True

    @lru_cache(maxsize=None)
    def _roles_for_id(self, foodon_id: str) -> Set[str]:
        """
        roles.json can hold multiple strategies:
          {
            "by_id": {"FOODON:...": ["protein"]},
            "by_parent": {"FOODON:PARENT": ["leafy_green"]},
            "by_label_substring": {"milk": ["dairy"], "yogurt": ["dairy"]}
          }
        """
        cfg = self._roles or {}
        out: Set[str] = set()

        node = self._foodon_idx.get(foodon_id) or {}
        label = (node.get("label") or "").lower()
        parents = set(node.get("parents", []))

        for r in cfg.get("by_id", {}).get(foodon_id, []):
            out.add(r)

        by_parent = cfg.get("by_parent", {})
        for p in parents:
            for r in by_parent.get(p, []):
                out.add(r)

        by_label = cfg.get("by_label_substring", {})
        for needle, rs in by_label.items():
            if needle.lower() in label:
                for r in rs:
                    out.add(r)

        return out

    # ---------------------- Merge & rank ----------------------------------- #
    def _merge_and_rank(self, a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        best: Dict[str, Dict[str, Any]] = {}
        for item in list(a) + list(b):
            tid = item["target_id"]
            if tid not in best or item["score"] > best[tid]["score"]:
                best[tid] = item
        merged = list(best.values())
        merged.sort(key=lambda x: (-x["score"], x["target_id"]))
        return merged


# ---------------------- Diet / Allergen predicates ------------------------- #
def _diet_ok(node: Dict[str, Any], required: Set[str]) -> bool:
    if not required:
        return True
    node_tags = {str(x).lower() for x in (node.get("diet_tags") or [])}
    return required.issubset(node_tags)


def _allergen_ok(node: Dict[str, Any], avoid: Set[str]) -> bool:
    if not avoid:
        return True
    node_all = {str(x).lower() for x in (node.get("allergen_tags") or [])}
    return node_all.isdisjoint(avoid)


if __name__ == "__main__":
    # quick smoke
    svc = SubstitutionService()
    src = "FOODON:03302116"  # example ID; adjust to your cache if needed
    print(svc.suggest_for(src, role="dairy", diet_must_include={"vegan"}, avoid_allergens={"contains-milk"}))
