# ARES Trading System Kernel v2.0

## Overview

ARES v2.0 is a fundamental architectural redesign of the trading system, replacing the distributed state coordination (REBAL_GATE, scattered Redis flags) with a **single, atomic state machine** (Trading Kernel).

This design was produced through consensus of 4 AI models:
- **Grok-4.20** (X.AI) — Multi-agent analysis
- **Claude Opus 4.5** (Anthropic) — Architecture design
- **GPT-5.2 Pro** (OpenAI) — Implementation patterns
- **Gemini 2.5 Flash** (Google) — Validation & edge cases

## Architecture

```
                    ┌──────────────────────┐
                    │   Trading Kernel     │
                    │   (State Machine)    │
                    │                      │
                    │ BOOT → DATA_READY    │
                    │   → POS_READY        │
                    │   → TRADING          │
                    │   ↔ REBALANCING      │
                    │   → DEGRADED         │
                    │   → HALTED           │
                    └──────┬───────────────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
    ┌───────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
    │  Position    │ │ Unified  │ │  Rebalance  │
    │  Recon       │ │ Risk Mgr │ │  Job Mgr    │
    │  Engine      │ │          │ │             │
    └──────────────┘ └──────────┘ └─────────────┘
            │              │              │
    ┌───────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
    │  Session     │ │ Monitor  │ │  Kernel     │
    │  Manager     │ │ Daemon   │ │  Daemon     │
    └──────────────┘ └──────────┘ └─────────────┘
```

## Components

| Component | File | Description |
|-----------|------|-------------|
| Trading Kernel | `kernel.py` | Central state machine with atomic Lua transitions |
| Kernel Client | `kernel_client.py` | Read-only client for other services |
| Kernel Daemon | `kernel_daemon.py` | PM2-managed daemon running the kernel |
| Position Recon | `position_recon.py` | Broker-truth reconciliation with audit trail |
| Risk Manager | `risk_manager.py` | Unified risk checks (replaces OFG+LATENCY_GUARD) |
| Rebalance Job | `rebalance_job.py` | Batch-based rebalance with progress tracking |
| Session Manager | `session_manager.py` | Readiness-based session lifecycle |
| Monitoring | `monitoring.py` | 5-area health checks with alerting |
| Monitor Daemon | `monitor_daemon.py` | 24/7 monitoring daemon |

## Deployment

### Shadow Mode (Default)

Shadow mode runs all new components alongside existing logic. New components LOG decisions but DON'T ENFORCE them.

```bash
# Deploy to EC2
bash deploy.sh

# Verify deployment
python3 verify_deployment.py
```

### Feature Flags

All enforcement is controlled via Redis feature flags:

```bash
# Check current flags
redis-cli HGETALL ares:kernel:flags

# Enable kernel enforcement (after verification)
redis-cli HSET ares:kernel:flags kernel_enforce 1

# Enable risk enforcement
redis-cli HSET ares:kernel:flags risk_enforce 1

# Enable rebalance enforcement
redis-cli HSET ares:kernel:flags rebalance_enforce 1

# Enable position recon enforcement
redis-cli HSET ares:kernel:flags posrecon_enforce 1
```

### PM2 Commands

```bash
# Start ARES daemons
pm2 start ecosystem.ares.config.cjs

# View status
pm2 status

# View logs
pm2 logs ares-kernel-daemon
pm2 logs ares-monitor-daemon

# Restart
pm2 restart ares-kernel-daemon
pm2 restart ares-monitor-daemon
```

## Redis Keys

### Kernel Keys
| Key | Type | Description |
|-----|------|-------------|
| `ares:kernel:state` | STRING | Current state (BOOT, TRADING, etc.) |
| `ares:kernel:status` | HASH | Full status with metadata |
| `ares:kernel:transitions` | STREAM | Transition audit log |
| `ares:kernel:readiness` | HASH | Subsystem readiness |
| `ares:kernel:flags` | HASH | Feature flags |
| `ares:kernel:heartbeat` | STRING | Daemon heartbeat (120s TTL) |

### Shadow Keys
| Key | Type | Description |
|-----|------|-------------|
| `ares:shadow:cycle_metrics` | HASH | Latest cycle metrics from orchestrator |
| `ares:shadow:emit_log` | STREAM | Shadow emit tracking |

### Recon Keys
| Key | Type | Description |
|-----|------|-------------|
| `ares:recon:latest` | STRING | Latest snapshot timestamp |
| `ares:recon:snapshot:{ts}` | STRING | Snapshot JSON (24h TTL) |
| `ares:recon:mismatches` | SET | Current mismatch symbols |

## Rollout Plan

1. **Phase 1: Shadow Mode** (current)
   - All components running, logging only
   - Compare old vs new decisions
   - Zero risk to production

2. **Phase 2: Kernel Enforce**
   - Enable `kernel_enforce` flag
   - Kernel state gates trading (replaces scattered flags)
   - Backward-compatible keys still written

3. **Phase 3: Risk Enforce**
   - Enable `risk_enforce` flag
   - Unified risk manager replaces OFG circuit breaker
   - Context-aware rate limiting active

4. **Phase 4: Full Enforcement**
   - Enable all enforce flags
   - Remove backward-compatible key writes
   - Delete REBAL_GATE code from orchestrator

## Troubleshooting

### Kernel stuck in BOOT
```bash
# Check readiness
redis-cli HGETALL ares:kernel:readiness

# Force transition (emergency)
redis-cli SET ares:kernel:state TRADING
```

### Monitoring alerts
```bash
# Check health
redis-cli HGETALL ares:monitor:health:latest

# Check specific area
redis-cli HGET ares:monitor:health:latest price_feed
```

### Rollback
```bash
# Disable all enforcement
redis-cli HSET ares:kernel:flags kernel_enforce 0
redis-cli HSET ares:kernel:flags risk_enforce 0

# Stop daemons
pm2 stop ares-kernel-daemon ares-monitor-daemon

# Restore orchestrator backup
cp /home/ubuntu/orchestrator.py.ares_v2_backup_* /home/ubuntu/orchestrator.py
pm2 restart nextgen2-live
```
