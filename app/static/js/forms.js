import { request } from "./api.js";
import { escape, number } from "./views.js";

const descriptions = {
  receipt: "Товар сразу появится на выбранном фулфилменте.",
  transfer: "После отправки товар будет в пути. На другом ФФ он появится только после приёмки.",
  fbs: "Товар останется на ФФ, но будет недоступен для других отправок до отмены или отправки резерва.",
  fbo: "Товар покинет ФФ и будет в пути до подтверждения приёмки маркетплейсом.",
};
const submitLabels = {
  receipt: "Оформить приход",
  transfer: "Отправить на ФФ",
  fbs: "Зарезервировать",
  fbo: "Отправить FBO",
};

export function setupForms(getState, afterChange) {
  const operationDialog = document.getElementById("operation-dialog");
  const operationForm = document.getElementById("operation-form");
  const warehouseDialog = document.getElementById("warehouse-dialog");
  const warehouseForm = document.getElementById("warehouse-form");
  const confirmDialog = document.getElementById("confirm-dialog");
  const confirmForm = document.getElementById("confirm-form");
  let warehouseId = null;
  let confirmedAction = null;
  let submitting = false;
  let balanceVersion = 0;
  let productVersion = 0;
  let productTimer;

  async function updateBalanceHint() {
    const version = ++balanceVersion;
    const productId = Number(operationForm.elements.product_id.value);
    const sourceId = Number(operationForm.elements.source_id.value);
    const hint = document.getElementById("balance-hint");
    hint.hidden = operationForm.elements.kind.value === "receipt";
    if (hint.hidden) return;
    if (!productId || !sourceId) {
      hint.textContent = "Выберите товар и фулфилмент.";
      return;
    }
    hint.textContent = "Проверяем остаток…";
    try {
      const balances = await request(`/products/${productId}/balances`);
      if (version !== balanceVersion) return;
      const balance = balances.find((row) => row.warehouse_id === sourceId);
      hint.textContent = `На выбранном ФФ свободно ${number(balance?.available || 0)} шт. · в резерве ${number(balance?.reserved || 0)} шт.`;
    } catch (error) {
      if (version === balanceVersion) hint.textContent = error.message;
    }
  }

  async function findProducts() {
    const version = ++productVersion;
    const term = operationForm.elements.product_search.value;
    document.getElementById("operation-submit").disabled = true;
    operationForm.elements.product_id.disabled = true;
    try {
      const result = await request(`/products?q=${encodeURIComponent(term)}`);
      if (version !== productVersion) return;
      operationForm.elements.product_id.innerHTML = result.items
        .map((row) => `<option value="${row.id}">${escape(row.sku)} · ${escape(row.name)}</option>`)
        .join("");
      if (!result.items.length)
        operationForm.elements.product_id.innerHTML = '<option value="">Товары не найдены</option>';
      operationForm.elements.product_id.disabled = false;
      document.getElementById("operation-submit").disabled = !result.items.length;
      updateBalanceHint();
    } catch (error) {
      if (version !== productVersion) return;
      operationForm.elements.product_id.innerHTML =
        '<option value="">Не удалось загрузить товары</option>';
      const errorBox = document.getElementById("operation-error");
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    }
  }

  function updateOperation() {
    const state = getState();
    const kind = operationForm.elements.kind.value;
    const source = operationForm.elements.source_id;
    const destination = operationForm.elements.destination_id;
    const sourceValue = source.value;
    const destinationValue = destination.value;
    const sourceWarehouses = state.warehouses.filter((row) => !row.archived || kind === "transfer");
    source.innerHTML = sourceWarehouses
      .map(
        (row) =>
          `<option value="${row.id}">${escape(row.name)}${row.archived ? " (архив)" : ""}</option>`,
      )
      .join("");
    if (sourceWarehouses.some((row) => String(row.id) === sourceValue)) source.value = sourceValue;
    const destinations = state.warehouses.filter(
      (row) => !row.archived && (kind !== "transfer" || String(row.id) !== source.value),
    );
    destination.innerHTML = destinations
      .map((row) => `<option value="${row.id}">${escape(row.name)}</option>`)
      .join("");
    if (destinations.some((row) => String(row.id) === destinationValue))
      destination.value = destinationValue;
    document.getElementById("source-field").hidden = kind === "receipt";
    source.required = kind !== "receipt";
    source.disabled = kind === "receipt";
    document.getElementById("destination-field").hidden = !["receipt", "transfer"].includes(kind);
    destination.required = ["receipt", "transfer"].includes(kind);
    destination.disabled = !destination.required;
    const external = operationForm.elements.destination;
    document.getElementById("external-field").hidden = !["fbs", "fbo"].includes(kind);
    external.required = ["fbs", "fbo"].includes(kind);
    external.disabled = !external.required;
    document.getElementById("external-label").textContent =
      kind === "fbs" ? "Клиент / заказ" : "Склад маркетплейса";
    external.placeholder = kind === "fbs" ? "Например, клиент WB-1042" : "Например, WB · Коледино";
    document.getElementById("operation-description").textContent = descriptions[kind];
    document.getElementById("operation-submit").textContent = submitLabels[kind];
    updateBalanceHint();
  }

  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("click", (event) => {
      if (event.target.closest("[data-close]") && !submitting) dialog.close();
    });
    dialog.addEventListener("cancel", (event) => {
      if (submitting) event.preventDefault();
    });
  }

  async function submit(event, dialog, errorId, action) {
    event.preventDefault();
    if (submitting) return;
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const errorBox = document.getElementById(errorId);
    errorBox.hidden = true;
    submitting = true;
    const controls = [...form.querySelectorAll("button, input, select")];
    const disabled = controls.map((control) => control.disabled);
    const result = action();
    controls.forEach((control) => {
      control.disabled = true;
    });
    let success = false;
    try {
      await result;
      dialog.close();
      success = true;
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    } finally {
      controls.forEach((control, index) => {
        control.disabled = disabled[index];
      });
      submitting = false;
    }
    if (success) await afterChange();
  }

  document.getElementById("new-operation").addEventListener("click", () => {
    if (!getState()) return;
    clearTimeout(productTimer);
    operationForm.reset();
    operationForm.elements.product_id.innerHTML = '<option value="">Загрузка товаров…</option>';
    document.getElementById("operation-error").hidden = true;
    updateOperation();
    operationDialog.showModal();
    findProducts();
  });
  operationForm.elements.product_search.addEventListener("input", () => {
    clearTimeout(productTimer);
    productVersion++;
    document.getElementById("operation-submit").disabled = true;
    operationForm.elements.product_id.disabled = true;
    productTimer = setTimeout(findProducts, 250);
  });
  operationForm.addEventListener("change", (event) => {
    if (["kind", "source_id", "destination_id", "product_id"].includes(event.target.name))
      updateOperation();
  });
  operationForm.addEventListener("submit", (event) =>
    submit(event, operationDialog, "operation-error", () => {
      const fields = operationForm.elements;
      const kind = fields.kind.value;
      const body = {
        kind,
        product_id: Number(fields.product_id.value),
        quantity: Number(fields.quantity.value),
        reference: fields.reference.value.trim(),
      };
      if (kind !== "receipt") body.source_id = Number(fields.source_id.value);
      if (["receipt", "transfer"].includes(kind))
        body.destination_id = Number(fields.destination_id.value);
      if (["fbs", "fbo"].includes(kind)) body.destination = fields.destination.value.trim();
      return request("/movements", "POST", body);
    }),
  );

  function openWarehouse(warehouse = null) {
    warehouseId = warehouse?.id || null;
    warehouseForm.reset();
    warehouseForm.elements.name.value = warehouse?.name || "";
    document.getElementById("warehouse-title").textContent = warehouse
      ? "Переименовать ФФ"
      : "Новый фулфилмент";
    document.getElementById("warehouse-error").hidden = true;
    warehouseDialog.showModal();
  }
  document.getElementById("new-warehouse").addEventListener("click", () => openWarehouse());
  warehouseForm.addEventListener("submit", (event) =>
    submit(event, warehouseDialog, "warehouse-error", () =>
      request(
        warehouseId ? `/warehouses/${warehouseId}` : "/warehouses",
        warehouseId ? "PATCH" : "POST",
        { name: warehouseForm.elements.name.value.trim() },
      ),
    ),
  );

  function confirm(title, description, label, action, danger = false) {
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-description").textContent = description;
    document.getElementById("confirm-submit").textContent = label;
    document.getElementById("confirm-submit").className = `button ${danger ? "danger" : "primary"}`;
    document.getElementById("confirm-error").hidden = true;
    confirmedAction = action;
    confirmDialog.showModal();
  }
  confirmForm.addEventListener("submit", (event) =>
    submit(event, confirmDialog, "confirm-error", confirmedAction),
  );
  document.getElementById("warehouse-list").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const id = Number(button.closest("[data-warehouse]").dataset.warehouse);
    const warehouse = getState().warehouses.find((row) => row.id === id);
    if (button.dataset.action === "rename") return openWarehouse(warehouse);
    confirm(
      "Удалить фулфилмент?",
      `ФФ «${warehouse.name}» перейдёт в архив. Остатки останутся на этом ФФ, история сохранится.\n\nМожно будет вывезти свободный товар на другой ФФ и завершить уже начатые операции. Новые приходы и резервы будут недоступны.`,
      "Перевести в архив",
      () => request(`/warehouses/${id}`, "DELETE"),
      true,
    );
  });
  document.getElementById("movement-list").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const card = button.closest("[data-movement]");
    const id = card.dataset.movement;
    const action = button.dataset.action;
    const titles = {
      ship: "Отправить резерв FBS?",
      cancel: "Снять резерв?",
      receive: "Подтвердить приёмку?",
      deliver: "Подтвердить доставку?",
    };
    const descriptions = {
      ship: "Товар покинет ФФ и будет числиться в пути к клиенту.",
      cancel: "Товар снова будет доступен на исходном ФФ.",
      receive:
        "Подтверждайте только фактически полученный товар. Приёмка выполняется на всё количество.",
      deliver: "Товар перестанет числиться в пути к клиенту.",
    };
    const product = card.querySelector(".product-name").textContent;
    const quantity = card.querySelector(".quantity").textContent;
    confirm(
      titles[action],
      `${product} · ${quantity}\n\n${descriptions[action]}`,
      button.textContent,
      () => request(`/movements/${id}/actions`, "POST", { action }),
    );
  });
}
