# nexus_bridge V5.6 네이티브 전환 상태

## 현황
- nexus_bridge_v56_compat.py: V5.5->V5.6 호환 브릿지 (현재 운영 중)
- 목적: V5.5 레거시 키를 V5.6 canonical 키로 변환

## 전환 계획
- Phase 1 (완료): 오케스트레이터 모듈 분리 6/6 완료
- Phase 2 (진행 중): V5.6 네이티브 키 직접 사용으로 전환
  - use_canonical_stream: true (이미 설정됨)
  - CANONICAL_INTENT_STREAM = emarkos:v6:order:intent (이미 사용 중)
- Phase 3 (계획): 브릿지 제거 후 V5.6 단일 운영

## Shadow Mode 테스트 결과
- shadow mode에서 V5.6 canonical stream 정상 동작 확인
- use_canonical_stream: true 설정으로 이미 V5.6 네이티브 스트림 사용 중
- 브릿지는 하위 호환성을 위해 유지 (안전한 점진적 전환)

## 기술 부채 해소 로드맵
1. 리팩토링 Phase 1 완료 (6/6 모듈) ✅
2. V5.6 canonical stream 전환 ✅ (use_canonical_stream: true)
3. 브릿지 의존성 제거 (Phase 2에서 진행)
4. 레거시 V5.5 키 정리 (Phase 3에서 진행)
