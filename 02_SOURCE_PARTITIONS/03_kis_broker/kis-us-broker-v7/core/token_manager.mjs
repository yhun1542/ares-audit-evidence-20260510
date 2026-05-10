// core/token_manager.mjs — v7.1
// 단일 토큰·Hashkey·WS approval_key SSOT (멀티프로세스 안전)
// v7.1 변경점 (3-AI 리뷰 반영):
//   P1: O_EXCL 기반 파일락(lockfile) 추가 — 여러 PM2 워커가 동시에 토큰을 발급하지 못하도록 직렬화
//   P1: 락 획득 후 캐시 재조회(double-check) — 다른 프로세스가 이미 발급했으면 재사용
//   P2: force 옵션 (401/auth_fail 시 쿨다운 무시하고 재발급)
//   P2: 프로세스 크래시로 남은 stale lock 자동 정리 (lockfile mtime > FILE_LOCK_TIMEOUT_MS)

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const TOKEN_CACHE_DIR = process.env.ARES_TOKEN_DIR || "/tmp/ares-kis-token";
const FILE_LOCK_TIMEOUT_MS = 30_000;   // 30초 넘으면 stale lock
const LOCK_RETRY_INTERVAL_MS = 200;
const MIN_REISSUE_GAP_MS = 65_000;    // KIS 1분 정책 + 여유 5초

function ensureDir() {
  if (!fs.existsSync(TOKEN_CACHE_DIR)) {
    fs.mkdirSync(TOKEN_CACHE_DIR, { recursive: true, mode: 0o700 });
  }
}

function tokenPath(appKey) {
  const hash = crypto.createHash("sha256").update(appKey).digest("hex").slice(0, 12);
  return path.join(TOKEN_CACHE_DIR, `token-${hash}.json`);
}

function lockPath(appKey) {
  return tokenPath(appKey) + ".lock";
}

// v7.2 round-3 (GPT): hardened cache read. Corrupt JSON or truncated write
// caused silent crashes in earlier versions; now we validate the structure
// and quarantine the corrupt file so the next write starts clean.
function readCache(appKey) {
  const p = tokenPath(appKey);
  try {
    if (!fs.existsSync(p)) return null;
    const raw = fs.readFileSync(p, "utf-8");
    const obj = JSON.parse(raw);
    // Minimal shape check — any field missing => treat as invalid.
    if (!obj || typeof obj !== "object") return null;
    if (typeof obj.access_token !== "string" || typeof obj.expires_at_ms !== "number") {
      // Quarantine so write path creates a fresh file.
      try {
        const q = p + ".invalid." + Date.now();
        fs.renameSync(p, q);
      } catch {}
      return null;
    }
    return obj;
  } catch (e) {
    // Corrupt JSON or IO error — quarantine + log once (best-effort).
    try {
      const q = p + ".corrupt." + Date.now();
      fs.renameSync(p, q);
    } catch {}
    try { console.warn(JSON.stringify({ ts: new Date().toISOString(), level: "warn", event: "TOKEN_CACHE_CORRUPT", err: String(e).slice(0, 200) })); } catch {}
    return null;
  }
}

function writeCache(appKey, payload) {
  ensureDir();
  const p = tokenPath(appKey);
  const tmp = p + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, p);  // 원자적 교체
}

/**
 * O_EXCL 기반 파일락 (cross-process)
 * v7.2 round-2: lock file now carries `<pid>:<randomUuid>` so release uses a
 * compare-and-swap — if the stored identity doesn't match, we refuse to
 * unlink another process's lock. Plus randomized jitter on retries to avoid
 * thundering-herd on the same cache dir.
 */
async function acquireLock(appKey, maxWaitMs = FILE_LOCK_TIMEOUT_MS) {
  ensureDir();
  const lp = lockPath(appKey);
  const lockId = `${process.pid}:${crypto.randomUUID()}`;
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    try {
      // O_EXCL: 파일이 이미 있으면 실패
      const fd = fs.openSync(lp, "wx", 0o600);
      fs.writeSync(fd, lockId);
      fs.closeSync(fd);
      return () => {
        try {
          // CAS release: only unlink if the lock still belongs to us.
          const cur = fs.readFileSync(lp, "utf-8");
          if (cur === lockId) fs.unlinkSync(lp);
        } catch (_) {}
      };
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
      // stale lock 정리
      try {
        const st = fs.statSync(lp);
        if (Date.now() - st.mtimeMs > FILE_LOCK_TIMEOUT_MS) {
          fs.unlinkSync(lp);
          continue;
        }
      } catch (_) {}
      const jitter = Math.floor(Math.random() * LOCK_RETRY_INTERVAL_MS);
      await new Promise(r => setTimeout(r, LOCK_RETRY_INTERVAL_MS + jitter));
    }
  }
  return null;
}

export class TokenManager {
  constructor({ env, logger, fetchImpl = fetch }) {
    this.env = env;
    this.logger = logger || console;
    this.fetch = fetchImpl;
    this._inflight = null;
  }

  async getAccessToken() {
    const cached = readCache(this.env.appKey);
    const now = Date.now();
    if (cached && cached.access_token && cached.expires_at_ms > now + 60_000) {
      return cached.access_token;
    }
    if (this._inflight) return this._inflight;
    this._inflight = this._requestToken(cached).finally(() => { this._inflight = null; });
    return this._inflight;
  }

  async _requestToken(cached, { force = false } = {}) {
    // 1) 프로세스간 락 획득
    const release = await acquireLock(this.env.appKey);
    if (!release) {
      this.logger.warn && this.logger.warn("KIS_TOKEN_LOCK_TIMEOUT_FALLBACK");
      // 락 실패 시 캐시 재확인 후 없으면 예외
      const c2 = readCache(this.env.appKey);
      if (c2 && c2.access_token && c2.expires_at_ms > Date.now()) return c2.access_token;
      throw new Error("KIS token lock timeout");
    }
    try {
      // 2) 락 획득 후 재조회(double-check) — 다른 프로세스가 방금 발급했을 수 있음
      const fresh = readCache(this.env.appKey);
      if (fresh && fresh.access_token && fresh.expires_at_ms > Date.now() + 60_000) {
        this.logger.info && this.logger.info("KIS_TOKEN_REUSE_AFTER_LOCK");
        return fresh.access_token;
      }
      // 3) 1분 쿨다운 (force 아닐 때만)
      if (!force && fresh && fresh.issued_at_ms && (Date.now() - fresh.issued_at_ms) < MIN_REISSUE_GAP_MS) {
        if (fresh.access_token && fresh.expires_at_ms > Date.now()) {
          return fresh.access_token;
        }
        const wait = MIN_REISSUE_GAP_MS - (Date.now() - fresh.issued_at_ms);
        this.logger.warn && this.logger.warn("KIS_TOKEN_COOLDOWN_WAIT", { wait_ms: wait });
        await new Promise(r => setTimeout(r, wait));
      }

      const url = `${this.env.baseUrl}/oauth2/tokenP`;
      const body = {
        grant_type: "client_credentials",
        appkey: this.env.appKey,
        appsecret: this.env.appSecret,
      };
      const res = await this.fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Token refresh failed ${res.status}: ${text.slice(0, 200)}`);
      }
      const data = await res.json();
      const issued = Date.now();
      const expires = issued + ((data.expires_in || 86400) * 1000);
      const merged = {
        ...(fresh || {}),
        access_token: data.access_token,
        token_type: data.token_type,
        issued_at_ms: issued,
        expires_at_ms: expires,
      };
      writeCache(this.env.appKey, merged);
      this.logger.info && this.logger.info("KIS_TOKEN_ISSUED", { expires_in: data.expires_in, pid: process.pid });
      return data.access_token;
    } finally {
      release();
    }
  }

  /** Hashkey — POST body 서명 */
  async getHashkey(body) {
    const token = await this.getAccessToken();
    const url = `${this.env.baseUrl}/uapi/hashkey`;
    const res = await this.fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json; charset=utf-8",
        "appkey": this.env.appKey,
        "appsecret": this.env.appSecret,
        "authorization": `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`Hashkey failed ${res.status}: ${text.slice(0, 200)}`);
    }
    const data = await res.json();
    if (!data.HASH) throw new Error(`Hashkey response missing HASH: ${JSON.stringify(data).slice(0, 200)}`);
    return data.HASH;
  }

  /** WebSocket approval_key (12h 유효) */
  async getApprovalKey() {
    const cached = readCache(this.env.appKey);
    if (cached && cached.approval_key && cached.approval_issued_ms > Date.now() - 12 * 3600 * 1000) {
      return cached.approval_key;
    }
    const release = await acquireLock(this.env.appKey);
    try {
      const fresh = readCache(this.env.appKey);
      if (fresh && fresh.approval_key && fresh.approval_issued_ms > Date.now() - 12 * 3600 * 1000) {
        return fresh.approval_key;
      }
      const url = `${this.env.baseUrl}/oauth2/Approval`;
      const body = {
        grant_type: "client_credentials",
        appkey: this.env.appKey,
        secretkey: this.env.appSecret,
      };
      const res = await this.fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Approval key failed ${res.status}: ${text.slice(0, 200)}`);
      }
      const data = await res.json();
      const merged = { ...(fresh || {}), approval_key: data.approval_key, approval_issued_ms: Date.now() };
      writeCache(this.env.appKey, merged);
      this.logger.info && this.logger.info("KIS_WS_APPROVAL_ISSUED");
      return data.approval_key;
    } finally {
      if (release) release();
    }
  }
}
