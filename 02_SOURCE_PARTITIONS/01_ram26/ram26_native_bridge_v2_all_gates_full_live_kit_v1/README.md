# RAM26 Native Bridge v2 ALL_GATES_GO + Full Live Kit v1

## 목적

Pre-flight에서 확인된 실제 문제를 직접 해결합니다.

- Engine v1은 `--config / --strategy-id / --stdout-json`를 지원하지 않음
- `ram26_final_to_champion_bridge_v2.mjs`가 부재
- 기존 completion check가 STRING JSON을 HGET하여 WRONGTYPE 발생
- `ares:bridge:active` alias 충돌

## 실행 순서

```bash
tar -xzf ram26_native_bridge_v2_all_gates_full_live_kit_v1.tar.gz -C /home/ubuntu/ares_scripts/
cd /home/ubuntu/ares_scripts/ram26_native_bridge_v2_all_gates_full_live_kit_v1

RAM26_NATIVE_ALLIN_CONFIRM=YES_NATIVE_BRIDGE_FIX_ALL_GATES_AND_FULL_LIVE \
DURATION_SEC=1800 \
bash ./08_all_in_native_bridge_full_live.sh
```

## 개별 실행

```bash
RAM26_BRIDGE_V2_INSTALL_CONFIRM=YES_INSTALL_RAM26_NATIVE_BRIDGE_V2 bash ./01_install_ram26_native_bridge_v2.sh
RAM26_BRIDGE_V2_STAGE_CONFIRM=YES_RUN_RAM26_BRIDGE_V2_STAGE bash ./02_run_bridge_v2_stage.sh
RAM26_PATCH_G8_CONFIRM=YES_PATCH_G8_BRIDGE_ALIAS bash ./03_patch_g8_bridge_alias.sh
RAM26_BRIDGE_V2_PROMOTE_CONFIRM=YES_PROMOTE_RAM26_BRIDGE_V2_CURRENT bash ./04_promote_bridge_v2_current.sh
RAM26_ALL_GATES_NATIVE_VERIFY_CONFIRM=YES_VERIFY_NATIVE_BRIDGE_ALL_GATES bash ./05_verify_native_all_gates.sh
RAM26_NATIVE_FULL_LIVE_CONFIRM=YES_NATIVE_BRIDGE_ALL_GATES_FULL_LIVE bash ./06_native_full_live_go.sh
RAM26_NATIVE_MONITOR_CONFIRM=YES_MONITOR_NATIVE_BRIDGE_FULL_LIVE DURATION_SEC=1800 bash ./07_native_post_live_monitor.sh
```

## Rollback

```bash
RAM26_NATIVE_ROLLBACK_CONFIRM=YES_ROLLBACK_NATIVE_BRIDGE_FULL_LIVE \
bash ./99_rollback_native_bridge_full_live.sh
```
