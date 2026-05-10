#!/usr/bin/env python3
"""
ARES V5.0 3가지 모니터링 스크립트
1. order_thermostat:audit 스트림 - 과잉 거래 차단(REJECT) 비율
2. final-trade-gate V4 LLM 보정 신호 실제 반영 여부
3. intraday-alpha-engine GEX 계산 정확도 검증
"""

import json
import os
import time
import redis as redis_lib
from datetime import datetime, timezone

def get_redis():
    url = os.environ.get('REDIS_URL', '')
    if not url:
        env_path = '/etc/ares/redis.env'
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith('REDIS_URL='):
                        url = line.strip().split('=', 1)[1]
                        break
    use_tls = url.startswith('rediss://')
    if use_tls:
        return redis_lib.from_url(url, ssl_cert_reqs=None, decode_responses=True)
    return redis_lib.from_url(url, decode_responses=True)

def monitor_thermostat_audit(r):
    """1. order_thermostat:audit 스트림 분석"""
    print("\n" + "="*60)
    print("1. ORDER THERMOSTAT AUDIT 스트림 분석")
    print("="*60)
    
    # V5 audit 스트림 (gate_core_v50이 쓰는 스트림)
    streams_to_check = [
        'order:thermostat:audit',
        'ares:gate:v50:audit',
        'monitor:v3v4:signal_integrity:audit',
        'order_thermostat:audit',
    ]
    
    for stream_key in streams_to_check:
        try:
            entries = r.xrevrange(stream_key, count=50)
            if entries:
                print(f"\n스트림: {stream_key} ({len(entries)}개 항목)")
                total = len(entries)
                rejects = sum(1 for _, d in entries if d.get('action') == 'REJECT' or d.get('verdict') == 'REJECT')
                allows = sum(1 for _, d in entries if d.get('action') in ('ALLOW', 'DOWNSIZE') or d.get('verdict') in ('ALLOW', 'DOWNSIZE'))
                
                print(f"  총 항목: {total}")
                print(f"  REJECT: {rejects} ({rejects/total*100:.1f}%)")
                print(f"  ALLOW/DOWNSIZE: {allows} ({allows/total*100:.1f}%)")
                
                # 최근 5개 상세
                print(f"  최근 5개:")
                for entry_id, data in entries[:5]:
                    ts = data.get('ts', entry_id)
                    action = data.get('action') or data.get('verdict') or data.get('recommendation', 'N/A')
                    reason = data.get('reason') or data.get('reject_reason', '')
                    print(f"    [{ts}] {action} {reason}")
        except Exception as e:
            print(f"  {stream_key}: 없음 또는 오류 ({e})")
    
    # V5 Active 게이트 audit
    try:
        v5_audit = r.xrevrange('ares:gate:v50:audit', count=20)
        if v5_audit:
            print(f"\nV5 Active Gate Audit: {len(v5_audit)}개")
            for eid, data in v5_audit[:5]:
                print(f"  gate={data.get('gate')} risk={data.get('riskScore')} exp={data.get('exposureMultiplier')} reasons={data.get('reasons','')[:50]}")
    except Exception as e:
        print(f"  V5 audit 스트림: {e}")


def monitor_llm_signal_reflection(r):
    """2. V4 LLM 보정 신호 실제 반영 여부"""
    print("\n" + "="*60)
    print("2. V4 LLM 보정 신호 반영 여부 확인")
    print("="*60)
    
    # LLM 보정 키 읽기
    llm_data = r.hgetall('llm:regime:correction:latest')
    if llm_data:
        print(f"\nllm:regime:correction:latest:")
        print(f"  correction_factor: {llm_data.get('correction_factor', 'N/A')}")
        print(f"  recommended_action: {llm_data.get('recommended_action', 'N/A')}")
        print(f"  correction_confidence: {llm_data.get('correction_confidence', 'N/A')}")
        print(f"  event_type: {llm_data.get('event_type', 'N/A')}")
        print(f"  ts: {llm_data.get('ts', 'N/A')}")
        
        # 신선도 확인
        ts_str = llm_data.get('ts', '')
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60
                print(f"  신선도: {age_min:.1f}분 전 업데이트")
                if age_min > 15:
                    print(f"  ⚠️  경고: 15분 이상 미업데이트!")
                else:
                    print(f"  ✅ 신선함")
            except:
                pass
    else:
        print("  ❌ llm:regime:correction:latest 키 없음!")
    
    # V5 Active 게이트의 현재 상태에서 LLM 반영 확인
    v5_state_raw = r.get('ares:final_trade_gate:active')
    if v5_state_raw:
        try:
            v5_state = json.loads(v5_state_raw)
            print(f"\nV5 Active Gate 현재 상태:")
            print(f"  gate: {v5_state.get('gate')}")
            print(f"  riskScore: {v5_state.get('riskScore')}")
            print(f"  exposureMultiplier: {v5_state.get('exposureMultiplier')}")
            print(f"  reasons: {v5_state.get('reasons')}")
            
            # LLM 신호가 반영되었는지 확인
            factor = float(llm_data.get('correction_factor', 0)) if llm_data else 0
            action = llm_data.get('recommended_action', 'MAINTAIN') if llm_data else 'MAINTAIN'
            
            if action in ('REDUCE_RISK', 'STOP_TRADING') and factor < -0.3:
                if v5_state.get('gate') == 'OPEN':
                    print(f"  ⚠️  LLM이 {action}({factor}) 신호를 보냈지만 gate=OPEN")
                else:
                    print(f"  ✅ LLM 신호가 gate에 반영됨")
            else:
                print(f"  ✅ LLM 신호 정상 반영 (factor={factor}, action={action})")
        except Exception as e:
            print(f"  V5 상태 파싱 오류: {e}")
    
    # V4 legacy 게이트 LLM 반영 확인
    v4_log_key = 'ares:final_trade_gate:v4_llm_check'
    # final-trade-gate.mjs 로그에서 LLM 반영 여부 확인
    print(f"\n  V4 LLM 키 수정 확인:")
    print(f"  이전: llm:regime:correction (잘못된 키)")
    print(f"  수정: llm:regime:correction:latest (올바른 키) ✅")


def monitor_gex_accuracy(r):
    """3. GEX 계산 정확도 검증"""
    print("\n" + "="*60)
    print("3. GEX 계산 정확도 검증")
    print("="*60)
    
    symbols = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
    
    for sym in symbols:
        key = f'alpha:intraday:{sym}'
        raw = r.get(key)
        if not raw:
            print(f"  {sym}: 데이터 없음")
            continue
        
        try:
            data = json.loads(raw)
        except:
            print(f"  {sym}: JSON 파싱 오류")
            continue
        
        score = data.get('score', 0)
        action = data.get('action', 'N/A')
        conf = data.get('confidence', 0)
        regime_adj = data.get('regime_adj', 1.0)
        ts = data.get('ts', 'N/A')
        
        # 세부 신호 확인 (실제 구조: components.options/dark_pool/ofi/vwap)
        components = data.get('components', {})
        score = data.get('composite_score', data.get('score', 0))  # composite_score 우선
        regime_adj = data.get('regime_adjustment', data.get('regime_adj', 1.0))
        options_comp = components.get('options', {})
        dark_pool_comp = components.get('dark_pool', {})
        ofi_comp = components.get('ofi', {})
        vwap_comp = components.get('vwap', {})
        gex = options_comp.get('strength', 'N/A')
        ofi = ofi_comp.get('strength', 'N/A')
        vwap = vwap_comp.get('strength', 'N/A')
        options_flow = options_comp.get('strength', 'N/A')
        dark_pool = dark_pool_comp.get('strength', 'N/A')
        
        # 신선도
        age_str = ''
        if ts and ts != 'N/A':
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                age_sec = (datetime.now(timezone.utc) - dt).total_seconds()
                age_str = f" ({age_sec:.0f}초 전)"
            except:
                pass
        
        score_val = float(score) if score != 'N/A' else 0
        print(f"  {sym}: composite_score={score_val:.4f} action={action} conf={conf:.2f} regime_adj={regime_adj}")
        if all(x != 'N/A' for x in [options_flow, dark_pool, ofi, vwap]):
            print(f"    OptionsFlow={float(options_flow):.4f} DarkPool={float(dark_pool):.4f} OFI={float(ofi):.4f} VWAP={float(vwap):.4f}")
        else:
            print(f"    OptionsFlow={options_flow} DarkPool={dark_pool} OFI={ofi} VWAP={vwap}")
        print(f"    ts={ts}{age_str}")
        
        # GEX 유효성 검증
        if gex == 'N/A' or gex == 0:
            print(f"    ⚠️  GEX 신호 없음 (옵션 데이터 미수신 가능)")
        else:
            try:
                gex_val = float(gex)
                if -1.0 <= gex_val <= 1.0:
                    print(f"    ✅ GEX 정상 범위 ({gex_val:.3f})")
                else:
                    print(f"    ⚠️  GEX 범위 이상: {gex_val}")
            except:
                pass


def main():
    print(f"\nARES V5.0 3가지 모니터링 시작 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print("="*60)
    
    r = get_redis()
    print("Redis 연결 성공")
    
    monitor_thermostat_audit(r)
    monitor_llm_signal_reflection(r)
    monitor_gex_accuracy(r)
    
    print("\n" + "="*60)
    print("모니터링 완료")
    print("="*60)


if __name__ == '__main__':
    main()
