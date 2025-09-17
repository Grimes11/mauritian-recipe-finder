/* =========================================================================
   Mauritian Recipe Finder — main.js
   Progressive enhancement for Search, Results, and Recipe Detail pages.
   Mobile-first; safe to load on every page.
   ========================================================================= */

/* ----------------------------- Tiny Utilities ---------------------------- */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Debounce for inputs / typeahead */
function debounce(fn, wait = 250) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(null, args), wait);
  };
}

/** Toast helper (super light, no CSS dependency; uses alert fallback) */
function toast(message, type = "info", timeout = 3000) {
  const host = $("#toast-host") || (() => {
    const div = document.createElement("div");
    div.id = "toast-host";
    div.style.cssText = `
      position: fixed; inset: auto 0 1rem 0; display:flex; justify-content:center;
      pointer-events: none; z-index: 9999;
    `;
    document.body.appendChild(div);
    return div;
  })();

  const node = document.createElement("div");
  node.textContent = message;
  node.setAttribute("role", "status");
  node.style.cssText = `
    pointer-events:auto; max-width: 90vw; margin: 0.25rem; padding: .65rem .9rem;
    border-radius: 999px; color: #fff; font-size: .95rem; line-height:1.2;
    box-shadow: 0 6px 18px rgba(0,0,0,.18);
  `;
  node.style.background = type === "error" ? "#e11d48" : type === "success" ? "#16a34a" : "#2563eb";
  host.appendChild(node);
  setTimeout(() => node.remove(), timeout);
}

/** JSON fetch with standard error handling */
async function fetchJSON(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data && data.error) msg = data.error;
    } catch {}
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/* ----------------------------- Local Storage ----------------------------- */

const LS_KEYS = {
  favorites: "mrf:favorites",         // array of recipe_index
  lastSearch: "mrf:last-search",      // payload we sent to /search
};

function getFavorites() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEYS.favorites)) || [];
  } catch {
    return [];
  }
}
function setFavorites(arr) {
  localStorage.setItem(LS_KEYS.favorites, JSON.stringify(arr));
}
function isFavorite(id) {
  return getFavorites().includes(id);
}
function toggleFavorite(id) {
  const favs = getFavorites();
  const i = favs.indexOf(id);
  if (i === -1) favs.push(id);
  else favs.splice(i, 1);
  setFavorites(favs);
  return favs.includes(id);
}

function saveLastSearch(payload) {
  localStorage.setItem(LS_KEYS.lastSearch, JSON.stringify(payload));
  // small bonus: session fallback used by results page if URL has no params
  sessionStorage.setItem("mrf:last-query", JSON.stringify(payload));
}
function loadLastSearch() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEYS.lastSearch)) || null;
  } catch {
    return null;
  }
}

/* --------------------------- Typeahead Bootstrap ------------------------- */

async function initTypeahead() {
  const datalists = $$("datalist[data-populate='ingredients']");
  if (datalists.length === 0) return;

  try {
    const ta = await fetchJSON("/typeahead");
    const opts = ta.slice(0, 1000) // cap to avoid overloading DOM
      .map((x) => ({ id: x.id, term: x.term || x.q || "" }))
      .filter((x) => x.term);

    datalists.forEach((dl) => {
      dl.innerHTML = "";
      opts.forEach(({ term, id }) => {
        const opt = document.createElement("option");
        opt.value = term;
        opt.dataset.id = id;
        dl.appendChild(opt);
      });
    });
  } catch (err) {
    console.warn("Typeahead init failed:", err);
  }
}

/* ----------------------------- Search Handling --------------------------- */

function parseCommaList(value) {
  return (value || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function collectSearchPayload(form) {
  const haveInput = $("#have-input", form) || $("[name='have']", form);
  const avoidInput = $("#avoid-input", form) || $("[name='avoid']", form);

  const have = haveInput ? parseCommaList(haveInput.value) : [];
  const avoid = avoidInput ? parseCommaList(avoidInput.value) : [];

  const diet = $$("input[type='checkbox'][data-role='diet']:checked", form).map((el) =>
    (el.value || "").trim().toLowerCase()
  );

  const avoid_allergens = $$("input[type='checkbox'][data-role='allergen']:checked", form).map((el) =>
    (el.value || "").trim().toLowerCase()
  );

  const limitInput = $("#limit", form);
  const limit = limitInput && limitInput.value ? parseInt(limitInput.value, 10) || 20 : 20;

  const attachLabelsInput = $("#attach-labels", form);
  const attach_labels = !!(attachLabelsInput && attachLabelsInput.checked);

  return { have, avoid, diet, avoid_allergens, limit, attach_labels };
}

function showSpinner(btnOrForm, show = true) {
  const spinner = $("#spinner", btnOrForm) || $("#spinner");
  if (!spinner) return;
  spinner.style.display = show ? "inline-block" : "none";
}

function injectResults(results, opts = {}) {
  const root = $("#results-root");
  if (!root) return;

  if (!results || !Array.isArray(results.results) || results.results.length === 0) {
    root.innerHTML = renderEmptyState();
    return;
  }

  root.innerHTML = results.results.map(renderResultCard).join("");
  wireFavoriteButtons(root);
  root.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderEmptyState() {
  return `
  <section class="empty-state" aria-live="polite">
    <div class="empty-emoji" role="img" aria-label="No results">😢</div>
    <h2>No Recipes Found</h2>
    <p>We couldn’t find any recipes matching your ingredients.</p>
    <div class="tips">
      <div class="tip">
        <span class="tip-emoji">🔍</span>
        <div>
          <strong>Try removing one ingredient</strong>
          <div class="muted">Simplify your search for better results.</div>
        </div>
      </div>
      <div class="tip">
        <span class="tip-emoji">✨</span>
        <div>
          <strong>Broaden your search</strong>
          <div class="muted">Use general terms or categories.</div>
        </div>
      </div>
    </div>
    <div class="empty-actions">
      <a class="btn" href="/search">Try Again</a>
      <a class="btn btn-outline" href="/">Go Back to Search</a>
    </div>
  </section>
  `;
}

function renderResultCard(item) {
  const favOn = isFavorite(item.recipe_index);
  const heart = favOn ? "♥" : "♡";
  const heartTitle = favOn ? "Remove from favorites" : "Save to favorites";

  const ingList = (item.ingredients_adapted || [])
    .slice(0, 5)
    .map((ing) => {
      const label = ing.label || ing.id;
      const qty = ing.qty ? `<span class="qty">${escapeHtml(ing.qty)}</span>` : "";
      return `<li>${escapeHtml(label)} ${qty}</li>`;
    })
    .join("");

  return `
  <article class="recipe-card" data-recipe-id="${item.recipe_index}">
    <header class="recipe-card__header">
      <h3 class="recipe-card__title">${escapeHtml(item.title || "Untitled")}</h3>
      <button class="fav-btn" type="button" data-action="toggle-fav" aria-label="${heartTitle}" title="${heartTitle}">
        <span class="fav-emoji" aria-hidden="true">${heart}</span>
      </button>
    </header>

    <div class="recipe-card__score">
      <span class="score-chip" title="Score">${item.score ?? 0}</span>
      <span class="meta-chip" title="You have">${item.have_count ?? 0} have</span>
      <span class="meta-chip" title="Missing">${item.missing_count ?? 0} missing</span>
    </div>

    <ul class="recipe-card__ings">
      ${ ingList || `<li class="muted">Ingredients will appear here</li>` }
    </ul>

    <footer class="recipe-card__footer">
      <a class="btn btn-small" href="/recipes/${item.recipe_index}">View Details</a>
    </footer>
  </article>
  `;
}

function wireFavoriteButtons(root = document) {
  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action='toggle-fav']");
    if (!btn) return;

    const card = e.target.closest("[data-recipe-id]");
    if (!card) return;

    const id = parseInt(card.dataset.recipeId, 10);
    const on = toggleFavorite(id);
    btn.querySelector(".fav-emoji").textContent = on ? "♥" : "♡";
    btn.setAttribute("aria-label", on ? "Remove from favorites" : "Save to favorites");
    toast(on ? "Saved to favorites" : "Removed from favorites", on ? "success" : "info");
  });
}

/* ------------------------- Search Form Enhancement ------------------------ */

function initSearchForm() {
  const form = document.querySelector("[data-search-form]") || document.getElementById("search-form");
  if (!form) return;

  // If we are on /search, submit should redirect to /results with query params
  if (location.pathname.startsWith("/search")) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();

      const payload = collectSearchPayload(form);
      saveLastSearch(payload);

      const qs = new URLSearchParams();
      if (payload.have.length)  qs.set("have",  payload.have.join(","));
      if (payload.avoid.length) qs.set("avoid", payload.avoid.join(","));
      if (payload.diet.length)  qs.set("diet",  payload.diet.join(","));
      if (payload.avoid_allergens.length) qs.set("avoid_allergens", payload.avoid_allergens.join(","));
      if (payload.limit && payload.limit !== 20) qs.set("limit", String(payload.limit));
      if (payload.attach_labels) qs.set("attach_labels", "1");

      // Also keep a session copy of the exact query body for results.html fallback
      sessionStorage.setItem("lastQuery", JSON.stringify(payload));

      const url = `/results${qs.toString() ? "?" + qs.toString() : "?limit=20"}`;
      window.location.assign(url);
    });
    return; // do not attach AJAX path on /search
  }

  // (For any other page embedding a search form, keep AJAX)
  const last = loadLastSearch();
  if (last) {
    const haveInput = $("#have-input", form) || $("[name='have']", form);
    const avoidInput = $("#avoid-input", form) || $("[name='avoid']", form);
    if (haveInput && Array.isArray(last.have)) haveInput.value = last.have.join(", ");
    if (avoidInput && Array.isArray(last.avoid)) avoidInput.value = last.avoid.join(", ");

    if (Array.isArray(last.diet)) {
      last.diet.forEach((v) => {
        const cb = form.querySelector(`input[type='checkbox'][data-role='diet'][value="${CSS.escape(v)}"]`);
        if (cb) cb.checked = true;
      });
    }
    if (Array.isArray(last.avoid_allergens)) {
      last.avoid_allergens.forEach((v) => {
        const cb = form.querySelector(`input[type='checkbox'][data-role='allergen'][value="${CSS.escape(v)}"]`);
        if (cb) cb.checked = true;
      });
    }
    if (typeof last.limit === "number") {
      const limitInput = $("#limit", form);
      if (limitInput) limitInput.value = String(last.limit);
    }
    if (typeof last.attach_labels === "boolean") {
      const attach = $("#attach-labels", form);
      if (attach) attach.checked = last.attach_labels;
    }
  }

  // AJAX submit (non-/search pages)
  form.addEventListener("submit", async (e) => {
    if (form.hasAttribute("data-no-ajax")) return;

    e.preventDefault();
    const submitBtn = $("button[type='submit'], input[type='submit']", form);
    submitBtn && (submitBtn.disabled = true);
    showSpinner(form, true);

    const payload = collectSearchPayload(form);
    saveLastSearch(payload);

    try {
      const data = await fetchJSON("/search", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      if ($("#results-root")) {
        injectResults(data);
      } else {
        sessionStorage.setItem("mrf:last-results", JSON.stringify(data));
        window.location.assign("/results");
      }
    } catch (err) {
      console.error(err);
      toast(`Search failed: ${err.message || "Server error"}`, "error");
    } finally {
      showSpinner(form, false);
      submitBtn && (submitBtn.disabled = false);
    }
  });
}

/* --------------------------- Results Page Boost --------------------------- */

function initResultsPage() {
  // If the server-rendered results page uses a container id, prefer that.
  const root = $("#results-root");

  // Our current templates use #results-list and inline page JS.
  // This is a safety fallback: if there are NO query params AND we do have
  // a cached payload in sessionStorage, let’s populate #results-list
  // so the page doesn’t look empty.
  const list = $("#results-list");
  const hasQueryParams = location.search.length > 1;

  if (!list && !root) return;

  if (!hasQueryParams) {
    try {
      const cached = JSON.parse(sessionStorage.getItem("mrf:last-results") || "null");
      if (cached) {
        if (root) {
          injectResults(cached);
        } else if (list) {
          // Render minimal cards similar to results.html behavior
          list.innerHTML = "";
          const items = Array.isArray(cached.results) ? cached.results : [];
          if (!items.length) return;

          items.forEach(item => {
            const card = document.createElement("article");
            card.className = "card recipe-card";
            const idx = item.recipe_index;
            const title = (item.title || "Recipe");
            const quick = `
              <div class="quick">
                <span title="match score">⭐ ${item.score ?? 0}</span>
                <span title="you have">${item.have_count ?? 0} have</span>
                <span title="missing">${item.missing_count ?? 0} missing</span>
                ${(item.avoid_count ?? 0) ? `<span class="warn" title="avoid">${item.avoid_count} avoided</span>` : ""}
              </div>`;
            const ingPreview = (item.ingredients_adapted || []).slice(0,4)
              .map(i => i.label || i.id || "ingredient").join(", ");

            card.innerHTML = `
              <div class="card__body">
                <div class="card__row">
                  <h3 class="h4">${escapeHtml(title)}</h3>
                  <button class="icon-btn" title="Favorite (coming soon)" aria-label="Favorite" disabled>❤️</button>
                </div>
                ${quick}
                <p class="muted">Ingredients: ${escapeHtml(ingPreview)}${(item.ingredients_adapted || []).length > 4 ? "…" : ""}</p>
                <div class="actions">
                  <a class="btn btn-secondary" href="/recipes/${idx}">View Details</a>
                  <button class="btn" disabled title="Coming soon">Save</button>
                </div>
              </div>`;
            list.appendChild(card);
          });
        }
      }
    } catch {}
  }

  if (root) wireFavoriteButtons(root);
  if (list) wireFavoriteButtons(list);
}

/* -------------------------- Recipe Detail Helpers ------------------------- */

function initRecipeDetailPage() {
  const favBtn = $("[data-detail-fav]");
  if (!favBtn) return;

  const id = parseInt(favBtn.dataset.recipeId, 10);
  const favEmoji = $(".fav-emoji", favBtn);

  if (isFavorite(id)) {
    favEmoji && (favEmoji.textContent = "♥");
    favBtn.setAttribute("aria-label", "Remove from favorites");
  }

  favBtn.addEventListener("click", () => {
    const on = toggleFavorite(id);
    favEmoji && (favEmoji.textContent = on ? "♥" : "♡");
    favBtn.setAttribute("aria-label", on ? "Remove from favorites" : "Save to favorites");
    toast(on ? "Saved to favorites" : "Removed from favorites", on ? "success" : "info");
  });
}

/* --------------------------- Input niceties (UX) -------------------------- */

function initInputChips() {
  const have = $("#have-input");
  const avoid = $("#avoid-input");

  [have, avoid].forEach((inp) => {
    if (!inp) return;
    inp.addEventListener(
      "change",
      () => {
        const uniq = [...new Set(parseCommaList(inp.value))];
        inp.value = uniq.join(", ");
      },
      false
    );
  });
}

/* --------------------------------- Helpers -------------------------------- */

function escapeHtml(str) {
  return (str ?? "")
    .toString()
    .replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]));
}

/* --------------------------------- Init ----------------------------------- */

document.addEventListener("DOMContentLoaded", async () => {
  await initTypeahead();     // harmless if /typeahead missing
  initSearchForm();          // now handles /search submit -> /results?...
  initResultsPage();         // adds sessionStorage-based fallback
  initRecipeDetailPage();    // enhances /recipe(s)/<id> page
  initInputChips();          // small input cleanups
});
