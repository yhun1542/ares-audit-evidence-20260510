// ws/ws_stream.mjs — KIS WebSocket 실시간 스트림 (주식/옵션/체결통보 통합)
// 제공 스트림:
//   STOCK_ASKING(HDFSASP0), STOCK_CCNL(HDFSCNT0)
//   OPT_ASKING(HDFFF010),   OPT_CCNL(HDFFF020)
//   STOCK_FILL_NOTICE(H0GSCNI0),  OPT_FILL_NOTICE(HDFFF2C0),  OPT_ORDER_NOTICE(HDFFF1C0)
// - 메시지 형식: "0|<tr_id>|<data_cnt>|<payload>" 또는 "1|..."(암호화, 체결통보)
// - 체결통보는 SUBSCRIBE 응답의 output.key / output.iv로 AES256-CBC 복호화

import WebSocket from "ws";
import crypto from "node:crypto";
import { WS_TR, WS_FIELD_COUNT, MODE } from "../core/tr_ids.mjs";
import { EventEmitter } from "node:events";

export class KisWsStream extends EventEmitter {
  constructor({ env, tokenManager, logger = console }) {
    super();
    this.env = env;
    this.tm = tokenManager;
    this.logger = logger;
    this.mode = env.isPaper ? MODE.PAPER : MODE.REAL;
    this.ws = null;
    this.approvalKey = null;
    this.subscriptions = new Map();     // key: `${tr_id}|${tr_key}` -> {tr_id, tr_key, custtype}
    this.pendingCrypto = new Map();     // tr_id -> { key, iv }
    this.reconnectDelayMs = 1000;
    this.maxReconnectDelayMs = 60_000;
    this.pingInterval = null;
    this._shouldRun = false;
    // v7.2 round-3 (Claude): if AES decrypt keeps failing, KIS likely rotated
    // the approval key or encoding. Track failures and force a full reconnect
    // (which re-SUBSCRIBE-s and re-derives key/iv from KIS) when exceeded.
    this._encFailCount = 0;
    this._encFailThresholdForceReconnect = Number(
      process.env.ARES_WS_ENC_FAIL_FORCE_RECONNECT || 10);
    this._encFailLastForceMs = 0;
    // Exported counters for host-level metrics scraping.
    this.metrics = { enc_fail_total: 0, force_reconnects: 0, reconnects: 0 };
  }

  async start() {
    this._shouldRun = true;
    this.approvalKey = await this.tm.getApprovalKey();
    await this._connect();
  }

  async stop() {
    this._shouldRun = false;
    if (this.pingInterval) { clearInterval(this.pingInterval); this.pingInterval = null; }
    if (this.ws) { try { this.ws.close(); } catch (_) {} this.ws = null; }
  }

  _connect() {
    const url = this.env.wsUrl;
    this.logger.info && this.logger.info("KIS_WS_CONNECTING", { url });
    this.ws = new WebSocket(url);

    this.ws.on("open", async () => {
      this.logger.info && this.logger.info("KIS_WS_OPEN");
      this.reconnectDelayMs = 1000;
      this.emit("open");
      // 기존 구독 재주입
      for (const s of this.subscriptions.values()) this._sendSubscribe(s);
      // keep-alive ping
      if (this.pingInterval) clearInterval(this.pingInterval);
      this.pingInterval = setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          try { this.ws.ping(); } catch (_) {}
        }
      }, 30_000);
    });

    this.ws.on("message", (raw) => this._onMessage(raw));

    this.ws.on("close", (code, reason) => {
      this.logger.warn && this.logger.warn("KIS_WS_CLOSE", { code, reason: String(reason).slice(0, 120) });
      this.emit("close", { code, reason });
      if (this.pingInterval) { clearInterval(this.pingInterval); this.pingInterval = null; }
      if (this._shouldRun) {
        // v7.2 round-2: add randomized jitter (±30 %) to reconnect delay to
        // avoid thundering-herd reconnects when a KIS WS gateway hiccups.
        const base = this.reconnectDelayMs;
        const jitter = Math.floor((Math.random() - 0.5) * 0.6 * base);
        const wait = Math.max(250, base + jitter);
        setTimeout(() => this._connect(), wait);
      }
      this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, this.maxReconnectDelayMs);
    });

    this.ws.on("error", (err) => {
      this.logger.error && this.logger.error("KIS_WS_ERROR", { err: String(err).slice(0, 160) });
      this.emit("error", err);
    });
  }

  _onMessage(raw) {
    const str = raw.toString("utf-8");
    // JSON 응답 (SUBSCRIBE 결과, PINGPONG 등)
    if (str.startsWith("{")) {
      let obj;
      try { obj = JSON.parse(str); } catch (_) { return; }
      // PINGPONG 응답
      if (obj.header && obj.header.tr_id === "PINGPONG") {
        try { this.ws.send(str); } catch (_) {}
        return;
      }
      // SUBSCRIBE / UNSUBSCRIBE 응답
      const h = obj.header || {};
      const b = obj.body || {};
      if (b.rt_cd && b.rt_cd !== "0") {
        this.logger.warn && this.logger.warn("KIS_WS_SUB_ERR", { tr_id: h.tr_id, msg_cd: b.msg_cd, msg: b.msg1 });
      }
      // 체결통보 암호키(key/iv) 수신
      if (b.output && b.output.key && b.output.iv) {
        this.pendingCrypto.set(h.tr_id, { key: b.output.key, iv: b.output.iv });
        // v7.2 round-4: invalidate the decoded cache so new key/iv take effect.
        if (this._decodedCrypto) this._decodedCrypto.delete(h.tr_id);
        this.logger.info && this.logger.info("KIS_WS_CRYPTO_READY", { tr_id: h.tr_id });
      }
      this.emit("control", obj);
      return;
    }

    // 데이터 프레임: "0|tr_id|cnt|payload"  또는  "1|..."(암호화)
    const parts = str.split("|");
    if (parts.length < 4) return;
    const isEncrypted = parts[0] === "1";
    const trId = parts[1];
    const dataCnt = Number(parts[2] || 1);
    let payload = parts.slice(3).join("|");

    if (isEncrypted) {
      const keyIv = this.pendingCrypto.get(trId);
      if (!keyIv) {
        this.logger.warn && this.logger.warn("KIS_WS_ENC_NO_KEY", { tr_id: trId });
        return;
      }
      // v7.2 round-4 (Grok): cache the decoded key/iv + encoding per tr_id to
      // avoid re-decoding on every frame. pendingCrypto stores the raw strings
      // from the SUBSCRIBE response; _decodedCrypto stores resolved Buffers.
      if (!this._decodedCrypto) this._decodedCrypto = new Map();
      let decoded = this._decodedCrypto.get(trId);
      if (!decoded || decoded.rawKey !== keyIv.key || decoded.rawIv !== keyIv.iv) {
        // (re)derive and cache once per key rotation
        const pickKeyBuf = (s, want) => {
          try {
            const b64 = Buffer.from(s, "base64");
            if (b64.length === want) return { buf: b64, enc: "base64" };
          } catch {}
          const utf = Buffer.from(s, "utf8");
          return { buf: utf, enc: "utf8" };
        };
        const k = pickKeyBuf(keyIv.key, 32);
        const v = pickKeyBuf(keyIv.iv,  16);
        decoded = { rawKey: keyIv.key, rawIv: keyIv.iv, keyBuf: k.buf, ivBuf: v.buf, enc: `${k.enc}/${v.enc}` };
        this._decodedCrypto.set(trId, decoded);
        this.logger.info && this.logger.info("KIS_WS_CRYPTO_CACHED",
          { tr_id: trId, key_len: k.buf.length, iv_len: v.buf.length, enc: decoded.enc });
      }
      const keyBuf = decoded.keyBuf;
      const ivBuf  = decoded.ivBuf;
      if (keyBuf.length !== 32 || ivBuf.length !== 16) {
        this._noteEncFail("KIS_WS_ENC_KEY_LEN_BAD",
          { tr_id: trId, key_len: keyBuf.length, iv_len: ivBuf.length });
        return;
      }
      try {
        const decipher = crypto.createDecipheriv("aes-256-cbc", keyBuf, ivBuf);
        let dec = decipher.update(payload, "base64", "utf8");
        dec += decipher.final("utf8");
        payload = dec;
        // v7.2 round-3: a successful decrypt resets the failure counter so
        // transient glitches don't accumulate forever.
        this._encFailCount = 0;
      } catch (e) {
        this._noteEncFail("KIS_WS_DECRYPT_FAIL",
          { tr_id: trId, err: String(e).slice(0, 120) });
        return;
      }
    }

    // v7.1: dataCnt에 따라 payload를 필드수 단위로 정확히 분할 (다건 프레임 유실 방지)
    const rows = this._splitFrames(trId, payload, dataCnt);
    for (const row of rows) {
      const event = this._buildEvent(trId, [row]);
      if (event) {
        this.emit("tick", event);
        this.emit(trId, event);  // TR_ID별 채널 리스닝 가능
      }
    }
  }

  /**
   * v7.1: TR_ID별 필드 수(FIELD_COUNT) 기준으로 payload를 정확히 분할.
   * KIS 실시간은 레코드 내부도 '^'로 필드를 구분하므로, 총 토큰 개수 / cnt 로 필드수를 역산한다.
   * (알려진 TR은 테이블에서 직접 필드수 사용)
   */
  _splitFrames(trId, payload, cnt) {
    const toks = payload.split("^");
    if (cnt <= 1) return [toks];
    const fieldCount = WS_FIELD_COUNT[trId] || Math.floor(toks.length / cnt);
    if (fieldCount <= 0 || toks.length < fieldCount) return [toks];
    const out = [];
    for (let i = 0; i < cnt && i * fieldCount < toks.length; i++) {
      out.push(toks.slice(i * fieldCount, (i + 1) * fieldCount));
    }
    return out.length ? out : [toks];
  }

  _buildEvent(trId, rows) {
    if (!rows || !rows[0]) return null;
    const f = rows[0];
    switch (trId) {
      case WS_TR.STOCK_CCNL: {  // HDFSCNT0 : 해외주식 실시간체결(지연 포함)
        // 공식 필드 순서 (legacy/websocket/python/ws_overseas_stock_realtime.py 기준 요약)
        return {
          kind: "STOCK_CCNL",
          symbol: f[0],         // RSYM 실시간종목코드 (예: DNASAAPL)
          ymd: f[1],
          localTime: f[2],
          kstTime: f[3],
          open: Number(f[4] || 0),
          high: Number(f[5] || 0),
          low:  Number(f[6] || 0),
          last: Number(f[7] || 0),
          sign: f[8],
          diff: Number(f[9] || 0),
          rate: Number(f[10] || 0),
          bid:  Number(f[11] || 0),
          ask:  Number(f[12] || 0),
          tradeVol: Number(f[13] || 0),
          cumVol:   Number(f[14] || 0),
          cumAmount:Number(f[15] || 0),
          raw: f
        };
      }
      case WS_TR.STOCK_ASKING: {  // HDFSASP0
        return { kind: "STOCK_ASKING", symbol: f[0], raw: f };
      }
      case WS_TR.OPT_CCNL: {      // HDFFF020
        return {
          kind: "OPT_CCNL", pdno: f[0], localTime: f[1], last: Number(f[2] || 0),
          tradeVol: Number(f[3] || 0), raw: f
        };
      }
      case WS_TR.OPT_ASKING: {
        return { kind: "OPT_ASKING", pdno: f[0], raw: f };
      }
      case WS_TR.STOCK_FILL_NOTICE_REAL:
      case WS_TR.STOCK_FILL_NOTICE_PAPER: {
        return {
          kind: "STOCK_FILL",
          htsId: f[0], accountNo: f[1], orderNo: f[2], origOrderNo: f[3],
          side: f[4] === "02" ? "BUY" : "SELL",
          brokerNo: f[5], symbol: f[8], ccldQty: Number(f[9] || 0),
          ccldPrice: Number(f[10] || 0), ccldTime: f[11],
          raw: f
        };
      }
      case WS_TR.OPT_FILL_NOTICE: {
        return {
          kind: "OPT_FILL", htsId: f[0], accountNo: f[1], orderNo: f[2],
          ccldQty: Number(f[3] || 0), ccldPrice: Number(f[4] || 0),
          raw: f
        };
      }
      case WS_TR.OPT_ORDER_NOTICE: {
        return { kind: "OPT_ORDER_NOTICE", orderNo: f[0], raw: f };
      }
      default:
        return { kind: trId, raw: f };
    }
  }

  // v7.2 round-3 (Claude): centralized AES-fail handling with forced
  // reconnect when the fail count exceeds threshold, throttled so we don't
  // reconnect-storm the KIS WS gateway.
  _noteEncFail(event, extra) {
    this._encFailCount += 1;
    this.metrics.enc_fail_total += 1;
    if (this._encFailCount <= 3 || this._encFailCount % 50 === 0) {
      this.logger.warn && this.logger.warn(event,
        { ...extra, total_fail: this._encFailCount });
    }
    if (this._encFailCount >= this._encFailThresholdForceReconnect) {
      const now = Date.now();
      // at most once per 60 s
      if (now - this._encFailLastForceMs > 60_000) {
        this._encFailLastForceMs = now;
        this._encFailCount = 0;
        this.metrics.force_reconnects += 1;
        this.logger.warn && this.logger.warn("KIS_WS_FORCE_RECONNECT",
          { reason: "enc_fail_threshold_exceeded" });
        // Drop stale crypto so subscribe response will re-seed key/iv.
        try { this.pendingCrypto.clear(); } catch {}
        try { this.ws && this.ws.close(4000, "enc_fail_force_reconnect"); } catch {}
      }
    }
  }

  // ============================== 구독 관리 ==============================

  subscribe(tr_id, tr_key, { custtype = "P", notice = false } = {}) {
    const key = `${tr_id}|${tr_key}`;
    const s = { tr_id, tr_key, custtype, notice };
    this.subscriptions.set(key, s);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this._sendSubscribe(s);
  }

  unsubscribe(tr_id, tr_key) {
    const key = `${tr_id}|${tr_key}`;
    this.subscriptions.delete(key);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        header: { approval_key: this.approvalKey, custtype: "P", tr_type: "2", "content-type": "utf-8" },
        body:   { input: { tr_id, tr_key } }
      }));
    }
  }

  _sendSubscribe(s) {
    const frame = {
      header: {
        approval_key: this.approvalKey,
        custtype: s.custtype,
        tr_type: "1",
        "content-type": "utf-8",
      },
      body: { input: { tr_id: s.tr_id, tr_key: s.tr_key } }
    };
    try {
      this.ws.send(JSON.stringify(frame));
      this.logger.info && this.logger.info("KIS_WS_SUB_SENT", { tr_id: s.tr_id, tr_key: s.tr_key });
    } catch (e) {
      this.logger.warn && this.logger.warn("KIS_WS_SUB_SEND_FAIL", { err: String(e).slice(0, 120) });
    }
  }

  // ============================== 헬퍼 ==============================

  /** 미국 주식 실시간 체결(가격) 구독 */
  subscribeStockPrice(wsSymbol) { this.subscribe(WS_TR.STOCK_CCNL, wsSymbol); }
  subscribeStockAsking(wsSymbol){ this.subscribe(WS_TR.STOCK_ASKING, wsSymbol); }

  /** 미국 옵션 실시간 체결/호가 구독 */
  subscribeOptionPrice(pdno)  { this.subscribe(WS_TR.OPT_CCNL, pdno); }
  subscribeOptionAsking(pdno) { this.subscribe(WS_TR.OPT_ASKING, pdno); }

  /** 내 주식 체결통보 구독 (HTS ID 필요) */
  subscribeStockFillNotice() {
    if (!this.env.htsId) throw new Error("KIS_HTS_ID 미설정");
    const tr = this.mode === MODE.PAPER ? WS_TR.STOCK_FILL_NOTICE_PAPER : WS_TR.STOCK_FILL_NOTICE_REAL;
    this.subscribe(tr, this.env.htsId);
  }

  /** 내 옵션 체결통보 구독 */
  subscribeOptionFillNotice() {
    if (!this.env.htsId) throw new Error("KIS_HTS_ID 미설정");
    this.subscribe(WS_TR.OPT_FILL_NOTICE, this.env.htsId);
    this.subscribe(WS_TR.OPT_ORDER_NOTICE, this.env.htsId);
  }
}
