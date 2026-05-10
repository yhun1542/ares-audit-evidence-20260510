# ARES OPS Archive — Option A1 (2026-05-07)

생성 일시: 2026-05-06T22:36:23-04:00
정책: **옵션 A1** (회귀, reconciler-v3 복구 금지, sentinel 복구 금지, equity-calculator 미수정)

## 디렉토리 구조

```
ops_archive_a1_20260507/
├── INDEX.md                            ← 본 파일
├── DECOMMISSION_REGISTRY.md            ← 자동 복구 금지 목록 (Phase 2에서 생성)
├── A1_FINAL_REPORT.md                  ← 최종 운영 보고서 (Phase 3에서 생성)
├── p0_patches/                         ← P0 배포본 (실제 EC2 적용됨, 그대로 운영)
├── p1_patches/                         ← P1 배포본 (P1-1 .env만 영구 / sentinel 5개 제거됨)
├── p2_patches/                         ← P2 배포본 (전부 sentinel, 모두 제거됨)
├── full_audit/                         ← read-only 진단 스크립트 + 결과
├── ec2_src/                            ← EC2에서 가져온 소스 사본
├── reports/                            ← 이전 단계 보고서들
└── backups_meta/                       ← EC2 측 백업 경로 메타 정보
```

## 이 archive가 보존되어야 하는 이유

1. **v50 마이그레이션 의사결정 추적**: 사용자가 reconciler-v3, 5개 sentinel을 의도적으로 제거하고 v50 / nextgen2 라인으로 이행한 흔적
2. **운영 변경 이력**: P0/P1/P2 배포 → 사용자 추가 수정 → 의도적 제거의 시계열 기록
3. **장애 재발 시 비교 자료**: 만약 broker-truth-writer-v2가 단독 publisher 상태에서 결함이 발견되면, 본 archive가 reconciler-v3 재도입 검토 자료
4. **운영 지시문 준수 증거**: A1 결정이 어떤 근거(소스 파일 삭제, 3회 SIGKILL, dump 제거)로 내려졌는지

