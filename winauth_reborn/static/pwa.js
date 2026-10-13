(() => {
  "use strict";
  const root = new URL("../", document.currentScript.src);
  let installPrompt = null;
  let registration = null;
  const status = (message) => { document.querySelector("#pwa-status").textContent = message; };
  const installed = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
  });
  window.addEventListener("appinstalled", () => {
    document.querySelector("#pwa-install").hidden = true;
    installPrompt = null;
    status("App installed.");
  });
  document.addEventListener("DOMContentLoaded", async () => {
    const install = document.querySelector("#pwa-install");
    const update = document.querySelector("#pwa-update");
    install.hidden = Boolean(installed());
    install.onclick = async () => {
      if (!installPrompt) {
        status("On iPhone/iPad: Share > Add to Home Screen. Else use your browser's Install app menu.");
        return;
      }
      try {
        await installPrompt.prompt();
        const choice = await installPrompt.userChoice;
        status(choice.outcome === "accepted" ? "Installing app..." : "Installation cancelled.");
        installPrompt = null;
      } catch (error) { status(`Could not install: ${error.message}`); }
    };
    if (!window.isSecureContext || !("serviceWorker" in navigator)) {
      status("Installation and offline support require HTTPS or localhost and service worker support.");
      return;
    }
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!reloading && update.dataset.accepted === "true") { reloading = true; location.reload(); }
    });
    const watchUpdate = () => {
      if (!registration.waiting) return;
      update.hidden = false;
      update.onclick = () => {
        if (window.confirm("Update the app now? Open changes will be discarded and a browser vault will lock.")) {
          update.dataset.accepted = "true";
          registration.waiting.postMessage("ACTIVATE_UPDATE");
        }
      };
    };
    try {
      registration = await navigator.serviceWorker.register(new URL("service-worker.js", root), {
        scope: root.href, updateViaCache: "none",
      });
      watchUpdate();
      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        worker.addEventListener("statechange", () => {
          if (worker.state === "installed") watchUpdate();
          if (worker.state === "redundant") status("Offline setup failed. Check your connection and reload to retry.");
        });
      });
      await navigator.serviceWorker.ready;
      status(window.browserVault ? "Offline app ready. Vault stays on this browser." : "App shell ready. Your server is required for codes.");
    } catch (error) { status(`Offline setup failed: ${error.message}`); }
  });
})();
