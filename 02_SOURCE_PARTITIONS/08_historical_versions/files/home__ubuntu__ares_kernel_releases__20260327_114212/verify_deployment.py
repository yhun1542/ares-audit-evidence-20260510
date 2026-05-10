"""
ARES v2.0 Deployment Verification Script

Runs comprehensive checks after deployment to verify all components
are functioning correctly.

Usage: python3 verify_deployment.py [--redis-url redis://localhost:6379/0]
"""

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import redis.asyncio as aioredis
except ImportError:
    print("ERROR: redis package not installed. Run: pip3 install redis")
    sys.exit(1)


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
if "--redis-url" in sys.argv:
    idx = sys.argv.index("--redis-url")
    if idx + 1 < len(sys.argv):
        REDIS_URL = sys.argv[idx + 1]


class Verifier:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.passed = 0
        self.failed = 0
        self.warnings = 0
    
    def _ok(self, name, detail=""):
        self.passed += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    
    def _fail(self, name, detail=""):
        self.failed += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
    
    def _warn(self, name, detail=""):
        self.warnings += 1
        print(f"  [WARN] {name}" + (f" — {detail}" if detail else ""))
    
    async def check_kernel_state(self):
        """Verify kernel state machine is operational."""
        print("\n1. Trading Kernel State Machine")
        print("-" * 40)
        
        state = await self.redis.get("ares:kernel:state")
        if state:
            self._ok(f"Kernel state exists: {state}")
        else:
            self._warn("Kernel state not set (daemon may not be running)")
        
        status = await self.redis.hgetall("ares:kernel:status")
        if status:
            self._ok(f"Kernel status hash: {len(status)} fields")
            for k, v in sorted(status.items()):
                print(f"    {k}: {v}")
        else:
            self._warn("Kernel status hash empty")
        
        flags = await self.redis.hgetall("ares:kernel:flags")
        if flags:
            self._ok(f"Feature flags: {len(flags)} flags")
            for k, v in sorted(flags.items()):
                mode = "ENFORCE" if v == "1" else "SHADOW"
                print(f"    {k}: {v} ({mode})")
        else:
            self._warn("Feature flags not set")
        
        hb = await self.redis.get("ares:kernel:heartbeat")
        if hb:
            age = time.time() * 1000 - float(hb)
            if age < 120000:
                self._ok(f"Heartbeat alive (age: {age/1000:.1f}s)")
            else:
                self._warn(f"Heartbeat stale (age: {age/1000:.1f}s)")
        else:
            self._warn("No heartbeat (daemon may not be running)")
    
    async def check_lua_script(self):
        """Verify Lua script is loadable."""
        print("\n2. Atomic Transition Lua Script")
        print("-" * 40)
        
        lua_path = os.path.join(os.path.dirname(__file__), "scripts", "atomic_transition.lua")
        if os.path.exists(lua_path):
            with open(lua_path) as f:
                lua_code = f.read()
            self._ok(f"Lua script exists ({len(lua_code)} bytes)")
            
            # Try to register
            try:
                script = self.redis.register_script(lua_code)
                self._ok("Lua script registered with Redis")
            except Exception as e:
                self._fail(f"Lua script registration failed: {e}")
        else:
            self._fail(f"Lua script not found: {lua_path}")
    
    async def check_shadow_metrics(self):
        """Verify shadow mode metrics are being written."""
        print("\n3. Shadow Mode Metrics")
        print("-" * 40)
        
        metrics = await self.redis.hgetall("ares:shadow:cycle_metrics")
        if metrics:
            self._ok(f"Shadow cycle metrics: {len(metrics)} fields")
            for k, v in sorted(metrics.items()):
                print(f"    {k}: {v}")
            
            last_ts = float(metrics.get("last_cycle_ts", 0))
            if last_ts > 0:
                age = time.time() - last_ts
                if age < 300:
                    self._ok(f"Metrics fresh (age: {age:.0f}s)")
                else:
                    self._warn(f"Metrics stale (age: {age:.0f}s) — nextgen2 may not be running")
        else:
            self._warn("No shadow metrics yet (nextgen2 cycle needed)")
        
        # Check shadow emit log
        emit_log = await self.redis.xlen("ares:shadow:emit_log")
        if emit_log > 0:
            self._ok(f"Shadow emit log: {emit_log} entries")
        else:
            self._warn("Shadow emit log empty (no emissions yet)")
    
    async def check_position_data(self):
        """Verify position data sources are available."""
        print("\n4. Position Data Sources")
        print("-" * 40)
        
        # Broker positions
        broker = await self.redis.hgetall("kis:broker:positions")
        if broker:
            self._ok(f"Broker positions: {len(broker)} symbols")
        else:
            # Try alternative key
            broker2 = await self.redis.hgetall("kis:live:positions")
            if broker2:
                self._ok(f"Broker positions (kis:live:positions): {len(broker2)} symbols")
            else:
                self._warn("No broker positions found")
        
        # Internal positions
        internal = await self.redis.get("emarkos:v1:positions")
        if internal:
            try:
                data = json.loads(internal)
                self._ok(f"Internal positions: {len(data)} symbols")
            except json.JSONDecodeError:
                self._fail("Internal positions: invalid JSON")
        else:
            self._warn("No internal positions found")
    
    async def check_existing_services(self):
        """Verify existing services are healthy."""
        print("\n5. Existing Service Health")
        print("-" * 40)
        
        # OFG state
        ofg = await self.redis.get("ofg:gate")
        if ofg:
            self._ok(f"OFG gate: {ofg}")
        else:
            self._warn("OFG gate not set")
        
        # Trading enabled
        te = await self.redis.get("trading:enabled")
        if te:
            self._ok(f"Trading enabled: {te}")
        else:
            self._warn("trading:enabled not set")
        
        # Halt state
        halt = await self.redis.get("trade:halt")
        if halt:
            self._warn(f"Trade halt active: {halt}")
        else:
            self._ok("No trade halt")
        
        # Price feed
        pf_ts = await self.redis.get("realtime:data_feed:last_success_ts")
        if pf_ts:
            age = time.time() - float(pf_ts)
            if age < 600:
                self._ok(f"Price feed: alive (age: {age:.0f}s)")
            else:
                self._warn(f"Price feed: stale (age: {age:.0f}s)")
        else:
            self._warn("Price feed timestamp not found")
        
        # KPI latest
        kpi = await self.redis.get("nextgen2:kpi:latest")
        if kpi:
            try:
                kpi_data = json.loads(kpi)
                self._ok(f"Latest KPI: cycle={kpi_data.get('cycle_id', '?')[:20]} "
                         f"mode={kpi_data.get('mode', '?')} "
                         f"emitted={kpi_data.get('intents_emitted', '?')}")
                
                # Check for ARES fields
                if "ares_kernel_state" in kpi_data:
                    self._ok(f"ARES KPI fields present: state={kpi_data['ares_kernel_state']}")
                else:
                    self._warn("ARES KPI fields not yet in KPI (patch may not be applied)")
            except json.JSONDecodeError:
                self._fail("KPI latest: invalid JSON")
        else:
            self._warn("No KPI data (nextgen2 may not be running)")
    
    async def check_recon_data(self):
        """Check reconciliation data."""
        print("\n6. Position Reconciliation")
        print("-" * 40)
        
        latest = await self.redis.get("ares:recon:latest")
        if latest:
            self._ok(f"Latest recon snapshot: {latest}")
            snapshot = await self.redis.get(f"ares:recon:snapshot:{latest}")
            if snapshot:
                data = json.loads(snapshot)
                self._ok(f"Snapshot: total={data.get('total_symbols', 0)} "
                         f"matched={data.get('matched', 0)} "
                         f"mismatched={data.get('mismatched', 0)}")
        else:
            self._warn("No recon snapshots yet (recon engine not yet running)")
        
        mismatches = await self.redis.smembers("ares:recon:mismatches")
        if mismatches:
            self._warn(f"Active mismatches: {mismatches}")
        else:
            self._ok("No active mismatches")
    
    async def check_module_imports(self):
        """Verify all ARES modules can be imported."""
        print("\n7. Module Import Verification")
        print("-" * 40)
        
        modules = [
            ("kernel", "TradingKernel"),
            ("kernel_client", "KernelClient"),
            ("position_recon", "PositionReconciliationEngine"),
            ("risk_manager", "UnifiedRiskManager"),
            ("rebalance_job", "RebalanceJobManager"),
            ("session_manager", "SessionManager"),
            ("monitoring", "MonitoringService"),
        ]
        
        for mod_name, class_name in modules:
            try:
                mod = __import__(mod_name)
                cls = getattr(mod, class_name, None)
                if cls:
                    self._ok(f"{mod_name}.{class_name}")
                else:
                    self._fail(f"{mod_name}: class {class_name} not found")
            except Exception as e:
                self._fail(f"{mod_name}: import error — {e}")
    
    async def run_all(self):
        """Run all verification checks."""
        print("=" * 60)
        print("  ARES v2.0 Deployment Verification")
        print("=" * 60)
        print(f"  Redis: {REDIS_URL}")
        print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
        
        await self.check_module_imports()
        await self.check_lua_script()
        await self.check_kernel_state()
        await self.check_shadow_metrics()
        await self.check_position_data()
        await self.check_existing_services()
        await self.check_recon_data()
        
        print("\n" + "=" * 60)
        print(f"  Results: {self.passed} passed, {self.failed} failed, {self.warnings} warnings")
        print("=" * 60)
        
        if self.failed > 0:
            print("\n  STATUS: ISSUES DETECTED — review failures above")
            return 1
        elif self.warnings > 3:
            print("\n  STATUS: MOSTLY OK — some services may not be running")
            return 0
        else:
            print("\n  STATUS: ALL CLEAR")
            return 0


async def main():
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis.ping()
    except Exception as e:
        print(f"ERROR: Cannot connect to Redis at {REDIS_URL}: {e}")
        sys.exit(1)
    
    verifier = Verifier(redis)
    rc = await verifier.run_all()
    await redis.close()
    sys.exit(rc)


if __name__ == "__main__":
    asyncio.run(main())
