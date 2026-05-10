const requireEnv = (name) => { const value = process.env[name]; if (!value) throw new Error(`${name} is required`); return value; };
const ARES_REDIS_URL = requireEnv("ARES_REDIS_URL");
module.exports = {
  apps: [
    {
      name: "ares-v56-live",
      script: "ares_v55_live_autopilot.py",
      args: "--config v55_live_autopilot_config.json",
      interpreter: "/usr/bin/python3",
      cwd: process.env.HOME + "/ares_v56_live/current",
      env: {
        ARES_REDIS_URL,
        ARES_LIVE_RUNTIME_DIR: process.env.HOME + "/ares_v56_live/current",
      },
      max_restarts: 10,
      restart_delay: 10000,
      autorestart: true,
    },
    {
      name: "ares-v56-bridge",
      script: "python3",
      args: `nexus_bridge_v56_compat.py --redis-url ${ARES_REDIS_URL}`,
      cwd: process.env.HOME + "/ares_v56_live/current",
      max_restarts: 5,
      restart_delay: 5000,
      autorestart: true,
    }
  ]
};
