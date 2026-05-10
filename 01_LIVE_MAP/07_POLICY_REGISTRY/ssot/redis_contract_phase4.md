# Redis Key Contract — Phase 4 Multi-Strategy Scaffold
Generated: 2026-04-01

## Strategy Target Keys
| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `strategy:{sleeve}:targets` | hash | 600s | Per-sleeve target weights |
| `strategy:{sleeve}:budget` | string | 600s | Per-sleeve budget allocation USD |
| `strategy:{sleeve}:scale` | string | 600s | Per-sleeve champion_scale |

## Attribution Keys
| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `attribution:{sleeve}:daily` | hash | 86400s | Daily PnL attribution per sleeve |
| `attribution:{sleeve}:cumulative` | hash | none | Cumulative attribution |
| `attribution:total:daily` | hash | 86400s | Total daily PnL across all sleeves |

## Three-Layer Regime Keys
| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `regime:market` | hash | 300s | Market-level regime (BULL/BEAR/CRISIS/etc.) |
| `regime:sector:{sector}` | hash | 300s | Sector-level regime signals |
| `regime:symbol:{symbol}` | hash | 300s | Symbol-level regime signals |
| `regime:composite` | hash | 300s | Weighted composite regime |

## Risk Evidence Producer Keys
| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `evidence:vix` | hash | 300s | VIX-based risk evidence |
| `evidence:drawdown` | hash | 300s | Drawdown-based risk evidence |
| `evidence:crash_prob` | hash | 300s | Crash probability evidence |
| `evidence:confidence` | hash | 300s | Regime confidence evidence |
| `evidence:composite` | hash | 300s | Composite risk score |

## Sleeves
- `core_alpha`: Primary momentum/value strategy
- `defensive_quality`: Quality factor defensive positions
- `flow_event`: Event-driven flow-based trades

## Migration Notes
- Current single-strategy keys (`ssot:target:v2:current`, `nextgen2:current_exposure`) remain active
- Phase 4 keys are scaffold-only — no live code writes to them yet
- Migration to multi-sleeve will require shadow period validation
