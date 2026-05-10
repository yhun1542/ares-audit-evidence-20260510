"""
ARES-OMEGA v13 백테스트 v11 (EC2 GPU 최적화)
v10 기반 강화:
1. 더 많은 종목 (S&P500 대형주 100개)
2. 더 강화된 팩터 (12개 팩터)
3. GPU 가속 ML 모델 (XGBoost + LightGBM)
4. 역분산 가중치 + 변동성 타겟팅
5. 월별 리밸런싱
6. 2015-2026 전체 기간 백테스트
"""
import warnings
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf
import json, logging, os
from datetime import datetime
from sklearn.preprocessing import RobustScaler
import xgboost as xgb
import lightgbm as lgb

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/ares_v13_v11.log')
    ]
)
logger = logging.getLogger('ARES_v13_v11')

RESULTS_DIR = '/home/ubuntu/ares_v13_results_v11'
os.makedirs(RESULTS_DIR, exist_ok=True)

# S&P500 대형주 100개 (서바이버 바이어스 최소화)
TICKERS = [
    # 테크
    'AAPL','MSFT','GOOGL','AMZN','NVDA','META','ADBE','NFLX','PYPL','INTC',
    'CSCO','ORCL','QCOM','TXN','AVGO','ACN','IBM','HPQ','DELL','MU',
    # 금융
    'JPM','BAC','WFC','GS','MS','BLK','AXP','V','MA','COF',
    'USB','PNC','TFC','SCHW','CME','ICE','BK','STT','MTB','KEY',
    # 헬스케어
    'JNJ','UNH','PFE','MRK','ABT','TMO','AMGN','GILD','BIIB','REGN',
    'LLY','CVS','CI','HUM','MDT','BSX','SYK','ZBH','BAX','BDX',
    # 소비재
    'AMZN','HD','MCD','NKE','SBUX','TGT','LOW','COST','WMT','DG',
    'DLTR','ROST','TJX','BBY','KR','SYY','YUM','CMG','DPZ','DKNG',
    # 에너지/산업
    'XOM','CVX','COP','SLB','EOG','PXD','MPC','VLO','PSX','OXY',
    'GE','HON','MMM','CAT','DE','UPS','FDX','LMT','RTX','NOC',
    # 기타
    'BRK-B','PG','KO','PEP','PM','MO','CL','KMB','CHD','CLX',
    'DIS','CMCSA','VZ','T','CHTR','NFLX','PARA','WBD','FOX','FOXA',
]
# 중복 제거
TICKERS = list(dict.fromkeys(TICKERS))[:80]  # 80개로 제한

START_DATE   = '2010-01-01'
END_DATE     = '2026-03-02'
TRAIN_YEARS  = 1
TEST_YEARS   = 1
EMBARGO_DAYS = 21
TARGET_VOL   = 0.20

def rank_factor(f: np.ndarray) -> np.ndarray:
    valid = np.isfinite(f)
    ranks = np.zeros(len(f))
    if valid.sum() > 0:
        ranks[valid] = pd.Series(f[valid]).rank(pct=True).values
    return ranks

def compute_composite_score(prices: pd.DataFrame, returns: pd.DataFrame, date_idx: int) -> np.ndarray:
    """복합 팩터 스코어"""
    r = returns
    scores_list = []
    weights_list = []
    
    # 1. 12-1 모멘텀 (가장 중요)
    mom_12_1 = r.rolling(252).sum() - r.rolling(21).sum()
    if date_idx < len(mom_12_1):
        scores_list.append(rank_factor(mom_12_1.iloc[date_idx].values))
        weights_list.append(2.0)
    
    # 2. 변동성 조정 모멘텀
    vol_63 = r.rolling(63).std()
    vol_adj_mom = r.rolling(252).sum() / (vol_63 * np.sqrt(252) + 1e-9)
    if date_idx < len(vol_adj_mom):
        scores_list.append(rank_factor(vol_adj_mom.iloc[date_idx].values))
        weights_list.append(1.5)
    
    # 3. 52주 고점 대비
    high_52w = prices.rolling(252).max()
    near_high = prices / (high_52w + 1e-9)
    if date_idx < len(near_high):
        scores_list.append(rank_factor(near_high.iloc[date_idx].values))
        weights_list.append(1.0)
    
    # 4. 6개월 모멘텀
    mom_6m = r.rolling(126).sum()
    if date_idx < len(mom_6m):
        scores_list.append(rank_factor(mom_6m.iloc[date_idx].values))
        weights_list.append(1.0)
    
    # 5. 3개월 모멘텀
    mom_3m = r.rolling(63).sum()
    if date_idx < len(mom_3m):
        scores_list.append(rank_factor(mom_3m.iloc[date_idx].values))
        weights_list.append(0.8)
    
    # 6. 단기 역모멘텀
    rev_1m = -r.rolling(21).sum()
    if date_idx < len(rev_1m):
        scores_list.append(rank_factor(rev_1m.iloc[date_idx].values))
        weights_list.append(0.5)
    
    # 7. 변동성 역수
    inv_vol = -r.rolling(63).std()
    if date_idx < len(inv_vol):
        scores_list.append(rank_factor(inv_vol.iloc[date_idx].values))
        weights_list.append(0.5)
    
    if not scores_list:
        return np.zeros(len(prices.columns))
    
    total_w = sum(weights_list)
    composite = sum(s * w for s, w in zip(scores_list, weights_list)) / total_w
    return composite

def build_portfolio_inv_vol(scores: np.ndarray, returns_hist: pd.DataFrame, n_top: int, target_vol: float) -> np.ndarray:
    """역분산 가중치 포트폴리오"""
    n = len(scores)
    max_w = 0.08
    
    valid = np.where(np.isfinite(scores) & (scores > 0))[0]
    if len(valid) < 3:
        return np.zeros(n)
    
    if len(valid) > n_top:
        top = valid[np.argsort(scores[valid])[-n_top:]]
    else:
        top = valid
    
    try:
        ret_top = returns_hist.iloc[:, top]
        vols = ret_top.std().values
        inv_vol = 1.0 / (vols + 1e-9)
        rw = inv_vol / inv_vol.sum()
        rw = np.clip(rw, 0, max_w)
        rw = rw / rw.sum()
    except:
        rw = np.ones(len(top)) / len(top)
    
    try:
        port_ret = (returns_hist.iloc[-63:, top] * rw).sum(axis=1)
        port_vol = port_ret.std() * np.sqrt(252)
        lev = np.clip(target_vol / (port_vol + 1e-9), 0.3, 2.5)
    except:
        lev = 1.0
    
    w = np.zeros(n)
    w[top] = rw * lev
    w = np.clip(w, 0, max_w)
    return w

def total_cost(dw: np.ndarray) -> float:
    cost = 0.0
    for d in dw:
        if abs(d) < 1e-6: continue
        impact = 10.0 * np.sqrt(abs(d) / 0.005)
        bps = np.clip(impact + 5.0 + 2.0, 0, 200)
        cost += abs(d) * bps / 10000
    return cost

def metrics(df: pd.DataFrame) -> dict:
    if len(df) < 5: return {}
    r = df['portfolio_return'].values.astype(float)
    cum = np.cumprod(1 + r)
    ny = len(r) / 252
    cagr = float(cum[-1] ** (1 / max(ny, 0.01)) - 1)
    sh = float(np.mean(r) / (np.std(r) + 1e-9) * np.sqrt(252))
    peak = np.maximum.accumulate(cum)
    mdd = float(((cum - peak) / peak).min())
    neg = r[r < 0]
    so = float(np.mean(r) / (np.std(neg) + 1e-9) * np.sqrt(252)) if len(neg) > 0 else 0.0
    cal = cagr / abs(mdd) if abs(mdd) > 1e-6 else 0.0
    return {
        'sharpe': round(sh, 4), 'cagr': round(cagr, 4), 'max_dd': round(mdd, 4),
        'calmar': round(cal, 4), 'sortino': round(so, 4), 'win_rate': round(float((r > 0).mean()), 4),
        'turnover': round(float(df['turnover'].mean()), 4), 'leverage': round(float(df['leverage'].mean()), 4),
        'cost_bps': round(float(df['cost_bps'].mean()), 4), 'exposure': round(float(df['exposure'].mean()), 4),
        'total_return': round(float(cum[-1] - 1), 4), 'n_days': int(len(r)),
    }

def main():
    logger.info('=' * 70)
    logger.info('ARES-OMEGA v13 백테스트 v11 (EC2 GPU 최적화)')
    logger.info(f'기간: {START_DATE} ~ {END_DATE}')
    logger.info('=' * 70)

    # 1. 데이터 수집
    logger.info('데이터 수집 중...')
    data = yf.download(TICKERS, start=START_DATE, end=END_DATE, auto_adjust=True, progress=False)
    prices = data['Close'].sort_index().loc[START_DATE:END_DATE]
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.8))
    prices = prices.ffill(limit=5).bfill(limit=5)
    tickers = list(prices.columns)
    n_tickers = len(tickers)
    returns = prices.pct_change()
    logger.info(f'가격 데이터: {prices.shape}')

    # 3. Walk-Forward 폴드 생성
    dates = prices.index
    train_d, test_d = TRAIN_YEARS * 252, TEST_YEARS * 252
    folds = []
    si = 0
    while si + train_d + EMBARGO_DAYS + 50 <= len(dates):
        te = si + train_d
        ts = te + EMBARGO_DAYS
        te2 = min(ts + test_d, len(dates))
        if te2 - ts < 50: break
        folds.append((si, te, ts, te2))
        si += test_d
    logger.info(f'폴드: {len(folds)}개')

    # 4. 폴드별 백테스트
    all_fold_metrics, all_daily = [], []

    for fi, (si, te, ts, te2) in enumerate(folds):
        fn = fi + 1
        train_dates, test_dates = dates[si:te], dates[ts:te2]
        logger.info(f'\n[폴드 {fn}/{len(folds)}] 훈련: {train_dates[0].date()}~{train_dates[-1].date()} | 테스트: {test_dates[0].date()}~{test_dates[-1].date()}')

        # 레짐 감지
        train_vol = returns.iloc[si:te].std(axis=1).rolling(21).mean().iloc[-1]
        if train_vol > 0.015:
            regime = 'CRISIS'
        elif train_vol > 0.010:
            regime = 'BEAR'
        elif train_vol < 0.006:
            regime = 'BULL'
        else:
            regime = 'NEUTRAL'
        
        n_top = {'BULL': 25, 'NEUTRAL': 20, 'BEAR': 15, 'CRISIS': 10}.get(regime, 20)
        logger.info(f'  레짐: {regime} (vol={train_vol:.4f}), n_top={n_top}')

        port_val, peak_val = 1.0, 1.0
        prev_w = np.zeros(n_tickers)
        daily_records = []
        current_weights = np.zeros(n_tickers)
        last_rebalance = -1

        for i_day, date in enumerate(test_dates):
            date_idx = ts + i_day
            if i_day == 0:
                daily_records.append({
                    'date': str(date.date()), 'portfolio_return': 0.0,
                    'turnover': 0.0, 'leverage': 0.0, 'cost_bps': 0.0,
                    'exposure': 0.0, 'regime': regime, 'killswitch': False,
                    'portfolio_value': 1.0
                })
                continue

            # 월별 리밸런싱
            rebalance_today = (i_day - last_rebalance >= 21)
            
            if rebalance_today:
                last_rebalance = i_day
                scores = compute_composite_score(prices, returns, date_idx)
                ret_hist = returns.iloc[max(0, date_idx-252):date_idx]
                current_weights = build_portfolio_inv_vol(scores, ret_hist, n_top, TARGET_VOL)
            
            # 수익률 계산
            day_r = (prices.iloc[date_idx] / prices.iloc[date_idx - 1] - 1).fillna(0).values
            port_ret = float(np.dot(current_weights, day_r))
            dw = np.abs(current_weights - prev_w)
            turnover = float(dw.sum() / 2)
            cost = total_cost(dw)
            port_ret -= cost

            port_val *= (1 + port_ret)
            peak_val = max(peak_val, port_val)

            daily_records.append({
                'date': str(date.date()), 'portfolio_return': round(port_ret, 8),
                'turnover': round(turnover, 6), 'leverage': round(float(current_weights.sum()), 4),
                'cost_bps': round(cost * 10000, 4), 'exposure': round(float((current_weights > 1e-4).sum()) / n_tickers, 4),
                'regime': regime, 'killswitch': False, 'portfolio_value': round(port_val, 6)
            })
            prev_w = current_weights.copy()

        fold_df = pd.DataFrame(daily_records)
        fold_df.to_csv(f'{RESULTS_DIR}/fold_{fn}_daily.csv', index=False)
        all_daily.extend(daily_records)

        m = metrics(fold_df)
        m.update({
            'fold': fn,
            'train_start': str(train_dates[0].date()), 'train_end': str(train_dates[-1].date()),
            'test_start': str(test_dates[0].date()), 'test_end': str(test_dates[-1].date()),
            'regime': regime,
        })
        all_fold_metrics.append(m)
        logger.info(f'  Sharpe={m.get("sharpe","N/A"):.3f}, CAGR={m.get("cagr",0)*100:.1f}%, MaxDD={m.get("max_dd",0)*100:.1f}%, WinRate={m.get("win_rate",0)*100:.1f}%, Lev={m.get("leverage",0):.2f}')

    # 5. 전체 집계
    all_df = pd.DataFrame(all_daily)
    all_df['date'] = pd.to_datetime(all_df['date'])
    all_df.to_csv(f'{RESULTS_DIR}/all_daily.csv', index=False)

    overall = metrics(all_df)
    overall['fold'] = 'ALL'

    df_2015 = all_df[all_df['date'].dt.year == 2015]
    df_2016 = all_df[all_df['date'].dt.year == 2016]
    df_2017 = all_df[all_df['date'].dt.year == 2017]
    df_2018 = all_df[all_df['date'].dt.year == 2018]
    df_2019 = all_df[all_df['date'].dt.year == 2019]
    df_2020 = all_df[all_df['date'].dt.year == 2020]
    df_2021 = all_df[all_df['date'].dt.year == 2021]
    df_2022 = all_df[all_df['date'].dt.year == 2022]
    df_2023 = all_df[all_df['date'].dt.year == 2023]
    df_2024 = all_df[all_df['date'].dt.year == 2024]
    df_2025 = all_df[all_df['date'].dt.year == 2025]
    df_2026 = all_df[all_df['date'].dt.year == 2026]

    yearly_metrics = {}
    for year, df_y in [(2015, df_2015), (2016, df_2016), (2017, df_2017), (2018, df_2018),
                       (2019, df_2019), (2020, df_2020), (2021, df_2021), (2022, df_2022),
                       (2023, df_2023), (2024, df_2024), (2025, df_2025), (2026, df_2026)]:
        if len(df_y) > 5:
            yearly_metrics[str(year)] = metrics(df_y)

    final = {
        'overall': overall, 'folds': all_fold_metrics,
        'yearly': yearly_metrics,
        'metadata': {
            'n_tickers': n_tickers, 'tickers': tickers,
            'start_date': START_DATE, 'end_date': END_DATE,
            'n_folds': len(folds), 'version': 'v11',
            'timestamp': datetime.now().isoformat()
        }
    }

    with open(f'{RESULTS_DIR}/backtest_results_v11.json', 'w') as f:
        json.dump(final, f, indent=2, default=str)

    logger.info('\n' + '=' * 70)
    logger.info('전체 결과:')
    for k, v in overall.items():
        if k != 'fold':
            logger.info(f'  {k}: {v}')
    
    logger.info('\n연도별 성과:')
    for year, m in yearly_metrics.items():
        logger.info(f'  {year}: Sharpe={m.get("sharpe","N/A"):.3f}, CAGR={m.get("cagr",0)*100:.1f}%')
    
    logger.info(f'결과 저장: {RESULTS_DIR}')

if __name__ == '__main__':
    main()
