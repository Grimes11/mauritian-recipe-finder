# backend/services/ontology_service.py
from __future__ import annotations

from typing import Dict, List, Optional
from utils.data_loader import get_foodon_cache

class OntologyService:
    def __init__(self) -> None:
        self.cache = get_foodon_cache()
        # Quick lookup dictionaries
        self.by_id: Dict[str, dict] = {item["id"]: item for item in self.cache}
        self.by_label: Dict[str, str] = {}
        self.by_synonym: Dict[str, str] = {}
        for item in self.cache:
            label = (item.get("label") or "").strip().lower()
            if label:
                self.by_label[label] = item["id"]
            for syn in item.get("synonyms", []) or []:
                s = (syn or "").strip().lower()
                if s:
                    self.by_synonym[s] = item["id"]

    def resolve(self, term: str) -> Optional[str]:
        """Turn user input into a FoodOn ID using label/synonyms."""
        if not term:
            return None
        t = term.strip().lower()
        if t in self.by_label:
            return self.by_label[t]
        if t in self.by_synonym:
            return self.by_synonym[t]
        return None

    def get(self, foodon_id: str) -> Optional[dict]:
        """Get the full record for a FoodOn ID."""
        return self.by_id.get(foodon_id)

    def get_parents(self, foodon_id: str) -> List[str]:
        rec = self.by_id.get(foodon_id)
        return rec.get("parents", []) if rec else []
