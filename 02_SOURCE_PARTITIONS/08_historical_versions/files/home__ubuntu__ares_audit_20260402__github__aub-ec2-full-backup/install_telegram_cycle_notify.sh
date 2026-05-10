#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/ubuntu/AUB
cd "$ROOT"

echo "[1/5] Create telegram env template if missing..."
sudo mkdir -p /etc/aub
if [ ! -f /etc/aub/telegram.env ]; then
  sudo tee /etc/aub/telegram.env >/dev/null <<'EOF'
# Telegram Bot settings (fill these)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
EOF
  sudo chmod 600 /etc/aub/telegram.env
  sudo chown root:root /etc/aub/telegram.env
  echo "[OK] created /etc/aub/telegram.env (fill token/chat_id)"
else
  echo "[OK] /etc/aub/telegram.env exists"
fi

echo "[2/5] Install reporter script..."
mkdir -p "$ROOT/experiment_system/tools"
cp /home/ubuntu/AUB/aub_telegram_reporter.py "$ROOT/experiment_system/tools/aub_telegram_reporter.py" 2>/dev/null || true
chmod +x "$ROOT/experiment_system/tools/aub_telegram_reporter.py"
python3 -m pip install -q requests || true

echo "[3/5] Patch aub_manus_autorun_v2.sh to call reporter (idempotent)..."
python3 - <<'PY'
from pathlib import Path
import re

p=Path("/home/ubuntu/AUB/aub_manus_autorun_v2.sh")
txt=p.read_text(encoding="utf-8")

hook = "\n# Telegram notify (end-of-cycle)\npython3 experiment_system/tools/aub_telegram_reporter.py || true\n"
if "aub_telegram_reporter.py" in txt:
    print("[OK] telegram hook already present")
else:
    m=re.search(r"python3 experiment_system/tools/aub_detailed_perf_table\.py.*$", txt, flags=re.M)
    if m:
        pos=m.end()
        txt = txt[:pos] + hook + txt[pos:]
        print("[OK] inserted after aub_detailed_perf_table.py")
    else:
        m=re.search(r"^log \"DONE\..*\".*$", txt, flags=re.M)
        if m:
            pos=m.start()
            txt = txt[:pos] + hook + txt[pos:]
            print("[OK] inserted before DONE marker")
        else:
            txt += hook
            print("[OK] appended telegram hook at end")
    bak=p.with_suffix(".bak_telegram")
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(txt, encoding="utf-8")
    print("[OK] wrote", p, "backup", bak)
PY

echo "[4/5] Add telegram env file to systemd services (A/B) if present..."
for svc in /etc/systemd/system/aub-manus-autopilot-A.service /etc/systemd/system/aub-manus-autopilot-B.service; do
  if [ -f "$svc" ]; then
    if ! grep -q "/etc/aub/telegram.env" "$svc"; then
      sudo sed -i '/EnvironmentFile=-\/etc\/aub\/4ai\.env/a EnvironmentFile=-/etc/aub/telegram.env' "$svc"
      echo "[OK] patched $svc"
    else
      echo "[OK] $svc already has telegram env"
    fi
  fi
done

echo "[5/5] Reload systemd and restart timers..."
sudo systemctl daemon-reload
sudo systemctl restart aub-manus-autopilot-A.timer || true
sudo systemctl restart aub-manus-autopilot-B.timer || true

echo ""
echo "✅ Telegram notify installed."
echo "Next:"
echo "  1) Fill /etc/aub/telegram.env with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
echo "  2) Trigger a run: sudo systemctl start aub-manus-autopilot-A.service"
