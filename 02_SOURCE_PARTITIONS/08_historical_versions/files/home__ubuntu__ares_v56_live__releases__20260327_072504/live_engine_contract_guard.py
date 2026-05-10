#!/usr/bin/env python3
"""
live_engine_contract_guard.py
-----------------------------
Fail-closed attestation for the ARES v56 live runtime.

This revision is tuned for the 2026-04-09 SSOT restore package:
- pins approved engine / adapter / alpha_config hashes
- allows known metadata aliases for the same approved champion
- writes a FAIL report as well as PASS reports
- suppresses false positives when the approved alpha_config intentionally
  overrides engine DEFAULT_PARAMS
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

HIGH_IMPACT_KEYS = [
    "target_vol",
    "per_cap",
    "ntb_base",
    "ntb_min",
    "ntb_max",
    "to_base",
    "to_max",
    "a2b2_base",
    "a2b2_max",
    "a2b2_to",
    "ace_base",
    "ace_max",
    "ace_floor",
    "vol_clamp_high",
    "crash_vix",
]


class ContractGuardError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _extract_default_params(engine_text: str) -> Dict[str, Any]:
    m = re.search(r"DEFAULT_PARAMS\s*=\s*(\{.*?\})\n\n", engine_text, re.S)
    if not m:
        raise ContractGuardError("DEFAULT_PARAMS block not found in engine file")
    return ast.literal_eval(m.group(1))


def _extract_contract_version(engine_text: str) -> Optional[str]:
    m = re.search(r'"version"\s*:\s*"([^"]+)"', engine_text)
    return m.group(1) if m else None


def _extract_header_release(engine_text: str) -> Optional[str]:
    m = re.search(r"^#\s*Release:\s*(.+)$", engine_text, re.M)
    return m.group(1).strip() if m else None


def _extract_adapter_meta(adapter_text: str) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {
        "champion": None,
        "version": None,
        "engine_file": None,
    }
    m = re.search(r'__champion__\s*=\s*"([^"]+)"', adapter_text)
    if m:
        out["champion"] = m.group(1)
    m = re.search(r'__version__\s*=\s*"([^"]+)"', adapter_text)
    if m:
        out["version"] = m.group(1)
    m = re.search(r'LIVE_TRUTH_ENGINE_FILE\s*=\s*"([^"]+)"', adapter_text)
    if m:
        out["engine_file"] = m.group(1)
    if not out["engine_file"]:
        m = re.search(r'_C6_PATH\s*=\s*os\.path\.join\([^\n]+,\s*"([^"]+)"\)', adapter_text)
        if m:
            out["engine_file"] = m.group(1)
    return out


def _load_policy(policy_path: Optional[Path]) -> Dict[str, Any]:
    default = {
        "mode": "fail_closed",
        "require_manifest_registry_hash_match": True,
        "require_runtime_current_dir": True,
        "require_engine_hash_match": True,
        "require_adapter_hash_match": False,
        "require_alpha_config_hash_match": False,
        "require_engine_header_contract_consistency": True,
        "approved_engine_hash": None,
        "approved_adapter_hash": None,
        "approved_alpha_config_hash": None,
        "approved_versions": [],
        "approved_engine_file": None,
        "approved_strategy_family": None,
        "suppress_param_drift_when_approved_config_hash_match": True,
        "allowed_param_drifts": {},
        "high_impact_keys": HIGH_IMPACT_KEYS,
    }
    if not policy_path or not policy_path.exists():
        return default
    raw = _load_json(policy_path)
    default.update(raw)
    return default


def _compare_params(default_params: Dict[str, Any], live_cfg: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    allowed = policy.get("allowed_param_drifts") or {}
    keys = policy.get("high_impact_keys") or HIGH_IMPACT_KEYS
    out: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        if key in default_params and key in live_cfg and default_params[key] != live_cfg[key]:
            allowed_value = allowed.get(key, "__NOT_SET__")
            if allowed_value == live_cfg[key]:
                continue
            out[key] = {
                "engine_default": default_params[key],
                "live_config": live_cfg[key],
            }
    return out


def _enforce_version_aliases(*, policy: Dict[str, Any], header_release: Optional[str], contract_version: Optional[str], cfg_version: Optional[str], adapter_meta: Dict[str, Optional[str]]) -> None:
    approved_versions = set(policy.get("approved_versions") or [])
    observed = [
        v
        for v in [header_release, contract_version, cfg_version, adapter_meta.get("version"), adapter_meta.get("champion")]
        if v
    ]
    if approved_versions:
        # [STRUCTURAL_FIX_V2] Support prefix matching for version aliases
        prefixes = set(policy.get("approved_version_prefixes") or [])
        def _is_approved(v):
            if v in approved_versions:
                return True
            for pfx in prefixes:
                if v.startswith(pfx):
                    return True
            # Wildcard support: V65_C6_E2E3fix_B40S60_F22_* matches V65_C6_E2E3fix_B40S60_F22_20260403
            import fnmatch
            for av in approved_versions:
                if '*' in av and fnmatch.fnmatch(v, av):
                    return True
            return False
        unknown = [v for v in observed if not _is_approved(v)]
        if unknown:
            raise ContractGuardError("unapproved version metadata detected: " + ", ".join(sorted(set(unknown))))
        return
    if cfg_version and contract_version and cfg_version != contract_version:
        raise ContractGuardError(
            f"engine CONTRACT_METADATA.version ({contract_version}) != alpha_config._meta.version ({cfg_version})"
        )


def attest_live_contract(
    *,
    manifest_path: Path,
    registry_path: Path,
    adapter_path: Path,
    alpha_config_path: Path,
    runtime_dir: Optional[Path] = None,
    policy_path: Optional[Path] = None,
) -> Dict[str, Any]:
    policy = _load_policy(policy_path)
    manifest = _load_json(manifest_path)
    registry = _load_json(registry_path)

    expected_engine_path = manifest.get("engine_path")
    expected_engine_hash = manifest.get("engine_hash")
    if not expected_engine_path or not expected_engine_hash:
        raise ContractGuardError("live_manifest missing engine_path or engine_hash")

    approved_engine_hash = policy.get("approved_engine_hash")
    if approved_engine_hash and approved_engine_hash != expected_engine_hash:
        raise ContractGuardError(
            f"policy approved_engine_hash ({approved_engine_hash}) != manifest engine_hash ({expected_engine_hash})"
        )

    if policy.get("require_manifest_registry_hash_match", True):
        reg_path = registry.get("current_live_engine_path")
        reg_hash = registry.get("current_live_engine_hash")
        if reg_path != expected_engine_path or reg_hash != expected_engine_hash:
            raise ContractGuardError(
                f"SSOT split-brain: manifest({expected_engine_path}, {expected_engine_hash}) != registry({reg_path}, {reg_hash})"
            )

    runtime_dir = runtime_dir or adapter_path.parent
    runtime_dir_input = runtime_dir
    runtime_dir_resolved = runtime_dir.resolve()
    if policy.get("require_runtime_current_dir", True):
        # Accept both /current (symlink) and its resolved target (releases/YYYYMMDD_HHMMSS)
        current_symlink = Path("/home/ubuntu/ares_v56_live/current")
        is_current_name = (runtime_dir_input.name == "current")
        is_current_resolved = (current_symlink.exists() and current_symlink.is_symlink()
                               and runtime_dir_resolved == current_symlink.resolve())
        if not is_current_name and not is_current_resolved:
            raise ContractGuardError(f"runtime_dir must be /current or its symlink target, got: {runtime_dir_input}")

    if not adapter_path.exists():
        raise ContractGuardError(f"adapter missing: {adapter_path}")
    if not alpha_config_path.exists():
        raise ContractGuardError(f"alpha config missing: {alpha_config_path}")

    expected_engine_name = Path(expected_engine_path).name
    approved_engine_file = policy.get("approved_engine_file")
    if approved_engine_file and approved_engine_file != expected_engine_name:
        raise ContractGuardError(
            f"policy approved_engine_file ({approved_engine_file}) != manifest engine basename ({expected_engine_name})"
        )

    candidate_engine = Path(expected_engine_path)
    if not candidate_engine.exists():
        candidate_engine = runtime_dir_resolved / expected_engine_name
    if not candidate_engine.exists():
        raise ContractGuardError(f"engine missing: manifest basename {expected_engine_name} not found under {runtime_dir}")

    engine_hash = _sha256(candidate_engine)
    adapter_hash = _sha256(adapter_path)
    cfg_hash = _sha256(alpha_config_path)

    if policy.get("require_engine_hash_match", True) and engine_hash != expected_engine_hash:
        raise ContractGuardError(
            f"engine hash mismatch: manifest={expected_engine_hash} actual={engine_hash} file={candidate_engine}"
        )

    approved_adapter_hash = policy.get("approved_adapter_hash")
    if policy.get("require_adapter_hash_match", False) and approved_adapter_hash and adapter_hash != approved_adapter_hash:
        raise ContractGuardError(
            f"adapter hash mismatch: approved={approved_adapter_hash} actual={adapter_hash} file={adapter_path}"
        )

    approved_cfg_hash = policy.get("approved_alpha_config_hash")
    approved_cfg_hash_match = False
    if policy.get("require_alpha_config_hash_match", False) and approved_cfg_hash:
        if cfg_hash != approved_cfg_hash:
            raise ContractGuardError(
                f"alpha_config hash mismatch: approved={approved_cfg_hash} actual={cfg_hash} file={alpha_config_path}"
            )
        approved_cfg_hash_match = True

    engine_text = candidate_engine.read_text()
    adapter_text = adapter_path.read_text()
    live_cfg = _load_json(alpha_config_path)

    adapter_meta = _extract_adapter_meta(adapter_text)
    default_params = _extract_default_params(engine_text)
    header_release = _extract_header_release(engine_text)
    contract_version = _extract_contract_version(engine_text)
    cfg_version = ((live_cfg.get("_meta") or {}).get("version"))
    param_drift = _compare_params(default_params, live_cfg, policy)

    if adapter_meta.get("engine_file") and adapter_meta["engine_file"] != expected_engine_name:
        raise ContractGuardError(
            f"adapter points to {adapter_meta['engine_file']} but manifest expects {expected_engine_name}"
        )

    if policy.get("require_engine_header_contract_consistency", True):
        _enforce_version_aliases(
            policy=policy,
            header_release=header_release,
            contract_version=contract_version,
            cfg_version=cfg_version,
            adapter_meta=adapter_meta,
        )

    if param_drift and not (approved_cfg_hash_match and policy.get("suppress_param_drift_when_approved_config_hash_match", True)):
        raise ContractGuardError("high-impact parameter drift detected: " + ", ".join(sorted(param_drift.keys())))

    result = {
        "status": "PASS",
        "runtime_dir": str(runtime_dir),
        "manifest_path": str(manifest_path),
        "registry_path": str(registry_path),
        "engine_path": str(candidate_engine),
        "adapter_path": str(adapter_path),
        "alpha_config_path": str(alpha_config_path),
        "manifest_engine_hash": expected_engine_hash,
        "engine_hash": engine_hash,
        "approved_engine_hash": approved_engine_hash,
        "adapter_hash": adapter_hash,
        "approved_adapter_hash": approved_adapter_hash,
        "alpha_config_hash": cfg_hash,
        "approved_alpha_config_hash": approved_cfg_hash,
        "engine_header_release": header_release,
        "engine_contract_version": contract_version,
        "alpha_config_version": cfg_version,
        "adapter_meta": adapter_meta,
        "policy": policy,
        "param_drift": param_drift,
        "param_drift_suppressed": bool(param_drift and approved_cfg_hash_match and policy.get("suppress_param_drift_when_approved_config_hash_match", True)),
    }
    return result


def guard_or_raise(
    *,
    manifest_path: str,
    registry_path: str,
    adapter_path: str,
    alpha_config_path: str,
    runtime_dir: Optional[str] = None,
    policy_path: Optional[str] = None,
    report_path: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        result = attest_live_contract(
            manifest_path=Path(manifest_path),
            registry_path=Path(registry_path),
            adapter_path=Path(adapter_path),
            alpha_config_path=Path(alpha_config_path),
            runtime_dir=Path(runtime_dir) if runtime_dir else None,
            policy_path=Path(policy_path) if policy_path else None,
        )
        if report_path:
            Path(report_path).write_text(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as exc:
        if report_path:
            fail = {
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "manifest_path": manifest_path,
                "registry_path": registry_path,
                "adapter_path": adapter_path,
                "alpha_config_path": alpha_config_path,
                "runtime_dir": runtime_dir,
                "policy_path": policy_path,
            }
            Path(report_path).write_text(json.dumps(fail, indent=2, sort_keys=True))
        raise


def _cli() -> int:
    ap = argparse.ArgumentParser(description="ARES live engine contract attestation")
    ap.add_argument("--manifest", default=os.getenv("ARES_LIVE_MANIFEST_PATH", "/home/ubuntu/ssot/live_manifest.json"))
    ap.add_argument("--registry", default=os.getenv("ARES_CHAMPION_REGISTRY_PATH", "/home/ubuntu/ssot/champion_registry.json"))
    ap.add_argument("--adapter", default="./engine_v65_c6_adapter.py")
    ap.add_argument("--alpha-config", default="./tc6x_config.json")
    ap.add_argument("--runtime-dir", default=os.getcwd())
    ap.add_argument("--policy", default=os.getenv("ARES_LIVE_DRIFT_POLICY_PATH", "./live_drift_policy.json"))
    ap.add_argument("--report", default=os.getenv("ARES_STARTUP_ATTESTATION_PATH", "./startup_attestation.json"))
    args = ap.parse_args()

    result = guard_or_raise(
        manifest_path=args.manifest,
        registry_path=args.registry,
        adapter_path=args.adapter,
        alpha_config_path=args.alpha_config,
        runtime_dir=args.runtime_dir,
        policy_path=args.policy,
        report_path=args.report,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
