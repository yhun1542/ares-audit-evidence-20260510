# ares_execution_module.py - RedisExecutionBus
class RedisExecutionBus:
    def __init__(self, store: RedisStore, cfg: LiveAutopilotConfig):
        self.store = store
        self.cfg = cfg

    def submit(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        exec_id = intent["exec_id"]
        idem_key = f"{LiveKeys.EXECUTION_IDEMP}:{exec_id}"

        if str(os.getenv("AOA_DISABLE_ARES_DIRECT_EMIT", "false")).lower() in {"1", "true", "yes", "on"}:
            self.store.set_json("ares:aoa:ares_v56_shadow_submit", {
                "ts": time.time(),
                "exec_id": exec_id,
                "intent_id": str(intent.get("intent_id", exec_id)),
                "status": "shadow_only",
                "reason": "AOA_ARES_DIRECT_EMIT_DISABLED",
            }, ex=3600)
            return {
                "status": "acked",
                "exec_id": exec_id,
                "shadow_only": True,
                "reason": "AOA_ARES_DIRECT_EMIT_DISABLED",
            }
        # Canonicalize intent contract for executor compatibility.
        intent.setdefault("run_id", self.cfg.execution.run_id)
        intent.setdefault("intent_id", exec_id)
        intent.setdefault("schema", "ORDER_INTENT")
        target_stream = LiveKeys.CANONICAL_INTENT_STREAM if self.cfg.execution.use_canonical_stream else LiveKeys.EXECUTION_STREAM
        payload = json.dumps(intent, default=str)
        fields = {
            "json": payload,
            "payload": payload,
            "exec_id": exec_id,
            "intent_id": str(intent.get("intent_id", exec_id)),
            "run_id": str(intent.get("run_id", self.cfg.execution.run_id)),
            "schema": str(intent.get("schema", "ORDER_INTENT")),
            "published_at": utcnow().isoformat(),
        }
        # [LUA-ATOMIC] XADD + idempotency SET in single Lua call
        stream_id = self._atomic.emit_order_intent(
            stream=target_stream,
            fields=fields,
            idem_key=idem_key,
            idem_val="SENT:ares-v56",
            idem_ttl=86400,
        )
        if stream_id is None:
            return {"status": "duplicate", "exec_id": exec_id}
        self.store.set_json(LiveKeys.EXECUTION_LAST, {
            "ts": time.time(),
            "exec_id": exec_id,
            "intent_id": str(intent.get("intent_id", exec_id)),
            "stream": target_stream,
            "stream_id": stream_id.decode() if isinstance(stream_id, bytes) else stream_id,
        })
        if self.cfg.execution.ack_timeout_sec <= 0:
            return {"status": "submitted", "exec_id": exec_id, "stream_id": stream_id}

        deadline = time.time() + self.cfg.execution.ack_timeout_sec
        ack_key = f"{LiveKeys.EXECUTION_ACK}:{exec_id}"
        while time.time() < deadline:
            ack = self.store.get_json(ack_key)
            if ack:
                return {"status": "acked", "exec_id": exec_id, "ack": ack, "stream_id": stream_id}
            time.sleep(0.25)
