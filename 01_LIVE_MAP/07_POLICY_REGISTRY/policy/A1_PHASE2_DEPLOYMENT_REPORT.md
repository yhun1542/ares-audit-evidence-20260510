# A1 Phase 2 배포·검증 보고서 (1+2+3 통합)

> **작업 시각**: 2026-05-07 (KST 새벽~오전)
> **작업 범위**: Redis wrapper P2 진단 + Decommission Registry EC2 배포 + Archive 1.9MB EC2 동기화
> **결과**: 3건 모두 **성공 완료**

---

## 1. SSH 접속 복구 경위 (전제 작업)

### 1.1 초기 차단 증상
- `ssh -p 22 ubuntu@43.202.16.31` 8회 모두 `kex_exchange_identification: Connection closed by remote host`
- TCP 연결은 성립되나 sshd가 키 교환 시작 직전 close

### 1.2 진단 (AWS Root Key + SSM Run Command 활용)
- **AWS CLI 설치** + `~/.aws/credentials` 구성 → `sts get-caller-identity` `767261182754` 확인
- **EC2 인스턴스 식별**: `i-0b641c9af9456cfa8` (g5.4xlarge "g4dn-to-g5-backup-2025-01"), Public IP `43.202.16.31`
- **Security Group**: `sg-05a0d044b51a761ff` — **22 포트 inbound에 `0.0.0.0/0` 이미 열림**
- **SSM Run Command**로 EC2 내부 진단 (sandbox IP가 22 포트로 도달 못 함을 확인):
  - sshd active (running) 상태, `Port 22, 2222 / ListenAddress 0.0.0.0`
  - 동시각 다른 IP(186.50.51.150, 171.250.165.35)는 22 정상 접속
  - **fail2ban-sshd jail에 sandbox IP 없음** (`115.190.167.144` 1개만 ban)
  - hosts.deny / hosts.allow 비어 있음
  - 결론: **AWS/EC2 측 차단 없음** → 경로상 ISP 또는 sandbox 출구단 22 포트 reset

### 1.3 우회 (Stable fallback)
- EC2가 운영하던 **fallback SSH ports** 확인:
  - **Port 2222**: sshd (sshd_config의 `Port 22` + `Port 2222`)
  - Port 2223: Dropbear ("Stable fallback SSH via Dropbear")
  - Port 2224: python3 listener
- **Port 2222로 SSH 즉시 성공** → `~/.ssh/config`에 영구 반영
  ```
  Host ares-ec2
      HostName 43.202.16.31
      Port 2222            ← 추가
      User ubuntu
      IdentityFile /home/ubuntu/upload/kim1219.pem
  ```

### 1.4 재발 시 운영 가이드
22 포트 차단이 재현되면, sandbox는 **2222 또는 2223** 사용. Sandbox IP 변경은 불필요. AWS CLI/SSM은 secondary-channel 진단에 그대로 활용.

---

## 2. Redis wrapper P2 진단 결과 (요청 사항 #1)

### 2.1 결론
**`redis_cli_link.sh` wrapper는 "고장난" 것이 아니라 v50/nextgen2 마이그레이션 과정에서 의도적으로 제거된 자산입니다.**

### 2.2 근거
- `find /home/ubuntu -maxdepth 4 -name redis_cli_link.sh` → **0건** (파일 자체가 EC2에 없음)
- `/home/ubuntu/.env`의 모든 `REDIS_*` export 라인이 `#RCA20260424:` 주석 처리 + 헤더에 다음 명시:
  > `# user-local env: Redis credentials moved to SSOT /etc/ares/redis.env.`
  > `# Shell usage: set -a; source /etc/ares/redis.env; set +a`
  > `# systemd usage: EnvironmentFile=/etc/ares/redis.env (higher priority)`

### 2.3 신규 SSOT (정상 동작)
**`/etc/ares/redis.env`** (root-owned)에서 다음 변수 export:

| 변수 | 용도 |
|---|---|
| `REDIS_URL` | 메인 ACL `ares-admin` (rediss://) |
| `REDIS_URL_TRADER` | trader ACL |
| `REDIS_URL_GUARD` | guard ACL |
| `REDIS_URL_READONLY` | readonly ACL |
| `REDIS_URL_EQUITY_WRITER` | equity-writer ACL |
| `ARES_REDIS_HOST/PORT/USERNAME/PASSWORD/SSL/DB` | 분리 변수 (legacy 호환) |

**검증**: `sudo bash -c "set -a; source /etc/ares/redis.env; set +a; redis-cli -u $REDIS_URL --tls --insecure PING"` → **`PONG`**

### 2.4 키 토폴로지 변경 (가장 중요한 contract 변경)
이전 보고서가 가정했던 키들이 v50에서 분리·재설계됨:

| 구버전 키 (가정) | 신버전 키 (실제) | TYPE |
|---|---|---|
| `ofg:state` | `ofg:status`, `ofg:equity:health`, `ofg:gate:alert_level`, `ofg:gate:version`, `ofg:unifier:epoch`, `ofg:equity:verified` 등 | string (JSON) |
| `ares:trade:halt` | `ares:halt:active`, `ares:halt:active:reason`, `ares:halt:locked`, `policy:trade:halt_reason`, `policy:unhalt:auto_approve` | string |
| `lifetime:peak` | `ares:equity:lifetime_peak`, `ofg:lifetime:peak`, `ares:peak:lifetime` | string |
| `lifetime:dd` | `ares:audit:lifetime_dd`(stream), `ofg:lifetime:dd`(string) | mixed |

**실 측정값** (`2026-05-07T06:41Z` 시점):
- `ares:equity:total = 223283.01` (TTL=116, 갱신 활성)
- `ofg:status.state = OPEN`, `ofg:status.reason = all_clear`
- `ofg:status.current_lifetime_dd = -1.15%`, `ofg:status.in_deep_dd = false`
- `ofg:status.lifetime_peak = $225,877.07`
- `trading:enabled = true`, `trading:armed = true`, `trading:mode = LIVE`
- `ares:halt:active = false`, `ares:halt:locked = true` (사용자 5/6 수동 잠금: `USER_MANUAL_HALT_at_2026-05-06T13:50:18Z`)

### 2.5 운영 영향
**거래 차단 없음**. `ofg:status.state = OPEN`이며 `trading:enabled = true`. `ares:halt:locked = true`는 메타 잠금이지 active halt가 아니며, `policy:unhalt:auto_approve = true`라 자동 해제 경로도 살아 있음.

### 2.6 주의 사항 (운영 정보 차원)
1. **`ofg:gate:alert_level = CRIT`** — 본 status는 OPEN이지만 gate level은 CRIT. 원인은 `ofg:equity:health.warnings = ["verified_age_or_sla_missing"]` (verified equity의 source_ts 메타가 null). 거래 차단으로는 이어지지 않으나 **`ofg:equity:verified.source_ts`를 채우는 publisher가 누락**된 상태로 보입니다. 사용자께서 직접 수정 중인 `equity-calculator` 변경 범위에 포함될 가능성 있어 본 보고에서는 건드리지 않습니다.

### 2.7 권고
- `redis_cli_link.sh`는 **Decommission Registry에 합류 등록 권고** (별도 항목 추가 필요 시 알려 주세요)
- 향후 모든 redis-cli 호출은 다음 패턴으로 통일:
  ```bash
  sudo bash -c "set -a; source /etc/ares/redis.env; set +a; redis-cli -u $REDIS_URL --tls --insecure ..."
  ```

---

## 3. Decommission Registry EC2 배포 (요청 사항 #2)

### 3.1 배포 위치
- **EC2**: `/home/ubuntu/ops/policy/DECOMMISSION_REGISTRY.md`
- **EC2 (참조용 동봉)**: `/home/ubuntu/ops/policy/A1_FINAL_REPORT.md`

### 3.2 무결성 검증
| 파일 | Sandbox md5 | EC2 md5 | 일치 |
|---|---|---|---|
| DECOMMISSION_REGISTRY.md | `ce094e61d4d0e75f6680e2434a2cd264` | `ce094e61d4d0e75f6680e2434a2cd264` | ✅ |
| A1_FINAL_REPORT.md | `05c75bf467563f2cf08c9625cfd6a099` | `05c75bf467563f2cf08c9625cfd6a099` | ✅ |

### 3.3 후속 권고
guardian / self_healer / ops-autorecovery 등이 본 파일을 **읽도록** 변경하는 작업은 별도 코드 패치가 필요하며, 본 작업 범위(파일 배치)는 완료. 코드 통합이 필요하면 별도 지시 부탁드립니다.

---

## 4. Archive EC2 동기화 (요청 사항 #3)

### 4.1 전송 방식
초기 rsync 시도가 sandbox 출력 버퍼 이슈로 결과 확인 어려워 **tar.gz 단일 패키징 → scp → EC2에서 압축 해제** 방식 사용.

| 단계 | 결과 |
|---|---|
| 패키징 (`/tmp/ops_archive_a1.tgz`) | 350,296 B, md5 `86021c5f62657107d52b5694f77e7129` |
| EC2 전송 후 md5 | `86021c5f62657107d52b5694f77e7129` (동일) |
| EC2 압축 해제 | `/home/ubuntu/ops_archive_a1_20260507/` 60 files, 1.9 MB |

### 4.2 60개 파일 전수 무결성 검증
- Sandbox측 `find . -type f -exec md5sum` 60 라인
- EC2측 동일 명령 60 라인
- **`diff /tmp/local.md5 /tmp/remote_clean.md5` → 빈 diff (RC=0)** = 60개 파일 전부 md5 일치

### 4.3 EC2 측 디렉토리 트리
```
/home/ubuntu/ops_archive_a1_20260507/
├── A1_FINAL_REPORT.md
├── DECOMMISSION_REGISTRY.md
├── INDEX.md
├── A1_PHASE2_DEPLOYMENT_REPORT.md  (본 보고서 — sandbox에서만, 별도 sync 필요시 추가)
├── backups_meta/
├── ec2_src/
├── full_audit/
├── p0_patches/
├── p1_patches/
├── p2_patches/
└── reports/
```

---

## 5. 최종 시스템 상태 스냅샷 (2026-05-07T06:41Z = 15:41 KST)

| 카테고리 | 항목 | 값 | 평가 |
|---|---|---|---|
| Redis | PING via SSOT | PONG | ✅ |
| Equity | `ares:equity:total` | $223,283.01 (TTL=116) | ✅ 정상 갱신 |
| OFG | `ofg:status.state` | OPEN / all_clear | ✅ |
| OFG | `current_lifetime_dd` | -1.15% | ✅ 안전 |
| OFG | `lifetime_peak` | $225,877.07 | tracking |
| OFG | `in_deep_dd` | false | ✅ |
| OFG | `gate:alert_level` | CRIT (warning level) | ⚠️ 모니터링 |
| Halt | `ares:halt:active` | false | ✅ |
| Halt | `ares:halt:locked` | true | ⚠️ 사용자 수동 잠금 (5/6) |
| Trading | `enabled` | true | ✅ |
| Trading | `armed` | true | ✅ |
| Trading | `mode` | LIVE | ✅ |
| PM2 | guardian-self-audit, structural-guardian, system-invariant-checker, circuit-breaker-daemon, flapping-detector, chronos-ttl-canceller, factor-engine, gpu-* 등 | online (uptimes 41m~8h) | ✅ |
| SSH | EC2 access | 2222 정상 | ✅ |

---

## 6. 종료 상태

세 가지 요청(1+2+3) **모두 완료**. 운영 영향 0, 거래 차단 0. 사용자께서 작업 종료 또는 추가 점검을 결정해 주십시오.
