# KIS WS Status Synthesizer Install Report

## Verdict
KIS_WS_STATUS_SYNTH_INSTALLED

## Writes
- kis:ws:status:current
- kis:ws:heartbeat
- kis:fills:last_ts
- kis:ws:status:synth:events

## Safety
No KIS API calls, no orders, no restarts except own PM2 process.
