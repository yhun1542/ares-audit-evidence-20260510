# GitHub token leak guard

The guard detects GitHub token-shaped plaintext without printing its value. It does not authenticate or revoke tokens.

Enable the optional local hook in each clone:

```sh
git config core.hooksPath .githooks
python3 tools/security/check_github_pats.py --selftest
python3 tools/security/check_github_pats.py
```

The hook checks the complete staged index. New environment files and PM2 environment snapshots are ignored; already tracked files remain tracked and must stay sanitized. Keep credentials outside version control.

A redaction commit does not erase older commits, branches, tags, forks, cached views, or existing clones. Exposed credentials require separate revocation/rotation. History rewrites require coordinated approval.
