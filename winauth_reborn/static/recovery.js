"use strict";

function clearRecovery() {
  clearTimeout(recoveryTimer);
  recoveryGeneration++;
  recoveryAccount = null;
  $("#recovery-fields").replaceChildren();
  $("#recovery-error").textContent = "";
}
async function openRecovery(account) {
  if (!await confirmAction("Reveal Steam recovery details?", "These secrets can grant access to your Steam account or remove its authenticator. Only reveal them in a private place.", "Reveal details", true)) return;
  clearRecovery();
  const sequence = recoveryGeneration;
  recoveryAccount = account.id;
  $("#recovery-account").textContent = account.name;
  $("#recovery-fields").append(element("p", "muted", "Loading recovery details..."));
  $("#recovery-dialog").showModal();
  recoveryTimer = setTimeout(() => {
    clearRecovery();
    $("#recovery-dialog").close();
  }, 60000);
  try {
    const data = await json(`/api/accounts/${account.id}/steam/recovery`, { method: "POST" });
    if (sequence !== recoveryGeneration || !$("#recovery-dialog").open) return;
    const fields = [
      ["guard_code", "Current Steam Guard code"],
      ["recovery_code", "Recovery / revocation code"],
      ["shared_secret", "Steam Guard secret (Base32)"],
      ["shared_secret_base64", "Steam Guard secret (Base64)"],
      ["serial_number", "Authenticator serial"],
    ];
    $("#recovery-fields").replaceChildren(...fields.map(([key, label]) => {
      const row = element("label", "field recovery-field", label);
      if (!data[key]) {
        row.append(element("span", "muted", "Not included in the imported export"));
        return row;
      }
      const group = element("div", "secret-row");
      const input = element("input");
      input.readOnly = true;
      input.value = String(data[key]);
      input.dataset.field = key;
      input.autocomplete = "off";
      const button = element("button", "button secondary small", "Copy");
      button.type = "button";
      button.dataset.copy = key;
      button.setAttribute("aria-label", `Copy ${label}`);
      button.onclick = async () => {
        try {
          if (key === "guard_code") {
            const fresh = await json("/api/accounts");
            const current = fresh.accounts.find((item) => item.id === account.id);
            if (!current) throw new Error("Account was removed.");
            await copy(current.code);
          } else await copy(input.value, "Secret copied. Keep it private.");
        } catch (error) { $("#recovery-error").textContent = error.message; }
      };
      group.append(input, button);
      row.append(group);
      return row;
    }));
  } catch (error) {
    if (sequence !== recoveryGeneration || !$("#recovery-dialog").open) return;
    $("#recovery-fields").replaceChildren();
    $("#recovery-error").textContent = error.message;
  }
}
$("#recovery-dialog").addEventListener("close", clearRecovery);
