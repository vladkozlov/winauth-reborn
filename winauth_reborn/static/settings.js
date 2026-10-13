"use strict";

const steamProxyForm = $("#steam-proxy-form");

function fillSettings(values) {
  const form = $("#settings-form");
  for (const [key, value] of Object.entries(values)) {
    if (key === "hide_codes") form.elements[key].checked = value;
    else form.elements[key].value = value;
  }
}
function openSettings() {
  fillSettings(settings || defaults);
  $("#settings-error").textContent = "";
  $("#settings-dialog").showModal();
  if (steamProxyForm) loadSteamProxySettings();
}
function showSteamProxyStatus(proxy) {
  steamProxyForm.elements.mode.value = proxy.mode;
  steamProxyForm.elements.url.value = "";
  const savedCustom = proxy.mode === "custom";
  steamProxyForm.dataset.savedCustom = String(savedCustom);
  $("#steam-proxy-url-field").hidden = proxy.mode !== "custom";
  steamProxyForm.elements.url.disabled = proxy.mode !== "custom";
  steamProxyForm.elements.url.required = proxy.mode === "custom" && !savedCustom;
  $("#steam-proxy-status").textContent = proxy.mode === "custom"
    ? "A custom proxy is saved securely. Leave the URL empty to keep it, or enter a replacement."
    : proxy.mode === "environment"
      ? proxy.active ? "Using the server's WINAUTH_STEAM_PROXY setting." : "No server proxy is configured; Steam connects directly."
      : "Steam connects directly from the server.";
}
async function loadSteamProxySettings() {
  $("#steam-proxy-error").textContent = "";
  try {
    const result = await json("/api/steam/proxy");
    showSteamProxyStatus(result.proxy);
  } catch (error) { $("#steam-proxy-error").textContent = error.message; }
}
$("#settings-button").onclick = openSettings;
$("#reset-settings").onclick = () => fillSettings(defaults);
$("#settings-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target;
  const submit = event.submitter;
  submit.disabled = true;
  $("#settings-error").textContent = "";
  const values = Object.fromEntries(new FormData(form));
  values.hide_codes = form.elements.hide_codes.checked;
  values.auto_hide_seconds = Number(values.auto_hide_seconds);
  try {
    const result = await json("/api/settings", { method: "PATCH", body: JSON.stringify(values) });
    applySettings(result.settings, true);
    lastActivity = performance.now();
    $("#settings-dialog").close();
    toast("Settings saved");
  } catch (error) { $("#settings-error").textContent = error.message; }
  finally { submit.disabled = false; }
};
if (steamProxyForm) {
  steamProxyForm.elements.mode.onchange = () => {
    const custom = steamProxyForm.elements.mode.value === "custom";
    $("#steam-proxy-url-field").hidden = !custom;
    steamProxyForm.elements.url.disabled = !custom;
    steamProxyForm.elements.url.required = custom && steamProxyForm.dataset.savedCustom !== "true";
  };
  steamProxyForm.onsubmit = async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    const mode = steamProxyForm.elements.mode.value;
    const proxy = { mode };
    if (mode === "custom") proxy.url = steamProxyForm.elements.url.value;
    submit.disabled = true;
    $("#steam-proxy-error").textContent = "";
    try {
      const result = await json("/api/steam/proxy", {
        method: "PUT",
        body: JSON.stringify(proxy),
      });
      steamProxyForm.elements.url.value = "";
      showSteamProxyStatus(result.proxy);
      toast("Steam proxy applied");
    } catch (error) { $("#steam-proxy-error").textContent = error.message; }
    finally { submit.disabled = false; }
  };
  $("#settings-dialog").addEventListener("close", () => {
    steamProxyForm.elements.url.value = "";
  });
}
