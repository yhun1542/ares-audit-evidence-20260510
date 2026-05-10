# Redis Sentinel Cutover Runbook

## 목표
하드코딩된 `127.0.0.1:6379` 단일 접속을 제거하고, ARES 전 프로세스를 Sentinel-aware 구조로 전환한다.

## 원칙
- 모든 프로세스는 master 이름(`ares-master`)과 Sentinel 노드 목록만 안다.
- 직접 IP:port 고정 금지
- 재연결 중에도 `emergency lane` heartbeat 유지

## 단계
1. Sentinel 노드 3개 이상 상태 확인
2. `ares-master` master resolution 확인
3. 새 Sentinel-aware 클라이언트로 canary 프로세스 1개 전환
4. 강제 failover 수행
5. 아래 확인
   - reconnect 성공
   - state lock 재획득
   - emergency lane 유지
   - preopen gate false red 없음
6. 전 프로세스 cutover
7. `config rewrite` 및 구성 백업

## 합격 기준
- forced failover 3회 연속 성공
- RTO <= 300초
- unexpected HALT = 0
- emergency sell path success = 100%
