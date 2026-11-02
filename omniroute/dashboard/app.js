'use strict';

const REFRESH_MS = 60000;
const PAGE_ROWS = 50;
const SEVERITY_KO = { quota: '할당량', auth: '인증', config: '설정', transient: '일시적', client: '요청 형식', unknown: '미분류' };
const STATE_KO = { ok: '정상', limited: '속도 제한 중', error: '오류', inactive: '비활성', unknown: '알 수 없음' };
const MODEL_COLUMNS = [
  ['model', '모델', m => `${m.provider}/${m.model}`],
  ['requests', '요청', m => m.requests],
  ['ok_rate', '성공률', m => m.ok_rate],
  ['tok_in', '입력', m => m.tok_in],
  ['tok_cache_read', '캐시', m => m.tok_cache_read],
  ['tok_out', '출력', m => m.tok_out],
  ['tok_reasoning', '추론', m => m.tok_reasoning],
  ['p50_latency_ms', '지연 p50 / p95', m => m.p50_latency_ms],
  ['quota', '할당량', m => (m.quota ? m.quota.remaining_pct : null)],
  ['last_used', '마지막 사용', m => m.last_used],
];

const state = {
  range: '24h',
  data: {},
  sortKey: 'requests',
  sortDir: -1,
  filter: '',
  rows: PAGE_ROWS,
  openErrors: new Set(),
  charts: {},
};

const nf = new Intl.NumberFormat('ko-KR');
const cf = new Intl.NumberFormat('ko-KR', { notation: 'compact', maximumFractionDigits: 1 });
const fmt = n => (n == null ? '–' : nf.format(n));
const fmtC = n => (n == null ? '–' : cf.format(n));
const fmtPct = r => (r == null ? '–' : `${(r * 100).toFixed(1)}%`);
const fmtMs = v => (v == null ? '–' : v >= 1000 ? `${(v / 1000).toFixed(1)}초` : `${v}ms`);
const kstFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
});
const kst = iso => {
  if (!iso) return '–';
  const p = Object.fromEntries(kstFmt.formatToParts(new Date(iso)).map(x => [x.type, x.value]));
  return `${p.month}/${p.day} ${p.hour}:${p.minute}`;
};

function ago(iso) {
  if (!iso) return '아직 없음';
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 90) return `${s}초 전`;
  if (s < 5400) return `${Math.round(s / 60)}분 전`;
  return `${Math.round(s / 3600)}시간 전`;
}

function untilReset(sec) {
  if (sec == null) return '리셋 시각 모름';
  if (sec <= 0) return '리셋 시각 지남';
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return `리셋까지 ${d}일 ${h}시간`;
  if (h) return `리셋까지 ${h}시간 ${m}분`;
  return `리셋까지 ${m}분`;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  const add = kid => {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) kid.forEach(add);
    else el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  };
  kids.forEach(add);
  return el;
}

const $ = sel => document.querySelector(sel);

function box(el, ...kids) { el.replaceChildren(...kids); }
function emptyBox(msg) { return h('div', { class: 'state-box' }, msg); }
function errorBox(err, retry) {
  return h('div', { class: 'state-box err', role: 'alert' }, `불러오지 못했습니다: ${err.message || err}`,
    h('button', { type: 'button', class: 'ghost', onclick: retry }, '다시 시도'));
}

async function getJSON(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path} 응답 ${res.status}`);
  return res.json();
}

function qClass(q) {
  if (!q || q.remaining_pct == null) return 'q-na';
  if (q.exhausted || q.remaining_pct < 20) return 'q-bad';
  if (q.remaining_pct < 50) return 'q-warn';
  return 'q-good';
}

function gauge(pct, cls) {
  const v = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return h('div', { class: `gauge ${cls}`, role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(v) },
    h('i', { style: { width: `${v}%` } }));
}

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function themeColors() {
  return {
    ink: cssVar('--ink-2'), muted: cssVar('--muted'), grid: cssVar('--grid'), surface: cssVar('--surface'),
    series: [cssVar('--s1'), cssVar('--s2'), cssVar('--s3'), cssVar('--s4')], crit: cssVar('--crit'),
  };
}
function alpha(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}
function mkChart(key, canvas, config) {
  if (state.charts[key]) state.charts[key].destroy();
  state.charts[key] = new Chart(canvas, config);
}
function chartBox(cls) {
  const canvas = h('canvas');
  return [h('div', { class: `chart-box ${cls || ''}` }, canvas), canvas];
}
function chartDefaults(c) {
  Chart.defaults.color = c.muted;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
}
const noChartLib = () => emptyBox('차트 라이브러리(Chart.js)를 불러오지 못해 차트를 표시하지 못했습니다. 표와 숫자는 정상입니다.');

function renderKpi(d) {
  const tile = (label, value, sub) => h('div', { class: 'tile' },
    h('div', { class: 'label' }, label), h('div', { class: 'value' }, value), h('div', { class: 'sub' }, sub || ' '));
  box($('#kpi'),
    tile('요청 수', fmt(d.requests), '업스트림 시도 기준'),
    tile('성공률', fmtPct(d.ok_rate), `실패 ${fmt(d.requests - Math.round((d.ok_rate || 0) * d.requests))}건`),
    tile('입력 토큰', fmtC(d.tok_in), `새 입력 ${fmtC(d.tok_in - d.tok_cache_read)} + 캐시`),
    tile('캐시 토큰', fmtC(d.tok_cache_read), `입력 중 ${fmtPct(d.cache_ratio)}가 캐시`),
    tile('출력 토큰', fmtC(d.tok_out), fmt(d.tok_out)),
    tile('추론 토큰', fmtC(d.tok_reasoning), '대부분 출력에 포함'),
    tile('중간 지연', fmtMs(d.p50_latency_ms), '성공한 요청의 p50'));
  const err = d.last_collect_error ? ` · 수집 오류: ${d.last_collect_error}` : '';
  $('#collected').textContent = `마지막 수집 ${kst(d.last_collect_at)} (${ago(d.last_collect_at)}) · 데이터 시작 ${kst(d.data_since)}${err}`;
}

function bucketLabel(t, kind) {
  return kind === 'day' ? t.slice(5) : `${t.slice(5, 10)} ${t.slice(11, 13)}시`;
}

function renderTokens(d, el) {
  if (!d.points.length || d.points.every(p => !p.requests)) return box(el, emptyBox('이 기간에는 사용 기록이 없습니다.'));
  if (!window.Chart) return box(el, noChartLib());
  const c = themeColors();
  chartDefaults(c);
  const labels = d.points.map(p => bucketLabel(p.t, d.bucket));
  // input already contains cache reads and output already contains reasoning, so stacking those would double count
  const spec = [['새 입력', p => p.tok_in - p.tok_cache_read], ['캐시', p => p.tok_cache_read], ['출력', p => p.tok_out]];
  const [wrap, canvas] = chartBox();
  const [wrap2, canvas2] = chartBox('small');
  box(el, wrap, h('div', { class: 'chart-label' }, '오류 수 (실패한 업스트림 시도)'), wrap2);
  mkChart('tokens', canvas, {
    type: 'line',
    data: {
      labels,
      datasets: spec.map(([label, key], i) => ({
        label, data: d.points.map(key), fill: i === 0 ? 'origin' : '-1',
        backgroundColor: alpha(c.series[i], 0.55), borderColor: c.series[i], borderWidth: 1.5, tension: 0.25,
        pointRadius: 0, pointHoverRadius: 4,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: innerWidth < 600 ? 4 : 8, maxRotation: 0 } },
        y: { stacked: true, grid: { color: c.grid }, border: { display: false }, ticks: { callback: v => fmtC(v) } },
      },
      plugins: {
        legend: { position: 'top', align: 'start', labels: { usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 10, boxHeight: 10, color: c.ink } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}` } },
      },
    },
  });
  mkChart('errors-ts', canvas2, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: '오류', data: d.points.map(p => p.errors), fill: 'origin', backgroundColor: alpha(c.crit, 0.2),
        borderColor: c.crit, borderWidth: 1.5, tension: 0.25, pointRadius: 0, pointHoverRadius: 4,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: innerWidth < 600 ? 4 : 8, maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: c.grid }, border: { display: false }, ticks: { precision: 0 } },
      },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `오류 ${fmt(ctx.parsed.y)}건` } } },
    },
  });
}

function stateBadge(conn) {
  const st = conn ? conn.status : 'unknown';
  const tip = conn && conn.last_error ? `${STATE_KO[st]} · 마지막 오류: ${conn.last_error}` : STATE_KO[st];
  return h('div', { class: 'state', title: tip }, h('span', { class: `dot ${st}` }), STATE_KO[st] || st);
}

function quotaCell(q) {
  if (!q) return h('div', { class: 'qcell' }, h('div', { class: 'qtxt' }, '할당량 정보 없음'));
  const cls = qClass(q);
  const txt = q.exhausted ? '소진' : `${q.remaining_pct == null ? '–' : q.remaining_pct.toFixed(0)}% 남음`;
  return h('div', { class: `qcell ${cls}`, title: `창: ${q.window_key}${q.model_specific ? '' : ' (이 계정의 가장 적게 남은 창)'}` },
    gauge(q.remaining_pct, cls), h('div', { class: 'qtxt' }, h('b', null, txt), ` · ${kst(q.next_reset)} 리셋`));
}

function memberRow(m, maxReq) {
  const tokens = `입력 ${fmtC(m.tok_in)} · 캐시 ${fmtC(m.tok_cache_read)} · 출력 ${fmtC(m.tok_out)} · 추론 ${fmtC(m.tok_reasoning)}`;
  return h('div', { class: 'member' },
    h('span', { class: 'rank' }, m.rank),
    h('div', { class: 'mname' }, h('b', null, m.model), h('small', null, m.provider)),
    h('div', { class: 'use' },
      h('div', { class: 'use-bar', title: `${fmt(m.requests_24h)}회` }, h('i', { style: { width: `${maxReq ? (m.requests_24h / maxReq) * 100 : 0}%` } })),
      h('div', { class: 'use-txt' }, m.requests_24h ? `${fmt(m.requests_24h)}회 · ${tokens}` : '24시간 동안 사용 없음')),
    h('div', { class: 'succ' }, m.requests_24h ? fmtPct(m.ok_rate) : '–'),
    quotaCell(m.quota),
    stateBadge(m.connection));
}

function renderCandidates(d, el) {
  if (!d.combos.length && !d.picks.length) return box(el, emptyBox('후보 정보가 아직 수집되지 않았습니다.'));
  const cards = d.combos.map(combo => {
    const maxReq = Math.max(0, ...combo.members.map(m => m.requests_24h));
    return h('div', { class: 'combo' }, h('h3', null, combo.name, ' ', h('small', null, `후보 ${combo.members.length}개, 위쪽이 우선`)),
      combo.members.map(m => memberRow(m, maxReq)));
  });
  const picks = d.picks.length
    ? h('div', { class: 'picks' }, h('h3', null, 'autotune 선택 ', h('small', null, `${d.picks.length}개`)),
      h('div', { class: 'pick-grid' }, d.picks.map(m => {
        const cls = qClass(m.quota);
        const q = m.quota ? (m.quota.exhausted ? '소진' : `할당량 ${m.quota.remaining_pct == null ? '–' : m.quota.remaining_pct.toFixed(0)}%`) : '할당량 정보 없음';
        return h('div', { class: `pick ${cls}` },
          h('div', { class: 'mname' }, h('b', null, m.model)),
          h('div', { class: 'pline' }, `${m.requests_24h ? `${fmt(m.requests_24h)}회 · 성공 ${fmtPct(m.ok_rate)}` : '24시간 사용 없음'} · `,
            h('span', { style: { color: 'var(--c)' } }, q)),
          h('div', { style: { marginTop: '6px' } }, stateBadge(m.connection)));
      })))
    : null;
  box(el, h('div', { class: 'combo-grid' }, cards), picks);
}

function sparkline(history, cls) {
  const W = 84, H = 24, ns = 'http://www.w3.org/2000/svg', n = 60;
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', W);
  svg.setAttribute('height', H);
  svg.setAttribute('class', `spark ${cls}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', '최근 7일 남은 비율');
  if (history.length < 2) return svg;
  const pts = history.map(([b, v]) => `${((b / (n - 1)) * (W - 2) + 1).toFixed(1)},${(H - 2 - (Math.max(0, Math.min(100, v)) / 100) * (H - 4)).toFixed(1)}`);
  const line = document.createElementNS(ns, 'polyline');
  line.setAttribute('points', pts.join(' '));
  line.setAttribute('fill', 'none');
  line.setAttribute('stroke', 'var(--c)');
  line.setAttribute('stroke-width', '1.5');
  line.setAttribute('stroke-linejoin', 'round');
  svg.append(line);
  return svg;
}

function quotaRow(i) {
  const cls = qClass(i);
  const reset = i.exhausted && i.reset_in_sec != null && i.reset_in_sec <= 0 ? '소진됐으나 리셋 시각 지남 (갱신 대기)' : untilReset(i.reset_in_sec);
  return h('div', { class: `qrow ${cls}` },
    h('div', { class: 'wk' }, i.window_key),
    h('div', { class: 'gline' }, gauge(i.remaining_pct, cls),
      h('b', null, i.exhausted ? '소진' : i.remaining_pct == null ? '–' : `${i.remaining_pct.toFixed(0)}%`)),
    sparkline(i.history, cls),
    h('div', { class: 'rs' }, `${reset}${i.next_reset ? ` (${kst(i.next_reset)})` : ''}${i.stale ? ' · 오래된 값' : ''}`));
}

function acctCards(items) {
  const byConn = new Map();
  for (const i of items) {
    if (!byConn.has(i.connection_id)) byConn.set(i.connection_id, []);
    byConn.get(i.connection_id).push(i);
  }
  return h('div', { class: 'quota-grid' }, [...byConn.values()].map(rows => h('div', { class: 'acct' },
    h('h3', null, rows[0].label, ' ', h('small', null, rows[0].provider)), rows.map(quotaRow))));
}

function renderQuota(d, el) {
  const fresh = d.items.filter(i => !i.stale), stale = d.items.filter(i => i.stale);
  if (!d.items.length) return box(el, emptyBox('할당량 기록이 아직 없습니다.'));
  const low = fresh.filter(i => i.exhausted || (i.remaining_pct != null && i.remaining_pct < 20)).length;
  const mid = fresh.filter(i => !i.exhausted && i.remaining_pct != null && i.remaining_pct >= 20 && i.remaining_pct < 50).length;
  box(el,
    h('div', { class: 'sum-line' }, `최근 24시간 안에 갱신된 창 ${fresh.length}개 중 소진·20% 미만 ${low}개, 20~50% ${mid}개, 나머지는 50% 이상입니다.`),
    fresh.length ? acctCards(fresh) : emptyBox('최근 24시간 안에 갱신된 할당량이 없습니다.'),
    stale.length ? h('details', { class: 'more' }, h('summary', null, `오래된 창 ${stale.length}개 보기 (24시간 넘게 갱신 없음)`), acctCards(stale)) : null);
}

function badgesOf(m) {
  const list = m.candidates.slice(0, 2).map(c => h('span', { class: 'chip', title: `${c.combo} ${c.rank}순위` }, `${c.combo} #${c.rank}`));
  if (m.candidates.length > 2) list.push(h('span', { class: 'chip' }, `+${m.candidates.length - 2}`));
  return list;
}

function renderModels(d, el) {
  if (!d.models.length) return box(el, emptyBox('이 기간에는 사용한 모델이 없습니다.'));
  const count = h('span', { class: 'muted' });
  const input = h('input', {
    type: 'search', placeholder: '모델·공급자·콤보 이름으로 거르기', 'aria-label': '모델 거르기', value: state.filter,
    oninput: e => { state.filter = e.target.value; state.rows = PAGE_ROWS; paint(); },
  });
  const head = h('tr', null, MODEL_COLUMNS.map(([key, label]) => h('th', {
    tabindex: 0, 'data-key': key, 'aria-sort': 'none',
    onclick: () => sortBy(key), onkeydown: e => { if (e.key === 'Enter') sortBy(key); },
  }, label)));
  const tbody = h('tbody');
  const more = h('button', { type: 'button', class: 'more-btn', onclick: () => { state.rows += PAGE_ROWS; paint(); } });
  box(el, h('div', { class: 'toolbar' }, input, count),
    h('div', { class: 'table-wrap' }, h('table', null, h('thead', null, head), tbody)), more);

  function sortBy(key) {
    state.sortDir = state.sortKey === key ? -state.sortDir : (key === 'model' ? 1 : -1);
    state.sortKey = key;
    paint();
  }

  function paint() {
    const q = state.filter.trim().toLowerCase();
    let rows = d.models.filter(m => !q || `${m.provider}/${m.model} ${m.candidates.map(c => c.combo).join(' ')}`.toLowerCase().includes(q));
    const get = MODEL_COLUMNS.find(c => c[0] === state.sortKey)[2];
    rows = rows.slice().sort((a, b) => {
      const x = get(a), y = get(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * state.sortDir;
    });
    const maxOf = key => Math.max(1, ...rows.map(m => m[key] || 0));
    const max = { requests: maxOf('requests'), tok_in: maxOf('tok_in'), tok_cache_read: maxOf('tok_cache_read'), tok_out: maxOf('tok_out'), tok_reasoning: maxOf('tok_reasoning') };
    const barCell = (key, color, value) => h('td', { class: 'num bar-cell' },
      h('i', { class: 'b', style: { width: `${(value / max[key]) * 100}%`, background: color } }), h('span', null, fmtC(value)));
    const shown = rows.slice(0, state.rows);
    tbody.replaceChildren(...shown.map(m => h('tr', null,
      h('td', { class: 'model' }, h('b', null, m.model), h('small', null, ` ${m.provider}`), h('div', null, badgesOf(m))),
      h('td', { class: 'num bar-cell', title: fmt(m.requests) }, h('i', { class: 'b', style: { width: `${(m.requests / max.requests) * 100}%`, background: 'var(--s1)' } }), h('span', null, fmt(m.requests))),
      h('td', { class: 'num bar-cell' }, h('i', { class: 'b', style: { width: `${(m.ok_rate || 0) * 100}%`, background: 'var(--good)' } }), h('span', null, fmtPct(m.ok_rate))),
      barCell('tok_in', 'var(--s1)', m.tok_in),
      barCell('tok_cache_read', 'var(--s2)', m.tok_cache_read),
      barCell('tok_out', 'var(--s3)', m.tok_out),
      barCell('tok_reasoning', 'var(--s4)', m.tok_reasoning),
      h('td', { class: 'num' }, `${fmtMs(m.p50_latency_ms)} / ${fmtMs(m.p95_latency_ms)}`),
      h('td', { class: `num ${qClass(m.quota)}`, title: m.quota ? `창: ${m.quota.window_key}` : '' },
        m.quota ? h('b', { style: { color: 'var(--c)' } }, m.quota.exhausted ? '소진' : `${m.quota.remaining_pct.toFixed(0)}%`) : '–'),
      h('td', { class: 'num' }, kst(m.last_used)))));
    count.textContent = `${fmt(rows.length)}개 중 ${fmt(shown.length)}개 표시`;
    more.hidden = shown.length >= rows.length;
    more.textContent = `더 보기 (+${PAGE_ROWS})`;
    head.querySelectorAll('th').forEach(th => {
      const on = th.dataset.key === state.sortKey;
      th.setAttribute('aria-sort', on ? (state.sortDir > 0 ? 'ascending' : 'descending') : 'none');
      th.textContent = MODEL_COLUMNS.find(c => c[0] === th.dataset.key)[1] + (on ? (state.sortDir > 0 ? ' ▲' : ' ▼') : '');
    });
    if (!shown.length) tbody.replaceChildren(h('tr', null, h('td', { colspan: MODEL_COLUMNS.length, style: { textAlign: 'left' } }, '조건에 맞는 모델이 없습니다.')));
  }
  paint();
}

function errorRow(k) {
  const open = state.openErrors.has(k.key);
  return h('tr', null,
    h('td', { class: 'cnt', 'data-label': '건수' }, fmt(k.count)),
    h('td', { 'data-label': '제목' }, h('b', null, k.title), ' ', h('span', { class: `chip sev-${k.severity}` }, SEVERITY_KO[k.severity] || k.severity),
      h('div', null, h('small', null, `${kst(k.first_seen)} ~ ${kst(k.last_seen)}`))),
    h('td', { class: 'cause', 'data-label': '원인 설명' }, k.cause),
    h('td', { class: 'action', 'data-label': '해결 방법' }, k.action),
    h('td', { 'data-label': '공급자별' }, k.providers.slice(0, 6).map(p => h('span', { class: 'chip' }, `${p.provider} ${fmt(p.count)}`)),
      k.providers.length > 6 ? h('span', { class: 'chip' }, `+${k.providers.length - 6}`) : null),
    h('td', { class: 'exs', 'data-label': '실제 메시지' }, h('details', {
      open, ontoggle: e => { if (e.target.open) state.openErrors.add(k.key); else state.openErrors.delete(k.key); },
    }, h('summary', null, `예시 ${k.examples.length}개`),
    k.examples.map(x => h('div', { class: 'ex' }, h('b', null, `${fmt(x.count)}회  `), x.text)))));
}

function renderErrors(d, el) {
  if (!d.kinds.length) {
    return box(el, emptyBox(d.calls_total ? '이 기간에는 오류가 없었습니다.' : `이 기간에는 요청 기록이 없습니다. 오류 본문은 ${kst(d.calls_since)}부터 보관됩니다.`));
  }
  const parts = [h('div', { class: 'sum-line' },
    `요청 ${fmt(d.calls_total)}건 중 ${fmt(d.total)}건(${fmtPct(d.calls_total ? d.total / d.calls_total : null)})이 오류로 끝났습니다. 오류 본문은 ${kst(d.calls_since)}부터 보관한 기록입니다. `,
    `업스트림 시도 단위로는 ${fmt(d.attempt_failures.total)}건이 실패했고, 그중 상당수는 재시도나 다음 후보 전환으로 해결됐습니다.`)];
  if (window.Chart) {
    const c = themeColors();
    chartDefaults(c);
    const [wrap, canvas] = chartBox();
    wrap.style.height = `${Math.max(120, d.kinds.length * 30 + 40)}px`;
    const [wrap2, canvas2] = chartBox('small');
    parts.push(wrap, h('div', { class: 'chart-label' }, '일별 오류 수 (한국 시간)'), wrap2);
    queueMicrotask(() => {
      const valueLabels = {
        id: 'valueLabels',
        afterDatasetsDraw(chart) {
          const { ctx } = chart;
          ctx.save();
          ctx.fillStyle = c.ink;
          ctx.textBaseline = 'middle';
          chart.getDatasetMeta(0).data.forEach((bar, i) => ctx.fillText(fmt(d.kinds[i].count), bar.x + 6, bar.y));
          ctx.restore();
        },
      };
      mkChart('error-kinds', canvas, {
        type: 'bar',
        data: { labels: d.kinds.map(k => k.title), datasets: [{ data: d.kinds.map(k => k.count), backgroundColor: c.series[0], borderRadius: 4, barThickness: 18 }] },
        options: {
          indexAxis: 'y', responsive: true, maintainAspectRatio: false, layout: { padding: { right: 48 } },
          scales: {
            x: { beginAtZero: true, grid: { color: c.grid }, border: { display: false } },
            y: { grid: { display: false }, ticks: { callback(v) { const l = this.getLabelForValue(v); return innerWidth < 600 && l.length > 11 ? `${l.slice(0, 10)}…` : l; } } },
          },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `${fmt(ctx.parsed.x)}건` } } },
        },
        plugins: [valueLabels],
      });
      mkChart('error-daily', canvas2, {
        type: 'bar',
        data: { labels: d.daily.map(x => x.day.slice(5)), datasets: [{ data: d.daily.map(x => x.count), backgroundColor: c.crit, borderRadius: 3, maxBarThickness: 40 }] },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: c.grid }, border: { display: false }, ticks: { precision: 0 } } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `오류 ${fmt(ctx.parsed.y)}건` } } },
        },
      });
    });
  } else {
    parts.push(noChartLib());
  }
  parts.push(h('div', { class: 'table-wrap', style: { marginTop: '14px' } }, h('table', { class: 'err-table' },
    h('thead', null, h('tr', null, ['건수', '제목', '원인 설명', '해결 방법', '공급자별', '실제 메시지'].map(t => h('th', null, t)))),
    h('tbody', null, d.kinds.map(errorRow)))));
  box(el, ...parts);
}

const SECTIONS = [
  ['overview', r => `/api/overview?range=${r}`, d => renderKpi(d), '#kpi'],
  ['timeseries', r => `/api/timeseries?range=${r}`, renderTokens, '#tokens-body'],
  ['candidates', () => '/api/candidates', renderCandidates, '#cand-body'],
  ['quota', () => '/api/quota', renderQuota, '#quota-body'],
  ['models', r => `/api/models?range=${r}`, renderModels, '#models-body'],
  ['errors', r => `/api/errors?range=${r}`, renderErrors, '#errors-body'],
];

async function loadSection([name, url, render, sel]) {
  const el = $(sel);
  try {
    const data = await getJSON(url(state.range));
    state.data[name] = data;
    render(data, el);
  } catch (err) {
    console.warn(name, err);
    box(el, errorBox(err, () => loadSection([name, url, render, sel])));
  }
}

function loadAll() {
  return Promise.all(SECTIONS.map(loadSection));
}

function repaintCharts() {
  for (const [name, , render, sel] of SECTIONS) {
    if (state.data[name] && (name === 'timeseries' || name === 'errors')) render(state.data[name], $(sel));
  }
}

function setRange(r) {
  state.range = r;
  state.rows = PAGE_ROWS;
  document.querySelectorAll('#range-tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.range === r)));
  try { localStorage.setItem('omni-dash-range', r); } catch (e) { /* storage may be blocked */ }
  loadAll();
}

function initTheme() {
  const btn = $('#theme');
  let theme = null;
  try { theme = localStorage.getItem('omni-dash-theme'); } catch (e) { /* storage may be blocked */ }
  const apply = t => {
    document.documentElement.dataset.theme = t;
    btn.textContent = t === 'dark' ? '☀' : '☾';
    btn.title = t === 'dark' ? '밝은 화면으로' : '어두운 화면으로';
  };
  apply(theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
  btn.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    apply(next);
    try { localStorage.setItem('omni-dash-theme', next); } catch (e) { /* storage may be blocked */ }
    repaintCharts();
  });
}

function init() {
  initTheme();
  let saved = null;
  try { saved = localStorage.getItem('omni-dash-range'); } catch (e) { /* storage may be blocked */ }
  document.querySelectorAll('#range-tabs button').forEach(b => b.addEventListener('click', () => setRange(b.dataset.range)));
  setRange(['24h', '7d', '30d', 'all'].includes(saved) ? saved : '24h');
  setInterval(loadAll, REFRESH_MS);
}

init();
