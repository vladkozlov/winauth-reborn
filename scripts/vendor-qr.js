"use strict";

const fs = require("node:fs");
const path = require("node:path");

const packageDirectory = path.dirname(require.resolve("jsqr/package.json"));
const staticDirectory = path.resolve("winauth_reborn/static");
fs.copyFileSync(path.join(packageDirectory, "dist", "jsQR.js"), path.join(staticDirectory, "qr-decoder.js"));
fs.copyFileSync(
  path.join(packageDirectory, "LICENSE"),
  path.join(staticDirectory, "qr-decoder.LICENSE"),
);
