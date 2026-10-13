(() => {
  "use strict";
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const steamAlphabet = "23456789BCDFGHJKMNPQRTVWXY";
  const maxCounter = (1n << 64n) - 1n;
  const icons = ["initial", "shield-check", "key-round", "gamepad-2", "mail", "globe",
    "briefcase-business", "star", "custom"];
  const defaults = { theme: "dark", density: "comfortable", hide_codes: false,
    sort_order: "imported", auto_hide_seconds: 0 };

  function base64(bytes) {
    let text = "";
    for (let start = 0; start < bytes.length; start += 32768) {
      text += String.fromCharCode(...bytes.subarray(start, start + 32768));
    }
    return btoa(text);
  }
  function unbase64(text) {
    if (typeof text !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) {
      throw new Error("Expected valid Base64 data.");
    }
    try { return Uint8Array.from(atob(text), (character) => character.charCodeAt(0)); }
    catch { throw new Error("Expected valid Base64 data."); }
  }
  function decodeSecret(secret) {
    if (typeof secret !== "string") throw new Error("Secret must be valid Base32.");
    const normalized = secret.replaceAll(" ", "").toUpperCase().replace(/=+$/, "");
    if (!/^[A-Z2-7]+$/.test(normalized) || ![0, 2, 4, 5, 7].includes(normalized.length % 8)) {
      throw new Error("Secret must be valid Base32.");
    }
    let buffer = 0;
    let bits = 0;
    const bytes = [];
    for (const character of normalized) {
      buffer = (buffer << 5) | alphabet.indexOf(character);
      bits += 5;
      if (bits >= 8) { bits -= 8; bytes.push((buffer >>> bits) & 255); }
    }
    if (!bytes.length) throw new Error("Secret cannot be empty.");
    return Uint8Array.from(bytes);
  }
  function integer(text, name, minimum, maximum) {
    if (!/^[+-]?\d+$/.test(String(text).trim())) throw new Error(`${name} must be an integer.`);
    const value = Number(text);
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
      throw new Error(`${name} must be between ${minimum} and ${maximum}.`);
    }
    return value;
  }
  function counterValue(text) {
    if (!/^[+]?\d+$/.test(String(text).trim())) throw new Error("Counter must be an unsigned 64-bit integer.");
    const value = BigInt(text);
    if (value < 0n || value > maxCounter) throw new Error("Counter must be an unsigned 64-bit integer.");
    return value;
  }
  async function generateCode(record, timestamp) {
    const counter = record.kind === "hotp" ? counterValue(record.counter)
      : counterValue(Math.floor(timestamp / record.period));
    const message = new Uint8Array(8);
    new DataView(message.buffer).setBigUint64(0, counter);
    const key = await crypto.subtle.importKey("raw", decodeSecret(record.secret), {
      name: "HMAC", hash: record.algorithm.replace("SHA", "SHA-"),
    }, false, ["sign"]);
    const digest = new Uint8Array(await crypto.subtle.sign("HMAC", key, message));
    const offset = digest[digest.length - 1] & 15;
    let value = new DataView(digest.buffer).getUint32(offset) & 0x7fffffff;
    if (record.kind === "steam") {
      let code = "";
      for (let i = 0; i < 5; i++) {
        code += steamAlphabet[value % steamAlphabet.length];
        value = Math.floor(value / steamAlphabet.length);
      }
      return code;
    }
    return String(value % (10 ** record.digits)).padStart(record.digits, "0");
  }
  async function normalizeIcon(value) {
    if (typeof value !== "string" ||
        !/^data:image\/(png|jpeg|webp);base64,/.test(value)) {
      throw new Error("Custom icons must be PNG, JPEG, or WebP images, not SVG or URLs.");
    }
    if (value.length > 180000) throw new Error("Custom icon must be smaller than 128 KB.");
    const bytes = unbase64(value.slice(value.indexOf(",") + 1));
    if (bytes.length > 128 * 1024) throw new Error("Custom icon must be smaller than 128 KB.");
    const png = [137, 80, 78, 71, 13, 10, 26, 10].every((byte, i) => bytes[i] === byte);
    const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    const webp = String.fromCharCode(...bytes.slice(0, 4)) === "RIFF" &&
      String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
    if (!png && !jpeg && !webp) throw new Error("Custom icon is not a supported image.");
    let bitmap;
    try { bitmap = await createImageBitmap(new Blob([bytes])); }
    catch { throw new Error("Could not read this icon. Choose a valid PNG, JPEG, or WebP image."); }
    try {
      if (bitmap.width > 2048 || bitmap.height > 2048) {
        throw new Error("Custom icon dimensions must not exceed 2048 pixels.");
      }
      const ratio = Math.min(1, 64 / bitmap.width, 64 / bitmap.height);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(bitmap.width * ratio));
      canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
      const context = canvas.getContext("2d");
      if (!context) throw new Error("This browser cannot process custom images.");
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/png");
    } finally { bitmap.close(); }
  }
  async function presentation(changes) {
    const result = { ...changes };
    if ("icon" in result && !icons.includes(result.icon)) throw new Error("Choose a supported icon.");
    if ("color" in result && (typeof result.color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(result.color))) {
      throw new Error("Color must be a six-digit hex color.");
    }
    if ("pinned" in result && typeof result.pinned !== "boolean") throw new Error("Pinned must be a boolean.");
    if ("custom_icon" in result) {
      if (result.custom_icon === null) result.custom_icon = "";
      else if (result.custom_icon) result.custom_icon = await normalizeIcon(result.custom_icon);
      else if (typeof result.custom_icon !== "string") throw new Error("Custom icon must be an image or empty text.");
    }
    return result;
  }
  async function parseLine(line) {
    const question = line.indexOf("?");
    const labelPart = question < 0 ? line : line.slice(0, question);
    const queryPart = question < 0 ? "" : line.slice(question);
    let uri;
    try { uri = new URL(labelPart.replaceAll("#", "%23") + queryPart); }
    catch { throw new Error("Expected an otpauth://totp/ or otpauth://hotp/ URI."); }
    if (uri.protocol !== "otpauth:" || !["totp", "hotp"].includes(uri.host) || uri.username || uri.password) {
      throw new Error("Expected an otpauth://totp/ or otpauth://hotp/ URI.");
    }
    if (uri.hash) throw new Error("Unexpected URI fragment; encode # as %23 in query values.");
    const pairs = [...uri.searchParams];
    if (new Set(pairs.map(([key]) => key)).size !== pairs.length) {
      throw new Error("Duplicate query parameters are not supported.");
    }
    const params = Object.fromEntries(pairs);
    decodeSecret(params.secret || "");
    const label = decodeURIComponent(uri.pathname.replace(/^\/+/, ""));
    const colon = label.indexOf(":");
    const issuer = params.issuer || (colon < 0 ? "" : label.slice(0, colon));
    const name = colon < 0 ? label : label.slice(colon + 1);
    const kind = issuer.toLowerCase() === "steam" ? "steam" : uri.host;
    const algorithm = (params.algorithm || "SHA1").toUpperCase();
    if (!["SHA1", "SHA256", "SHA512"].includes(algorithm)) throw new Error("Algorithm must be SHA1, SHA256, or SHA512.");
    const digits = integer(params.digits ?? (issuer.toLowerCase() === "battlenet" ? "8" : "6"), "Digits", 5, 10);
    const period = integer(params.period ?? "30", "Period", 1, 86400);
    if (uri.host === "hotp" && !("counter" in params)) throw new Error("HOTP requires a counter.");
    const counter = counterValue(params.counter ?? "0").toString();
    let steamData = {};
    if (kind === "steam") {
      if (uri.host !== "totp" || algorithm !== "SHA1") throw new Error("Steam requires TOTP with SHA1.");
      try { steamData = JSON.parse(params.data || "{}"); }
      catch { throw new Error("Steam data must be a JSON object."); }
      if (!steamData || typeof steamData !== "object" || Array.isArray(steamData)) {
        throw new Error("Steam data must be a JSON object.");
      }
      if ("identity_secret" in steamData && steamData.identity_secret !== null &&
          !unbase64(steamData.identity_secret).length) {
        throw new Error("Steam identity_secret must be non-empty Base64.");
      }
    }
    const appearance = {};
    for (const field of ["icon", "color", "custom_icon"]) {
      if (`x-reborn-${field}` in params) appearance[field] = params[`x-reborn-${field}`];
    }
    if ("x-reborn-pinned" in params) {
      if (!["true", "false"].includes(params["x-reborn-pinned"])) throw new Error("Invalid pinned value.");
      appearance.pinned = params["x-reborn-pinned"] === "true";
    }
    const validated = await presentation(appearance);
    if (validated.icon === "custom" && !validated.custom_icon) throw new Error("Custom icon is missing its image.");
    return { id: crypto.randomUUID().replaceAll("-", ""), name: name || issuer || "Imported",
      issuer, kind, secret: params.secret, algorithm, digits: kind === "steam" ? 5 : digits,
      period, counter, uri: uri.href, steam_data: steamData, device_id: params.deviceid || "",
      icon: "initial", color: "#b4ef82", custom_icon: "", pinned: false, ...validated };
  }
  async function parseExport(text) {
    if (typeof text !== "string" || !text || new TextEncoder().encode(text).length > 2 * 1024 * 1024) {
      throw new Error("Import text is required and must be at most 2 MB.");
    }
    if (text.trimStart().startsWith("-----BEGIN PGP")) throw new Error("This export is PGP-encrypted. Decrypt it locally before importing the .txt.");
    const lines = text.replace(/^\ufeff/, "").split(/\r\n|\n|\r/);
    const records = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line || line.startsWith("#")) continue;
      try { records.push(await parseLine(line)); }
      catch (error) { throw new Error(`Line ${i + 1}: ${error.message}`); }
      if (records.length > 1000) throw new Error("Import at most 1000 authenticators at a time.");
    }
    if (!records.length) throw new Error("No authenticators found in this file.");
    return records;
  }
  function exportRecord(record) {
    const uri = new URL(record.uri);
    if (record.kind === "hotp") uri.searchParams.set("counter", record.counter);
    for (const key of ["icon", "color", "custom_icon", "pinned"]) {
      uri.searchParams.set(`x-reborn-${key}`, String(record[key]));
    }
    return uri.href;
  }
  async function editRecord(record, rawChanges) {
    const allowed = ["name", "issuer", "secret", "algorithm", "digits", "period", "icon", "color", "custom_icon", "pinned"];
    if (!Object.keys(rawChanges).length || Object.keys(rawChanges).some((key) => !allowed.includes(key))) {
      throw new Error("Provide supported account fields to update.");
    }
    const changes = await presentation(rawChanges);
    for (const key of ["name", "issuer"]) {
      if (key in changes) {
        if (typeof changes[key] !== "string" || changes[key].length > 200 || (key === "name" && !changes[key].trim())) {
          throw new Error(`${key} must be text of at most 200 characters.`);
        }
        changes[key] = changes[key].trim();
      }
    }
    if (changes.issuer?.includes(":")) throw new Error("Issuer must not contain a colon.");
    for (const key of ["digits", "period"]) {
      if (key in changes && !Number.isInteger(changes[key])) throw new Error(`${key} must be an integer.`);
    }
    if (record.kind === "steam") {
      if ((changes.issuer ?? record.issuer).toLowerCase() !== "steam") throw new Error("A Steam authenticator's issuer must remain Steam.");
      if (["secret", "algorithm", "digits", "period"].some((key) => key in changes)) throw new Error("Steam key parameters are read-only.");
    } else if ((changes.issuer ?? record.issuer).toLowerCase() === "steam") {
      throw new Error("Changing an ordinary account into Steam is not supported.");
    }
    if ("algorithm" in changes && typeof changes.algorithm !== "string") throw new Error("Algorithm must be text.");
    if ("secret" in changes) decodeSecret(changes.secret);
    const edited = { ...record, ...changes };
    if (edited.icon === "custom" && !edited.custom_icon) throw new Error("Upload an image before selecting the custom icon.");
    const uri = new URL(exportRecord(edited));
    for (const field of ["secret", "issuer", "algorithm", "digits", "period"]) {
      uri.searchParams.set(field, String(edited[field]));
    }
    uri.pathname = "/" + (edited.issuer ? encodeURIComponent(edited.issuer) + ":" : "") + encodeURIComponent(edited.name);
    const validated = await parseLine(uri.href);
    if (validated.kind !== record.kind) throw new Error("Account type cannot be changed.");
    return { ...validated, id: record.id, counter: record.counter };
  }
  function validateSettings(changes) {
    const choices = { theme: ["dark", "light", "system"], density: ["comfortable", "compact"],
      sort_order: ["imported", "name", "issuer"] };
    for (const [key, value] of Object.entries(changes)) {
      if (!Object.hasOwn(defaults, key)) throw new Error(`Unknown setting: ${key}.`);
      if (Object.hasOwn(choices, key) && !choices[key].includes(value)) throw new Error(`Invalid ${key} setting.`);
      if (key === "hide_codes" && typeof value !== "boolean") throw new Error("hide_codes must be a boolean.");
      if (key === "auto_hide_seconds" && ![0, 30, 60, 300].includes(value)) throw new Error("Invalid auto-hide interval.");
    }
    return changes;
  }
  function identity(record) {
    return JSON.stringify([record.kind, base64(decodeSecret(record.secret)), record.name,
      record.issuer, record.algorithm, record.digits, record.period]);
  }
  window.WinAuthTokens = Object.freeze({ base64, unbase64, decodeSecret, generateCode,
    parseExport, exportRecord, editRecord, validateSettings, identity, defaults, maxCounter });
})();
