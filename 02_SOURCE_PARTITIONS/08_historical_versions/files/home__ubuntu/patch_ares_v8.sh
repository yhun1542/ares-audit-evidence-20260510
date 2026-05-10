#!/usr/bin/env bash
# ARES-KIS-US-AUTOPILOT v8 Production Patch
# Based on KIS Official API Audit (May 2026)
echo "Patching ARES-KIS-US-AUTOPILOT..."
# 1. Update tr_ids.mjs WS_FIELD_COUNT
sed -i 's/HDFSCNT0: 26/HDFSCNT0: 25/' /home/ubuntu/kis-us-broker-v7/core/tr_ids.mjs
sed -i 's/H0GSCNI0: 26/H0GSCNI0: 25/' /home/ubuntu/kis-us-broker-v7/core/tr_ids.mjs
sed -i 's/H0GSCNI9: 26/H0GSCNI9: 25/' /home/ubuntu/kis-us-broker-v7/core/tr_ids.mjs
sed -i 's/HDFSASP0: 11/HDFSASP0: 16/' /home/ubuntu/kis-us-broker-v7/core/tr_ids.mjs
sed -i 's/HDFSASP1: 11/HDFSASP1: 16/' /home/ubuntu/kis-us-broker-v7/core/tr_ids.mjs
echo "Updated WS_FIELD_COUNT in tr_ids.mjs"
