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
    const reload = () => {
      if (!reloading) { reloading = true; location.reload(); }
    };
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (update.dataset.accepted === "true") reload();
    });
    const watchUpdate = () => {
      const worker = registration.waiting;
      update.hidden = !worker;
      if (!worker) return;
      update.onclick = () => {
        if (!window.confirm("Update the app now? Open changes will be discarded and a browser vault will lock.")) return;
        if (worker.state === "redundant") {
          status("This update is no longer available. Reload to check for a newer version.");
          watchUpdate();
          return;
        }
        update.dataset.accepted = "true";
        update.disabled = true;
        worker.addEventListener("statechange", () => {
          if (worker.state === "activated") reload();
          else if (worker.state === "redundant") {
            update.dataset.accepted = "false";
            update.disabled = false;
            status("App update failed. Reload to check for a newer version.");
            watchUpdate();
          }
        });
        if (worker.state === "activated" || navigator.serviceWorker.controller === worker) {
          reload();
        } else {
          try { worker.postMessage("ACTIVATE_UPDATE"); }
          catch (error) {
            update.dataset.accepted = "false";
            update.disabled = false;
            status(`Could not activate the update: ${error.message}`);
            watchUpdate();
          }
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
        if (!worker) { watchUpdate(); return; }
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
