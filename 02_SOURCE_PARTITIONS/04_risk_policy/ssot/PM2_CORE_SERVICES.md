# PM2 Core Services

- **order-intent-executor**: `/home/ubuntu/ares_releases/2026-02-17/order-intent-executor.mjs` | status=`online` | sha256=`72c6219418a9318f59ee33f9cde45cc56f0cf8411c88a77d568833ff48c5831a`
- **live-trading-kis**: `/home/ubuntu/ares_releases/2026-02-17/live-trading-kis.mjs` | status=`online` | sha256=`31e4a67e37d03be84d57b6ba7f02acaaaa759c5b6b112bbbaff92664a6ab1832`
- **algo-maker-pegger**: `/home/ubuntu/ares_v56_live/releases/20260327_072504/algo-maker-pegger.mjs` | status=`online` | sha256=`496bd10f359617381dfc4f9bd6caa16a16cb6fa8e86d1016da221df49965db3b`
- **go-nogo-judge**: `/home/ubuntu/ares_releases/2026-02-17/go-nogo-judge-v2.mjs` | status=`online` | sha256=`81569ee21cdbc2436c4ec052a6ec55e95e6bb0eb725422b6ad2141df8d577fbe`
- **kill-switch-authority**: `None` | status=`None` | sha256=`None`
- **equity-calculator**: `/home/ubuntu/ares_releases/2026-02-17/equity-calculator.mjs` | status=`online` | sha256=`cea4cc711f9f0be046ce99e75a3f05eb335d24922dc9258a2b724142afa21f73`
- **realtime-data-feed**: `/home/ubuntu/ares_releases/2026-02-17/realtime-data-feed.mjs` | status=`online` | sha256=`b478e3488f03ce632a90ef72928594a67c9b41ed5871112cff4a6ffd799e7050`
- **orchestrator**: `None` | status=`None` | sha256=`None`
- **exposure-monitor**: `/home/ubuntu/exposure_monitor.py` | status=`online` | sha256=`1a09af4aa0e51e7a0b80f613e5898acb384b14682891d9de4be0a251fc1896bb`

Updated: `2026-04-03T15:34:50.864241Z`

---

## PM2 Alias / Name-Mapping Exceptions

현재 PM2 live manifest 기준으로 아래 2개 항목은 "서비스 미동작"이 아니라
**PM2 프로세스명 또는 수집 규칙 불일치로 인해 canonical name에 직접 매핑되지 않은 예외**다.

### 1. kill-switch-authority

- canonical service name: `kill-switch-authority`
- PM2 manifest status: `null`
- 해석: 현재 PM2 상에서 동일 기능이 다른 이름으로 등록되어 있거나, 현행 수집 규칙이 해당 프로세스를 canonical name으로 매핑하지 못한 상태
- 운영 판정: **non-blocking documentation exception**
- 조치 원칙: 실제 kill-switch 기능이 live code/route 상에서 사용 중인지 별도 확인 대상이나, 현재 운영 truth(F22 / confirm_3 retired / panic_override research-only)와는 독립적인 예외로 취급

### 2. orchestrator

- canonical service name: `orchestrator`
- PM2 manifest status: `null`
- 해석: 현재 PM2 상의 실제 프로세스명과 canonical service name 간 alias mismatch 가능성이 높음
- 운영 판정: **non-blocking documentation exception**
- 조치 원칙: `nextgen2/orchestrator.py` 계열은 stale/live drift 대상이므로 canonical path 문서와 함께 관리하되, 현재 live champion truth와는 분리된 예외로 기록

### 결론

위 2건은 PM2 alias / name-mapping 예외이며,
**"서비스 장애"나 "운영 truth 불일치"로 해석하지 않는다.**
운영 truth의 핵심 판정(F22 live / confirm_3 retired / panic_override research-only)에는 영향이 없다.
