#!/usr/bin/env node
// Generates wrangler.toml from wrangler.toml.tmpl by injecting the KV
// namespace id from the environment. Keeps the id out of version control:
// the template ships a __PERGAM_KV_ID__ placeholder, the real id lives in
// web/worker/.env (gitignored). Runs automatically via predev/predeploy.

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const workerDir = join(here, "..");
const tmplPath = join(workerDir, "wrangler.toml.tmpl");
const outPath = join(workerDir, "wrangler.toml");
const envPath = join(workerDir, ".env");

const PLACEHOLDER = "__PERGAM_KV_ID__";

function loadEnvFile(path) {
  if (!existsSync(path)) return {};
  const out = {};
  for (const raw of readFileSync(path, "utf8").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

const fileEnv = loadEnvFile(envPath);
const kvId = process.env.PERGAM_KV_ID ?? fileEnv.PERGAM_KV_ID;

if (!kvId) {
  console.error(
    "[gen-wrangler-config] PERGAM_KV_ID is not set.\n" +
      "  Copy web/worker/.env.example to web/worker/.env and set PERGAM_KV_ID,\n" +
      "  or export PERGAM_KV_ID in the environment before running.",
  );
  process.exit(1);
}

const tmpl = readFileSync(tmplPath, "utf8");
if (!tmpl.includes(PLACEHOLDER)) {
  console.error(`[gen-wrangler-config] ${PLACEHOLDER} not found in template.`);
  process.exit(1);
}

writeFileSync(outPath, tmpl.replaceAll(PLACEHOLDER, kvId));
console.log(`[gen-wrangler-config] wrote wrangler.toml (KV id from ${process.env.PERGAM_KV_ID ? "env" : ".env"}).`);
