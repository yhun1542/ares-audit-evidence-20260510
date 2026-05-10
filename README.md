# ARES Audit Evidence — 2026-05-10 (UTC)

Public, source-level audit evidence package for the ARES live trading system, prepared for downstream code-level review by GPT or other agents that can read this repository through public URLs only.

> **Branch / Tag**: `main` / `v20260510-ares-total-audit`
> **Created (UTC)**: 2026-05-10
> **Source EC2**: `43.202.16.31` (audit collection only; no mutations performed during this snapshot)
> **Audit operator**: Manus (autonomous agent), under explicit user instructions
> **Maintainer**: yhun1542

---

## 1. Purpose

This repository carries a redacted, point-in-time evidence bundle that allows an external reviewer to perform **source-level forensic analysis** of the ARES system without requiring SSH or production credentials. It includes:

- The live PM2 process map and all PM2 script bodies as currently mounted on the production EC2 instance.
- The four canonical RAM26 / execution / KIS broker / risk policy partitions of the source tree.
- A snapshot of the KIS broker truth (`broker_truth_events`, `fills_canonical`, `order_executions`, `ops_ledger`, etc.) drawn directly from the production Redis streams.
- A redacted dump of every operationally relevant `/etc/ares/*` and `~/.env*` configuration file (every secret replaced by `***REDACTED***`).
- A historical versions index together with the small (≤25 MB) historical files for diff inspection.
- A mirror of the `yhun1542` GitHub organization (summary + repo URLs).
- The auxiliary scaffolding required by GPT-style audits: `00_SUMMARY/`, `MANIFEST.md`, SHA-256 manifests, and pointers to large artifacts shipped as Release Assets.

## 2. Repository Layout

| Path | Contents | Approx. size |
|------|----------|-------------:|
| `00_SUMMARY/` | Executive summary, file inventory, manifest. | small |
| `01_LIVE_MAP/` | Output of `01_build_ares_live_system_map.sh` — PM2 jlist (redacted), PM2 script bodies, RAM26 champion, execution-order path, KIS broker truth, risk guards, policy registry. | ~38 MB |
| `02_SOURCE_PARTITIONS/` | Output of `02_build_partitioned_source_packs.sh` — 9 domain partitions of the live tree (`01_ram26`, `02_execution`, `03_kis_broker`, `04_risk_policy`, `06_observability`, `07_configs_redacted`, `08_historical_versions`, `09_github_repos`). The high-volume `05_features_data` partition (8.8 GB) ships only as a Release Asset. | ~268 MB |
| `03_KIS_BROKER_TRUTH/` | Output of `06_kis_broker_truth_audit.sh` — last 2 000 entries of every KIS-relevant Redis stream. | ~4 MB |
| `04_PERFORMANCE_30D/` | 30-day performance CSV summaries (NAV, equity, target weights, broker positions). To be filled by downstream `kit2/05` (still running on EC2 at the time of publication). | small |
| `05_VERSION_DIFF/` | Output of `03_exhaustive_version_diff_scan.sh` — feature matrix, candidate hits, GitHub mirror diff. To be filled when scan completes. | small |
| `06_AB_GATE/` | Output of `04_abtest_gatekeeper.py` — `AB_GATE_SUMMARY.md` and `ab_gate_summary.json` for current A/B configuration. | small |
| `07_PATCH_CANDIDATES/` | Curated list of patch candidates derived from the feature matrix (one Markdown per candidate). To be filled. | small |
| `MANIFEST.md` | Per-directory file inventory + SHA-256 of every Release Asset. | small |

## 3. Release Assets (large artifacts)

The full, byte-for-byte tarballs are attached to the GitHub Release `v20260510-ares-total-audit`:

| Asset | Size | SHA-256 |
|-------|-----:|---------|
| `ares_live_system_map_20260510T000205Z.tar.gz` | 149 MB | `88a518e1a0cc7915f4883ceddec89df0d9946f2bd4f557209c40922dde0349f5` |
| `ares_kis_truth_audit_20260510T001247Z.tar.gz` | 406 KB | `e9ac96c0ebe0f622c4901cd75540b651bce28e399f06b021aa8bd1b9b0b997e0` |
| `01_ram26_20260510T000335Z.tar.gz` | 1.1 MB | `d474e59fc5841562b63ef7292b3e31ae4f8aa24a2570ddc84af91680aaf3ad6c` |
| `02_execution_20260510T000335Z.tar.gz` | 1.1 MB | `98f8d6705e62be22b71d00a416e31c72b14d62fca15b4b9bc88fb90079553591` |
| `03_kis_broker_20260510T000335Z.tar.gz` | 1.1 MB | `780fbc8dd891c7657f6e0af9bfbff868c7791fcb279f88bc364f981947d5d553` |
| `04_risk_policy_20260510T000335Z.tar.gz` | 2.3 MB | `dee8a0d8cfcca7dcd3ca8062b058be4cef463b0ebbf5c30473464484db7e93c7` |
| `06_observability_20260510T000335Z.tar.gz` | 1.1 MB | `6fa15a820e15b8d2859782ab5c7072252ff535f68ddfb37448fef7d578c25cfa` |
| `07_configs_redacted_20260510T000335Z.tar.gz` | 1.2 MB | `b7a94b3cc4d0cfd437fbfd4dc101bd310226be1fd46f589b326a62ca44674cd9` |
| `08_historical_versions_20260510T000335Z.tar.gz` | 33 MB | `7f5907130becb10252c354eea3866728ffe333bd22a5f2ae1c3aaaade781b6eb` |
| `09_github_repos_20260510T000335Z.tar.gz` | 1.8 MB | `3bac1db2b1c1a02352a253285c8f668244a34e6bd090cb281e84af9e53d1851c` |

The combined `ARES_SOURCE_PARTITIONED_ALL_20260510T000335Z.tar.gz` (8.8 GB, includes `05_features_data` raw historical features) is **not** distributed through GitHub Release because GitHub enforces a 2 GB-per-asset hard limit. It is preserved on the EC2 host at:

```
/home/ubuntu/ares_source_packs/ARES_SOURCE_PARTITIONED_20260510T000335Z/ARES_SOURCE_PARTITIONED_ALL_20260510T000335Z.tar.gz
```

If needed, a downstream operator may fetch it directly from EC2 or split it with `split -b 1500M` and upload as multipart Release Assets.

## 4. Security Posture

- **No production credentials are present in this repository.** The repository was scanned with `ripgrep` against the following patterns immediately before commit and the result was `0 hits` in 21 240 files / 268 MB:
  - `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCESS_TOKEN`, `KIS_ACCOUNT_NO`, `approval_key`, `access_token`
  - `REDIS_URL` carrying a real password, `REDIS_PASSWORD`
  - `AKIA[A-Z0-9]{16}` (AWS Access Key IDs), `aws_secret_access_key`
  - `gh[pousr]_[A-Za-z0-9]{30,}` (GitHub PATs), `BEGIN .* PRIVATE KEY` (PEM material)
  - `TELEGRAM_BOT_TOKEN`, `bot[0-9]{8,}:[A-Za-z0-9_-]{30,}`
- All `/etc/ares/*.env*` and `~/.env*` files appear in `02_SOURCE_PARTITIONS/07_configs_redacted/` with the `.redacted` suffix and every secret replaced by `***REDACTED***`.
- Account numbers are partially masked (`8137****`).
- The KIS broker stream snapshots in `03_KIS_BROKER_TRUTH/` contain only price, side, quantity, timestamps, and broker-side identifiers; no API key material.

If you discover any leaked secret, please open an issue and the owner will rotate the credential and redact the file immediately.

## 5. How to use this repository (for the reviewing agent)

1. Read `00_SUMMARY/EXECUTIVE_SUMMARY.md` for the global state, known issues, and direction of the audit.
2. Read `MANIFEST.md` to obtain a deterministic file inventory and to verify SHA-256 of every Release Asset.
3. Open `01_LIVE_MAP/00_INDEX/INDEX.md` for the live PM2 process roster and the redis contract snapshot.
4. Use `02_SOURCE_PARTITIONS/01_ram26/` and `02_execution/` as the entry point for source-level inspection of the live order path.
5. Cross-reference against `03_KIS_BROKER_TRUTH/` to study fill / order reconciliation behavior over the most recent 2 000 stream entries.
6. Fetch any of the Release Assets above for a byte-for-byte copy of the original tarballs.

## 6. Provenance and audit notes

- The audit was generated by running the **Manus-fixed v1.1** build of two automation kits authored by the user:
  - `ares_structural_audit_abtest_org_kit_v1` (kit1)
  - `ares_total_system_reorg_compare_abtest_kit_v1` (kit2)
- All scripts in those kits were syntax-checked and patched for the following classes of issues before deployment:
  1. `set -Eeuo pipefail` interaction with `head -c` and `head -nN` (SIGPIPE → exit 141).
  2. Unbounded `find /home/ubuntu` traversal that timed out on a 36 GB historical tree.
  3. PM2 `jlist` raw JSON storage (security: now removed in favor of redacted-only output).
  4. GitHub clone path now honours the `GH_TOKEN` environment variable for authenticated access.
  5. `04_abtest_gatekeeper.py` hardened against missing CSV columns and emits both Markdown and JSON.
  Each fix is documented in `CHANGELOG_PATCHES.md` (in the kit tarballs preserved on EC2).
- A complete Manus-side run log of the audit is preserved at `/home/ubuntu/dl/` on the audit sandbox.

## 7. License

This repository is published for internal, single-operator review under all-rights-reserved by the maintainer. Not intended for general redistribution.
