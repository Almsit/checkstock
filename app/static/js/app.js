import { request } from "./api.js";
import { setupForms } from "./forms.js";
import {
  renderHistory,
  renderMovements,
  renderStock,
  renderSummary,
  renderWarehouses,
} from "./views.js";

let state = null;
let movements = { items: [], next: null };
let history = { items: [], next: null };
let generation = 0;
let toastTimer;
let searchTimer;
let stockCursor = null;
const stockPages = [];
const errorBox = document.getElementById("page-error");
const refreshButton = document.getElementById("refresh");

function showError(error) {
  errorBox.textContent = error.message;
  errorBox.hidden = false;
}

function renderLists() {
  renderMovements(movements.items);
  renderHistory(history.items);
  document.getElementById("more-movements").hidden = !movements.next;
  document.getElementById("more-history").hidden = !history.next;
}

async function refresh() {
  const current = ++generation;
  refreshButton.disabled = true;
  errorBox.hidden = true;
  try {
    const search = document.getElementById("stock-search").value;
    const parameters = new URLSearchParams({ q: search });
    if (stockCursor) parameters.set("after", stockCursor);
    const results = await Promise.all([
      request(`/state?${parameters}`),
      request("/movements"),
      request("/history"),
    ]);
    if (current !== generation) return;
    [state, movements, history] = results;
    renderSummary(state);
    renderStock(state, document.getElementById("stock-search").value);
    renderWarehouses(state);
    renderLists();
    document.getElementById("stock-pagination").hidden = !state.next && stockPages.length === 0;
    document.getElementById("previous-stock").disabled = stockPages.length === 0;
    document.getElementById("next-stock").disabled = !state.next;
    document.getElementById("updated-at").textContent =
      `Обновлено в ${new Intl.DateTimeFormat("ru-RU", { timeStyle: "short" }).format(new Date())}`;
  } catch (error) {
    if (current === generation) {
      showError(error);
      document.getElementById("updated-at").textContent = state
        ? "Данные могли измениться. Обновите страницу."
        : "Не удалось загрузить данные";
    }
  } finally {
    if (current === generation) refreshButton.disabled = false;
  }
}

setupForms(
  () => state,
  async () => {
    const toast = document.getElementById("toast");
    clearTimeout(toastTimer);
    toast.textContent = "Операция сохранена";
    toast.hidden = false;
    toastTimer = setTimeout(() => {
      toast.hidden = true;
    }, 3500);
    await refresh();
  },
);
refreshButton.addEventListener("click", refresh);
document.getElementById("stock-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    stockCursor = null;
    stockPages.length = 0;
    refresh();
  }, 250);
});
document.getElementById("next-stock").addEventListener("click", () => {
  if (!state?.next || refreshButton.disabled) return;
  stockPages.push(stockCursor);
  stockCursor = state.next;
  refresh();
});
document.getElementById("previous-stock").addEventListener("click", () => {
  if (!stockPages.length || refreshButton.disabled) return;
  stockCursor = stockPages.pop();
  refresh();
});
document.querySelector(".tabs").addEventListener("click", (event) => {
  const selected = event.target.closest("[data-panel]");
  if (!selected) return;
  for (const tab of document.querySelectorAll("[data-panel]")) {
    const active = tab === selected;
    tab.classList.toggle("active", active);
    if (active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
    document.getElementById(`panel-${tab.dataset.panel}`).hidden = !active;
  }
});
for (const section of ["movements", "history"]) {
  document.getElementById(`more-${section}`).addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const current = generation;
    button.disabled = true;
    try {
      const list = section === "movements" ? movements : history;
      const page = await request(`/${section}?before=${list.next}`);
      if (current !== generation) return;
      list.items.push(...page.items);
      list.next = page.next;
      renderLists();
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });
}
refresh();
