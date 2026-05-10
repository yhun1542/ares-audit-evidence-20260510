# 외부 감사용 1페이지 요약 — Champion v8.7d (AUTO)

**생성시각(UTC)**: 2026-03-25T07:00:01Z

---

## 1) 버전/재현 기준

- **SSOT 디렉터리**: `/home/ubuntu/ssot/v8.7d`
- **해시(all.sha256 sha)**: `249a352b7fdbf141f5fe114f8fcaf43858de255f1a3631db36c0e9fd35b9d48e`
- **재현 명령**:
```bash
cd /home/ubuntu/ssot/v8.7d
sha256sum -c hashes/all.sha256
```

---

## 2) 유니버스 (운영 고정)

- **종목 수**: 48
- **목록**: AAPL, ABBV, AMZN, ASML, BA, BIL, CAT, CL, CMCSA, COP, CRWD, CVX, DE, GILD, GOOGL, HD, HYG, IEF, INTC, IWM...

---

## 3) 정책 우선순위 (운영)

1. **System Shock** (BUY 신규/증액 차단)
2. **News LLM** (종목 단위 risk-only)
3. **XGBoost** (risk_flag only, BUY 증액만)

---

## 4) 레짐 정밀화 (v8.8)

- **모드**: A(수익 회복 우선)
- **프리셋**: NORMAL_V1, CAUTIOUS_V1, DEFENSIVE_V1, CRISIS_V1
- **결합 규칙**: 가장 위험한 레이어가 우선 (보수적 결합)

---

## 5) 핵심 정책 요약

| 정책 | 설정 |
|------|------|
| Shock | apply_mode=full, hold_minutes=60 |
| News | default_scale_down=0.9 |
| XGB | apply_mode=live, block_p=0.9 |

---

## 6) 하드 가드레일 (운영 안전)

- **Full-series MDD** >= -0.05
- **Daily Loss Limit**: -0.03
- **Max Leverage**: 2.0

---

## 7) 최근 운영/검증 결과

- N/A

---

## 8) 태그 요약

```
N/A
```

---

## 9) 파일 목록 (SSOT)

```
-rw-r--r-- 1 ubuntu ubuntu 1116 Mar 24 02:08 /home/ubuntu/ssot/v8.7d/final_selected_v87d.json
-rw-r--r-- 1 ubuntu ubuntu 3152 Mar 25 04:51 /home/ubuntu/ssot/v8.7d/regime_policy_v88.json
-rw-rw-r-- 1 ubuntu ubuntu  209 Mar 21 09:00 /home/ubuntu/ssot/v8.7d/universe.csv
```

---

*이 문서는 자동 생성되었습니다. 수동 편집 금지.*
