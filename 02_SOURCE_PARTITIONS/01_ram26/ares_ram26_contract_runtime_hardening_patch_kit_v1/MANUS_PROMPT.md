작업명:
ARES RAM26 Contract Runtime Hardening — 30D Forensic 후속 프로덕션 패치

현재 근거:
- RAM26 active_strategy_id, features, engine gate, policy champion, G8, Full Live는 대체로 PASS.
- 그러나 ssot:target:v2:current top-level은 engine_version=v7.0_LOCKED_20260130이고 targets가 {gross,meta,net,positions} envelope라 projector가 n=2/gross=1.5로 오판.
- champion:targets:ssot는 ticker + _meta_marker 정상이나 champion:target legacy hash에는 _meta_marker가 없음.
- state:risk_rate/state:risk_rate:ts가 TYPE=none.
- kis:ws:status/current heartbeat last fill status keys가 TYPE=none.
- PM2 logs에는 WRONGPASS/ECONNREFUSED 흔적이 남아 있어 canonical env drift 재발 위험이 있음.

목표:
1. projector v3를 설치해 SSOT_TARGET_V2 envelope의 targets.positions[]를 정상 ticker map으로 project한다.
2. _meta_marker와 phase9_b1_marker를 champion:targets:ssot와 champion:target 양쪽에 전파한다.
3. LIVE 상태에서도 source가 valid하면 projector가 publish하도록 mode_live_refuse_projection을 제거한다.
4. risk_rate sidecar를 설치해 state:risk_rate와 state:risk_rate:ts telemetry를 복구한다.
5. KIS WS status synthesizer를 설치해 kis:ws:status:current, heartbeat, fills last timestamp를 publish한다.
6. PM2 Redis env canonical audit를 수행하고, mismatch 앱은 별도 승인 후 targeted reload한다.
7. hardening gate를 통과할 때까지 결과를 검증한다.

금지:
- ssot:target:v2:current write 금지
- policy:champion:active write 금지
- mode/trading key 변경 금지
- PM2 global restart 금지
- nextgen2-live restart 금지
- position-sync-service restart 금지
- order-intent-executor restart 금지
- production fill stream write 금지
- BUY/SELL test 주문 금지

실행:
tar -xzf /path/to/ares_ram26_contract_runtime_hardening_patch_kit_v1.tar.gz -C /home/ubuntu/ares_scripts/
cd /home/ubuntu/ares_scripts/ares_ram26_contract_runtime_hardening_patch_kit_v1

ARES_HARDEN_ALL_CONFIRM=YES_APPLY_ARES_CONTRACT_RUNTIME_HARDENING \
bash ./07_apply_all_hardening.sh

성공 기준:
- projector_pass=true
- champion:targets:ssot HLEN >= 9, numeric keys 없음, _meta_marker 있음
- champion:target HLEN >= 9, _meta_marker 있음
- state:risk_rate:ts fresh < 120s
- kis:ws:status:current present
- active_strategy_id=RAM26_ALPHA_0001_b215c119ea23
- features PUBLISHED
- engine gate pass=true
- policy champion RAM26
- G8 READY
