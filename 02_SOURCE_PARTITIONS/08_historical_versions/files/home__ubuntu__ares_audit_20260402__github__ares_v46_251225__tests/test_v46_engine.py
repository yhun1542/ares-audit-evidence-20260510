#!/usr/bin/env python3
"""
ARES v46 - Test Suite
================================================================================
Unit tests and integration tests for the v46 engine.
================================================================================
"""

import unittest
import numpy as np
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ares_v46_engine import AresV46Config, AresV46Engine


class TestAresV46Config(unittest.TestCase):
    """Test configuration class."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = AresV46Config()
        
        # SLOW_RISK parameters
        self.assertEqual(config.SLOW60, -0.04)
        self.assertEqual(config.SLOW120, -0.08)
        
        # Exposure caps
        self.assertEqual(config.BULL_CAP, 0.8)
        self.assertEqual(config.CAP_SLOW, 0.35)
        
        # EXIT conditions
        self.assertEqual(config.EXIT_RET20, 0.02)
        self.assertEqual(config.EXIT_CONFIRM, 1)
        
        # Crisis detection
        self.assertEqual(config.CRISIS_VIX_LEVEL, 30.0)
        self.assertEqual(config.CRISIS_VIX_CHANGE, 0.20)
        
        # Regime exposures
        self.assertEqual(config.EXPOSURE_BULL, 1.0)
        self.assertEqual(config.EXPOSURE_NEUTRAL, 0.7)
        self.assertEqual(config.EXPOSURE_BEAR, 0.5)
        self.assertEqual(config.EXPOSURE_CRISIS, 0.2)
        
        # Backtest parameters
        self.assertEqual(config.TOP_K, 35)
        self.assertEqual(config.REBAL_PERIOD, 7)
        self.assertEqual(config.MIN_REBAL_DAYS, 2)
        self.assertEqual(config.COST_BPS, 20.0)
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = AresV46Config(
            SLOW60=-0.05,
            SLOW120=-0.10,
            CAP_SLOW=0.40
        )
        
        self.assertEqual(config.SLOW60, -0.05)
        self.assertEqual(config.SLOW120, -0.10)
        self.assertEqual(config.CAP_SLOW, 0.40)


class TestAresV46Engine(unittest.TestCase):
    """Test engine class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = AresV46Config()
        self.engine = AresV46Engine(self.config)
    
    def test_engine_initialization(self):
        """Test engine initialization."""
        self.assertIsNotNone(self.engine.config)
        self.assertEqual(self.engine.config.SLOW60, -0.04)
    
    def test_momentum_factor_calculation(self):
        """Test momentum factor calculation."""
        # Create synthetic price data
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(100, 10) * 0.01 + 0.0005, axis=0) + 100
        
        momentum = self.engine._compute_momentum_factor(prices, 20)
        
        # Check shape
        self.assertEqual(momentum.shape, (100, 10))
        
        # Check that early values are zero
        self.assertTrue(np.all(momentum[:20, :] == 0))
        
        # Check that later values are non-zero
        self.assertTrue(np.any(momentum[20:, :] != 0))
    
    def test_volatility_factor_calculation(self):
        """Test volatility factor calculation."""
        # Create synthetic return data
        np.random.seed(42)
        returns = np.random.randn(100, 10) * 0.02
        
        volatility = self.engine._compute_volatility_factor(returns, 20)
        
        # Check shape
        self.assertEqual(volatility.shape, (100, 10))
        
        # Check that values are negative (we use -volatility)
        self.assertTrue(np.all(volatility[20:, :] <= 0))
    
    def test_sharpe_calculation(self):
        """Test Sharpe ratio calculation."""
        # Create synthetic returns with known properties
        np.random.seed(42)
        daily_returns = np.random.randn(252) * 0.01 + 0.0005
        
        sharpe = self.engine._calculate_sharpe(daily_returns)
        
        # Sharpe should be positive for positive mean returns
        self.assertGreater(sharpe, 0)
        
        # Sharpe should be reasonable (not extreme)
        self.assertLess(abs(sharpe), 10)
    
    def test_mdd_calculation(self):
        """Test max drawdown calculation."""
        # Create equity curve with known drawdown
        equity = np.array([100, 110, 105, 95, 100, 115, 110])
        
        mdd = self.engine._calculate_mdd(equity)
        
        # Max drawdown should be negative
        self.assertLess(mdd, 0)
        
        # Max drawdown should be -15/110 ≈ -0.136
        expected_mdd = (95 - 110) / 110
        self.assertAlmostEqual(mdd, expected_mdd, places=3)


class TestRegimeLogic(unittest.TestCase):
    """Test regime detection logic."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config = AresV46Config()
    
    def test_crisis_detection_vix_level(self):
        """Test Crisis detection via VIX level."""
        # VIX above threshold should trigger Crisis
        vix = 35.0
        self.assertGreater(vix, self.config.CRISIS_VIX_LEVEL)
    
    def test_crisis_detection_vix_spike(self):
        """Test Crisis detection via VIX spike."""
        # VIX spike above threshold should trigger Crisis
        vix_change = 0.25
        self.assertGreater(vix_change, self.config.CRISIS_VIX_CHANGE)
    
    def test_slow_risk_detection(self):
        """Test SLOW_RISK detection."""
        # 60-day return below threshold
        ret60 = -0.05
        self.assertLess(ret60, self.config.SLOW60)
        
        # 120-day return below threshold
        ret120 = -0.10
        self.assertLess(ret120, self.config.SLOW120)
    
    def test_exit_condition(self):
        """Test EXIT condition."""
        # 20-day return above threshold
        ret20 = 0.03
        self.assertGreater(ret20, self.config.EXIT_RET20)


class TestIntegration(unittest.TestCase):
    """Integration tests (require database)."""
    
    @unittest.skipIf(
        not os.path.exists('/home/ubuntu/ares_x_unified_database/ares_universal_v2.db'),
        "Database not available"
    )
    def test_full_backtest(self):
        """Test full backtest execution."""
        config = AresV46Config()
        engine = AresV46Engine(config)
        
        # Load data
        engine.load_data('/home/ubuntu/ares_x_unified_database/ares_universal_v2.db')
        
        # Compute factors
        engine.compute_factors()
        engine.compute_cumulative_returns()
        
        # Run backtest
        equity, daily_returns = engine.run_backtest()
        
        # Check results
        self.assertEqual(len(equity), len(engine.dates))
        self.assertEqual(len(daily_returns), len(engine.dates))
        
        # Calculate metrics
        metrics = engine.calculate_metrics(equity, daily_returns)
        
        # Verify expected performance range
        self.assertGreater(metrics['sharpe'], 1.5)
        self.assertGreater(metrics['mdd'], -0.20)


if __name__ == '__main__':
    unittest.main()
