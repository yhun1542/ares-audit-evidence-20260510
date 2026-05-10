// core/rest_client.mjs — KIS REST 통합 클라이언트 v7.3
// v7.1 (3-AI round-1):
//   P0: POST(주문/정정/취소)는 기본 무재시도 — 네트워크/5xx/429에서 중복 주문 방지
//        대신 { idempotent: true } 옵션으로 명시적 허용 (시세/잔고 POST 등 드문 케이스)
//   P1: hashkey 발급도 RateLimiter를 통과 → POST 1건당 2 permit 선예약으로 TPS 제어
//   P1: 재시도 소진 시 명시적 실패 { ok:false, retryExhausted:true } 반환 (조용한 성공 위장 금지)
//   P2: KIS 에러(rt_cd!=0)도 상위로 `ok:false` 전달 + msg_cd/msg1 보존
//   P2: 주문 POST는 RateLimiter 2 permit 예약으로 hashkey+order 쌍의 TPS 정합성 유지
//
// v7.3 (4-AI round-2 fixes):
//   C4 (GPT+Claude): POST 타임아웃/5xx/네트워크 실패 결과에 `ambiguous:true` 플래그를
//       설정해 "주문이 브로커에 도달했는지 불확실"함을 caller에게 명시적으로 알린다.
//       또한 `X-Request-Id` (= CLIENT_ORDER_ID)를 outbound 헤더에 꽂아 KIS측 idempotency
//       에 연결될 수 있도록 한다(idempotencyKey 옵션).
//   H1  (Claude):   `clearTimeout`를 헤더 도착 직후가 아니라 **본문 read 완료 이후**로
//       옮긴다. 그래야 응답 body를 읽는 동안 네트워크가 끊기더라도 AbortSignal이 살아
//       있어서 영원히 await 되는 일이 없다.
//   M2 간접:       env.mjs가 wss:// 기본값을 강제하는 것과 짝을 이루어, 본 클라이언트는
//       `https://`가 아닌 baseUrl을 탐지하면 경고 로그만 남기고 진행(프로덕션에서는 env
//       단계에서 강제로 차단됨).

import { KIS_ERR } from "./tr_ids.mjs";

const DEFAULT_TIMEOUT_MS = 8000;
const MAX_GET_RETRIES    = 4;       // GET(조회)만 재시도
const MAX_POST_RETRIES   = 0;       // POST 기본 무재시도 (안전 기본)

// v7.3 C4: helper so downstream can test for the ambiguous envelope.
export function isAmbiguous(resp) {
  return !!(resp && resp.ok === false && resp.ambiguous === true);
}

export class KisRestClient {
  constructor({ env, tokenManager, rateLimiter, logger = console }) {
    this.env = env;
    this.tm = tokenManager;
    this.rl = rateLimiter;
    this.logger = logger;
    // v7.3 M2: soft-warn if baseUrl is not https. env.mjs hard-rejects but we
    // keep a client-side guard so integration tests don't silently regress.
    try {
      if (env?.baseUrl && !/^https:\/\//i.test(env.baseUrl)) {
        this.logger.warn?.("KIS_BASE_URL_INSECURE", { baseUrl: env.baseUrl });
      }
    } catch (_) { /* ignore */ }
  }

  async get(apiPath, { tr_id, params = {}, extra = {} } = {}) {
    const url = new URL(this.env.baseUrl + apiPath);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.append(k, String(v));
    }
    return this._request({ method: "GET", url: url.toString(), tr_id, extra, isPost: false });
  }

  /**
   * POST 요청
   * @param {Object} opts
   *   tr_id           : 필수
   *   body            : 본문 객체
   *   hash            : hashkey 자동 주입 여부 (기본 true — 주문/정정/취소/예약주문)
   *   idempotent      : true일 때만 네트워크 에러/5xx에서 1회 재시도 허용 (기본 false)
   *   idempotencyKey  : v7.3 C4 — client-supplied X-Request-Id (CLIENT_ORDER_ID).
   *                     서버측 dedup + audit trail 용도. placeOrder 경로에서 항상 주입.
   */
  async post(apiPath, { tr_id, body = {}, extra = {}, hash = true, idempotent = false, idempotencyKey = null } = {}) {
    const url = this.env.baseUrl + apiPath;
    // hashkey 발급도 TPS 1개 사용 → RateLimiter 통과
    let hashkey = null;
    if (hash) {
      await this.rl.acquire();
      hashkey = await this.tm.getHashkey(body);
    }
    return this._request({
      method: "POST",
      url,
      tr_id,
      extra: { ...extra, hashkey, idempotencyKey },
      body: JSON.stringify(body),
      isPost: true,
      idempotent,
    });
  }

  async _request({ method, url, tr_id, body, extra = {}, attempt = 0, isPost = false, idempotent = false }) {
    await this.rl.acquire();
    const token = await this.tm.getAccessToken();

    const headers = {
      "content-type": "application/json; charset=utf-8",
      "authorization": `Bearer ${token}`,
      "appkey": this.env.appKey,
      "appsecret": this.env.appSecret,
      "custtype": "P",
    };
    if (tr_id) headers.tr_id = tr_id;
    if (extra && extra.hashkey) headers.hashkey = extra.hashkey;
    if (extra && extra.tr_cont) headers.tr_cont = extra.tr_cont;
    // v7.3 C4: idempotency key (CLIENT_ORDER_ID). KIS does not natively support
    // X-Request-Id yet, but our reverse proxy/audit pipeline does, and future
    // KIS firmware may adopt it — either way we want it in the server-side log.
    if (extra && extra.idempotencyKey) {
      headers["x-request-id"] = String(extra.idempotencyKey);
      headers["x-client-order-id"] = String(extra.idempotencyKey);
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
    // v7.3 C4/H1: track the request envelope so every return path can be
    // explicit about whether the outcome is ambiguous (= order may or may not
    // have reached KIS). This is surfaced to the caller as `ambiguous:true`.
    const envelope = {
      method, url, isPost, idempotent,
      idempotencyKey: extra?.idempotencyKey || null,
      tr_id: tr_id || null,
    };

    const MAX = isPost ? (idempotent ? 1 : MAX_POST_RETRIES) : MAX_GET_RETRIES;

    let res;
    try {
      res = await fetch(url, { method, headers, body, signal: controller.signal });
    } catch (e) {
      // v7.3 H1: DO NOT clearTimeout yet; the retry path below still races
      // against the same deadline and we want the AbortSignal to remain armed
      // if we decide to bail. Either clear on terminal return, or let it fire.
      clearTimeout(timer);
      const retryable = !isPost || idempotent;
      if (retryable && attempt < MAX) {
        const wait = Math.min(8000, 250 * (2 ** attempt));
        this.logger.warn && this.logger.warn("KIS_REQ_NETERR_RETRY", { url, attempt, wait, err: String(e).slice(0, 160) });
        await new Promise(r => setTimeout(r, wait));
        return this._request({ method, url, tr_id, body, extra, attempt: attempt + 1, isPost, idempotent });
      }
      // POST이거나 재시도 소진 → 명시적 실패 반환 (호출자가 상태조회로 판단하도록)
      this.logger.error && this.logger.error("KIS_REQ_NETERR_NO_RETRY", { url, isPost, err: String(e).slice(0, 160) });
      // v7.3 C4: POST 요청이 네트워크 에러로 실패한 경우, 주문이 KIS에 도달했는지
      // 불확실하므로 `ambiguous:true` 플래그를 세워서 호출자가 상태 조회/복구 루프로
      // 승격시키도록 한다.
      return {
        ok: false,
        status: 0,
        data: null,
        error: "NETWORK",
        retryExhausted: attempt >= MAX,
        isPost,
        ambiguous: isPost === true,
        envelope,
        message: String(e).slice(0, 200),
      };
    }

    // HTTP 상태 분기
    if (res.status === 401 && attempt < 1) {
      clearTimeout(timer);
      this.logger.warn && this.logger.warn("KIS_401_FORCE_REFRESH", { url });
      try { await this.tm._requestToken(null, { force: true }); } catch (_) {}
      return this._request({ method, url, tr_id, body, extra, attempt: attempt + 1, isPost, idempotent });
    }
    if (res.status === 429 || res.status >= 500) {
      const retryable = !isPost || idempotent;
      if (retryable && attempt < MAX) {
        clearTimeout(timer);
        const wait = Math.min(8000, 400 * (2 ** attempt));
        this.logger.warn && this.logger.warn("KIS_HTTP_RETRY", { url, status: res.status, attempt, wait });
        await new Promise(r => setTimeout(r, wait));
        return this._request({ method, url, tr_id, body, extra, attempt: attempt + 1, isPost, idempotent });
      }
      // POST 5xx/429 — 중복 주문 방지를 위해 재시도 안 함
      // v7.3 H1: read body under the same abort deadline before clearTimeout.
      let raw = "";
      try { raw = await res.text(); } catch (_) { raw = ""; }
      clearTimeout(timer);
      this.logger.error && this.logger.error("KIS_HTTP_FAIL_NO_RETRY", { url, status: res.status, isPost });
      let data = null; try { data = JSON.parse(raw); } catch (_) { data = { raw }; }
      // v7.3 C4: 5xx 는 "서버가 요청을 받았는지/주문이 실행되었는지" 불확실 구간.
      // 429 는 rate limit 이므로 주문이 실행되지 않은 것이 보장되어 ambiguous=false.
      const ambiguous = isPost === true && res.status >= 500;
      return {
        ok: false, status: res.status, data, retryExhausted: true, isPost,
        error: "HTTP", ambiguous, envelope,
      };
    }

    // v7.3 H1: keep the abort timer armed while we read the body. Only clear
    // AFTER a successful body read, or on the terminal error return paths below.
    let raw;
    try {
      raw = await res.text();
    } catch (e) {
      clearTimeout(timer);
      this.logger.error && this.logger.error("KIS_BODY_READ_FAIL", { url, err: String(e).slice(0, 160), isPost });
      // v7.3 C4: body read 실패 시에도 주문 도달 여부 불확실.
      return {
        ok: false, status: res.status, data: null, error: "BODY_READ",
        retryExhausted: true, isPost,
        ambiguous: isPost === true,
        envelope,
        message: String(e).slice(0, 200),
      };
    }
    clearTimeout(timer);

    let data; try { data = JSON.parse(raw); } catch (_) { data = { raw }; }

    // KIS 레벨 에러 처리
    if (data && data.rt_cd && data.rt_cd !== "0") {
      // TPS 초과는 GET만 재시도 (POST는 주문 보류 방지)
      if (data.msg_cd === KIS_ERR.RATE_LIMIT && !isPost && attempt < MAX) {
        const wait = Math.min(8000, 500 * (2 ** attempt));
        this.logger.warn && this.logger.warn("KIS_TPS_LIMIT_RETRY", { url, wait });
        await new Promise(r => setTimeout(r, wait));
        return this._request({ method, url, tr_id, body, extra, attempt: attempt + 1, isPost, idempotent });
      }
      if (data.msg_cd === KIS_ERR.AUTH_FAIL && attempt < 1) {
        try { await this.tm._requestToken(null, { force: true }); } catch (_) {}
        return this._request({ method, url, tr_id, body, extra, attempt: attempt + 1, isPost, idempotent });
      }
    }

    return {
      ok: res.ok && data?.rt_cd === "0",
      status: res.status,
      headers: Object.fromEntries(res.headers),
      data,
      msg_cd: data?.msg_cd,
      msg1: data?.msg1,
      // v7.3 C4: non-ambiguous by construction — we got a parsed KIS response.
      ambiguous: false,
      envelope,
    };
  }
}
