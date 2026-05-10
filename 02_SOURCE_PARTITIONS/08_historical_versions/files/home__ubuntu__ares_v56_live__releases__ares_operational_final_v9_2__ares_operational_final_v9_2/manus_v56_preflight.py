#!/usr/bin/env python3
"""
V5.6 live deployment preflight
Checks only; does not emit orders.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict


def load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_db(db_path: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": db_path, "ok": False}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_ohlcv'")
    if not cur.fetchone():
        raise RuntimeError("daily_ohlcv table missing")
    cur.execute("SELECT COUNT(DISTINCT symbol) FROM daily_ohlcv")
    out["n_symbols"] = int(cur.fetchone()[0])
    cur.execute("SELECT MIN(date), MAX(date) FROM daily_ohlcv")
    mn, mx = cur.fetchone()
    out["date_min"] = mn
    out["date_max"] = mx
    out["ok"] = True
    conn.close()
    return out


def check_redis(redis_url: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"url": redis_url, "ok": False}
    try:
        import redis  # type: ignore
    except Exception as e:
        out["error"] = f"redis import failed: {e}"
        return out
    try:
        r = redis.from_url(redis_url, decode_responses=True)
        pong = r.ping()
        out["ping"] = bool(pong)
        sample = {
            "positions_key_exists": r.exists("emarkos:v1:positions"),
            "equity_key_exists": r.exists("emarkos:v1:equity") or r.exists("ofg:equity:verified"),
            "spy_key_exists": r.exists("realtime:feed:SPY:last") or r.exists("realtime:price:SPY"),
            "vix_key_exists": r.exists("realtime:vix:current") or r.exists("market:vix") or r.exists("realtime:feed:VIXY:last"),
        }
        out["sample_keys"] = sample
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--output", default="preflight_report.json")
    args = p.parse_args()

    report: Dict[str, Any] = {"ok": True, "checks": {}, "warnings": []}
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        report["ok"] = False
        report["checks"]["config"] = {"ok": False, "error": "config missing"}
        print(json.dumps(report, indent=2, default=str))
        return 1

    cfg = json.loads(cfg_path.read_text())
    exec_cfg = cfg.get("execution", {})
    root = cfg_path.parent.resolve()

    required_files = {
        "autopilot": root / "ares_v55_live_autopilot.py",
        "bridge": root / "nexus_bridge_v56_compat.py",
        "engine": Path(exec_cfg.get("engine_path", "")).resolve() if exec_cfg.get("engine_path") else None,
        "alpha_config": Path(exec_cfg.get("alpha_config_path", "")).resolve() if exec_cfg.get("alpha_config_path") else None,
        "db": Path(exec_cfg.get("db_path", "")).resolve() if exec_cfg.get("db_path") else None,
    }
    for name, path in required_files.items():
        if path is None:
            report["ok"] = False
            report["checks"][name] = {"ok": False, "error": "path missing in config"}
        else:
            ok = path.exists()
            report["checks"][name] = {"ok": ok, "path": str(path)}
            if not ok:
                report["ok"] = False

    # imports
    try:
        mod = load_module(str(root / "ares_v55_live_autopilot.py"), "ares_v55_live_autopilot")
        report["checks"]["autopilot_import"] = {"ok": True, "module": getattr(mod, "__name__", "?")}
    except Exception as e:
        report["ok"] = False
        report["checks"]["autopilot_import"] = {"ok": False, "error": str(e)}

    try:
        mod2 = load_module(str(root / "nexus_bridge_v56_compat.py"), "nexus_bridge_v56_compat")
        report["checks"]["bridge_import"] = {"ok": True, "module": getattr(mod2, "__name__", "?")}
    except Exception as e:
        report["ok"] = False
        report["checks"]["bridge_import"] = {"ok": False, "error": str(e)}

    # DB
    db_path = str(required_files["db"]) if required_files.get("db") else ""
    if db_path and Path(db_path).exists():
        try:
            db_info = check_db(db_path)
            report["checks"]["db_integrity"] = db_info
            if db_info.get("n_symbols", 0) < int(exec_cfg.get("min_match", 40)):
                report["warnings"].append(f"DB symbols {db_info.get('n_symbols')} < min_match {exec_cfg.get('min_match')}")
        except Exception as e:
            report["ok"] = False
            report["checks"]["db_integrity"] = {"ok": False, "error": str(e)}

    # Redis
    redis_url = exec_cfg.get("redis_url", "")
    redis_info = check_redis(redis_url)
    report["checks"]["redis"] = redis_info
    if not redis_info.get("ok"):
        report["ok"] = False

    # Param sanity
    sanity = {
        "actual_cost_bps": cfg.get("actual_cost_bps", 0),
        "shadow_slippage_bps": cfg.get("shadow_slippage_bps", 0),
        "shadow_impact_coeff_bps": cfg.get("shadow_impact_coeff_bps", 0),
        "max_live_turnover_pct": exec_cfg.get("max_live_turnover_pct", 0),
        "max_published_exposure": exec_cfg.get("max_published_exposure", 0),
        "long_only": cfg.get("kernel", {}).get("long_only", False),
    }
    sanity["ok"] = (
        sanity["actual_cost_bps"] >= 10 and
        sanity["shadow_slippage_bps"] >= 3 and
        sanity["shadow_impact_coeff_bps"] >= 4 and
        0 < sanity["max_live_turnover_pct"] <= 0.50 and
        0 < sanity["max_published_exposure"] <= 0.98 and
        bool(sanity["long_only"]) is True
    )
    report["checks"]["param_sanity"] = sanity
    if not sanity["ok"]:
        report["warnings"].append("parameter sanity outside recommended production bounds")

    Path(args.output).write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
