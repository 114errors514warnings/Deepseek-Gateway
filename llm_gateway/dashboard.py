"""极简 Dashboard（V2）：单页 HTML，自取 /metrics 与 /v1/usage，无外部依赖。"""
from __future__ import annotations

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>LLM Gateway · Dashboard</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0; padding: 24px; background:#0f1420; color:#e6ecf5; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color:#8b9bb4; font-size: 13px; margin-bottom: 20px; }
  .cards { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap:12px; }
  .card { background:#182034; border:1px solid #26324a; border-radius:10px; padding:14px; }
  .card .k { color:#8b9bb4; font-size:12px; }
  .card .v { font-size:22px; font-weight:600; margin-top:6px; }
  section { margin-top:24px; }
  h2 { font-size:15px; color:#c7d3e6; border-left:3px solid #4f8cff; padding-left:8px; }
  table { width:100%; border-collapse: collapse; font-size:13px; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid #26324a; }
  th { color:#8b9bb4; font-weight:500; }
  .ok { color:#5dd39e; } .bad { color:#ff7b7b; }
  .muted { color:#8b9bb4; }
  button { background:#26324a; color:#e6ecf5; border:1px solid #35455f; border-radius:7px; padding:6px 12px; cursor:pointer; }
  #spark { width:100%; height:80px; }
</style>
</head>
<body>
  <h1>LLM Gateway · Dashboard</h1>
  <div class="sub">本地网关可观测面板 · 数据来自 SQLite 计量表 · <button onclick="refresh()">刷新</button></div>

  <div class="cards" id="cards"></div>

  <section>
    <h2>成本 / 成功率 时间序列</h2>
    <svg id="spark" viewBox="0 0 600 80" preserveAspectRatio="none"></svg>
  </section>

  <section>
    <h2>档位分布</h2>
    <table id="effort"><thead><tr><th>effort</th><th>请求</th><th>成功</th><th>成本 (USD)</th></tr></thead><tbody></tbody></table>
  </section>

  <section>
    <h2>错误 Top-N</h2>
    <table id="errors"><thead><tr><th>error_type</th><th>次数</th></tr></thead><tbody></tbody></table>
  </section>

  <section>
    <h2>最近请求</h2>
    <table id="recent"><thead><tr><th>时间</th><th>effort</th><th>路由原因</th><th>token(in/out)</th><th>成本</th><th>延迟</th><th>状态</th></tr></thead><tbody></tbody></table>
  </section>

<script>
const $ = (id) => document.getElementById(id);
const fmt = (v, d=2) => (v === null || v === undefined || v === '') ? '—' : Number(v).toFixed(d);
const pct = (v) => (v === null || v === undefined) ? '—' : (Number(v) * 100).toFixed(1) + '%';

async function refresh() {
  const m = await (await fetch('/metrics')).json();
  const cards = [
    ['总请求', m.requests],
    ['成功率', pct(m.success_rate)],
    ['总成本 (USD)', fmt(m.cost_usd, 4)],
    ['单次成本', fmt(m.cost_per_request_usd, 5)],
    ['缓存命中率', pct(m.cache_hit_rate)],
    ['重试率', pct(m.retry_rate)],
    ['P50 (ms)', fmt(m.p50_latency_ms, 0)],
    ['P95 (ms)', fmt(m.p95_latency_ms, 0)],
    ['今日花费', fmt(m.spend_today_usd, 4)],
  ];
  $('cards').innerHTML = cards.map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

  const rows = (m.by_effort || []).map(r =>
    `<tr><td>${r.effort ?? '—'}</td><td>${r.requests ?? 0}</td><td>${r.successes ?? 0}</td><td>${fmt(r.cost_usd, 4)}</td></tr>`).join('');
  $('effort').querySelector('tbody').innerHTML = rows || '<tr><td colspan="4" class="muted">暂无数据</td></tr>';

  const errs = (m.errors || []).map(r => `<tr><td>${r.error_type}</td><td>${r.count}</td></tr>`).join('');
  $('errors').querySelector('tbody').innerHTML = errs || '<tr><td colspan="2" class="muted">无错误</td></tr>';

  const series = m.time_series || [];
  drawSpark(series);

  const recent = (m.recent || []);
  $('recent').querySelector('tbody').innerHTML = recent.map(r => {
    const cls = r.success ? 'ok' : 'bad';
    return `<tr><td class="muted">${r.timestamp ?? ''}</td><td>${r.effort ?? '—'}</td><td class="muted">${r.routed_reason ?? ''}</td>`
      + `<td>${r.input_tokens ?? 0}/${r.output_tokens ?? 0}</td><td>${fmt(r.cost_usd, 5)}</td>`
      + `<td>${r.latency_ms ?? 0}</td><td class="${cls}">${r.success ? 'OK' : (r.error_type || 'FAIL')}</td></tr>`;
  }).join('') || '<tr><td colspan="7" class="muted">暂无数据</td></tr>';
}

function drawSpark(series) {
  const svg = $('spark');
  if (!series.length) { svg.innerHTML = ''; return; }
  const max = Math.max(...series.map(s => s.cost_usd || 0), 1e-9);
  const n = series.length;
  const step = n > 1 ? 600 / (n - 1) : 0;
  const pts = series.map((s, i) => `${i * step},${70 - (s.cost_usd / max) * 60}`).join(' ');
  svg.innerHTML = `<polyline fill="none" stroke="#4f8cff" stroke-width="2" points="${pts}" />`;
}

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""
