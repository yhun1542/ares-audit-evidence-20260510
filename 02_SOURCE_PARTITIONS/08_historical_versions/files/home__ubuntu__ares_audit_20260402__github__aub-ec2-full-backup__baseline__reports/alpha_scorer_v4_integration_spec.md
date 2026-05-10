# alpha_scorer_v4 통합 명세

## 개요
- **소스**: GitHub ares-x-v4-production-final/alpha.py
- **특징**: weights_from_icir() 통합, 알파 점수 생성
- **상태**: PENDING_INTEGRATION

## CLI Flag
```
--enable_alpha_scorer_v4 (0/1)
```

## Diagnostics Schema
```yaml
diagnostics.addon.alpha_scorer_v4:
  enabled: bool
  available: bool
  last_reason: str  # ok, missing_data
  applied_count: int
  effect_avg: float
  weights_modified: int
```

## 역할 분리
- **icir_v4**: 가중치 블렌딩 (기존)
- **alpha_scorer_v4**: 알파 점수 생성 (신규)

## 통합 작업
1. CLI argparse에 --enable_alpha_scorer_v4 추가
2. compute_alpha_scores_v4() 함수 구현
3. _build_portfolio에서 호출
4. diagnostics 기록

## 예상 공수: 1일
