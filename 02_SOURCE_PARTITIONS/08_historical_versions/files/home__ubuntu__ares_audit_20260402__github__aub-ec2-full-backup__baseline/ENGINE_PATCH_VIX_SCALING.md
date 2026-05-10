# ENGINE_PATCH_VIX_SCALING.md
v661_addon_engine_v4_full.py 에 `VIXScalingEngine`를 **OFF=baseline 동일** 규율로 통합하는 패치 가이드

## 목표
- enable_vix_scaling=0 (기본): baseline과 완전 동일(가능한 한 결과 JSON 구조도 동일)
- enable_vix_scaling=1: 리밸런스 시 목표 weights에 scale factor 적용 + diagnostics.addon 기록

---

## 1) 파일 추가
`/home/ubuntu/AUB/baseline/vix_scaling_engine.py`

---

## 2) 엔진 CLI 플래그 추가 (argparse)
추가:
- `--enable_vix_scaling` (int, default=0)
- `--vix_scaling_json` (str, default="")  # policy override 없이 단독 테스트용

---

## 3) __init__에서 lazy init (OFF일 때 import 금지)
```python
self.enable_vix_scaling = bool(args.enable_vix_scaling)
self.vix_scaler = None
if self.enable_vix_scaling:
    from vix_scaling_engine import VIXScalingEngine, create_vix_scaling_config_from_dict
    vix_cfg = create_vix_scaling_config_from_dict(policy.get("vix_scaling", {}))
    if args.vix_scaling_json:
        vix_cfg = create_vix_scaling_config_from_dict(json.loads(args.vix_scaling_json))
    self.vix_scaler = VIXScalingEngine(vix_cfg)
```

---

## 4) 리밸런스에 적용 (pass-through 금지)
목표 weights를 만든 직후, no_trade_band 적용 전에:

```python
if self.vix_scaler is not None:
    vix_scale = self.vix_scaler.get_scale_factor(float(vix_today), date=dt)
    gross_before = float(np.sum(target_weights))
    target_weights = target_weights * vix_scale
    gross_after = float(np.sum(target_weights))

    # 엔진이 레버리지 미지원이면 상한 1.0 보호 권장
    if gross_after > 1.0:
        target_weights = target_weights / gross_after
        gross_after = 1.0

    self.vix_scaler.log_rebalance(dt, float(vix_today), float(vix_scale), gross_before, gross_after)
```

---

## 5) fold diagnostics 기록 (ON일 때만)
```python
if self.vix_scaler is not None:
    fold_diag.setdefault("addon", {})
    fold_diag["addon"]["vix_scaling"] = self.vix_scaler.get_diagnostics()
```
OFF일 때는 이 키를 넣지 마세요(해시/시그니처 변동 위험).

---

## 6) OFF=baseline 동일성 테스트
- enable_vix_scaling=0 run: result_hash/scenario_sig/metrics baseline과 동일해야 PASS

## 7) ON 테스트
- enable_vix_scaling=1 run에서 diagnostics.addon.vix_scaling.enabled=True 및 rebalances>0 확인
