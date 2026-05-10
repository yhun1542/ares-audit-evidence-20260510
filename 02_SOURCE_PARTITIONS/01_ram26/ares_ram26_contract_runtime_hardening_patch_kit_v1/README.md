# ARES RAM26 Contract Runtime Hardening Patch Kit v1

## 목적

30D Forensic 결과의 잔여 P0/P1 결함을 운영 중단 없이 수리합니다.

핵심 결함:
1. `ssot:target:v2:current.targets = {gross, meta, net, positions}` envelope를 projector가 잘못 해석
2. `champion:target` legacy hash에 `_meta_marker` 누락
3. `state:risk_rate`, `state:risk_rate:ts` writer 부재
4. `kis:ws:status:current`, `kis:ws:heartbeat`, `kis:fills:last_ts` 상태 키 부재
5. PM2 Redis env drift 재발 위험

## 실행

```bash
tar -xzf ares_ram26_contract_runtime_hardening_patch_kit_v1.tar.gz -C /home/ubuntu/ares_scripts/
cd /home/ubuntu/ares_scripts/ares_ram26_contract_runtime_hardening_patch_kit_v1

ARES_HARDEN_ALL_CONFIRM=YES_APPLY_ARES_CONTRACT_RUNTIME_HARDENING \
bash ./07_apply_all_hardening.sh
```

## 개별 실행

```bash
ARES_HARDEN_AUDIT_CONFIRM=YES_ARES_HARDEN_READONLY_AUDIT bash ./01_audit_contracts_readonly.sh
RAM26_PROJECTOR_V3_CONFIRM=YES_INSTALL_RAM26_PROJECTOR_V3 bash ./02_install_projector_v3.sh
RAM26_RISK_RATE_CONFIRM=YES_INSTALL_RAM26_RISK_RATE_PUBLISHER bash ./03_install_risk_rate_publisher.sh
KIS_WS_STATUS_SYNTH_CONFIRM=YES_INSTALL_KIS_WS_STATUS_SYNTH bash ./04_install_kis_ws_status_synthesizer.sh
ARES_REDIS_ENV_AUDIT_CONFIRM=YES_AUDIT_PM2_REDIS_ENV bash ./05_pm2_redis_env_audit_fix.sh
bash ./06_verify_hardening_gate.sh
```

## 안전

기본 시퀀스는 다음을 하지 않습니다.

- `ssot:target:v2:current` write
- `policy:champion:active` write
- `trading:enabled=true`
- `emarkos:v1:mode=LIVE`
- production fill stream write
- PM2 global restart
- nextgen2-live / position-sync-service / order-intent-executor restart

## Rollback

```bash
ARES_HARDEN_ROLLBACK_CONFIRM=YES_ROLLBACK_ARES_HARDENING_PROCESSES \
bash ./99_rollback_sidecars.sh
```
