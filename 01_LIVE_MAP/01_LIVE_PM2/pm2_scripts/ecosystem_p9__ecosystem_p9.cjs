// ============================================================
// PM2 ecosystem for P9 ops:halt:request lifecycle sentinel
// ------------------------------------------------------------
// Deploy: pm2 start ecosystem_p9.cjs --update-env
// Status: pm2 list | grep ops-halt-request-lifecycle-sentinel
// ============================================================
module.exports = {
  apps: [
    {
      name: "ops-halt-request-lifecycle-sentinel",
      script: "/home/ubuntu/ares_current/p9/ops_halt_request_lifecycle_sentinel.py",
      interpreter: "/usr/bin/python3",
      cwd: "/home/ubuntu/ares_current/p9",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 50,
      kill_timeout: 8000,
      stop_exit_codes: [0],
      env: {
        // REDIS connection — Manus 보강: ARES_REDIS_URL 폴백 지원
        REDIS_URL: process.env.REDIS_URL || process.env.ARES_REDIS_URL || "",
        ARES_REDIS_URL: process.env.ARES_REDIS_URL || process.env.REDIS_URL || "",
        // P9 tuning
        P9_STALE_THRESHOLD_S: "600",     // 10 min — request stale 기준
        P9_REQUEST_TTL_S: "300",         // 5 min — TTL 없는 request에 적용할 default
        P9_POLL_INTERVAL_S: "1.0",       // 1초 cycle
        P9_LEADER_LOCK_TTL_S: "30",      // leader lock TTL
        PYTHONUNBUFFERED: "1",
      },
      log_date_format: "YYYY-MM-DD HH:mm:ss.SSS Z",
      merge_logs: true,
      out_file: "/home/ubuntu/.pm2/logs/p9-lifecycle-sentinel-out.log",
      error_file: "/home/ubuntu/.pm2/logs/p9-lifecycle-sentinel-err.log",
    },
  ],
};
