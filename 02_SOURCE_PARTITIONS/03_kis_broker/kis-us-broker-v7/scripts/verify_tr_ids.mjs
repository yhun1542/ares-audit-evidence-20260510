#!/usr/bin/env node
// scripts/verify_tr_ids.mjs — v7.1: TR_ID CI 검증
// 사용: node scripts/verify_tr_ids.mjs <공식저장소경로>
// 예  : node scripts/verify_tr_ids.mjs /home/ubuntu/official_repos/open-trading-api

import fs from "node:fs";
import path from "node:path";
import { ALL_TR_IDS } from "../core/tr_ids.mjs";

const OFFICIAL = process.argv[2] || "/home/ubuntu/ares_kis_upgrade/official_repos/open-trading-api";
if (!fs.existsSync(OFFICIAL)) {
  console.error(`[verify] 공식 저장소 경로 없음: ${OFFICIAL}`);
  process.exit(2);
}

// 재귀적으로 .py/.md/.json 수집
function walk(dir, out = []) {
  const ents = fs.readdirSync(dir, { withFileTypes: true });
  for (const e of ents) {
    if (e.name.startsWith(".")) continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(py|md|json|txt|ipynb)$/i.test(e.name)) out.push(p);
  }
  return out;
}

const files = walk(OFFICIAL);
console.error(`[verify] scanning ${files.length} files...`);

// 전체 텍스트 연결 (정규식 한 번에)
let bigText = "";
for (const f of files) {
  try { bigText += "\n" + fs.readFileSync(f, "utf-8"); } catch (_) {}
}

const missing = [];
const found = [];
for (const tr of ALL_TR_IDS) {
  if (bigText.includes(tr)) found.push(tr);
  else missing.push(tr);
}

console.log(`[verify] total=${ALL_TR_IDS.length} found=${found.length} missing=${missing.length}`);
if (missing.length) {
  console.log("[verify] MISSING TR_IDS:");
  for (const m of missing) console.log("  -", m);
  process.exit(1);
}
console.log("[verify] ✅ ALL TR_IDS EXIST IN OFFICIAL REPO");
