#!/usr/bin/env python3
"""Release Attestation — PM2 실행 파일 해시 검증."""
import hashlib, json, os, subprocess, sys, time

MANIFEST_PATH = "/home/ubuntu/release.manifest.json"

def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def generate_manifest():
    procs = json.loads(subprocess.check_output(["pm2", "jlist"], text=True, timeout=10))
    entries = {}
    for p in procs:
        name = p.get("name", "")
        script = p.get("pm2_env", {}).get("pm_exec_path", "")
        if script and os.path.exists(script):
            entries[name] = {
                "path": script,
                "sha256": compute_sha256(script),
                "size": os.path.getsize(script),
                "mtime": os.path.getmtime(script),
            }
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entries": entries,
    }
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest: {len(entries)} entries -> {MANIFEST_PATH}")
    return manifest

def verify_manifest():
    if not os.path.exists(MANIFEST_PATH):
        print("No manifest found. Run: python3 release_attestation.py generate")
        return False
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
    ok = fail = 0
    for name, entry in manifest["entries"].items():
        path = entry["path"]
        if not os.path.exists(path):
            print(f"  FAIL {name}: FILE MISSING ({path})"); fail += 1; continue
        actual = compute_sha256(path)
        if actual != entry["sha256"]:
            print(f"  FAIL {name}: HASH MISMATCH"); fail += 1
        else:
            print(f"  OK   {name}"); ok += 1
    print(f"\n{ok} OK, {fail} FAIL")
    return fail == 0

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    {"generate": generate_manifest, "verify": lambda: sys.exit(0 if verify_manifest() else 1)}[cmd]()
