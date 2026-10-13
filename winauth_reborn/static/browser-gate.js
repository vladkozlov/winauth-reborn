document.addEventListener("DOMContentLoaded", async () => {
  "use strict";
  const form = $("#browser-unlock-form");
  const submit = $("#browser-unlock-submit");
  let creating = false;
  try {
    if (window.top !== window.self) throw new Error("Open this app directly, not inside an embedded frame.");
    creating = !await window.browserVault.open();
    $("#browser-unlock-title").textContent = creating ? "Your private browser vault." : "Unlock your browser vault.";
    $("#browser-unlock-confirm").hidden = !creating;
    form.elements.confirm_password.disabled = !creating;
    form.elements.confirm_password.required = creating;
    form.elements.password.minLength = creating ? 12 : 1;
    form.elements.password.autocomplete = creating ? "new-password" : "current-password";
    submit.textContent = creating ? "Create browser vault" : "Unlock vault";
    submit.disabled = false;
    form.elements.password.focus();
  } catch (error) { $("#browser-unlock-error").textContent = error.message; }
  form.onsubmit = async (event) => {
    event.preventDefault();
    submit.disabled = true;
    $("#browser-unlock-error").textContent = "";
    try {
      if (creating && form.elements.password.value !== form.elements.confirm_password.value) {
        throw new Error("The vault passwords do not match.");
      }
      await window.browserVault.unlock(form.elements.password.value, creating);
      form.reset();
      $("#browser-unlock").hidden = true;
      document.body.classList.remove("vault-locked");
      initialized = true;
      await loadAccounts(true);
    } catch (error) { $("#browser-unlock-error").textContent = error.message; }
    finally {
      form.elements.password.value = "";
      form.elements.confirm_password.value = "";
      submit.disabled = false;
    }
  };
});
