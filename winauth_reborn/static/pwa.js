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
    const cleanOldCaches = (worker) => new Promise((resolve, reject) => {
      const channel = new MessageChannel();
      const timeout = setTimeout(() => {
        channel.port1.close();
        reject(new Error("Timed out while cleaning old app caches."));
      }, 5000);
      channel.port1.onmessage = () => {
        clearTimeout(timeout);
        channel.port1.close();
        resolve();
      };
      channel.port1.onmessageerror = () => {
        clearTimeout(timeout);
        channel.port1.close();
        reject(new Error("Could not confirm old app cache cleanup."));
      };
      worker.postMessage("CLEAN_OLD_CACHES", [channel.port2]);
    });
    const activateUpdate = (worker) => new Promise((resolve, reject) => {
      const channel = new MessageChannel();
      const timeout = setTimeout(() => {
        channel.port1.close();
        reject(new Error("Timed out while activating the app update."));
      }, 5000);
      channel.port1.onmessage = () => {
        clearTimeout(timeout);
        channel.port1.close();
        status("Update activation requested. Waiting for the new worker to take control...");
        resolve();
      };
      channel.port1.onmessageerror = () => {
        clearTimeout(timeout);
        channel.port1.close();
        reject(new Error("Could not confirm app update activation."));
      };
      worker.postMessage("ACTIVATE_UPDATE", [channel.port2]);
    });
    const watchUpdate = () => {
      const waitingWorker = registration.waiting;
      update.hidden = !waitingWorker;
      if (!waitingWorker) {
        update.onclick = null;
        return;
      }
      update.onclick = () => {
        const worker = registration.waiting || waitingWorker;
        if (!worker) {
          watchUpdate();
          return;
        }
        if (!window.confirm("Update the app now? Open changes will be discarded and a browser vault will lock.")) return;
        if (worker.state === "redundant") {
          status("This update is no longer available. Reload to check for a newer version.");
          watchUpdate();
          return;
        }
        update.dataset.accepted = "true";
        update.disabled = true;
        status("Activating app update...");
        let completingUpdate = false;
        const completeUpdate = async () => {
          if (completingUpdate || worker.state !== "activated" ||
              navigator.serviceWorker.controller !== worker) return;
          completingUpdate = true;
          try {
            await cleanOldCaches(worker);
            reload();
          } catch (error) {
            completingUpdate = false;
            update.dataset.accepted = "false";
            update.disabled = false;
            status(`Could not finish the app update: ${error.message}`);
          }
        };
        const reloadIfControlled = () => {
          completeUpdate();
        };
        worker.addEventListener("statechange", () => {
          if (worker.state === "activated") reloadIfControlled();
          else if (worker.state === "redundant") {
            update.dataset.accepted = "false";
            update.disabled = false;
            status("App update failed. Reload to check for a newer version.");
            watchUpdate();
          }
        });
        navigator.serviceWorker.addEventListener("controllerchange", reloadIfControlled);
        if (worker.state === "activated") reloadIfControlled();
        else if (navigator.serviceWorker.controller !== worker) {
          activateUpdate(worker).catch((error) => {
            update.dataset.accepted = "false";
            update.disabled = false;
            status(`Could not activate the update: ${error.message}`);
            watchUpdate();
          });
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
          if (worker.state === "installed") setTimeout(watchUpdate, 0);
          if (worker.state === "activated") watchUpdate();
          if (worker.state === "redundant") {
            status("Offline setup failed. Check your connection and reload to retry.");
            watchUpdate();
          }
        });
      });
      await navigator.serviceWorker.ready;
      status(window.browserVault ? "Offline app ready. Vault stays on this browser." : "App shell ready. Your server is required for codes.");
    } catch (error) { status(`Offline setup failed: ${error.message}`); }
  });
})();
