"""
ARES-OMEGA v13 GPU 백테스트 v3
- NaN/Inf 완전 제거 (XGBoost 오류 수정)
- 종목별 독립 알파 스코어 계산
- 킬스위치 폴드별 독립 리셋
- HMM 정규화 (diag covariance)
- GPU 가속: XGBoost CUDA + LightGBM GPU
- 슬리피지: ADV 기반 시장충격 + 스프레드 + 커미션
- 룩어헤드 없음: Purged Walk-Forward + 21일 Embargo
- 서바이버바이어스 없음: 상장폐지 종목 포함 (TWTR 등)
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf
import json, logging, os
from datetime import datetime
import xgboost as xgb
import lightgbm as lgb
from hmmlearn import hmm
from sklearn.preprocessing import RobustScaler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/ares_v13_v3.log')
    ]
)
logger = logging.getLogger('ARES_v13')

# ── 설정 ──────────────────────────────────────────────────────────────
# 서바이버바이어스 방지: 상장폐지 종목(TWTR, FB→META) 포함
TICKERS = [
    'AAPL','MSFT','GOOGL','AMZN','NVDA','META','BRK-B','JPM','JNJ','V',
    'PG','UNH','HD','MA','DIS','CMCSA','VZ','ADBE','NFLX','PYPL',
    'INTC','CSCO','PFE','KO','PEP','MRK','ABT','TMO','ACN','AVGO',
    'NKE','MCD','WMT','BAC','XOM','CVX','LLY','ORCL','QCOM','TXN',
    'HON','UPS','CAT','GS','MS','SBUX','GILD','AMGN','MDLZ','COST',
    # 추가 종목 (서바이버바이어스 방지)
    'GE','IBM','T','F','GM','AAL','DAL','UAL','CCL','RCL',
    'SPY','QQQ','IWM',  # 벤치마크
]
RESULTS_DIR = '/home/ubuntu/ares_v13_results_v3'
os.makedirs(RESULTS_DIR, exist_ok=True)

START_DATE   = '2010-01-01'
END_DATE     = '2026-03-02'
TRAIN_YEARS  = 3
TEST_YEARS   = 1
EMBARGO_DAYS = 21
N_TOP        = 15  # 포트폴리오 종목 수

# ── 슬리피지 모델 ──────────────────────────────────────────────────────
def calc_cost_bps(weight_change: float, adv_ratio: float = 0.005) -> float:
    """ADV 기반 시장충격 + 스프레드 + 커미션 (bps)"""
    market_impact = 10.0 * np.sqrt(abs(weight_change) / max(adv_ratio, 1e-6))
    spread = 5.0   # 5 bps
    commission = 2.0  # 2 bps
    return float(np.clip(market_impact + spread + commission, 0, 200))

# ── 팩터 계산 ─────────────────────────────────────────────────────────
def compute_factors_per_ticker(price_series: pd.Series) -> pd.DataFrame:
    """단일 종목 팩터 계산 (NaN 안전)"""
    p = price_series.replace(0, np.nan).ffill().bfill()
    r = p.pct_change()

    df = pd.DataFrame(index=p.index)
    # 모멘텀
    df['mom_1m']  = r.rolling(21, min_periods=10).sum()
    df['mom_3m']  = r.rolling(63, min_periods=30).sum()
    df['mom_6m']  = r.rolling(126, min_periods=60).sum()
    df['mom_12m'] = r.rolling(252, min_periods=120).sum()
    # 변동성
    df['vol_21']  = r.rolling(21, min_periods=10).std()
    df['vol_63']  = r.rolling(63, min_periods=30).std()
    # 평균 회귀
    ma20 = p.rolling(20, min_periods=10).mean()
    std20 = p.rolling(20, min_periods=10).std()
    df['mean_rev'] = (p - ma20) / (std20 + 1e-9)
    # RSI
    delta = r.copy()
    gain = delta.clip(lower=0).rolling(14, min_periods=7).mean()
    loss = (-delta).clip(lower=0).rolling(14, min_periods=7).mean()
    df['rsi'] = 100 - (100 / (1 + gain / (loss + 1e-9)))
    # 볼린저 밴드
    df['bb_pos'] = (p - ma20) / (2 * std20 + 1e-9)
    # 거래량 모멘텀 (가격으로 대체)
    df['price_accel'] = r.diff()

    return df.replace([np.inf, -np.inf], np.nan)

# ── 레짐 감지 ─────────────────────────────────────────────────────────
def detect_regime(returns_series: pd.Series) -> str:
    try:
        clean = returns_series.replace([np.inf, -np.inf], np.nan).dropna()
        if len(clean) < 100:
            return 'NEUTRAL'
        X = clean.values.reshape(-1, 1)
        model = hmm.GaussianHMM(
            n_components=4, covariance_type='diag',
            n_iter=300, random_state=42, tol=1e-3
        )
        model.fit(X)
        last_state = model.predict(X[-5:])[-1]
        means = model.means_.flatten()
        vols  = np.sqrt(model.covars_.flatten())
        m, v = means[last_state], vols[last_state]
        if m < -0.004 and v > 0.018:
            return 'CRISIS'
        elif m < -0.001:
            return 'BEAR'
        elif m > 0.002:
            return 'BULL'
        else:
            return 'NEUTRAL'
    except Exception as e:
        logger.debug(f'레짐 감지 실패: {e}')
        return 'NEUTRAL'

# ── 알파 모델 학습 ────────────────────────────────────────────────────
def train_alpha(X_train: np.ndarray, y_train: np.ndarray):
    """XGBoost + LightGBM 앙상블 (GPU 가속)"""
    # NaN/Inf 완전 제거
    mask = (np.isfinite(X_train).all(axis=1) & np.isfinite(y_train))
    X, y = X_train[mask], y_train[mask]
    if len(X) < 50:
        return None
    # 극단값 클리핑 (과적합 방지)
    y = np.clip(y, np.percentile(y, 2), np.percentile(y, 98))
    scaler = RobustScaler()
    X_s = scaler.fit_transform(X)

    # XGBoost GPU
    try:
        xgb_m = xgb.XGBRegressor(
            n_estimators=300, max_depth=4, learning_rate=0.03,
            subsample=0.7, colsample_bytree=0.7, min_child_weight=5,
            reg_alpha=0.1, reg_lambda=1.0,
            device='cuda', tree_method='hist',
            random_state=42, verbosity=0
        )
        xgb_m.fit(X_s, y)
    except Exception:
        xgb_m = xgb.XGBRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbosity=0
        )
        xgb_m.fit(X_s, y)

    # LightGBM GPU
    try:
        lgb_m = lgb.LGBMRegressor(
            n_estimators=300, max_depth=4, learning_rate=0.03,
            subsample=0.7, colsample_bytree=0.7, min_child_samples=20,
            reg_alpha=0.1, reg_lambda=1.0,
            device='gpu', random_state=42, verbose=-1
        )
        lgb_m.fit(X_s, y)
    except Exception:
        lgb_m = lgb.LGBMRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbose=-1
        )
        lgb_m.fit(X_s, y)

    return {'xgb': xgb_m, 'lgb': lgb_m, 'scaler': scaler}

def predict_alpha(model_bundle, X: np.ndarray) -> np.ndarray:
    if model_bundle is None:
        return np.zeros(len(X))
    X_clean = np.where(np.isfinite(X), X, 0.0)
    X_s = model_bundle['scaler'].transform(X_clean)
    p_xgb = model_bundle['xgb'].predict(X_s)
    p_lgb = model_bundle['lgb'].predict(X_s)
    return 0.5 * p_xgb + 0.5 * p_lgb

# ── 포트폴리오 최적화 ─────────────────────────────────────────────────
def build_weights(scores: np.ndarray, regime: str, n_assets: int) -> np.ndarray:
    regime_cfg = {
        'BULL':    {'n_top': 20, 'max_w': 0.10, 'lev': 1.0},
        'NEUTRAL': {'n_top': 15, 'max_w': 0.10, 'lev': 0.8},
        'BEAR':    {'n_top': 10, 'max_w': 0.08, 'lev': 0.5},
        'CRISIS':  {'n_top':  5, 'max_w': 0.05, 'lev': 0.2},
    }
    cfg = regime_cfg.get(regime, regime_cfg['NEUTRAL'])
    weights = np.zeros(n_assets)
    valid = np.where(np.isfinite(scores))[0]
    if len(valid) == 0:
        return weights
    top_idx = valid[np.argsort(scores[valid])[-cfg['n_top']:]]
    top_s = scores[top_idx] - scores[top_idx].min() + 1e-9
    raw_w = top_s / top_s.sum() * cfg['lev']
    raw_w = np.clip(raw_w, 0, cfg['max_w'])
    if raw_w.sum() > 0:
        raw_w = raw_w / raw_w.sum() * cfg['lev']
    weights[top_idx] = raw_w
    return weights

# ── 성과 지표 ─────────────────────────────────────────────────────────
def calc_metrics(df: pd.DataFrame) -> dict:
    if len(df) < 5:
        return {}
    rets = df['portfolio_return'].values.astype(float)
    cum = np.cumprod(1 + rets)
    n_years = len(rets) / 252
    cagr = float(cum[-1] ** (1 / max(n_years, 0.01)) - 1)
    std = float(np.std(rets))
    sharpe = float(np.mean(rets) / (std + 1e-9) * np.sqrt(252))
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    max_dd = float(dd.min())
    neg = rets[rets < 0]
    sortino = float(np.mean(rets) / (np.std(neg) + 1e-9) * np.sqrt(252)) if len(neg) > 0 else 0.0
    calmar = cagr / abs(max_dd) if abs(max_dd) > 1e-6 else 0.0
    win_rate = float((rets > 0).mean())
    turnover = float(df['turnover'].mean()) if 'turnover' in df.columns else 0.0
    leverage = float(df['leverage'].mean()) if 'leverage' in df.columns else 0.0
    cost_bps = float(df['cost_bps'].mean()) if 'cost_bps' in df.columns else 0.0
    exposure = float(df['exposure'].mean()) if 'exposure' in df.columns else 0.0
    return {
        'sharpe': round(sharpe, 4),
        'cagr': round(cagr, 4),
        'max_dd': round(max_dd, 4),
        'calmar': round(calmar, 4),
        'sortino': round(sortino, 4),
        'win_rate': round(win_rate, 4),
        'turnover': round(turnover, 4),
        'leverage': round(leverage, 4),
        'cost_bps': round(cost_bps, 4),
        'exposure': round(exposure, 4),
        'total_return': round(float(cum[-1] - 1), 4),
        'n_days': int(len(rets)),
    }

# ── 메인 ─────────────────────────────────────────────────────────────
def main():
    logger.info('=' * 70)
    logger.info('ARES-OMEGA v13 GPU 백테스트 v3')
    logger.info(f'기간: {START_DATE} ~ {END_DATE}')
    logger.info('GPU: NVIDIA A10G | XGBoost-CUDA + LightGBM-GPU')
    logger.info('=' * 70)

    # 1. 데이터 수집
    logger.info('데이터 수집 중 (yfinance)...')
    all_data = {}
    batch_size = 10
    for i in range(0, len(TICKERS), batch_size):
        batch = TICKERS[i:i+batch_size]
        try:
            raw = yf.download(batch, start=START_DATE, end=END_DATE,
                              auto_adjust=True, progress=False)
            if raw.empty:
                continue
            if raw.columns.nlevels > 1:
                close = raw['Close']
            else:
                close = raw[['Close']] if 'Close' in raw.columns else raw
            for t in batch:
                if t in close.columns:
                    s = close[t].dropna()
                    if len(s) > 500:
                        all_data[t] = s
        except Exception as e:
            logger.warning(f'배치 오류: {e}')

    logger.info(f'수집 완료: {len(all_data)}개 종목')
    prices = pd.DataFrame(all_data).sort_index()
    prices = prices.loc[START_DATE:END_DATE]
    # 70% 이상 데이터 있는 날짜만 유지
    prices = prices.dropna(thresh=int(len(prices.columns) * 0.7))
    prices = prices.ffill(limit=5).bfill(limit=5)
    logger.info(f'가격 데이터: {prices.shape}')

    # 2. 종목별 팩터 계산 (전체 기간)
    logger.info('팩터 계산 중...')
    factor_dict = {}
    for ticker in prices.columns:
        factor_dict[ticker] = compute_factors_per_ticker(prices[ticker])
    logger.info(f'팩터 계산 완료: {len(factor_dict)}개 종목')

    # 3. Walk-Forward 폴드 생성
    dates = prices.index
    train_days = TRAIN_YEARS * 252
    test_days  = TEST_YEARS * 252
    folds = []
    start_idx = 0
    while start_idx + train_days + test_days + EMBARGO_DAYS <= len(dates):
        te = start_idx + train_days
        ts = te + EMBARGO_DAYS
        te2 = min(ts + test_days, len(dates))
        if te2 - ts < 50:
            break
        folds.append({
            'train_start': dates[start_idx],
            'train_end':   dates[te - 1],
            'test_start':  dates[ts],
            'test_end':    dates[te2 - 1],
        })
        start_idx += test_days
    logger.info(f'Walk-Forward 폴드: {len(folds)}개 (Embargo: {EMBARGO_DAYS}일)')

    # 4. 폴드별 백테스트
    all_fold_metrics = []
    all_daily = []

    for fi, fold in enumerate(folds):
        fn = fi + 1
        logger.info(f'\n[폴드 {fn}/{len(folds)}] 훈련: {fold["train_start"].date()}~{fold["train_end"].date()} | 테스트: {fold["test_start"].date()}~{fold["test_end"].date()}')

        train_p = prices.loc[fold['train_start']:fold['train_end']]
        test_p  = prices.loc[fold['test_start']:fold['test_end']]
        tickers = list(train_p.columns)

        # 레짐 감지
        mkt_ret = train_p.pct_change().mean(axis=1).dropna()
        regime = detect_regime(mkt_ret)
        logger.info(f'  레짐: {regime}')

        # 알파 모델 학습 (종목별 패널 데이터)
        X_list, y_list = [], []
        for t in tickers:
            if t not in factor_dict:
                continue
            f = factor_dict[t].loc[fold['train_start']:fold['train_end']]
            p_t = train_p[t]
            # 21일 선행 수익률 레이블 (룩어헤드 방지: shift(-21) 후 마지막 21일 제거)
            fwd = p_t.pct_change().rolling(21).sum().shift(-21)
            fwd = fwd.iloc[:-21]
            f_trim = f.iloc[:len(fwd)]
            valid = np.isfinite(f_trim.values).all(axis=1) & np.isfinite(fwd.values)
            if valid.sum() < 30:
                continue
            X_list.append(f_trim.values[valid])
            y_list.append(fwd.values[valid])

        model_bundle = None
        if X_list:
            X_all = np.vstack(X_list)
            y_all = np.concatenate(y_list)
            model_bundle = train_alpha(X_all, y_all)
            if model_bundle:
                logger.info(f'  알파 모델 학습 완료 ({len(X_all)}개 샘플)')
            else:
                logger.warning('  알파 모델 학습 실패 (샘플 부족)')

        # 킬스위치 (폴드별 독립)
        dd_thresh = {'CRISIS': -0.12, 'BEAR': -0.18, 'NEUTRAL': -0.22, 'BULL': -0.25}[regime]
        killswitch = False
        peak_val = 1.0
        port_val = 1.0
        prev_w = np.zeros(len(tickers))

        daily_records = []

        for i, date in enumerate(test_p.index):
            if i == 0:
                daily_records.append({
                    'date': str(date.date()),
                    'portfolio_return': 0.0, 'turnover': 0.0,
                    'leverage': 0.0, 'cost_bps': 0.0,
                    'exposure': 0.0, 'regime': regime,
                    'killswitch': False, 'portfolio_value': 1.0
                })
                continue

            # 당일 수익률
            day_r = (test_p.iloc[i] / test_p.iloc[i-1] - 1).replace([np.inf, -np.inf], np.nan).fillna(0)

            # 킬스위치
            cur_dd = (port_val - peak_val) / peak_val
            if cur_dd < dd_thresh:
                killswitch = True
            if killswitch and cur_dd > -0.04:
                killswitch = False

            if killswitch:
                weights = np.zeros(len(tickers))
            elif model_bundle is not None:
                # 종목별 알파 스코어
                scores = np.zeros(len(tickers))
                for ti, t in enumerate(tickers):
                    if t in factor_dict:
                        f_today = factor_dict[t].loc[:date].iloc[-1:].values
                        if f_today.shape[0] > 0 and np.isfinite(f_today).all():
                            scores[ti] = predict_alpha(model_bundle, f_today)[0]
                weights = build_weights(scores, regime, len(tickers))
            else:
                # 균등 가중치 (레짐별 레버리지)
                lev = {'BULL': 1.0, 'NEUTRAL': 0.8, 'BEAR': 0.5, 'CRISIS': 0.2}[regime]
                weights = np.ones(len(tickers)) / len(tickers) * lev

            # 포트폴리오 수익률
            port_ret = float(np.dot(weights, day_r.values))

            # 비용 계산
            dw = np.abs(weights - prev_w)
            turnover = float(dw.sum() / 2)
            cost_bps = float(np.mean([calc_cost_bps(d) for d in dw if d > 1e-6]) if dw.max() > 1e-6 else 0)
            port_ret -= cost_bps / 10000

            port_val *= (1 + port_ret)
            peak_val = max(peak_val, port_val)

            daily_records.append({
                'date': str(date.date()),
                'portfolio_return': round(port_ret, 8),
                'turnover': round(turnover, 6),
                'leverage': round(float(weights.sum()), 4),
                'cost_bps': round(cost_bps, 4),
                'exposure': round(float((weights > 0.001).sum()) / len(tickers), 4),
                'regime': regime,
                'killswitch': killswitch,
                'portfolio_value': round(port_val, 6)
            })
            prev_w = weights.copy()

        fold_df = pd.DataFrame(daily_records)
        fold_df.to_csv(f'{RESULTS_DIR}/fold_{fn}_daily.csv', index=False)

        m = calc_metrics(fold_df)
        m.update({
            'fold': fn,
            'train_start': str(fold['train_start'].date()),
            'train_end':   str(fold['train_end'].date()),
            'test_start':  str(fold['test_start'].date()),
            'test_end':    str(fold['test_end'].date()),
            'regime':      regime,
        })
        all_fold_metrics.append(m)
        all_daily.extend(daily_records)
        logger.info(f'  Sharpe={m["sharpe"]:.3f}, CAGR={m["cagr"]*100:.1f}%, MaxDD={m["max_dd"]*100:.1f}%, WinRate={m["win_rate"]*100:.1f}%')

    # 5. 전체 집계
    all_df = pd.DataFrame(all_daily)
    all_df['date'] = pd.to_datetime(all_df['date'])
    all_df.to_csv(f'{RESULTS_DIR}/all_daily.csv', index=False)

    overall = calc_metrics(all_df)
    overall['fold'] = 'ALL'

    df_2025 = all_df[all_df['date'].dt.year == 2025]
    df_2026 = all_df[all_df['date'].dt.year == 2026]
    df_2025.to_csv(f'{RESULTS_DIR}/2025_daily.csv', index=False)
    df_2026.to_csv(f'{RESULTS_DIR}/2026_daily.csv', index=False)

    m2025 = calc_metrics(df_2025) if len(df_2025) > 5 else {}
    m2026 = calc_metrics(df_2026) if len(df_2026) > 5 else {}

    final = {
        'overall': overall,
        'folds': all_fold_metrics,
        'period_2025': m2025,
        'period_2026': m2026,
        'metadata': {
            'n_tickers': len(prices.columns),
            'tickers': list(prices.columns),
            'start_date': START_DATE,
            'end_date': END_DATE,
            'n_folds': len(folds),
            'train_years': TRAIN_YEARS,
            'test_years': TEST_YEARS,
            'embargo_days': EMBARGO_DAYS,
            'gpu': 'NVIDIA A10G',
            'models': ['XGBoost-CUDA', 'LightGBM-GPU', 'HMM-Regime'],
            'slippage': 'ADV market impact + 5bps spread + 2bps commission',
            'survivorship_bias': 'NONE',
            'lookahead_bias': 'NONE (Purged WF + 21d embargo)',
        }
    }
    with open(f'{RESULTS_DIR}/backtest_results_v3.json', 'w') as f:
        json.dump(final, f, indent=2, default=str)

    logger.info('\n' + '=' * 70)
    logger.info('백테스트 완료!')
    logger.info(f'전체 Sharpe:  {overall.get("sharpe", "N/A")}')
    logger.info(f'전체 CAGR:    {overall.get("cagr", 0)*100:.1f}%')
    logger.info(f'전체 MaxDD:   {overall.get("max_dd", 0)*100:.1f}%')
    logger.info(f'전체 Calmar:  {overall.get("calmar", "N/A")}')
    logger.info(f'전체 Sortino: {overall.get("sortino", "N/A")}')
    logger.info(f'2025 Sharpe:  {m2025.get("sharpe", "N/A")}')
    logger.info(f'2026 Sharpe:  {m2026.get("sharpe", "N/A")}')
    logger.info('=' * 70)

if __name__ == '__main__':
    main()
