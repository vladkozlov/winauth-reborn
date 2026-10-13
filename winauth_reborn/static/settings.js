"use strict";

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
