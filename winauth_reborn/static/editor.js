"use strict";

function updateEditorPreview() {
  const form = $("#edit-form");
  const preview = serviceIcon({
    name: form.elements.name.value || "Account", issuer: form.elements.issuer.value,
    kind: activeEdit.kind, icon: editorIcon, custom_icon: editorImage,
  });
  preview.id = "edit-icon-preview";
  $("#edit-icon-preview").replaceWith(preview);
  $(".editor-preview").style.setProperty("--card-color", form.elements.color.value);
  $("#edit-name-preview").textContent = form.elements.name.value || "Account";
  document.querySelectorAll("[data-icon]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.icon === editorIcon));
  });
  const custom = $('[data-icon="custom"]');
  custom.hidden = !editorImage;
  if (editorImage) $("#custom-icon-preview").src = editorImage;
  else $("#custom-icon-preview").removeAttribute("src");
}

function openEditor(account) {
  activeEdit = account;
  const form = $("#edit-form");
  form.reset();
  for (const key of ["name", "issuer", "algorithm", "digits", "period", "color"]) {
    form.elements[key].value = account[key];
  }
  form.elements.pinned.checked = account.pinned;
  form.elements.issuer.readOnly = account.kind === "steam";
  $("#edit-advanced").hidden = account.kind === "steam";
  $("#edit-advanced").open = false;
  $("#edit-readonly-note").hidden = account.kind !== "steam";
  $("#edit-recovery-button").hidden = account.kind !== "steam";
  for (const key of ["algorithm", "digits", "period", "secret"]) {
    form.elements[key].disabled = account.kind === "steam";
  }
  editorIcon = account.icon;
  editorImage = account.custom_icon;
  $("#edit-error").textContent = "";
  updateEditorPreview();
  $("#edit-dialog").showModal();
}

document.querySelectorAll("[data-icon]").forEach((button) => {
  button.onclick = () => { editorIcon = button.dataset.icon; updateEditorPreview(); };
});
document.querySelectorAll("[data-color]").forEach((button) => {
  button.style.backgroundColor = button.dataset.color;
  button.onclick = () => {
    $("#edit-color").value = button.dataset.color;
    updateEditorPreview();
  };
});
$("#edit-form").oninput = updateEditorPreview;
$("#edit-recovery-button").onclick = () => openRecovery(activeEdit);
$("#edit-icon-file").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 128 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
    $("#edit-error").textContent = "Choose a PNG, JPEG or WebP icon smaller than 128 KB.";
    event.target.value = "";
    return;
  }
  const account = activeEdit;
  const reader = new FileReader();
  reader.onload = () => {
    if (activeEdit !== account || !$("#edit-dialog").open) return;
    editorImage = String(reader.result);
    editorIcon = "custom";
    $("#edit-error").textContent = "";
    updateEditorPreview();
  };
  reader.onerror = () => { $("#edit-error").textContent = "Could not read this image."; };
  reader.readAsDataURL(file);
};
$("#edit-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target;
  const submit = event.submitter;
  const account = activeEdit;
  const changes = {
    name: form.elements.name.value, issuer: form.elements.issuer.value,
    icon: editorIcon, color: form.elements.color.value, pinned: form.elements.pinned.checked,
    custom_icon: editorIcon === "custom" ? editorImage : "",
  };
  if (account.kind !== "steam") {
    for (const key of ["digits", "period"]) changes[key] = Number(form.elements[key].value);
    changes.algorithm = form.elements.algorithm.value;
    if (form.elements.secret.value.trim()) {
      if (!await confirmAction("Replace authenticator secret?", "The service's secret will not change. If this replacement does not match the service, the generated codes will stop working. Keep a backup.", "Replace secret", true)) return;
      changes.secret = form.elements.secret.value.trim();
    }
  }
  submit.disabled = true;
  $("#edit-error").textContent = "";
  try {
    await json(`/api/accounts/${account.id}`, { method: "PATCH", body: JSON.stringify(changes) });
    hotpCodes.delete(account.id);
    revealedCodes.delete(account.id);
    $("#edit-dialog").close();
    await loadAccounts(true);
    toast("Authenticator updated");
  } catch (error) { $("#edit-error").textContent = error.message; }
  finally { submit.disabled = false; }
};
$("#edit-dialog").addEventListener("close", () => {
  activeEdit = null;
  editorImage = "";
  $("#edit-form").reset();
  $("#custom-icon-preview").removeAttribute("src");
  $("#edit-icon-preview").replaceChildren();
});
