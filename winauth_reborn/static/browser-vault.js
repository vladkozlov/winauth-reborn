(() => {
  "use strict";
  const tokens = window.WinAuthTokens;
  const encoder = new TextEncoder();
  const root = new URL("./", location.href);
  const databaseName = `winauth-browser-v1:${root.pathname}`;
  const lockName = `${databaseName}:write`;
  const iterations = 600000;
  const additionalData = encoder.encode(`${location.origin}${root.pathname}:winauth-browser-v1`);
  let database = null;
  let key = null;
  let salt = null;
  class VaultError extends Error {
    constructor(message, status = 400) { super(message); this.status = status; }
  }
  function transaction(mode, operation) {
    return new Promise((resolve, reject) => {
      const tx = database.transaction("vault", mode);
      const request = operation(tx.objectStore("vault"));
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(new VaultError(`Browser storage failed: ${tx.error?.message || "transaction aborted"}`));
      tx.onerror = () => { /* onabort reports the failure after the transaction rolls back. */ };
    });
  }
  const read = () => transaction("readonly", (store) => store.get("owner"));
  function validateEnvelope(envelope) {
    if (!envelope || envelope.version !== 1 || envelope.iterations !== iterations ||
        typeof envelope.salt !== "string" || typeof envelope.iv !== "string" ||
        typeof envelope.ciphertext !== "string" ||
        tokens.unbase64(envelope.salt).length !== 16 || tokens.unbase64(envelope.iv).length !== 12 ||
        tokens.unbase64(envelope.ciphertext).length < 16) {
      throw new VaultError("The browser vault is damaged or unsupported. Restore your exported backup.");
    }
  }
  async function derive(password, saltBytes) {
    const material = await crypto.subtle.importKey("raw", encoder.encode(password), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: saltBytes, iterations },
      material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  async function encrypt(payload, activeKey, saltText) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData },
      activeKey, encoder.encode(JSON.stringify(payload)));
    return { version: 1, iterations, salt: saltText, iv: tokens.base64(iv),
      ciphertext: tokens.base64(new Uint8Array(ciphertext)) };
  }
  async function decrypt(envelope, activeKey) {
    validateEnvelope(envelope);
    let plaintext;
    try {
      plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv: tokens.unbase64(envelope.iv),
        additionalData }, activeKey, tokens.unbase64(envelope.ciphertext));
    } catch (error) {
      if (error.name === "OperationError") throw new VaultError("Incorrect vault password or damaged vault. Restore a backup if necessary.");
      throw error;
    }
    const payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(plaintext));
    if (!payload || payload.version !== 1 || !Array.isArray(payload.accounts) ||
        !payload.settings || typeof payload.settings !== "object" || Array.isArray(payload.settings)) {
      throw new VaultError("The decrypted vault format is unsupported. Restore your exported backup.");
    }
    tokens.validateSettings(payload.settings);
    return payload;
  }
  async function open() {
    if (!window.isSecureContext || !crypto.subtle || !window.indexedDB || !navigator.locks) {
      throw new VaultError("Browser mode requires HTTPS or localhost, Web Crypto, IndexedDB, and Web Locks in a modern browser.");
    }
    database = await new Promise((resolve, reject) => {
      const request = indexedDB.open(databaseName, 1);
      request.onupgradeneeded = () => request.result.createObjectStore("vault");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(new VaultError(`Cannot open browser storage: ${request.error.message}`));
      request.onblocked = () => reject(new VaultError("Close other WinAuth browser tabs before upgrading storage."));
    });
    database.onversionchange = () => { key = null; database.close(); location.reload(); };
    const envelope = await read();
    if (envelope !== undefined) validateEnvelope(envelope);
    return envelope !== undefined;
  }
  async function unlock(password, creating) {
    if (typeof password !== "string" || !password || password.length > 1000) throw new VaultError("Enter your vault password.");
    await navigator.locks.request(lockName, async () => {
      const envelope = await read();
      if (creating && envelope !== undefined) throw new VaultError("A vault was already created in another tab. Reload and unlock it.");
      if (!creating && envelope === undefined) throw new VaultError("The local vault was removed. Reload to create one or import a backup.");
      if (creating && password.length < 12) throw new VaultError("Use a vault password of at least 12 characters.");
      const saltText = creating ? tokens.base64(crypto.getRandomValues(new Uint8Array(16))) : envelope.salt;
      const candidate = await derive(password, tokens.unbase64(saltText));
      if (creating) {
        const payload = { version: 1, accounts: [], settings: { ...tokens.defaults } };
        const encrypted = await encrypt(payload, candidate, saltText);
        await transaction("readwrite", (store) => store.put(encrypted, "owner"));
      } else { await decrypt(envelope, candidate); }
      salt = saltText;
      key = candidate;
    });
  }
  function locked() {
    return new VaultError("The browser vault is locked. Unlock it again.", 401);
  }
  async function withVault(write, operation) {
    return navigator.locks.request(lockName, async () => {
      const activeKey = key;
      if (!activeKey) throw locked();
      const envelope = await read();
      if (!envelope || envelope.salt !== salt) throw new VaultError("The stored vault changed. Lock and reload before continuing.", 401);
      const payload = await decrypt(envelope, activeKey);
      const result = await operation(payload);
      if (key !== activeKey) throw locked();
      if (write) {
        const encrypted = await encrypt(payload, activeKey, salt);
        if (key !== activeKey) throw locked();
        await transaction("readwrite", (store) => store.put(encrypted, "owner"));
      }
      return result;
    });
  }
  function find(payload, id) {
    const record = payload.accounts.find((item) => item.id === id);
    if (!record) throw new VaultError("Account not found.", 404);
    return record;
  }
  async function dispatch(path, method, data) {
    if (path === "/api/logout" && method === "POST") { key = null; salt = null; return { ok: true }; }
    if (!key) throw locked();
    if (path === "/api/accounts" && method === "GET") {
      return withVault(false, async (payload) => {
        const now = Date.now() / 1000;
        const accounts = await Promise.all(payload.accounts.map(async (record) => ({
          id: record.id, name: record.name, issuer: record.issuer, kind: record.kind,
          algorithm: record.algorithm, digits: record.digits, period: record.period,
          counter: record.counter, icon: record.icon, color: record.color, pinned: record.pinned,
          custom_icon: record.icon === "custom" ? record.custom_icon : "",
          code: record.kind === "hotp" ? null : await tokens.generateCode(record, now),
          remaining: record.period - now % record.period, steam_connected: false,
          confirmations_available: false,
        })));
        return { accounts, server_time: now, settings: payload.settings };
      });
    }
    if (path === "/api/settings" && ["GET", "PATCH"].includes(method)) {
      return withVault(method === "PATCH", (payload) => {
        if (method === "PATCH") Object.assign(payload.settings, tokens.validateSettings(data));
        return { settings: payload.settings };
      });
    }
    if (path === "/api/import" && method === "POST") {
      const records = await tokens.parseExport(data.text);
      return withVault(true, (payload) => {
        const existing = new Set(payload.accounts.map(tokens.identity));
        let added = 0;
        for (const record of records) {
          const identity = tokens.identity(record);
          if (existing.has(identity)) continue;
          payload.accounts.push(record);
          existing.add(identity);
          added++;
        }
        return { added, skipped: records.length - added };
      });
    }
    if (path === "/api/export" && method === "POST") {
      return withVault(false, (payload) => new Response(payload.accounts.map(tokens.exportRecord).join("\n") + "\n",
        { headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" } }));
    }
    const match = /^\/api\/accounts\/([a-f0-9]+)(\/hotp|\/steam\/recovery)?$/.exec(path);
    if (match) {
      const [, id, action] = match;
      if (!action && ["PATCH", "DELETE"].includes(method)) {
        return withVault(true, async (payload) => {
          const record = find(payload, id);
          if (method === "DELETE") payload.accounts = payload.accounts.filter((item) => item.id !== id);
          else payload.accounts[payload.accounts.indexOf(record)] = await tokens.editRecord(record, data);
          return { ok: true };
        });
      }
      if (action === "/hotp" && method === "POST") {
        return withVault(true, async (payload) => {
          const record = find(payload, id);
          if (record.kind !== "hotp") throw new VaultError("This account is not counter-based.");
          if (BigInt(record.counter) === tokens.maxCounter) throw new VaultError("HOTP counter is exhausted.");
          const code = await tokens.generateCode(record, 0);
          record.counter = (BigInt(record.counter) + 1n).toString();
          return { code };
        });
      }
      if (action === "/steam/recovery" && method === "POST") {
        return withVault(false, async (payload) => {
          const record = find(payload, id);
          if (record.kind !== "steam") throw new VaultError("This account is not a Steam authenticator.");
          const now = Date.now() / 1000;
          return { guard_code: await tokens.generateCode(record, now), remaining: record.period - now % record.period,
            shared_secret: record.secret, shared_secret_base64: tokens.base64(tokens.decodeSecret(record.secret)),
            recovery_code: record.steam_data.revocation_code || record.steam_data.recovery_code || null,
            serial_number: record.steam_data.serial_number || null };
        });
      }
    }
    throw new VaultError("This operation requires the server version. Browser mode cannot connect to Steam confirmations.", 501);
  }
  async function request(path, options = {}) {
    try {
      const data = options.body === undefined ? {} : JSON.parse(options.body);
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new VaultError("Request body must be a JSON object.");
      const result = await dispatch(path, options.method || "GET", data);
      return result instanceof Response ? result : Response.json(result, { headers: { "Cache-Control": "no-store" } });
    } catch (error) {
      return Response.json({ error: error.message }, { status: error.status || 400,
        headers: { "Cache-Control": "no-store" } });
    }
  }
  window.browserVault = Object.freeze({ open, unlock, request, databaseName });
})();
