작업명:
RAM26 Native Bridge v2 작성 + ALL_GATES_GO + Full Live GO

결정:
옵션 A로 Step 1~3에서 멈추지 않는다.
옵션 B를 제한된 방식으로 진행한다.
즉 bridge_v2와 engine native wrapper를 새로 작성/설치하되, ALL_GATES_GO 전까지 LIVE는 열지 않는다.

근거:
1. engine v1은 실제로 --once, --input-json, --output-json, --features-key, --output-key, --ssot-staged-key, --feature-max-age-sec, --publish-redis, --publish-ssot-staged를 지원한다.
2. engine v1은 --config, --strategy-id, --stdout-json을 지원하지 않는다.
3. ram26_final_to_champion_bridge_v2.mjs는 서버에 없다.
4. completion_check WRONGTYPE은 STRING JSON 키를 HGET 방식으로 읽어서 발생한다.
5. champion:targets:ssot HLEN=46, HMAC signed override는 이미 PASS다.

목표:
1. ram26_final_to_champion_bridge_v2.mjs를 새로 설치한다.
2. bridge_v2는 engine v1을 native args로 호출한다.
3. --feature-max-age-sec 172800을 전달해 daily/EOD source를 정상 인정한다.
4. ram26:engine:gate:last, ram26:alpha:targets:v1:latest, ram26:ssot:target:v2:staged를 생성한다.
5. staged target을 current/policy/champion hashes로 승격한다.
6. G8 guard가 LIVE_FINAL_TO_CHAMPION alias를 수용하도록 패치한다.
7. STRING JSON/HASH를 구분하는 robust all gates verifier로 ALL_GATES_GO를 확인한다.
8. Full Live GO를 실행한다.
9. 30분 모니터링한다.

금지:
- gate 실패 상태에서 LIVE 금지
- flat target promote 금지
- kill-switch active 상태에서 LIVE 금지
- PM2 global restart 금지
- nextgen2-live restart 금지
- position-sync-service restart 금지
- production fill stream 임의 write 금지
- BUY/SELL test 주문 금지

실행:
tar -xzf /path/to/ram26_native_bridge_v2_all_gates_full_live_kit_v1.tar.gz -C /home/ubuntu/ares_scripts/
cd /home/ubuntu/ares_scripts/ram26_native_bridge_v2_all_gates_full_live_kit_v1

RAM26_NATIVE_ALLIN_CONFIRM=YES_NATIVE_BRIDGE_FIX_ALL_GATES_AND_FULL_LIVE \
DURATION_SEC=1800 \
bash ./08_all_in_native_bridge_full_live.sh

성공 기준:
- RAM26_NATIVE_BRIDGE_V2_INSTALLED
- RAM26_BRIDGE_V2_STAGE_PASS
- RAM26_BRIDGE_V2_CURRENT_PROMOTED
- ALL_GATES_GO
- RAM26_NATIVE_BRIDGE_ALL_GATES_FULL_LIVE_SUCCESS
- RAM26_NATIVE_POST_LIVE_MONITOR_CLEAR
