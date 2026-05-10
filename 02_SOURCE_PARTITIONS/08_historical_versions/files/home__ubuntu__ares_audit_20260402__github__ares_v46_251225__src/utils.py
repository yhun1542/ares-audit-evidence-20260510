#!/usr/bin/env python3
"""
ARES v46 - Utility Functions
================================================================================
Common utility functions for data processing, metrics calculation, and reporting.
================================================================================
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any
from datetime import datetime


def calculate_sharpe_ratio(daily_returns: np.ndarray, risk_free_rate: float = 0.0) -> float:
    """
    Calculate annualized Sharpe ratio.
    
    Args:
        daily_returns: Array of daily returns
        risk_free_rate: Annual risk-free rate (default 0)
    
    Returns:
        Annualized Sharpe ratio
    """
    if len(daily_returns) < 10:
        return np.nan
    
    excess_returns = daily_returns - risk_free_rate / 252
    mean_ret = np.mean(excess_returns)
    std_ret = np.std(excess_returns)
    
    if std_ret == 0:
        return 0.0
    
    return mean_ret / std_ret * np.sqrt(252)


def calculate_max_drawdown(equity: np.ndarray) -> float:
    """
    Calculate maximum drawdown.
    
    Args:
        equity: Array of equity values
    
    Returns:
        Maximum drawdown as negative percentage
    """
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    return np.min(drawdown)


def calculate_calmar_ratio(annual_return: float, max_drawdown: float) -> float:
    """
    Calculate Calmar ratio (annual return / max drawdown).
    
    Args:
        annual_return: Annualized return
        max_drawdown: Maximum drawdown (negative value)
    
    Returns:
        Calmar ratio
    """
    if max_drawdown == 0:
        return np.inf
    return annual_return / abs(max_drawdown)


def calculate_sortino_ratio(daily_returns: np.ndarray, risk_free_rate: float = 0.0) -> float:
    """
    Calculate annualized Sortino ratio.
    
    Args:
        daily_returns: Array of daily returns
        risk_free_rate: Annual risk-free rate (default 0)
    
    Returns:
        Annualized Sortino ratio
    """
    if len(daily_returns) < 10:
        return np.nan
    
    excess_returns = daily_returns - risk_free_rate / 252
    mean_ret = np.mean(excess_returns)
    
    # Downside deviation
    negative_returns = excess_returns[excess_returns < 0]
    if len(negative_returns) == 0:
        return np.inf
    
    downside_std = np.std(negative_returns)
    if downside_std == 0:
        return np.inf
    
    return mean_ret / downside_std * np.sqrt(252)


def find_drawdown_windows(equity: np.ndarray, dates: List, top_n: int = 5) -> List[Dict]:
    """
    Find top N drawdown windows.
    
    Args:
        equity: Array of equity values
        dates: List of dates
        top_n: Number of top drawdowns to return
    
    Returns:
        List of drawdown window dictionaries
    """
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    
    windows = []
    used_indices = set()
    
    for _ in range(top_n):
        # Find minimum drawdown not in used indices
        valid_dd = drawdown.copy()
        for idx in used_indices:
            valid_dd[idx] = 0
        
        if np.min(valid_dd) >= 0:
            break
        
        trough_idx = np.argmin(valid_dd)
        
        # Find peak before trough
        peak_idx = np.argmax(equity[:trough_idx+1])
        
        # Find recovery after trough
        recovery_idx = trough_idx
        for i in range(trough_idx, len(equity)):
            if equity[i] >= equity[peak_idx]:
                recovery_idx = i
                break
        else:
            recovery_idx = len(equity) - 1
        
        # Mark indices as used
        for idx in range(peak_idx, recovery_idx + 1):
            used_indices.add(idx)
        
        windows.append({
            'rank': len(windows) + 1,
            'peak_date': dates[peak_idx],
            'trough_date': dates[trough_idx],
            'recovery_date': dates[recovery_idx],
            'max_dd': drawdown[trough_idx],
            'duration_to_trough': trough_idx - peak_idx,
            'duration_to_recovery': recovery_idx - peak_idx
        })
    
    return windows


def calculate_period_metrics(equity: np.ndarray, daily_returns: np.ndarray, 
                             dates: List, start: str, end: str) -> Dict[str, Any]:
    """
    Calculate metrics for a specific period.
    
    Args:
        equity: Array of equity values
        daily_returns: Array of daily returns
        dates: List of dates
        start: Start date string
        end: End date string
    
    Returns:
        Dictionary of period metrics
    """
    date_index = pd.DatetimeIndex(dates)
    mask = (date_index >= start) & (date_index <= end)
    indices = np.where(mask)[0]
    
    if len(indices) == 0:
        return {'error': 'No data in period'}
    
    period_equity = equity[indices]
    period_returns = daily_returns[indices]
    
    return {
        'start_date': dates[indices[0]],
        'end_date': dates[indices[-1]],
        'trading_days': len(indices),
        'total_return': (period_equity[-1] / period_equity[0] - 1) * 100,
        'sharpe': calculate_sharpe_ratio(period_returns),
        'max_dd': calculate_max_drawdown(period_equity),
        'volatility': np.std(period_returns) * np.sqrt(252)
    }


def calculate_regime_statistics(regime: np.ndarray, daily_returns: np.ndarray) -> Dict[str, Dict]:
    """
    Calculate statistics by regime.
    
    Args:
        regime: Array of regime values
        daily_returns: Array of daily returns
    
    Returns:
        Dictionary of statistics per regime
    """
    regime_names = {0: 'Bull', 1: 'Neutral', 2: 'Bear', 3: 'Crisis', 4: 'SLOW_RISK'}
    stats = {}
    
    for regime_id, regime_name in regime_names.items():
        mask = regime == regime_id
        if not np.any(mask):
            continue
        
        regime_returns = daily_returns[mask]
        
        stats[regime_name] = {
            'days': np.sum(mask),
            'pct_of_total': np.sum(mask) / len(regime) * 100,
            'avg_return': np.mean(regime_returns) * 100,
            'std_return': np.std(regime_returns) * 100,
            'sharpe': calculate_sharpe_ratio(regime_returns),
            'win_rate': np.sum(regime_returns > 0) / len(regime_returns) * 100
        }
    
    return stats


def generate_performance_report(equity: np.ndarray, daily_returns: np.ndarray,
                                dates: List, regime: np.ndarray = None) -> str:
    """
    Generate a comprehensive performance report.
    
    Args:
        equity: Array of equity values
        daily_returns: Array of daily returns
        dates: List of dates
        regime: Optional array of regime values
    
    Returns:
        Formatted report string
    """
    years = len(dates) / 252
    total_return = (equity[-1] / equity[0] - 1) * 100
    annual_return = ((equity[-1] / equity[0]) ** (1/years) - 1) * 100
    sharpe = calculate_sharpe_ratio(daily_returns)
    sortino = calculate_sortino_ratio(daily_returns)
    max_dd = calculate_max_drawdown(equity)
    calmar = calculate_calmar_ratio(annual_return / 100, max_dd)
    volatility = np.std(daily_returns) * np.sqrt(252) * 100
    
    report = []
    report.append("=" * 60)
    report.append("ARES v46 Performance Report")
    report.append("=" * 60)
    report.append(f"Period: {dates[0].strftime('%Y-%m-%d')} to {dates[-1].strftime('%Y-%m-%d')}")
    report.append(f"Trading Days: {len(dates)}")
    report.append(f"Years: {years:.1f}")
    report.append("")
    report.append("Performance Metrics")
    report.append("-" * 40)
    report.append(f"Total Return: {total_return:.1f}%")
    report.append(f"Annual Return: {annual_return:.1f}%")
    report.append(f"Sharpe Ratio: {sharpe:.3f}")
    report.append(f"Sortino Ratio: {sortino:.3f}")
    report.append(f"Calmar Ratio: {calmar:.3f}")
    report.append(f"Max Drawdown: {max_dd*100:.1f}%")
    report.append(f"Volatility: {volatility:.1f}%")
    report.append("")
    
    # Drawdown windows
    windows = find_drawdown_windows(equity, dates, top_n=5)
    report.append("Top 5 Drawdown Windows")
    report.append("-" * 40)
    for w in windows:
        report.append(f"#{w['rank']}: {w['max_dd']*100:.1f}% | "
                     f"{w['peak_date'].strftime('%Y-%m-%d')} to {w['trough_date'].strftime('%Y-%m-%d')} | "
                     f"Recovery: {w['duration_to_recovery']} days")
    
    # Regime statistics
    if regime is not None:
        report.append("")
        report.append("Regime Statistics")
        report.append("-" * 40)
        regime_stats = calculate_regime_statistics(regime, daily_returns)
        for regime_name, stats in regime_stats.items():
            report.append(f"{regime_name}: {stats['pct_of_total']:.1f}% | "
                         f"Sharpe: {stats['sharpe']:.2f} | "
                         f"Win Rate: {stats['win_rate']:.1f}%")
    
    report.append("=" * 60)
    
    return "\n".join(report)


def export_results_to_csv(equity: np.ndarray, daily_returns: np.ndarray,
                          dates: List, regime: np.ndarray, leverage: np.ndarray,
                          output_path: str) -> None:
    """
    Export backtest results to CSV.
    
    Args:
        equity: Array of equity values
        daily_returns: Array of daily returns
        dates: List of dates
        regime: Array of regime values
        leverage: Array of leverage values
        output_path: Path to save CSV
    """
    regime_names = {0: 'Bull', 1: 'Neutral', 2: 'Bear', 3: 'Crisis', 4: 'SLOW_RISK'}
    
    df = pd.DataFrame({
        'date': dates,
        'equity': equity,
        'daily_return': daily_returns,
        'regime_id': regime,
        'regime_name': [regime_names.get(r, 'Unknown') for r in regime],
        'leverage': leverage
    })
    
    # Add cumulative metrics
    df['cumulative_return'] = (df['equity'] / df['equity'].iloc[0] - 1) * 100
    df['peak'] = df['equity'].cummax()
    df['drawdown'] = (df['equity'] - df['peak']) / df['peak'] * 100
    
    df.to_csv(output_path, index=False)
    print(f"Results exported to {output_path}")
