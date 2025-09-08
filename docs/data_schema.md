# Data Schemas

## 1) foodon_cache.json  (array of objects)
Each object is one FoodOn concept we care about.
- id: string (FoodOn CURIE, e.g., "FOODON:03315759")
- label: string (canonical English name)
- synonyms: string[] (common/alt names)
- parents: string[] (FoodOn parent IDs)
- diet_tags: string[] (e.g., "vegan","vegetarian","pescetarian")
- allergen_tags: string[] (e.g., "contains-milk","contains-egg","gluten")

Example:
{
  "id": "FOODON:00001234",
  "label": "coconut milk",
  "synonyms": ["coco milk", "lait de coco"],
  "parents": ["FOODON:milk_or_milk_substitute"],
  "diet_tags": ["vegan", "vegetarian"],
  "allergen_tags": []
}

## 2) mx_mauritius.json  (object map)
Local/Creole/French term → FoodOn ID
Example:
{
  "dholl": "FOODON:00004567",
  "bred mouroum": "FOODON:00007890"
}

## 3) recipes.json  (array of recipe objects)
- id: string (R###)
- title: string
- tags: string[] (e.g., "seafood","spicy")
- ingredients: array of { id, qty, role }
  - id: FoodOn ID
  - qty: free text (MVP)
  - role: one of ["protein","base","fat","acid","binder","starch","aroma","veg"]
- steps: string[]
- source_url: string (optional)

Example:
{
  "id": "R001",
  "title": "Rougaille Poisson",
  "tags": ["seafood","spicy"],
  "ingredients": [
    { "id": "FOODON:fish_gener_
