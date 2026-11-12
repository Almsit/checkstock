export const number = (value) => new Intl.NumberFormat("ru-RU").format(value);
export const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
  );
const kindNames = { receipt: "Приход", transfer: "Между ФФ", fbs: "FBS", fbo: "FBO" };
const actionNames = {
  reserve: "Резерв",
  ship: "Отправка",
  receive: "Приёмка",
  cancel: "Отмена резерва",
  deliver: "Доставка",
};

export function renderSummary(state) {
  const totals = state.totals;
  for (const [id, value] of Object.entries({
    "on-hand": totals.on_hand,
    available: totals.available,
    reserved: totals.reserved,
    transit: totals.transfer + totals.fbo + totals.fbs,
    transfer: totals.transfer,
    fbo: totals.fbo,
    fbs: totals.fbs,
  }))
    document.getElementById(`total-${id}`).textContent = number(value);
  document.getElementById("summary").setAttribute("aria-busy", "false");
  document.getElementById("completed-totals").textContent =
    `Принято маркетплейсом FBO: ${number(totals.accepted_fbo)} шт. · Доставлено клиентам FBS: ${number(totals.delivered_fbs)} шт. за всё время`;
}

export function renderStock(state, search = "") {
  const term = search.trim().toLocaleLowerCase("ru");
  const products = state.products;
  const balances = new Map(
    state.balances.map((row) => [`${row.product_id}:${row.warehouse_id}`, row]),
  );
  const stock = (productId, warehouseId) =>
    balances.get(`${productId}:${warehouseId}`) || { on_hand: 0, available: 0, reserved: 0 };
  const cells = (balance) =>
    ["on_hand", "available", "reserved"]
      .map(
        (key, index) =>
          `<td class="numeric ${index === 0 ? "group-start" : index === 1 ? "free" : "reserved"} ${balance[key] === 0 ? "zero" : ""}">${number(balance[key])}</td>`,
      )
      .join("");
  let rows = products
    .map((product) => {
      const transit = state.quantities.filter(
        (row) => row.product_id === product.id && row.status === "in_transit",
      );
      const transitDetails = transit.length
        ? transit
            .map((row) => `<span>${kindNames[row.kind]}: <b>${number(row.quantity)}</b></span>`)
            .join("")
        : '<span class="zero">Нет отправок</span>';
      return `<tr>
        <td>
          <span class="product-name">${escape(product.name)}</span>
          <span class="sku">${escape(product.sku)}</span>
        </td>
        ${state.warehouses.map((warehouse) => cells(stock(product.id, warehouse.id))).join("")}
        <td class="transit-cell group-start">${transitDetails}</td>
      </tr>`;
    })
    .join("");
  const columns = 2 + state.warehouses.length * 3;
  if (!rows)
    rows = `<tr><td colspan="${columns}"><div class="empty"><strong>Товары не найдены</strong><p>Попробуйте другое название или артикул.</p></div></td></tr>`;
  const totals = state.warehouses
    .map((warehouse) =>
      cells(
        products.reduce(
          (sum, product) => {
            const balance = stock(product.id, warehouse.id);
            return {
              on_hand: sum.on_hand + balance.on_hand,
              available: sum.available + balance.available,
              reserved: sum.reserved + balance.reserved,
            };
          },
          { on_hand: 0, available: 0, reserved: 0 },
        ),
      ),
    )
    .join("");
  const warehouseHeaders = state.warehouses
    .map(
      (warehouse) => `
    <th colspan="3" scope="colgroup" class="warehouse-group">
      ${escape(warehouse.name)}
      ${warehouse.archived ? '<span class="badge archived">Архив</span>' : ""}
    </th>`,
    )
    .join("");
  const stockHeaders = state.warehouses
    .map(
      () => `
    <th scope="col" class="numeric group-start">На складе</th>
    <th scope="col" class="numeric">Свободно</th>
    <th scope="col" class="numeric">Резерв FBS</th>`,
    )
    .join("");
  const totalLabel =
    state.product_total > products.length
      ? "Итого на странице"
      : term
        ? "Итого по выборке"
        : "Итого на ФФ";
  document.getElementById("stock-table").innerHTML = `<table class="stock-table">
    <thead>
      <tr>
        <th rowspan="2" scope="col">Товар / артикул</th>
        ${warehouseHeaders}
        <th rowspan="2" scope="col" class="warehouse-group">В пути, шт.</th>
      </tr>
      <tr>${stockHeaders}</tr>
    </thead>
    <tbody>${rows}</tbody>
    <tfoot><tr><td>${totalLabel}</td>${totals}<td class="group-start"></td></tr></tfoot>
  </table>`;
  document.getElementById("product-count").textContent =
    `На странице: ${products.length} · найдено: ${state.product_total}`;
  document.getElementById("archive-note").hidden = !state.warehouses.some((row) => row.archived);
  const completed = state.completed.filter((row) =>
    products.some((product) => product.id === row.product_id),
  );
  const completedBox = document.getElementById("completed-products");
  completedBox.hidden = completed.length === 0;
  completedBox.innerHTML =
    "<h3>Принято и доставлено за всё время</h3>" +
    completed
      .map((row) => {
        const product = state.products.find((product) => product.id === row.product_id);
        return `<p>
          <span>${escape(product.sku)} · ${escape(product.name)}</span>
          <span>${row.kind === "fbo" ? "Принято FBO" : "Доставлено FBS"} · ${escape(row.destination)}</span>
          <strong>${number(row.quantity)} шт.</strong>
        </p>`;
      })
      .join("");
  if (state.completed_has_more)
    completedBox.innerHTML +=
      "<p>Показаны первые 100 получателей. Остальные операции доступны в истории.</p>";
}

export function renderMovements(items) {
  document.getElementById("movement-list").innerHTML = items.length
    ? items
        .map((item) => {
          const actionButtons =
            item.status === "reserved"
              ? '<button class="button primary" data-action="ship">Отправить</button><button class="button secondary" data-action="cancel">Снять резерв</button>'
              : item.kind === "fbs"
                ? '<button class="button primary" data-action="deliver">Подтвердить доставку</button>'
                : '<button class="button primary" data-action="receive">Принять товар</button>';
          return `<article class="movement-card" data-movement="${escape(item.id)}">
            <div>
              <p>
                <span class="product-name">${escape(item.product_name)}</span>
                <span class="sku">${escape(item.sku)}</span>
              </p>
              <span class="badge ${item.status === "reserved" ? "amber" : ""}">
                ${kindNames[item.kind]} · ${item.status === "reserved" ? "В резерве" : "В пути"}
              </span>
            </div>
            <div class="movement-route">
              <p>${escape(item.source_name)} → ${escape(item.destination_name)}</p>
              <small>${escape(item.reference) || "Без номера документа"}</small>
            </div>
            <div class="quantity">${number(item.quantity)} <small>шт.</small></div>
            <div class="movement-actions">${actionButtons}</div>
          </article>`;
        })
        .join("")
    : '<div class="empty"><strong>Все операции завершены</strong><p>Здесь появятся резервы FBS и товар в пути.</p></div>';
}

export function renderHistory(items) {
  document.getElementById("history-list").innerHTML = items.length
    ? items
        .map((item) => {
          const date = new Intl.DateTimeFormat("ru-RU", {
            dateStyle: "short",
            timeStyle: "short",
          }).format(new Date(item.created_at));
          const title =
            item.kind === "receipt"
              ? "Приход"
              : `${actionNames[item.action]} · ${kindNames[item.kind]}`;
          return `<tr>
            <td>${title}<small>${escape(date)} · № ${number(item.id)}</small></td>
            <td>
              <span class="product-name">${escape(item.product_name)}</span>
              <span class="sku">${escape(item.sku)}</span>
            </td>
            <td class="route">${escape(item.source)} → ${escape(item.destination)}</td>
            <td class="numeric">${number(item.quantity)} шт.</td>
            <td>${escape(item.reference) || "—"}</td>
          </tr>`;
        })
        .join("")
    : '<tr><td colspan="5"><div class="empty"><strong>Операций пока нет</strong><p>Оформите первый приход.</p></div></td></tr>';
}

export function renderWarehouses(state) {
  document.getElementById("warehouse-list").innerHTML = state.warehouses
    .map((warehouse) => {
      const totals = state.warehouse_totals.find((row) => row.warehouse_id === warehouse.id);
      const sum = (key) => number(totals?.[key] || 0);
      return `<article class="warehouse-card ${warehouse.archived ? "is-archived" : ""}" data-warehouse="${warehouse.id}">
        <h3>${escape(warehouse.name)}</h3>
        <span class="badge ${warehouse.archived ? "archived" : ""}">
          ${warehouse.archived ? "В архиве · остатки сохранены" : "Действующий ФФ"}
        </span>
        <div class="warehouse-numbers">
          <div><strong>${sum("on_hand")}</strong><span>на складе</span></div>
          <div><strong>${sum("available")}</strong><span>свободно</span></div>
          <div><strong>${sum("reserved")}</strong><span>в резерве</span></div>
        </div>
        <div class="warehouse-actions">
          <button class="text-button" data-action="rename">Переименовать</button>
          ${warehouse.archived ? "" : '<button class="text-button danger-text" data-action="archive">Удалить ФФ</button>'}
        </div>
      </article>`;
    })
    .join("");
}
