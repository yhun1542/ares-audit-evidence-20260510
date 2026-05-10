#!/usr/bin/env node
// ARES Structural Patch 2026-04-29
// Runtime/SSOT/universe identity registry. Writes ops:identity:current for observability.

// === ARES_STRUCT_FIX_20260508: SSOT auto-load + fail-fast ===
import _fs from 'node:fs';
(function(){
  if (process.env.REDIS_URL || process.env.ARES_REDIS_URL) return;
  const ssot = process.env.ARES_REDIS_SSOT || '/etc/ares/redis.env';
  if (_fs.existsSync(ssot)) {
    const raw = _fs.readFileSync(ssot, 'utf8');
    for (const ln of raw.split('\n')) {
      const t = ln.trim();
      if (!t || t.startsWith('#') || !t.includes('=')) continue;
      const i = t.indexOf('=');
      const k = t.slice(0,i).trim();
      const v = t.slice(i+1).trim().replace(/^['"]|['"]$/g,'');
      if (!process.env[k]) process.env[k] = v;
    }
  }
  if (!process.env.REDIS_URL && !process.env.ARES_REDIS_URL) {
    console.error('[STRUCT_FIX] REDIS_URL missing — refusing to start (fail-fast)');
    process.exit(78);
  }
})();
// === END ARES_STRUCT_FIX_20260508 ===
import Redis from 'ioredis';
const redis = new Redis(process.env.REDIS_URL || process.env.ARES_REDIS_URL, { connectionName: 'ares-identity-registry', maxRetriesPerRequest: 3 });
const INTERVAL_MS = Number(process.env.IDENTITY_REGISTRY_INTERVAL_MS || 60000);
function safeJson(x,d=null){ try{return x?JSON.parse(x):d;}catch{return d;} }
function log(level,msg,data={}){console.log(JSON.stringify({ts:new Date().toISOString(),svc:'ares-identity-registry',level,msg,...data}));}
async function scard(k){ try{ return await redis.scard(k); }catch{return null;} }
async function cycle(){
  const [champion, active, ssotRaw, stableRaw, stagedRaw, profile] = await Promise.all([
    redis.get('champion:version').catch(()=>null), redis.get('strategy:active_version').catch(()=>null),
    redis.get('ssot:target:v2:current').catch(()=>null), redis.get('ssot:champion:stable').catch(()=>null),
    redis.get('ssot:champion:staged').catch(()=>null), redis.get('policy:universe:active_profile').catch(()=>null)
  ]);
  const ssot=safeJson(ssotRaw,{}), stable=safeJson(stableRaw,{}), staged=safeJson(stagedRaw,{});
  const ssotMeta = ssot?.targets?.meta || ssot?.meta || {};
  const universe = { profile, policy_count: await scard('policy:universe:symbols'), champion_count: await scard('champion:universe:global'), champion21_count: await scard('universe:champion21') };
  const split = [];
  if (champion && ssotMeta.champion_version && champion !== ssotMeta.champion_version) split.push('runtime_champion_vs_ssot_champion');
  if (active && champion && active !== champion) split.push('strategy_active_vs_champion_version');
  const payload = { schema:'ares.identity.current.v1', ts:new Date().toISOString(), runtime:{champion_version:champion, strategy_active_version:active}, ssot:{engine_version:ssot.engine_version, champion_version:ssotMeta.champion_version, champion_strategy_name:ssotMeta.champion_strategy_name, engine_router_note:ssotMeta.engine_router_note}, stable, staged, universe, status: split.length?'WARN':'OK', split_brain: split };
  await redis.set('ops:identity:current', JSON.stringify(payload), 'EX', 120);
  await redis.xadd('ops:identity:audit','MAXLEN','~','5000','*','json',JSON.stringify(payload));
  if (split.length) await redis.xadd('ops:identity:alerts','MAXLEN','~','5000','*','json',JSON.stringify(payload));
  log(split.length?'warn':'info','identity_snapshot',payload);
}
async function main(){ await cycle(); setInterval(()=>cycle().catch(e=>log('error','cycle_failed',{err:String(e)})), INTERVAL_MS); }
main().catch(e=>{log('error','fatal',{err:String(e?.stack||e)});process.exit(1);});
