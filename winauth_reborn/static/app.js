"use strict";

const $ = (selector) => document.querySelector(selector);
const csrf = $('meta[name="csrf-token"]').content;
let accounts = [];
let loadedAt = performance.now();
let hiddenCodes = false;
let steamOnly = false;
let pinnedOnly = false;
let loadingPromise = null;
let settings = null;
let activeEdit = null;
let editorIcon = "initial";
let editorImage = "";
let recoveryAccount = null;
let recoveryTimer;
let recoveryGeneration = 0;
let lastActivity = performance.now();
let initialized = false;
let activeSteam = null;
let emailFlow = null;
let toastTimer;
const hotpCodes = new Map();
const defaults = { theme: "dark", density: "comfortable", hide_codes: false, sort_order: "imported", auto_hide_seconds: 0 };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, ...options.headers },
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("Authentication expired. Reload to sign in.");
    let message = `Request failed (${response.status}).`;
    if ((response.headers.get("Content-Type") || "").includes("application/json")) {
      message = (await response.json()).error || message;
    }
    throw new Error(message);
  }
  return response;
}

async function json(path, options) {
  return (await api(path, options)).json();
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").classList.toggle("error", error);
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 3500);
}

async function copy(text, message = "Code copied to clipboard") {
  if (!navigator.clipboard || !window.isSecureContext) {
    throw new Error("Clipboard access requires HTTPS or localhost. Select the code and copy manually.");
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    throw new Error("Clipboard permission was denied. Allow clipboard access or copy manually.");
  }
  toast(message);
}

function setPrivacy(hidden) {
  hiddenCodes = hidden;
  $("#privacy-button").textContent = hidden ? "Show codes" : "Hide codes";
  $("#privacy-button").setAttribute("aria-pressed", String(hidden));
  updateCards();
}

function applySettings(next, forcePrivacy = false) {
  const changed = !settings || JSON.stringify(next) !== JSON.stringify(settings);
  if (!changed && !forcePrivacy) return;
  if (forcePrivacy || !settings || settings.hide_codes !== next.hide_codes) setPrivacy(next.hide_codes);
  settings = next;
  document.documentElement.dataset.theme = next.theme;
  document.documentElement.dataset.density = next.density;
  renderAccounts();
}

function serviceIcon(account) {
  const container = element("div", `service-icon ${account.kind === "steam" ? "steam" : ""}`);
  if (account.icon === "custom" && account.custom_icon) {
    const image = element("img");
    image.src = account.custom_icon;
    image.alt = "";
    container.append(image);
  } else if (account.icon && account.icon !== "initial") {
    const glyph = element("span", `glyph icon-${account.icon}`);
    glyph.setAttribute("aria-hidden", "true");
    container.append(glyph);
  } else {
    container.textContent = (account.issuer || account.name).slice(0, 1).toUpperCase();
  }
  return container;
}

function confirmAction(title, message, action, dangerous = false) {
  const dialog = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-message").textContent = message;
  $("#confirm-ok").textContent = action;
  $("#confirm-ok").classList.toggle("danger", dangerous);
  dialog.showModal();
  return new Promise((resolve) => {
    const finish = (result) => {
      dialog.close();
      $("#confirm-ok").onclick = null;
      $("#confirm-cancel").onclick = null;
      dialog.removeEventListener("cancel", cancel);
      resolve(result);
    };
    const cancel = (event) => { event.preventDefault(); finish(false); };
    $("#confirm-ok").onclick = () => finish(true);
    $("#confirm-cancel").onclick = () => finish(false);
    dialog.addEventListener("cancel", cancel);
  });
}

function shownCode(account) {
  const code = account.kind === "hotp" ? hotpCodes.get(account.id) : account.code;
  if (!code) return "--- ---";
  if (hiddenCodes) return "\u2022".repeat(code.length);
  return /^\d{6}$/.test(code) ? `${code.slice(0, 3)} ${code.slice(3)}` : code;
}

function accountCard(account) {
  const card = element("article", "account-card");
  card.dataset.id = account.id;
  card.classList.toggle("is-pinned", account.pinned);
  card.style.setProperty("--card-color", account.color);
  const heading = element("div", "card-heading");
  heading.append(serviceIcon(account));
  const name = element("div", "card-name");
  const title = element("h3", "", account.name);
  title.title = account.name;
  name.append(title, element("p", "", account.issuer || "Authenticator"));
  heading.append(name);
  const actions = element("div", "card-actions");
  const pin = element("button", `card-action pin-button ${account.pinned ? "selected" : ""}`);
  pin.append(element("span", `glyph ${account.pinned ? "icon-star-filled" : "icon-star"}`));
  pin.setAttribute("aria-label", `${account.pinned ? "Unpin" : "Pin"} ${account.name}`);
  pin.setAttribute("aria-pressed", String(account.pinned));
  pin.title = account.pinned ? "Unpin card" : "Pin card";
  pin.onclick = async () => {
    pin.disabled = true;
    try {
      await json(`/api/accounts/${account.id}`, {
        method: "PATCH", body: JSON.stringify({ pinned: !account.pinned }),
      });
      await loadAccounts(true);
    } catch (error) { toast(error.message, true); }
    finally { pin.disabled = false; }
  };
  const edit = element("button", "card-action edit-button", "\u270e");
  edit.setAttribute("aria-label", `Edit ${account.name}`);
  edit.title = "Edit authenticator";
  edit.onclick = () => openEditor(accounts.find((item) => item.id === account.id) || account);
  actions.append(pin, edit);
  const remove = element("button", "delete-button", "\u00d7");
  remove.setAttribute("aria-label", `Delete ${account.name}`);
  remove.onclick = async () => {
    if (!await confirmAction("Delete authenticator?", `Remove ${account.name} from this vault? This does not disable two-factor authentication on the service. Keep a backup before deleting.`, "Delete", true)) return;
    try {
      await json(`/api/accounts/${account.id}`, { method: "DELETE" });
      hotpCodes.delete(account.id);
      await loadAccounts(true);
      toast("Authenticator removed");
    } catch (error) { toast(error.message, true); }
  };
  actions.append(remove);
  heading.append(actions);
  const codeRow = element("div", "code-row");
  const code = element("button", "code", shownCode(account));
  const copyButton = element("button", "copy-button", "Copy");
  copyButton.setAttribute("aria-label", `Copy code for ${account.name}`);
  code.setAttribute("aria-label", `Copy code for ${account.name}`);
  const copyCode = async () => {
    copyButton.disabled = true;
    try {
      if (account.kind === "hotp") {
        await copy(hotpCodes.get(account.id));
      } else {
        const fresh = await json("/api/accounts");
        const current = fresh.accounts.find((item) => item.id === account.id);
        if (!current) throw new Error("Account was removed. Refresh your vault.");
        await copy(current.code);
      }
    } catch (error) { toast(error.message, true); }
    finally { copyButton.disabled = false; }
  };
  code.onclick = copyCode;
  copyButton.onclick = copyCode;
  codeRow.append(code, copyButton);
  card.append(heading, codeRow);
  const timeRow = element("div", "time-row");
  if (account.kind !== "hotp") {
    const timer = element("div", "timer");
    timer.append(element("div", "timer-fill"));
    timeRow.append(timer, element("span", "remaining"));
  } else {
    timeRow.append(element("span", "muted", `Counter: ${account.counter}`));
  }
  card.append(timeRow);
  const footer = element("div", "card-footer");
  footer.append(element("span", "kind-badge",
    account.kind === "steam" ? "Steam Guard" : account.kind === "hotp" ? "Counter-based" : "Time-based"));
  if (account.kind === "steam") {
    const links = element("div", "card-links");
    const recovery = element("button", "steam-button", "Recovery details");
    recovery.onclick = () => openRecovery(account);
    links.append(recovery);
    const steamButton = element("button", "steam-button",
      account.confirmations_available ? "Confirmations \u2192" : "Missing confirmation data");
    steamButton.onclick = () => openSteam(accounts.find((item) => item.id === account.id) || account);
    links.append(steamButton);
    footer.append(links);
  } else if (account.kind === "hotp") {
    const next = element("button", "steam-button", "Generate next \u2192");
    next.onclick = async () => {
      if (!await confirmAction("Generate a counter-based code?", "This advances the counter permanently. Use this code before generating another to avoid getting out of sync.", "Generate code")) return;
      next.disabled = true;
      try {
        const result = await json(`/api/accounts/${account.id}/hotp`, { method: "POST" });
        hotpCodes.set(account.id, result.code);
        await loadAccounts(true);
      } catch (error) { toast(error.message, true); }
      finally { next.disabled = false; }
    };
    footer.append(next);
  }
  card.append(footer);
  return card;
}

function renderAccounts() {
  const query = $("#search").value.trim().toLowerCase();
  const filtered = accounts.filter((account) =>
    (!steamOnly || account.kind === "steam") &&
    (!pinnedOnly || account.pinned) &&
    `${account.name} ${account.issuer}`.toLowerCase().includes(query));
  filtered.sort((left, right) => {
    const pinned = Number(right.pinned) - Number(left.pinned);
    if (pinned) return pinned;
    const key = settings?.sort_order;
    return key === "name" || key === "issuer"
      ? left[key].localeCompare(right[key], undefined, { sensitivity: "base" }) : 0;
  });
  const cards = [];
  filtered.forEach((account, index) => {
    if (index && filtered[index - 1].pinned && !account.pinned) {
      const separator = element("div", "pin-separator");
      separator.setAttribute("role", "separator");
      separator.setAttribute("aria-label", "Other authenticators");
      cards.push(separator);
    }
    cards.push(accountCard(account));
  });
  $("#accounts").replaceChildren(...cards);
  $("#empty-state").hidden = accounts.length !== 0;
  $("#no-results").hidden = accounts.length === 0 || filtered.length !== 0;
  $("#results-label").textContent = `${filtered.length} account${filtered.length === 1 ? "" : "s"}`;
  $("#total-stat").textContent = accounts.length;
  $("#account-count").textContent = accounts.length;
  $("#steam-stat").textContent = accounts.filter((item) => item.kind === "steam").length;
  updateCards();
}

function updateCards() {
  const elapsed = (performance.now() - loadedAt) / 1000;
  for (const card of $("#accounts").children) {
    const account = accounts.find((item) => item.id === card.dataset.id);
    if (!account) continue;
    const code = card.querySelector(".code");
    const expired = account.kind !== "hotp" && account.remaining - elapsed <= 0;
    const unavailable = account.kind === "hotp" && !hotpCodes.has(account.id);
    code.textContent = expired ? "--- ---" : shownCode(account);
    code.classList.toggle("long-code", code.textContent.length > 7);
    code.disabled = expired || unavailable;
    card.querySelector(".copy-button").disabled = expired || unavailable;
    if (account.kind !== "hotp") {
      const remaining = Math.max(0, account.remaining - elapsed);
      card.querySelector(".timer-fill").style.transform = `scaleX(${remaining / account.period})`;
      card.querySelector(".timer-fill").classList.toggle("urgent", remaining <= 5);
      card.querySelector(".remaining").textContent = `${Math.ceil(remaining)}s`;
    }
  }
  if (recoveryAccount && $("#recovery-dialog").open) {
    const account = accounts.find((item) => item.id === recoveryAccount);
    const value = $("#recovery-fields [data-field='guard_code']");
    const button = $("#recovery-fields [data-copy='guard_code']");
    if (value && account) {
      const fresh = account.remaining - elapsed > 0;
      value.value = fresh ? account.code : "---";
      button.disabled = !fresh;
    }
  }
}

function loadAccounts(forceRender = false) {
  if (loadingPromise) {
    return forceRender ? loadingPromise.then(() => loadAccounts(true)) : loadingPromise;
  }
  loadingPromise = refreshAccounts(forceRender).finally(() => { loadingPromise = null; });
  return loadingPromise;
}

function cardMetadata(account) {
  const { code, remaining, ...metadata } = account;
  return JSON.stringify(metadata);
}

async function refreshAccounts(forceRender) {
  try {
    const data = await json("/api/accounts");
    const changed = forceRender || data.accounts.length !== accounts.length ||
      data.accounts.some((item, index) => !accounts[index] ||
        cardMetadata(item) !== cardMetadata(accounts[index]));
    accounts = data.accounts;
    loadedAt = performance.now();
    applySettings(data.settings);
    $("#connection-error").hidden = true;
    $("#refresh-status").textContent = "Vault connected";
    $("#accounts").setAttribute("aria-busy", "false");
    if (changed || !$("#accounts").children.length) renderAccounts();
    else updateCards();
  } catch (error) {
    $("#connection-error").textContent = error.message;
    $("#connection-error").hidden = false;
    $("#refresh-status").textContent = "Connection interrupted";
    $("#accounts").setAttribute("aria-busy", "false");
  }
}

function openImport() {
  $("#import-error").textContent = "";
  $("#import-dialog").showModal();
}

async function selectFile(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    $("#import-error").textContent = "Choose a file smaller than 2 MB.";
    return;
  }
  try {
    $("#import-text").value = await file.text();
    $("#file-label").textContent = file.name;
    $("#import-error").textContent = "";
  } catch {
    $("#import-error").textContent = "Could not read this file. Try selecting it again.";
  }
}

$("#import-button").onclick = openImport;
$("#empty-import").onclick = openImport;
$("#import-file").onchange = (event) => selectFile(event.target.files[0]);
$("#dropzone").onkeydown = (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    $("#import-file").click();
  }
};
$("#dropzone").ondragover = (event) => { event.preventDefault(); $("#dropzone").classList.add("dragover"); };
$("#dropzone").ondragleave = () => $("#dropzone").classList.remove("dragover");
$("#dropzone").ondrop = (event) => {
  event.preventDefault();
  $("#dropzone").classList.remove("dragover");
  selectFile(event.dataTransfer.files[0]);
};
$("#import-form").onsubmit = async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  submit.disabled = true;
  $("#import-error").textContent = "";
  try {
    const result = await json("/api/import", {
      method: "POST", body: JSON.stringify({ text: $("#import-text").value }),
    });
    $("#import-dialog").close();
    $("#import-form").reset();
    $("#file-label").textContent = "Choose a .txt file or drop it here";
    await loadAccounts(true);
    toast(`Imported ${result.added} account${result.added === 1 ? "" : "s"}${result.skipped ? `; skipped ${result.skipped} duplicate(s)` : ""}`);
  } catch (error) { $("#import-error").textContent = error.message; }
  finally { submit.disabled = false; }
};

$("#search").oninput = renderAccounts;
$("#privacy-button").onclick = () => {
  setPrivacy(!hiddenCodes);
};
$("#pinned-filter").onclick = () => {
  pinnedOnly = !pinnedOnly;
  $("#pinned-filter").setAttribute("aria-pressed", String(pinnedOnly));
  renderAccounts();
};
function setFilter(steam) {
  steamOnly = steam;
  $("#all-filter").classList.toggle("active", !steam);
  $("#steam-filter").classList.toggle("active", steam);
  renderAccounts();
}
$("#all-filter").onclick = () => setFilter(false);
$("#steam-filter").onclick = () => setFilter(true);
document.querySelectorAll("[data-close]").forEach((button) => {
  button.onclick = () => button.closest("dialog").close();
});

async function exportBackup() {
  if (!await confirmAction("Export unencrypted backup?", "The downloaded .txt contains all authenticator secrets, including Steam recovery data. Store it somewhere safe. It does not contain your saved Steam web sessions.", "Download backup")) return;
  try {
    const response = await api("/api/export", { method: "POST" });
    const url = URL.createObjectURL(await response.blob());
    const link = element("a");
    link.href = url;
    link.download = "winauth-export.txt";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Backup downloaded. Keep it private.");
  } catch (error) { toast(error.message, true); }
}
$("#export-button").onclick = exportBackup;
$("#mobile-export-button").onclick = exportBackup;

function setSteamConnected(connected) {
  $("#steam-connect").hidden = connected;
  $("#steam-connected").hidden = !connected;
}

async function openSteam(account) {
  activeSteam = account;
  emailFlow = null;
  $("#steam-account-name").textContent = account.name;
  $("#steam-error").textContent = "";
  $("#confirmations").replaceChildren();
  $("#steam-login-form").reset();
  $("#steam-code-form").reset();
  $("#steam-cookie-form").reset();
  $("#steam-login-form").hidden = false;
  $("#steam-code-form").hidden = true;
  setSteamConnected(account.steam_connected);
  $("#steam-dialog").showModal();
  if (!account.confirmations_available) {
    $("#steam-error").textContent = "This export lacks identity_secret. Export from WinAuth with full Steam data, not compatibility mode. Steam Guard codes still work.";
    $("#steam-connect").hidden = true;
  } else if (account.steam_connected) {
    await loadConfirmations();
  }
}

async function loadConfirmations() {
  const account = activeSteam;
  $("#steam-error").textContent = "";
  $("#refresh-confirmations").disabled = true;
  $("#confirmations").replaceChildren(element("p", "muted", "Loading Steam confirmations..."));
  try {
    const data = await json(`/api/accounts/${account.id}/steam/confirmations`);
    if (activeSteam?.id !== account.id || !$("#steam-dialog").open) return;
    if (!data.confirmations.length) {
      $("#confirmations").replaceChildren(element("p", "empty-state", "All caught up. No pending confirmations."));
      return;
    }
    $("#confirmations").replaceChildren(...data.confirmations.map((item) => {
      const row = element("article", "confirmation");
      row.append(element("h3", "", item.headline));
      const summaries = Array.isArray(item.summary) ? item.summary : [item.summary];
      summaries.forEach((text) => row.append(element("p", "", String(text))));
      row.append(element("p", "", `Confirmation ${item.id}${item.creator_id ? ` \u00b7 Reference ${item.creator_id}` : ""}`));
      const actions = element("div", "dialog-actions");
      for (const [operation, label] of [["cancel", "Reject"], ["allow", "Approve"]]) {
        const button = element("button", `button small ${operation === "allow" ? "primary" : "danger"}`, label);
        button.onclick = async () => {
          if (!await confirmAction(`${label} this confirmation?`, `${item.headline}\n${summaries.join("\n")}\nConfirmation: ${item.id}`, label, operation === "cancel")) return;
          actions.querySelectorAll("button").forEach((node) => { node.disabled = true; });
          try {
            await json(`/api/accounts/${account.id}/steam/confirmations/${encodeURIComponent(item.id)}`, {
              method: "POST", body: JSON.stringify({ operation, nonce: item.nonce }),
            });
            row.remove();
            toast(operation === "allow" ? "Confirmation approved" : "Confirmation rejected");
            if (!$("#confirmations").children.length) {
              $("#confirmations").append(element("p", "empty-state", "All caught up. No pending confirmations."));
            }
          } catch (error) {
            $("#steam-error").textContent = error.message;
            actions.querySelectorAll("button").forEach((node) => { node.disabled = false; });
          }
        };
        actions.append(button);
      }
      row.append(actions);
      return row;
    }));
  } catch (error) {
    $("#confirmations").replaceChildren();
    $("#steam-error").textContent = `${error.message} If your session expired, disconnect and reconnect.`;
  } finally { $("#refresh-confirmations").disabled = false; }
}

async function steamForm(event, endpoint) {
  event.preventDefault();
  const account = activeSteam;
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  if (endpoint === "login-code") data.flow = emailFlow;
  const submit = event.submitter;
  const label = submit.textContent;
  submit.disabled = true;
  submit.textContent = "Connecting...";
  $("#steam-error").textContent = "";
  try {
    const result = await json(`/api/accounts/${account.id}/steam/${endpoint}`, {
      method: "POST", body: JSON.stringify(data),
    });
    if (activeSteam?.id !== account.id || !$("#steam-dialog").open) return;
    if (result.needs_code) {
      emailFlow = result.flow;
      $("#steam-login-form").hidden = true;
      $("#steam-code-form").hidden = false;
      $("#steam-code-form input").focus();
    } else {
      setSteamConnected(true);
      activeSteam.steam_connected = true;
      await loadAccounts();
      await loadConfirmations();
    }
  } catch (error) { $("#steam-error").textContent = error.message; }
  finally {
    if (form.querySelector('[name="password"]')) form.querySelector('[name="password"]').value = "";
    if (form.querySelector('[name="cookie"]')) form.querySelector('[name="cookie"]').value = "";
    if (form.querySelector('[name="code"]')) form.querySelector('[name="code"]').value = "";
    submit.disabled = false;
    submit.textContent = label;
  }
}

$("#steam-login-form").onsubmit = (event) => steamForm(event, "login");
$("#steam-code-form").onsubmit = (event) => steamForm(event, "login-code");
$("#steam-cookie-form").onsubmit = (event) => steamForm(event, "session");
$("#refresh-confirmations").onclick = loadConfirmations;
$("#disconnect-steam").onclick = async () => {
  try {
    await json(`/api/accounts/${activeSteam.id}/steam/session`, { method: "DELETE" });
    setSteamConnected(false);
    $("#steam-error").textContent = "";
    $("#steam-login-form").hidden = false;
    $("#steam-code-form").hidden = true;
    await loadAccounts();
    toast("Saved Steam session removed");
  } catch (error) { $("#steam-error").textContent = error.message; }
};
$("#steam-dialog").addEventListener("close", () => {
  activeSteam = null;
  emailFlow = null;
  $("#steam-login-form").reset();
  $("#steam-code-form").reset();
  $("#steam-cookie-form").reset();
});

document.addEventListener("visibilitychange", () => {
  if (!initialized) return;
  if (!document.hidden) loadAccounts();
  else {
    clearRecovery();
    $("#recovery-dialog").close();
    if (settings?.auto_hide_seconds) setPrivacy(true);
  }
});
for (const event of ["pointerdown", "pointermove", "keydown", "scroll"]) {
  document.addEventListener(event, () => { lastActivity = performance.now(); }, { passive: true });
}
document.addEventListener("keydown", (event) => {
  if (!initialized || event.ctrlKey || event.metaKey || event.altKey || document.querySelector("dialog[open]") ||
      event.target.closest("input, textarea, select, [contenteditable]")) return;
  if (event.key === "/") { event.preventDefault(); $("#search").focus(); }
  else if (event.key.toLowerCase() === "h") setPrivacy(!hiddenCodes);
  else if (event.key === ",") { event.preventDefault(); openSettings(); }
});
setInterval(() => {
  if (!initialized) return;
  if (settings?.auto_hide_seconds && performance.now() - lastActivity > settings.auto_hide_seconds * 1000) {
    setPrivacy(true);
  }
  updateCards();
  if (!document.hidden) loadAccounts();
}, 1000);
document.addEventListener("DOMContentLoaded", () => {
  initialized = true;
  loadAccounts(true);
});
