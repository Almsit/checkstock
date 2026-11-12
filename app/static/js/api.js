const pending = new Map();

function commandKey(signature) {
  let saved;
  try {
    saved = sessionStorage.getItem(signature);
  } catch {}
  const key = saved || pending.get(signature) || crypto.randomUUID();
  pending.set(signature, key);
  try {
    sessionStorage.setItem(signature, key);
  } catch {}
  return key;
}

export async function request(path, method = "GET", body) {
  const signature = `checkstock:${method}:${path}:${JSON.stringify(body)}`;
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["Idempotency-Key"] = commandKey(signature);
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  for (let attempt = 0; attempt < 2; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      response = await fetch(`/api${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        credentials: "same-origin",
        cache: "no-store",
      });
      if (response.status !== 503 || attempt === 1) break;
    } catch {
      if (attempt === 1) {
        throw new Error(
          "Нет ответа от сервера. Повторите действие: повторный запрос не спишет товар дважды.",
        );
      }
    } finally {
      clearTimeout(timer);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("Сервер вернул неожиданный ответ. Попробуйте ещё раз.");
  }
  if (!response.ok) {
    let message = data.message || "Не удалось выполнить действие.";
    if (data.locations?.length) {
      message +=
        "\nДругие ФФ: " +
        data.locations
          .map((row) => `${row.name}${row.archived ? " (архив)" : ""} — ${row.available} шт.`)
          .join("; ");
    }
    throw new Error(message);
  }
  if (method !== "GET") {
    pending.delete(signature);
    try {
      sessionStorage.removeItem(signature);
    } catch {}
  }
  return data;
}
