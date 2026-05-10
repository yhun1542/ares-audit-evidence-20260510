// core/env.mjs — 시크릿 안전 로더 (평문 하드코딩 완전 제거)
// 우선순위: 프로세스 env > /etc/ares/kis.env (chmod 600) > AWS SSM Parameter Store (선택)
import fs from "node:fs";
import path from "node:path";

const SAFE_KEYS = [
  "KIS_APP_KEY", "KIS_APP_SECRET",
  "KIS_ACCOUNT_NO", "KIS_ACCOUNT_PROD_CODE",
  "KIS_HTS_ID",
  "KIS_BASE_URL", "KIS_WS_URL",
  "KIS_ENV"  // "real" | "paper"
];

function loadDotFile(filepath) {
  if (!fs.existsSync(filepath)) return {};
  const stat = fs.statSync(filepath);
  // 보안: 0600 권한 강제
  if ((stat.mode & 0o077) !== 0) {
    throw new Error(`[SEC] ${filepath} has loose permissions. Run: chmod 600 ${filepath}`);
  }
  const out = {};
  for (const line of fs.readFileSync(filepath, "utf-8").split("\n")) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!m) continue;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    out[m[1]] = v;
  }
  return out;
}

export function loadEnv() {
  // v7.3 M2 (GPT/Gemini): force TLS by default on BOTH the REST base URL and
  // the realtime WebSocket. KIS publishes wss endpoints on the standard ports
  // used by their official SDK; the legacy ws:// values were a historical
  // compatibility quirk that silently downgraded every realtime tick to
  // plaintext. Operators who absolutely must keep plaintext (test lab only)
  // can set ARES_WS_ALLOW_PLAINTEXT=1 and KIS_WS_URL explicitly.
  const defaults = {
    KIS_ENV: "real",
    KIS_BASE_URL_REAL: "https://openapi.koreainvestment.com:9443",
    KIS_BASE_URL_PAPER: "https://openapivts.koreainvestment.com:29443",
    KIS_WS_URL_REAL: "wss://ops.koreainvestment.com:21000",
    KIS_WS_URL_PAPER: "wss://ops.koreainvestment.com:31000",
    KIS_ACCOUNT_PROD_CODE: "01"
  };

  // 1) /etc/ares/kis.env (권장 위치) 또는 $ARES_ENV_FILE
  const envFile = process.env.ARES_ENV_FILE || "/etc/ares/kis.env";
  const fileEnv = loadDotFile(envFile);

  const env = { ...defaults, ...fileEnv, ...process.env };

  const mode = (env.KIS_ENV || "real").toLowerCase();
  if (!env.KIS_BASE_URL) {
    env.KIS_BASE_URL = mode === "paper" ? env.KIS_BASE_URL_PAPER : env.KIS_BASE_URL_REAL;
  }
  if (!env.KIS_WS_URL) {
    env.KIS_WS_URL = mode === "paper" ? env.KIS_WS_URL_PAPER : env.KIS_WS_URL_REAL;
  }

  // 필수 검증
  const missing = [];
  for (const k of ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO"]) {
    if (!env[k]) missing.push(k);
  }
  if (missing.length) {
    throw new Error(`[KIS ENV] Missing required: ${missing.join(", ")} — check ${envFile} or process env`);
  }

  // v7.3 M2: TLS enforcement. REST baseUrl MUST be https://. WS URL MUST be
  // wss:// unless ARES_WS_ALLOW_PLAINTEXT=1 is explicitly set (tests only).
  if (!/^https:\/\//i.test(env.KIS_BASE_URL)) {
    throw new Error(`[SEC] KIS_BASE_URL must be https://, got='${env.KIS_BASE_URL}'. Refusing to start with plaintext REST.`);
  }
  if (!/^wss:\/\//i.test(env.KIS_WS_URL)) {
    if (process.env.ARES_WS_ALLOW_PLAINTEXT !== "1") {
      throw new Error(`[SEC] KIS_WS_URL must be wss://, got='${env.KIS_WS_URL}'. Set ARES_WS_ALLOW_PLAINTEXT=1 only for isolated lab runs.`);
    }
    // Allowed by explicit opt-in — leave a breadcrumb in stderr so ops logs
    // capture the deviation.
    try { console.warn(`[SEC][WARN] KIS_WS_URL is plaintext (ws://); allowed by ARES_WS_ALLOW_PLAINTEXT=1`); } catch {}
  }

  // 프로덕션 가드: KIS_ENV=real 이면 반드시 ARES_PROD_ACK=yes-i-know-its-live 필요
  if (mode === "real" && process.env.ARES_PROD_ACK !== "yes-i-know-its-live") {
    throw new Error(
      "[PROD GUARD] Real trading requires ARES_PROD_ACK=yes-i-know-its-live. " +
      "Set paper mode with KIS_ENV=paper for testing."
    );
  }

  return {
    appKey:          env.KIS_APP_KEY,
    appSecret:       env.KIS_APP_SECRET,
    accountNo:       env.KIS_ACCOUNT_NO,
    accountProdCode: env.KIS_ACCOUNT_PROD_CODE,
    htsId:           env.KIS_HTS_ID || "",
    baseUrl:         env.KIS_BASE_URL,
    wsUrl:           env.KIS_WS_URL,
    isPaper:         mode === "paper",
    mode
  };
}

export function redact(str, keep = 4) {
  if (!str) return "";
  const s = String(str);
  if (s.length <= keep * 2) return "***";
  return s.slice(0, keep) + "***" + s.slice(-keep);
}
