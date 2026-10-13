(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const storageKey = "winauth.remembered-login.v1";
  const iterations = 600000;
  const encoder = new TextEncoder();
  let saved = null;
  let failedPins = 0;
  let blockedUntil = 0;
  const cryptoAvailable = window.isSecureContext && Boolean(window.crypto?.subtle);

  function bytesToBase64(bytes) {
    return btoa(String.fromCharCode(...bytes));
  }
  function base64ToBytes(text) {
    if (typeof text !== "string" || text.length > 12000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) {
      throw new Error("Saved login is damaged. Forget it and sign in with your password.");
    }
    try { return Uint8Array.from(atob(text), (character) => character.charCodeAt(0)); }
    catch { throw new Error("Saved login is damaged. Forget it and sign in with your password."); }
  }
  function readSaved() {
    let raw;
    try { raw = localStorage.getItem(storageKey); }
    catch { throw new Error("Browser storage is unavailable. Use your username and password."); }
    if (raw === null) return null;
    let data;
    try { data = JSON.parse(raw); }
    catch { throw new Error("Saved login is damaged. Forget it and use your password."); }
    if (!data || data.version !== 1 || data.iterations !== iterations ||
        base64ToBytes(data.salt).length !== 16 || base64ToBytes(data.iv).length !== 12 ||
        base64ToBytes(data.ciphertext).length < 16) {
      throw new Error("Saved login is damaged. Forget it and use your password.");
    }
    return data;
  }
  async function deriveKey(pin, salt) {
    if (!cryptoAvailable) throw new Error("PIN unlock requires HTTPS or localhost and Web Crypto support.");
    if (!/^[0-9]{6,12}$/.test(pin)) throw new Error("Use a PIN containing 6 to 12 digits.");
    const material = await crypto.subtle.importKey("raw", encoder.encode(pin), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({
      name: "PBKDF2", salt, iterations, hash: "SHA-256",
    }, material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function encryptLogin(credentials, pin) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(pin, salt);
    const encrypted = await crypto.subtle.encrypt({
      name: "AES-GCM", iv, additionalData: encoder.encode(location.origin),
    }, key, encoder.encode(JSON.stringify(credentials)));
    return { version: 1, iterations, salt: bytesToBase64(salt), iv: bytesToBase64(iv),
      ciphertext: bytesToBase64(new Uint8Array(encrypted)) };
  }
  async function decryptLogin(pin) {
    const key = await deriveKey(pin, base64ToBytes(saved.salt));
    let plain;
    try {
      plain = await crypto.subtle.decrypt({
        name: "AES-GCM", iv: base64ToBytes(saved.iv), additionalData: encoder.encode(location.origin),
      }, key, base64ToBytes(saved.ciphertext));
    } catch (error) {
      if (error.name === "OperationError") {
        throw new Error("Incorrect PIN or damaged saved login. You can use your password instead.");
      }
      throw error;
    }
    const credentials = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(plain));
    if (typeof credentials.username !== "string" || typeof credentials.password !== "string" ||
        !credentials.username || !credentials.password || credentials.username.length > 200 ||
        credentials.password.length > 1000) {
      throw new Error("Saved credentials are invalid. Forget this login and use your password.");
    }
    return credentials;
  }
  async function signIn(credentials) {
    if (credentials.username.includes(":")) throw new Error("Basic Auth usernames cannot contain a colon.");
    const authorization = bytesToBase64(encoder.encode(`${credentials.username}:${credentials.password}`));
    let response;
    try {
      response = await fetch("/api/login", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Authorization": `Basic ${authorization}`, "Content-Type": "application/json",
          "X-Requested-With": "WinAuthReborn" },
        body: "{}",
      });
    } catch { throw new Error("Could not reach your server. Check the connection and try again."); }
    if (!(response.headers.get("Content-Type") || "").includes("application/json")) {
      throw new Error("The server returned an unexpected login response.");
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Sign-in failed (${response.status}).`);
    if (result.ok !== true || typeof result.csrf !== "string") throw new Error("The login response is incomplete.");
    return result;
  }
  function setView(pin) {
    $("#password-view").hidden = pin;
    $("#pin-view").hidden = !pin;
    $("#use-saved-pin").hidden = !saved || !cryptoAvailable;
    $("#login-title").textContent = pin ? "Unlock your workspace." : "Welcome back.";
    $("#login-subtitle").textContent = pin
      ? "Your PIN decrypts the login saved on this browser." : "Sign in with your server's Basic Auth credentials.";
    $("#login-error").textContent = "";
    const input = pin ? $('#pin-login-form [name="pin"]') : $('#password-login-form [name="username"]');
    input.focus();
  }
  $("#remember-browser").onchange = () => {
    const enabled = $("#remember-browser").checked;
    $("#pin-setup").hidden = !enabled;
    for (const name of ["new_pin", "confirm_pin"]) {
      const input = $(`#password-login-form [name="${name}"]`);
      input.required = enabled;
      input.disabled = !enabled;
      if (!enabled) input.value = "";
    }
  };
  $("#remember-browser").onchange();
  $("#show-login-password").onclick = () => {
    const input = $('#password-login-form [name="password"]');
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $("#show-login-password").textContent = show ? "Hide" : "Show";
    $("#show-login-password").setAttribute("aria-pressed", String(show));
  };
  $("#password-login-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    const submit = event.submitter;
    submit.disabled = true;
    submit.textContent = "Signing in...";
    $("#login-error").textContent = "";
    try {
      const credentials = { username: form.elements.username.value, password: form.elements.password.value };
      let envelope = null;
      if ($("#remember-browser").checked) {
        if (form.elements.new_pin.value !== form.elements.confirm_pin.value) throw new Error("The PINs do not match.");
        envelope = await encryptLogin(credentials, form.elements.new_pin.value);
      }
      const result = await signIn(credentials);
      if (envelope) {
        try { localStorage.setItem(storageKey, JSON.stringify(envelope)); }
        catch {
          const logout = await fetch("/api/logout", {
            method: "POST", credentials: "same-origin",
            headers: { "X-CSRF-Token": result.csrf, "X-Requested-With": "WinAuthReborn" },
          });
          if (!logout.ok) throw new Error("Saving the PIN failed and sign-out failed. Close this session before retrying.");
          throw new Error("This browser could not save the PIN. Disable 'Remember this browser' and try again.");
        }
      }
      location.replace("/");
    } catch (error) { $("#login-error").textContent = error.message; }
    finally {
      for (const name of ["password", "new_pin", "confirm_pin"]) form.elements[name].value = "";
      form.elements.password.type = "password";
      $("#show-login-password").textContent = "Show";
      $("#show-login-password").setAttribute("aria-pressed", "false");
      submit.disabled = false;
      submit.textContent = "Sign in";
    }
  };
  $("#pin-login-form").onsubmit = async (event) => {
    event.preventDefault();
    if (Date.now() < blockedUntil) {
      $("#login-error").textContent = `Wait ${Math.ceil((blockedUntil - Date.now()) / 1000)} seconds before retrying.`;
      return;
    }
    const form = event.target;
    const submit = event.submitter;
    submit.disabled = true;
    submit.textContent = "Unlocking...";
    $("#login-error").textContent = "";
    let decrypted = false;
    try {
      const credentials = await decryptLogin(form.elements.pin.value);
      decrypted = true;
      await signIn(credentials);
      location.replace("/");
    } catch (error) {
      if (!decrypted) {
        failedPins++;
        if (failedPins >= 3) blockedUntil = Date.now() + Math.min(30, 5 * 2 ** (failedPins - 3)) * 1000;
      }
      $("#login-error").textContent = decrypted
        ? `${error.message} Use your password if the server credentials have changed.` : error.message;
    } finally {
      form.elements.pin.value = "";
      submit.disabled = false;
      submit.textContent = "Unlock vault";
    }
  };
  $("#use-password").onclick = () => setView(false);
  $("#use-saved-pin").onclick = () => setView(true);
  $("#forget-login").onclick = () => {
    $("#forget-error").textContent = "";
    $("#forget-dialog").showModal();
  };
  $("#cancel-forget").onclick = () => $("#forget-dialog").close();
  $("#confirm-forget").onclick = () => {
    try {
      localStorage.removeItem(storageKey);
      saved = null;
      $("#forget-login").hidden = true;
      $("#forget-dialog").close();
      setView(false);
    } catch { $("#forget-error").textContent = "Could not remove the saved login. Check browser storage permissions."; }
  };
  try {
    saved = readSaved();
    $("#forget-login").hidden = !saved;
    setView(Boolean(saved) && cryptoAvailable);
  } catch (error) {
    $("#forget-login").hidden = false;
    $("#login-error").textContent = error.message;
  }
  if (!cryptoAvailable) {
    $("#remember-browser").disabled = true;
    $("#secure-context-note").textContent = "PIN unlock requires HTTPS or localhost. Password login over public HTTP is unsafe.";
  }
})();
