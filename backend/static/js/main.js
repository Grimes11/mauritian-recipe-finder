// Basic, dependency-free typeahead using <datalist>.
// Assumes an <input id="ingredients-input" name="q"> exists on /search and /home.

let __typeaheadCache = null;

async function loadTypeahead() {
  if (__typeaheadCache) return __typeaheadCache;
  const res = await fetch('/typeahead.json', { cache: 'force-cache' });
  __typeaheadCache = await res.json();
  return __typeaheadCache;
}

async function attachTypeahead(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;

  const listId = inputId + "-datalist";
  let datalist = document.getElementById(listId);
  if (!datalist) {
    datalist = document.createElement('datalist');
    datalist.id = listId;
    document.body.appendChild(datalist);
    input.setAttribute('list', listId);
  }

  const data = await loadTypeahead();

  // If your browser slows down with very large lists,
  // keep only top N in DOM. Otherwise, use all.
  // Here we include ALL as you requested earlier.
  datalist.innerHTML = data
    .map(x => `<option value="${x.label}">${x.label}</option>`)
    .join('');

  // Optional: on input, if user types something that matches a synonym,
  // replace with the canonical label (nice for consistency).
  input.addEventListener('change', () => {
    const val = input.value.trim().toLowerCase();
    if (!val) return;
    const match = data.find(x =>
      x.label.toLowerCase() === val ||
      (Array.isArray(x.synonyms) && x.synonyms.some(s => (s || "").toLowerCase() === val))
    );
    if (match) input.value = match.label; // normalize to canonical label
  });
}

document.addEventListener('DOMContentLoaded', () => {
  // Hook typeahead to your primary search field
  attachTypeahead('ingredients-input');
});
