# ARES Security Checklist (Post-Audit)

## Immediate Actions
1. [ ] Rotate all credentials that may have been exposed in past uploads/archives
2. [ ] Ensure .env file permissions are 600
3. [ ] Verify PM2 dump, release archives, backup tarballs do not contain secrets
4. [ ] No entrypoint outside manifest.lock should be running in production

## Operational Contract
- Redis canonical keys must be documented
- No champion promote/rollback without champion_registry
- contract_scan.py and validate_post_patch.py must pass before any deployment

## Monitoring
- Daily health check cron installed (UTC 14:00 = ET 10:00)
- DLQ monitoring active
- Exposure floor alerts active
