# backend/utils/normalizer.py
from __future__ import annotations

from typing import Dict, Any
from . import data_loader as dl

class Normalizer:
    """
    Resolves free-text ingredient names into FoodOn IDs using:
    1. Mauritian alias map (mx_mauritius.json)
    2. FoodOn labels
    3. FoodOn synonyms
    4. Fuzzy match (basic for MVP)
    """

    def __init__(self):
        self.mx = dl.get_mx()
        self.label_idx = dl.get_label_index()
        self.syn_idx = dl.get_synonym_index()
        self.all_idx = dl.get_all_names_index()

    def resolve(self, raw: str) -> Dict[str, Any]:
        term = (raw or "").strip().lower()
        if not term:
            return {"foodon_id": None, "label": None, "confidence": 0.0, "source": "empty"}

        # 1. Mauritian aliases
        if term in self.mx:
            fid = self.mx[term]
            return {
                "foodon_id": fid,
                "label": dl.get_label(fid),
                "confidence": 1.0,
                "source": "mx"
            }

        # 2. Exact label
        if term in self.label_idx:
            fid = self.label_idx[term]
            return {
                "foodon_id": fid,
                "label": dl.get_label(fid),
                "confidence": 1.0,
                "source": "label"
            }

        # 3. Exact synonym
        if term in self.syn_idx:
            fid = self.syn_idx[term]
            return {
                "foodon_id": fid,
                "label": dl.get_label(fid),
                "confidence": 0.9,
                "source": "synonym"
            }

        # 4. Fuzzy fallback: naive startswith
        for key, fid in self.all_idx.items():
            if key.startswith(term[:3]):
                return {
                    "foodon_id": fid,
                    "label": dl.get_label(fid),
                    "confidence": 0.5,
                    "source": "fuzzy"
                }

        return {"foodon_id": None, "label": None, "confidence": 0.0, "source": "not_found"}
