/* Switchboard 控制台 */
(function () {
  'use strict';

  // ---------------------------------------------------------------- 基础工具
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function fmtNum(n) {
    n = Number(n) || 0;
    if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (n >= 1e4) return (n / 1e4).toFixed(1) + '万';
    return n.toLocaleString('zh-CN');
  }
  function fmtTime(ts) {
    if (!ts) return '-';
    const d = new Date(ts * 1000);
    const p = (x) => String(x).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }
  function fmtAgo(ts) {
    if (!ts) return '从未';
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return '刚刚';
    if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    return Math.floor(s / 86400) + ' 天前';
  }

  // ---------------------------------------------------------------- Toast
  function toast(msg, type = 'ok', ms = 2600) {
    const el = document.createElement('div');
    el.className = 'toast ' + (type || 'ok');
    el.textContent = msg;
    $('#toastWrap').appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .25s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 250);
    }, ms);
  }

  // ---------------------------------------------------------------- API
  let adminToken = localStorage.getItem('ccproxy_admin_token') || '';

  async function api(path, opts = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    if (adminToken) headers['X-Admin-Token'] = adminToken;
    let res;
    try {
      res = await fetch(path, Object.assign({}, opts, { headers }));
    } catch (e) {
      throw new Error('无法连接服务：' + e.message);
    }
    if (res.status === 401) {
      const t = prompt('该服务已启用管理口令，请输入：');
      if (t !== null) {
        adminToken = t.trim();
        localStorage.setItem('ccproxy_admin_token', adminToken);
        return api(path, opts);
      }
      throw new Error('需要管理口令');
    }
    if (!res.ok) {
      let msg = res.status + ' ' + res.statusText;
      try {
        const j = await res.json();
        msg = j.detail || j.error?.message || msg;
      } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) return res.json();
    return res.text();
  }
  const GET = (p) => api(p);
  const POST = (p, b) => api(p, { method: 'POST', body: JSON.stringify(b || {}) });
  const PUT = (p, b) => api(p, { method: 'PUT', body: JSON.stringify(b || {}) });
  const DEL = (p) => api(p, { method: 'DELETE' });

  // ---------------------------------------------------------------- 视图切换
  const VIEWS = {
    dashboard: { title: '概览', sub: '服务运行状态与用量统计' },
    usage: { title: '用量', sub: '5 小时 / 7 天 / 30 天滚动窗口的 Token 与请求消耗' },
    upstreams: { title: '上游管理', sub: '配置 CommandCode / OpenAI 上游节点与故障切换' },
    mappings: { title: '模型映射', sub: '把客户端请求的模型别名映射到上游实际模型' },
    keys: { title: '客户端 Key', sub: '为本地工具分发独立密钥并限速' },
    logs: { title: '请求日志', sub: '每次转发的明细与统计' },
    settings: { title: '设置', sub: '服务参数、容错策略与配置备份' },
  };
  let currentView = 'dashboard';

  function switchView(v) {
    currentView = v;
    $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === v));
    $$('.view').forEach((s) => s.classList.add('hidden'));
    $('#view-' + v).classList.remove('hidden');
    $('#viewTitle').textContent = VIEWS[v].title;
    $('#viewSub').textContent = VIEWS[v].sub;
    loaders[v] && loaders[v]();
  }

  // ---------------------------------------------------------------- 图表
  function lineChart(el, buckets) {
    // buckets: [{bucket, n, ok_n}]，渲染近 24 小时柱状 + 折线
    const H = 24;
    const arr = new Array(H).fill(0);
    const okArr = new Array(H).fill(0);
    (buckets || []).forEach((b) => {
      const i = Math.min(H - 1, Math.max(0, Number(b.bucket) || 0));
      arr[i] += Number(b.n) || 0;
      okArr[i] += Number(b.ok_n) || 0;
    });
    const max = Math.max(1, ...arr);
    const W = 100, Ht = 100, pad = 4;
    const bw = (W - pad * 2) / H;
    const y = (v) => Ht - pad - (v / max) * (Ht - pad * 2 - 10);

    let bars = '';
    arr.forEach((v, i) => {
      const h = Math.max(v > 0 ? 1.2 : 0, (v / max) * (Ht - pad * 2 - 10));
      const x = pad + i * bw;
      bars += `<rect x="${x.toFixed(2)}" y="${(Ht - pad - h).toFixed(2)}" width="${(bw * 0.62).toFixed(2)}" height="${h.toFixed(2)}" rx="1" fill="url(#gradBar)" opacity="${v ? 0.85 : 0.15}"><title>${v} 次请求（成功 ${okArr[i]}）</title></rect>`;
    });
    let pts = arr.map((v, i) => `${(pad + i * bw + bw * 0.31).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
    let grid = '';
    [0, 0.5, 1].forEach((r) => {
      const gy = y(max * r);
      grid += `<line x1="${pad}" y1="${gy.toFixed(1)}" x2="${W - pad}" y2="${gy.toFixed(1)}" stroke="#222938" stroke-width="0.3" stroke-dasharray="2 2"/>`;
      grid += `<text x="${pad}" y="${(gy - 1.5).toFixed(1)}" font-size="4" fill="#5c6880">${Math.round(max * r)}</text>`;
    });
    let labels = '';
    for (let i = 0; i < H; i += 4) {
      labels += `<text x="${(pad + i * bw + bw * 0.31).toFixed(1)}" y="${Ht - 0.5}" font-size="3.6" fill="#5c6880" text-anchor="middle">-${H - i}h</text>`;
    }

    el.innerHTML = `<svg viewBox="0 0 ${W} ${Ht}" preserveAspectRatio="none">
      <defs><linearGradient id="gradBar" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#7e96ff"/><stop offset="100%" stop-color="#5468e8" stop-opacity="0.35"/>
      </linearGradient></defs>
      ${grid}${bars}
      <polyline points="${pts}" fill="none" stroke="#b48cf2" stroke-width="0.7" stroke-linejoin="round" opacity="0.9"/>
      ${labels}
    </svg>`;
  }

  function barChart(el, items) {
    const data = (items || []).slice(0, 6);
    if (!data.length) {
      el.innerHTML = '<div class="empty-chart">暂无数据</div>';
      return;
    }
    const max = Math.max(1, ...data.map((d) => Number(d.n) || 0));
    const rowH = 26, W = 100, Ht = data.length * rowH + 6, labelW = 26;
    let out = '';
    data.forEach((d, i) => {
      const name = (d.upstream_name || '未命名').slice(0, 12);
      const w = ((Number(d.n) || 0) / max) * (W - labelW - 16);
      const y = i * rowH + 6;
      const rate = d.n ? Math.round((d.ok_n / d.n) * 100) : 0;
      const color = rate >= 95 ? '#45c98a' : rate >= 80 ? '#e0b25a' : '#f2706b';
      out += `<text x="0" y="${y + 5}" font-size="5" fill="#93a0b8">${esc(name)}</text>
        <rect x="${labelW}" y="${y}" width="${Math.max(0.6, w).toFixed(2)}" height="9" rx="1.5" fill="${color}" opacity="0.75"><title>${esc(name)}: ${d.n} 次，成功率 ${rate}%</title></rect>
        <text x="${(labelW + w + 2.5).toFixed(2)}" y="${y + 7}" font-size="4.6" fill="#5c6880">${d.n} (${rate}%)</text>`;
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${Ht}" preserveAspectRatio="xMinYMin meet">${out}</svg>`;
  }

  // ---------------------------------------------------------------- 弹窗
  let modalSubmit = null;
  function openModal(title, html, onSubmit, wide) {
    $('#modalTitle').textContent = title;
    $('#modalBody').innerHTML = html;
    $('#modalMask').classList.remove('hidden');
    $('.modal').classList.toggle('modal-wide', !!wide);
    modalSubmit = onSubmit;
  }
  function closeModal() {
    $('#modalMask').classList.add('hidden');
    modalSubmit = null;
  }
  function formVal(id) {
    const el = $('#' + id);
    if (!el) return '';
    if (el.type === 'checkbox') return el.checked;
    return el.value.trim();
  }

  // ---------------------------------------------------------------- 概览
  async function loadDashboard() {
    try {
      const d = await GET('/api/overview');
      const s = d.stats;
      const cards = [
        { c: 'accent', l: '总请求数', v: fmtNum(s.total_requests), f: `近 24h ${fmtNum(s.requests_24h)} 次` },
        { c: s.success_rate >= 95 ? 'green' : s.success_rate >= 80 ? 'yellow' : 'red', l: '成功率', v: s.success_rate + '%', f: `失败 ${fmtNum(s.failed_requests)} 次` },
        { c: 'purple', l: '输入 Tokens', v: fmtNum(s.total_input_tokens), f: '累计' },
        { c: 'cyan', l: '输出 Tokens', v: fmtNum(s.total_output_tokens), f: '累计' },
        { c: 'yellow', l: '平均延迟', v: (s.avg_latency_ms || 0) + ' ms', f: '仅统计成功请求' },
        { c: 'green', l: '可用上游', v: `${d.enabled_upstream_count}/${d.upstream_count}`, f: `${d.mapping_count} 条映射 · ${d.key_count} 个 Key` },
      ];
      $('#statCards').innerHTML = cards.map((c) =>
        `<div class="card ${c.c}"><div class="label">${c.l}</div><div class="value">${esc(c.v)}</div><div class="foot">${esc(c.f)}</div></div>`
      ).join('');

      const tl = (d.timeline || []).length;
      $('#timelineChart').innerHTML = tl
        ? '' : '<div class="empty-chart">暂无请求数据，先用 Claude Code 发一条消息试试</div>';
      if (tl) lineChart($('#timelineChart'), d.timeline);
      barChart($('#upstreamChart'), s.by_upstream);

      const rows = (s.by_model || []);
      $('#modelTable').innerHTML =
        `<thead><tr><th>模型</th><th>请求数</th><th>输入 Tokens</th><th>输出 Tokens</th><th>占比</th></tr></thead><tbody>` +
        (rows.length ? rows.map((m) => {
          const total = rows.reduce((a, b) => a + Number(b.n || 0), 0) || 1;
          const pct = ((m.n / total) * 100).toFixed(1);
          return `<tr><td class="mono">${esc(m.request_model || '-')}</td><td>${fmtNum(m.n)}</td><td>${fmtNum(m.tin)}</td><td>${fmtNum(m.tout)}</td>
            <td><div style="display:flex;align-items:center;gap:8px"><div style="flex:1;height:5px;background:#181e2b;border-radius:3px;overflow:hidden;min-width:50px">
            <div style="width:${pct}%;height:100%;background:#7e96ff"></div></div><span style="font-size:12px;color:#93a0b8">${pct}%</span></div></td></tr>`;
        }).join('') : '<tr><td class="empty" colspan="5">暂无数据</td></tr>') + '</tbody>';
    } catch (e) {
      toast('加载概览失败：' + e.message, 'err');
    }
  }

  // ---------------------------------------------------------------- 用量
  const USAGE_WINDOWS = ['5h', '7d', '30d'];
  const USAGE_COLORS = { '5h': 'accent', '7d': 'purple', '30d': 'cyan' };
  let usagePayload = null;
  let usageWindow = '5h';

  async function loadUsage() {
    try {
      usagePayload = await GET('/api/usage');
      renderUsage();
    } catch (e) {
      toast('加载用量失败：' + e.message, 'err');
    }
  }

  function renderUsage() {
    const wins = (usagePayload && usagePayload.windows) || [];
    const byKey = {};
    wins.forEach((w) => { byKey[w.key] = w; });
    const hasAny = wins.some((w) => w.requests > 0);

    // ---- 每个窗口一张卡
    $('#usageCards').innerHTML = USAGE_WINDOWS.map((k) => {
      const w = byKey[k];
      if (!w) return '';
      const rate = w.requests ? w.success_rate + '%' : '—';
      return `<div class="card ${USAGE_COLORS[k]}">
        <div class="label">${esc(w.label)} Tokens</div>
        <div class="value">${fmtNum(w.total_tokens)}</div>
        <div class="foot">输入 ${fmtNum(w.input_tokens)} · 输出 ${fmtNum(w.output_tokens)} · ${fmtNum(w.requests)} 次请求 · 成功率 ${rate}</div>
      </div>`;
    }).join('');

    // ---- 窗口对比表
    $('#usageTable').innerHTML =
      `<thead><tr><th>窗口</th><th>请求数</th><th>成功 / 失败</th><th>成功率</th>
        <th>输入 Tokens</th><th>输出 Tokens</th><th>合计 Tokens</th><th>平均延迟</th><th>最近一次</th></tr></thead><tbody>` +
      (wins.length ? wins.map((w) => {
        const badge = w.requests === 0
          ? '<span class="badge gray">无数据</span>'
          : (w.success_rate >= 95 ? '<span class="badge green">' : w.success_rate >= 80 ? '<span class="badge yellow">' : '<span class="badge red">') + w.success_rate + '%</span>';
        return `<tr>
          <td style="font-weight:500">${esc(w.label)}</td>
          <td>${fmtNum(w.requests)}</td>
          <td><span style="color:var(--green)">${fmtNum(w.ok_requests)}</span> / <span style="color:${w.failed_requests ? 'var(--red)' : 'inherit'}">${fmtNum(w.failed_requests)}</span></td>
          <td>${badge}</td>
          <td>${fmtNum(w.input_tokens)}</td>
          <td>${fmtNum(w.output_tokens)}</td>
          <td style="font-weight:500">${fmtNum(w.total_tokens)}</td>
          <td>${w.requests ? (w.avg_latency_ms || 0) + ' ms' : '-'}</td>
          <td style="font-size:11.5px;color:var(--text-dim)">${esc(fmtAgo(w.last_ts))}</td>
        </tr>`;
      }).join('') : '<tr><td class="empty" colspan="9">暂无数据</td></tr>') + '</tbody>';

    // ---- 提示：日志保留天数会截断长窗口
    const ret = usagePayload ? Number(usagePayload.retention_days || 0) : 0;
    const parts = [];
    if (ret > 0 && ret < 30) {
      parts.push(`<span class="badge yellow">注意</span> 日志只保留 <code>${ret}</code> 天，` +
        `「近 30 天」窗口实际最多只能看到 ${ret} 天的数据。到「设置 → 日志保留天数」改成 ` +
        `<code>0</code>（永久）或 <code>30</code> 以上才能看满。`);
    } else {
      parts.push('三个窗口都是<b>滚动</b>统计（从当前时刻往前回溯），不是自然日 / 自然月。');
    }
    if (!hasAny) parts.push('还没有任何请求记录，先用客户端发一条消息。');
    $('#usageHint').innerHTML = parts.join('<br>');

    // ---- 选定窗口的模型明细
    $('#usageWindowSel').value = usageWindow;
    const w = byKey[usageWindow];
    const rows = (w && w.by_model) || [];
    const totalTok = rows.reduce((a, m) => a + Number(m.tin || 0) + Number(m.tout || 0), 0) || 1;
    $('#usageModelTable').innerHTML =
      `<thead><tr><th>模型</th><th>请求数</th><th>输入 Tokens</th><th>输出 Tokens</th><th>合计</th><th>占比</th></tr></thead><tbody>` +
      (rows.length ? rows.map((m) => {
        const tok = Number(m.tin || 0) + Number(m.tout || 0);
        const pct = (tok / totalTok) * 100;
        return `<tr>
          <td class="mono">${esc(m.model || '-')}</td>
          <td>${fmtNum(m.n)}</td>
          <td>${fmtNum(m.tin)}</td>
          <td>${fmtNum(m.tout)}</td>
          <td style="font-weight:500">${fmtNum(tok)}</td>
          <td><div style="display:flex;align-items:center;gap:8px">
            <div style="flex:1;height:5px;background:#181e2b;border-radius:3px;overflow:hidden;min-width:50px">
            <div style="width:${pct.toFixed(1)}%;height:100%;background:#7e96ff"></div></div>
            <span style="font-size:12px;color:#93a0b8">${pct.toFixed(1)}%</span></div></td>
        </tr>`;
      }).join('') : `<tr><td class="empty" colspan="6">${esc((w && w.label) || '该窗口')}内没有请求</td></tr>`) + '</tbody>';
  }

  // ---------------------------------------------------------------- 上游
  async function loadUpstreams() {
    try {
      const list = await GET('/api/upstreams');
      const rows = list.map((u) => {
        const st = u.in_cooldown
          ? '<span class="badge red">冷却中</span>'
          : (u.healthy ? '<span class="badge green">正常</span>' : '<span class="badge yellow">异常</span>');
        const en = u.enabled ? '<span class="badge green">启用</span>' : '<span class="badge gray">停用</span>';
        return `<tr>
          <td><div style="font-weight:500">${esc(u.name)}</div>
            <div style="font-size:11px;color:#5c6880;margin-top:2px">${esc(u.note || '')}</div></td>
          <td class="mono" style="max-width:210px;overflow:hidden;text-overflow:ellipsis">${esc(u.base_url)}</td>
          <td><span class="badge ${u.protocol === 'anthropic' ? 'purple' : 'blue'}">${u.protocol}</span></td>
          <td class="mono" style="font-size:11.5px">${esc(u.api_key_masked || '未设置')}</td>
          <td>${u.priority}</td>
          <td>${st} ${en}</td>
          <td style="font-size:11.5px;color:#93a0b8">
            ${u.total_requests} 次 / 失败 ${u.failed_requests}<br>
            <span style="color:#5c6880">${u.last_latency_ms ? u.last_latency_ms + 'ms' : '-'}</span>
          </td>
          <td>${esc(u.last_error || '').slice(0, 60) || '<span style="color:#5c6880">-</span>'}</td>
          <td><div class="actions">
            <button class="btn ghost sm" data-act="edit" data-id="${u.id}">编辑</button>
            <button class="btn ghost sm" data-act="test" data-id="${u.id}">测试</button>
            <button class="btn ghost sm" data-act="health" data-id="${u.id}">体检</button>
            <button class="btn ghost sm" data-act="models" data-id="${u.id}">模型</button>
            <button class="btn ghost sm" data-act="reset" data-id="${u.id}">重置</button>
            <button class="btn danger sm" data-act="del" data-id="${u.id}">删除</button>
          </div></td></tr>`;
      }).join('');
      $('#upstreamTable').innerHTML =
        `<thead><tr><th>名称</th><th>Base URL</th><th>协议</th><th>API Key</th><th>优先级</th><th>状态</th><th>统计</th><th>最近错误</th><th>操作</th></tr></thead>
         <tbody>${rows || '<tr><td class="empty" colspan="9">还没有上游节点，点击右上角「新增上游」</td></tr>'}</tbody>`;

      $$('#upstreamTable [data-act]').forEach((b) => {
        b.onclick = () => {
          const act = b.dataset.act, id = b.dataset.id;
          const u = list.find((x) => x.id === id);
          if (act === 'edit') return editUpstream(u);
          if (act === 'del') return delUpstream(u);
          if (act === 'test') return testUpstream(u);
          if (act === 'health') return healthUpstream(u);
          if (act === 'reset') return POST('/api/upstreams/' + id + '/reset').then(() => { toast('已重置节点状态'); loadUpstreams(); });
          if (act === 'models') return pickModels(u);
        };
      });
    } catch (e) {
      toast('加载上游失败：' + e.message, 'err');
    }
  }

  function upstreamForm(u) {
    u = u || {};
    return `<div class="form">
      <div class="row">
        <label>名称<input class="input" id="f_name" value="${esc(u.name || '')}" placeholder="如：CommandCode Goat 主节点"></label>
        <label>协议<select class="input" id="f_protocol">
          <option value="anthropic" ${u.protocol === 'anthropic' ? 'selected' : ''}>Anthropic（/v1/messages）</option>
          <option value="openai" ${u.protocol === 'openai' ? 'selected' : ''}>OpenAI（/v1/chat/completions）</option>
        </select></label>
      </div>
      <label>Base URL<input class="input" id="f_base_url" value="${esc(u.base_url || '')}" placeholder="https://api.commandcode.dev/v1"></label>
      <label>API Key / Token<input class="input" id="f_api_key" value="${esc(u.api_key || '')}" placeholder="sk-..."></label>
      <div class="row">
        <label>优先级（越小越优先）<input class="input" id="f_priority" type="number" value="${u.priority ?? 100}"></label>
        <label>权重（同优先级内）<input class="input" id="f_weight" type="number" value="${u.weight ?? 1}"></label>
      </div>
      <div class="row">
        <label>超时（秒）<input class="input" id="f_timeout" type="number" value="${u.timeout ?? 180}"></label>
        <label>最大重试次数<input class="input" id="f_max_retries" type="number" value="${u.max_retries ?? 2}"></label>
      </div>
      <label>支持的模型（逗号分隔，留空表示不限）<input class="input" id="f_models" value="${esc((u.models || []).join(','))}" placeholder="claude-sonnet-4-5,claude-opus-4-1"></label>
      <label>备注<input class="input" id="f_note" value="${esc(u.note || '')}"></label>
      <label class="check"><input type="checkbox" id="f_enabled" ${u.enabled === false ? '' : 'checked'}> 启用该节点</label>
    </div>`;
  }

  function editUpstream(u) {
    openModal(u ? '编辑上游' : '新增上游', upstreamForm(u), async () => {
      const data = {
        name: formVal('f_name') || '未命名上游',
        protocol: formVal('f_protocol'),
        base_url: formVal('f_base_url').replace(/\/+$/, ''),
        api_key: formVal('f_api_key'),
        priority: Number(formVal('f_priority')) || 100,
        weight: Number(formVal('f_weight')) || 1,
        timeout: Number(formVal('f_timeout')) || 180,
        max_retries: Number(formVal('f_max_retries')) || 0,
        models: formVal('f_models') ? formVal('f_models').split(',').map((s) => s.trim()).filter(Boolean) : [],
        note: formVal('f_note'),
        enabled: formVal('f_enabled'),
      };
      if (!data.base_url) return toast('Base URL 不能为空', 'err');
      if (u) await PUT('/api/upstreams/' + u.id, data);
      else await POST('/api/upstreams', data);
      toast('已保存', 'ok');
      closeModal();
      loadUpstreams();
    });
  }

  async function delUpstream(u) {
    if (!confirm(`确定删除上游「${u.name}」？`)) return;
    await DEL('/api/upstreams/' + u.id);
    toast('已删除');
    loadUpstreams();
  }

  async function testUpstream(u) {
    toast('正在测试连通性…', 'warn', 30000);
    try {
      const r = await POST('/api/upstreams/' + u.id + '/test', { model: (u.models || [])[0] || '' });
      const body = r.ok
        ? `<div class="kv"><div class="k">结果</div><div class="v"><span class="badge green">连通成功</span></div>
           <div class="k">耗时</div><div class="v">${r.latency_ms} ms</div>
           <div class="k">模型</div><div class="v mono">${esc(r.model)}</div>
           <div class="k">响应</div><div class="v"><div class="code-block">${esc((r.preview || '').slice(0, 500))}</div></div></div>`
        : `<div class="kv"><div class="k">结果</div><div class="v"><span class="badge red">失败</span></div>
           <div class="k">状态码</div><div class="v">${r.status || '-'}</div>
           <div class="k">错误</div><div class="v"><div class="code-block">${esc(r.error || '未知错误')}</div></div></div>
           <p class="hint">请检查 Base URL、API Key 与协议是否匹配。若上游是 OpenAI 格式却选了 Anthropic，就会出现 404。</p>`;
      openModal('测试结果 · ' + u.name, body, closeModal, true);
    } catch (e) {
      toast('测试失败：' + e.message, 'err');
    }
  }

  async function healthUpstream(u) {
    try {
      const r = await POST('/api/upstreams/' + u.id + '/health');
      if (r.ok) toast(`${u.name} 健康（${r.latency_ms}ms）`, 'ok');
      else toast(`${u.name} 异常：${r.error || r.status}`, 'err', 5000);
      loadUpstreams();
    } catch (e) {
      toast('体检失败：' + e.message, 'err');
    }
  }

  async function pickModels(u) {
    toast('正在拉取模型列表…', 'warn');
    try {
      const r = await GET('/api/upstreams/' + u.id + '/models');
      if (!r.models.length) return toast('未获取到模型，请检查 Key 与 Base URL', 'err', 4000);
      const cur = new Set(u.models || []);
      const html = `<p class="hint">勾选后点击保存，将写入该上游的「支持模型」字段（用于调度筛选）。</p>
        <div style="max-height:320px;overflow:auto;margin-top:10px;border:1px solid #222938;border-radius:8px;padding:10px">
        ${r.models.map((m) => `<label class="check" style="padding:4px 0"><input type="checkbox" class="_m" value="${esc(m)}" ${cur.has(m) ? 'checked' : ''}> <span class="mono">${esc(m)}</span></label>`).join('')}
        </div>
        <p class="hint">共 ${r.models.length} 个模型。</p>`;
      openModal('可用模型 · ' + u.name, html, async () => {
        const sel = $$('._m').filter((x) => x.checked).map((x) => x.value);
        await PUT('/api/upstreams/' + u.id, { models: sel });
        toast(`已更新，选中 ${sel.length} 个模型`);
        closeModal();
        loadUpstreams();
      }, true);
    } catch (e) {
      toast('拉取失败：' + e.message, 'err');
    }
  }

  // ---------------------------------------------------------------- 映射
  async function loadMappings() {
    try {
      const list = await GET('/api/mappings');
      const rows = list.map((m) => `<tr>
        <td class="mono">${esc(m.alias)}</td>
        <td>→</td>
        <td class="mono">${esc(m.target)}</td>
        <td>${esc(m.upstream_name || '<span style="color:#5c6880">自动调度</span>')}</td>
        <td>${m.enabled ? '<span class="badge green">启用</span>' : '<span class="badge gray">停用</span>'}</td>
        <td style="font-size:12px;color:#5c6880">${esc(m.note || '')}</td>
        <td><div class="actions">
          <button class="btn ghost sm" data-act="edit" data-id="${m.id}">编辑</button>
          <button class="btn danger sm" data-act="del" data-id="${m.id}">删除</button>
        </div></td></tr>`).join('');
      $('#mappingTable').innerHTML =
        `<thead><tr><th>请求别名</th><th></th><th>目标模型</th><th>固定上游</th><th>状态</th><th>备注</th><th>操作</th></tr></thead>
         <tbody>${rows || '<tr><td class="empty" colspan="7">还没有映射，点击右上角「新增映射」</td></tr>'}</tbody>`;
      $$('#mappingTable [data-act]').forEach((b) => {
        b.onclick = () => {
          const m = list.find((x) => x.id === b.dataset.id);
          if (b.dataset.act === 'edit') return editMapping(m);
          if (b.dataset.act === 'del') return DEL('/api/mappings/' + m.id).then(() => { toast('已删除'); loadMappings(); });
        };
      });
    } catch (e) {
      toast('加载映射失败：' + e.message, 'err');
    }
  }

  async function editMapping(m) {
    const ups = await GET('/api/upstreams');
    const opts = ['<option value="">自动调度</option>'].concat(
      ups.map((u) => `<option value="${u.id}" ${m && m.upstream_id === u.id ? 'selected' : ''}>${esc(u.name)}</option>`)
    ).join('');
    openModal(m ? '编辑映射' : '新增映射', `<div class="form">
      <label>请求别名（客户端传来的模型名）<input class="input" id="f_alias" value="${esc(m ? m.alias : '')}" placeholder="claude-sonnet-4-5"></label>
      <label>目标模型（实际发给上游）<input class="input" id="f_target" value="${esc(m ? m.target : '')}" placeholder="claude-sonnet-4-5-20250929"></label>
      <label>固定上游<select class="input" id="f_upstream_id">${opts}</select></label>
      <label>备注<input class="input" id="f_note" value="${esc(m ? m.note : '')}"></label>
      <label class="check"><input type="checkbox" id="f_enabled" ${!m || m.enabled ? 'checked' : ''}> 启用</label>
    </div>`, async () => {
      const data = {
        alias: formVal('f_alias'), target: formVal('f_target'),
        upstream_id: formVal('f_upstream_id'), note: formVal('f_note'),
        enabled: formVal('f_enabled'),
      };
      if (!data.alias) return toast('别名不能为空', 'err');
      if (m) await PUT('/api/mappings/' + m.id, data);
      else await POST('/api/mappings', data);
      toast('已保存'); closeModal(); loadMappings();
    });
  }

  // ---------------------------------------------------------------- 客户端 Key
  async function loadKeys() {
    try {
      const list = await GET('/api/keys');
      const rows = list.map((k) => `<tr>
        <td><div style="font-weight:500">${esc(k.name)}</div>
          <div style="font-size:11px;color:#5c6880;margin-top:2px">${esc(k.note || '')}</div></td>
        <td><div style="display:flex;align-items:center;gap:6px">
          <span class="mono" style="font-size:12px">${esc(k.masked)}</span>
          <button class="btn ghost sm" data-act="copy" data-id="${k.id}">复制</button>
        </div></td>
        <td>${k.enabled ? '<span class="badge green">启用</span>' : '<span class="badge gray">停用</span>'}</td>
        <td>${k.rate_limit_rpm ? k.rate_limit_rpm + ' /分钟' : '<span style="color:#5c6880">不限</span>'}</td>
        <td>${k.daily_token_limit ? fmtNum(k.daily_token_limit) : '<span style="color:#5c6880">不限</span>'}
          <div style="font-size:11px;color:#5c6880">今日已用 ${fmtNum(k.used_today)}</div></td>
        <td style="font-size:12px">${k.allowed_models && k.allowed_models.length
          ? k.allowed_models.slice(0, 2).map((x) => `<span class="badge blue">${esc(x)}</span>`).join(' ')
          : '<span style="color:#5c6880">全部</span>'}</td>
        <td style="font-size:12px;color:#93a0b8">${k.total_requests} 次<br><span style="color:#5c6880">${fmtAgo(k.last_used_at)}</span></td>
        <td><div class="actions">
          <button class="btn ghost sm" data-act="edit" data-id="${k.id}">编辑</button>
          <button class="btn ghost sm" data-act="regen" data-id="${k.id}">重置密钥</button>
          <button class="btn danger sm" data-act="del" data-id="${k.id}">删除</button>
        </div></td></tr>`).join('');
      $('#keyTable').innerHTML =
        `<thead><tr><th>名称</th><th>Key</th><th>状态</th><th>速率</th><th>每日额度</th><th>允许模型</th><th>用量</th><th>操作</th></tr></thead>
         <tbody>${rows || '<tr><td class="empty" colspan="8">还没有密钥，点击右上角「新增 Key」</td></tr>'}</tbody>`;
      const all = list;
      $$('#keyTable [data-act]').forEach((b) => {
        b.onclick = async () => {
          const k = all.find((x) => x.id === b.dataset.id);
          if (b.dataset.act === 'edit') return editKey(k);
          if (b.dataset.act === 'copy') {
            try { await navigator.clipboard.writeText(k.key); toast('已复制到剪贴板'); }
            catch (_) { toast('复制失败，请手动复制：' + k.key, 'warn', 6000); }
            return;
          }
          if (b.dataset.act === 'regen') {
            if (!confirm(`重置「${k.name}」的密钥？旧密钥将立即失效。`)) return;
            await POST('/api/keys/' + k.id + '/regenerate');
            toast('已重置密钥'); loadKeys(); return;
          }
          if (b.dataset.act === 'del') {
            if (!confirm(`删除「${k.name}」？`)) return;
            await DEL('/api/keys/' + k.id); toast('已删除'); loadKeys();
          }
        };
      });
    } catch (e) {
      toast('加载密钥失败：' + e.message, 'err');
    }
  }

  async function editKey(k) {
    const ups = await GET('/api/upstreams');
    const opts = ['<option value="">自动调度</option>'].concat(
      ups.map((u) => `<option value="${u.id}" ${k && k.upstream_id === u.id ? 'selected' : ''}>${esc(u.name)}</option>`)
    ).join('');
    openModal(k ? '编辑 Key' : '新增 Key', `<div class="form">
      <label>名称<input class="input" id="f_name" value="${esc(k ? k.name : '')}" placeholder="如：VS Code Claude Code"></label>
      <label>速率限制（次/分钟，0 = 不限）<input class="input" id="f_rpm" type="number" value="${k ? k.rate_limit_rpm : 0}"></label>
      <label>每日 Token 额度（0 = 不限）<input class="input" id="f_daily" type="number" value="${k ? k.daily_token_limit : 0}"></label>
      <label>允许的模型（逗号分隔，留空 = 全部）<input class="input" id="f_models" value="${esc(k ? (k.allowed_models || []).join(',') : '')}"></label>
      <label>绑定上游<select class="input" id="f_upstream_id">${opts}</select></label>
      <label>备注<input class="input" id="f_note" value="${esc(k ? k.note : '')}"></label>
      <label class="check"><input type="checkbox" id="f_enabled" ${!k || k.enabled ? 'checked' : ''}> 启用</label>
    </div>`, async () => {
      const data = {
        name: formVal('f_name') || '未命名',
        rate_limit_rpm: Number(formVal('f_rpm')) || 0,
        daily_token_limit: Number(formVal('f_daily')) || 0,
        allowed_models: formVal('f_models') ? formVal('f_models').split(',').map((s) => s.trim()).filter(Boolean) : [],
        upstream_id: formVal('f_upstream_id'),
        note: formVal('f_note'), enabled: formVal('f_enabled'),
      };
      if (k) await PUT('/api/keys/' + k.id, data);
      else await POST('/api/keys', data);
      toast('已保存'); closeModal(); loadKeys();
    });
  }

  // ---------------------------------------------------------------- 日志
  let logPage = 0;
  const LOG_SIZE = 50;
  async function loadLogs() {
    try {
      const ok = $('#logFilter').value;
      const kw = $('#logKeyword').value.trim();
      const q = `/api/logs?limit=${LOG_SIZE}&offset=${logPage * LOG_SIZE}` +
        (ok !== '' ? `&ok=${ok}` : '') + (kw ? `&keyword=${encodeURIComponent(kw)}` : '');
      const d = await GET(q);
      const rows = (d.items || []).map((r) => `<tr>
        <td style="white-space:nowrap;font-size:12px;color:#93a0b8">${fmtTime(r.ts)}</td>
        <td>${r.ok ? '<span class="badge green">成功</span>' : '<span class="badge red">失败</span>'}</td>
        <td class="mono" style="font-size:12px">${esc(r.request_model || '-')}</td>
        <td style="font-size:12px">${esc(r.upstream_name || '-')}</td>
        <td style="font-size:12px">${esc(r.client_name || '-')}</td>
        <td><span class="badge ${r.out_protocol === 'anthropic' ? 'purple' : 'blue'}" style="font-size:10.5px">${esc(r.out_protocol || '-')}</span></td>
        <td>${r.input_tokens || 0} / ${r.output_tokens || 0}</td>
        <td>${r.latency_ms} ms</td>
        <td style="font-size:11.5px;color:#f2706b;max-width:260px;overflow:hidden;text-overflow:ellipsis" title="${esc(r.error || '')}">${esc(r.error || '-')}</td>
      </tr>`).join('');
      $('#logTable').innerHTML =
        `<thead><tr><th>时间</th><th>状态</th><th>模型</th><th>上游</th><th>客户端</th><th>协议</th><th>Tokens 入/出</th><th>耗时</th><th>错误</th></tr></thead>
         <tbody>${rows || '<tr><td class="empty" colspan="9">暂无日志</td></tr>'}</tbody>`;
      const totalPages = Math.max(1, Math.ceil((d.total || 0) / LOG_SIZE));
      $('#logPageInfo').textContent = `第 ${logPage + 1} / ${totalPages} 页 · 共 ${d.total || 0} 条`;
      $('#logPrev').disabled = logPage <= 0;
      $('#logNext').disabled = logPage + 1 >= totalPages;
    } catch (e) {
      toast('加载日志失败：' + e.message, 'err');
    }
  }

  // ---------------------------------------------------------------- 设置
  let settingsCache = null;
  async function loadSettings() {
    try {
      const s = await GET('/api/settings');
      settingsCache = s;
      const set = (id, v) => { const el = $(id); if (el) el.value = v; };
      set('#setHost', s.host); set('#setPort', s.port); set('#setToken', s.admin_token);
      set('#setTimeout', s.request_timeout); set('#setFailThreshold', s.failure_threshold);
      set('#setCooldown', s.cooldown_seconds); set('#setHealthInterval', s.health_check_interval);
      set('#setRetention', s.log_retention_days);
      $('#setHealthEnabled').checked = !!s.health_check_enabled;
      $('#setEnableLog').checked = !!s.enable_log;
      $('#setAutoOpen').checked = !!s.auto_open_browser;
      const hint = $('#settingsHint');
      if (hint) hint.textContent = '修改监听地址 / 端口后保存，服务会自动重启到新地址并生效；若只改监听地址不改端口，需先停止服务再启动。';
    } catch (e) {
      toast('加载设置失败：' + e.message, 'err');
    }
  }

  async function saveSettings() {
    const tip = $('#saveTip');
    const nextHost = (formVal('setHost') || '').trim() || '127.0.0.1';
    const nextPort = Number(formVal('setPort')) || 8000;
    const addrChanged = !!settingsCache &&
      (nextHost.toLowerCase() !== String(settingsCache.host).toLowerCase() ||
       nextPort !== Number(settingsCache.port));
    try {
      if (addrChanged) {
        const okGo = confirm(
          `监听地址将改为 ${nextHost}:${nextPort}\n` +
          '服务会自动重启到新地址（几秒内完成，期间请求短暂中断），\n' +
          '成功后本页面会自动跳转到新地址。确定继续？');
        if (!okGo) { loadSettings(); return; }
        tip.className = 'save-tip';
        tip.textContent = '正在重启服务到新地址，请稍候…';
        const r = await POST('/api/server/restart', { host: nextHost, port: nextPort });
        if (r.restarted === false) {
          tip.textContent = '';
          toast('监听地址未变化，无需重启');
          return;
        }
        tip.textContent = '';
        const navHost = ['', '0.0.0.0', '::'].includes(nextHost.toLowerCase()) ? location.hostname : nextHost;
        toast(`服务已在 ${r.base_url} 就绪，正在跳转…`, 'ok', 6000);
        setTimeout(() => { location.href = `http://${navHost}:${nextPort}/`; }, 1200);
        return;
      }
      await PUT('/api/settings', {
        admin_token: formVal('setToken'),
        request_timeout: Number(formVal('setTimeout')) || 180,
        failure_threshold: Number(formVal('setFailThreshold')) || 3,
        cooldown_seconds: Number(formVal('setCooldown')) || 60,
        health_check_interval: Number(formVal('setHealthInterval')) || 120,
        log_retention_days: Number(formVal('setRetention')) || 0,
        health_check_enabled: formVal('setHealthEnabled'),
        enable_log: formVal('setEnableLog'),
        auto_open_browser: formVal('setAutoOpen'),
      });
      tip.className = 'save-tip';
      tip.textContent = '已保存';
      setTimeout(() => { tip.textContent = ''; }, 4000);
      toast('设置已保存');
    } catch (e) {
      tip.className = 'save-tip err';
      tip.textContent = '保存失败：' + e.message;
    }
  }

  // ---------------------------------------------------------------- 接入指南
  async function showQuickstart() {
    try {
      const d = await GET('/api/quickstart');
      const body = `<div class="form">
        <p class="hint">把下面的环境变量写到你的 shell 里，然后直接运行 <code>claude</code> 即可走本地中转。</p>
        <label>Base URL<div class="code-block" id="c_base">${esc(d.base_url)}<button class="copy-btn" data-copy="${esc(d.base_url)}">复制</button></div></label>
        <label>API Key<div class="code-block" id="c_key">${esc(d.api_key)}<button class="copy-btn" data-copy="${esc(d.api_key)}">复制</button></div></label>
        <label>macOS / Linux（bash / zsh）<div class="code-block">${esc(d.snippets.env)}<button class="copy-btn" data-copy="${esc(d.snippets.env)}">复制</button></div></label>
        <label>Windows（PowerShell）<div class="code-block">${esc(d.snippets.powershell)}<button class="copy-btn" data-copy="${esc(d.snippets.powershell)}">复制</button></div></label>
        <label>ccswitch 配置片段<div class="code-block">${esc(d.snippets.ccswitch)}<button class="copy-btn" data-copy="${esc(d.snippets.ccswitch)}">复制</button></div></label>
        <p class="hint">Windows 永久生效：<code>setx ANTHROPIC_BASE_URL "${esc(d.base_url)}"</code> 与 <code>setx ANTHROPIC_API_KEY "你的Key"</code>，重启终端。</p>
      </div>`;
      openModal('接入指南', body, closeModal, true);
      $$('#modalBody [data-copy]').forEach((b) => {
        b.onclick = async () => {
          try { await navigator.clipboard.writeText(b.dataset.copy); toast('已复制'); }
          catch (_) { toast('复制失败', 'err'); }
        };
      });
    } catch (e) {
      toast('获取接入信息失败：' + e.message, 'err');
    }
  }

  // ---------------------------------------------------------------- 健康检查
  async function ping() {
    try {
      await GET('/healthz');
      $('#serviceDot').className = 'dot ok';
      $('#serviceText').textContent = '服务运行中';
    } catch (_) {
      $('#serviceDot').className = 'dot bad';
      $('#serviceText').textContent = '服务未连接';
    }
  }

  const loaders = {
    dashboard: loadDashboard, usage: loadUsage, upstreams: loadUpstreams,
    mappings: loadMappings, keys: loadKeys, logs: loadLogs, settings: loadSettings,
  };

  // ---------------------------------------------------------------- 事件绑定
  function bind() {
    $$('.nav-item').forEach((b) => { b.onclick = () => switchView(b.dataset.view); });
    $('#btnRefresh').onclick = () => { loaders[currentView](); ping(); toast('已刷新'); };
    $('#usageWindowSel').onchange = (e) => { usageWindow = e.target.value; renderUsage(); };
    $('#btnQuickstart').onclick = showQuickstart;
    $('#btnAddUpstream').onclick = () => editUpstream(null);
    $('#btnAddMapping').onclick = () => editMapping(null);
    $('#btnAddKey').onclick = () => editKey(null);
    $('#btnSaveSettings').onclick = saveSettings;
    $('#btnHealthAll').onclick = async () => {
      toast('正在体检全部节点…', 'warn');
      try {
        const r = await POST('/api/upstreams/health-all');
        const bad = r.filter((x) => x.ok === false);
        if (!bad.length) toast('全部节点正常', 'ok');
        else toast(`${bad.length} 个节点异常：${bad.map((x) => x.name).join('、')}`, 'err', 6000);
        loadUpstreams();
      } catch (e) { toast('体检失败：' + e.message, 'err'); }
    };
    $('#btnSearchLog').onclick = () => { logPage = 0; loadLogs(); };
    $('#logPrev').onclick = () => { if (logPage > 0) { logPage--; loadLogs(); } };
    $('#logNext').onclick = () => { logPage++; loadLogs(); };
    $('#btnClearLog').onclick = async () => {
      if (!confirm('清空全部请求日志？此操作不可恢复。')) return;
      await DEL('/api/logs'); toast('已清空'); logPage = 0; loadLogs();
    };
    $('#btnExport').onclick = async () => {
      const cfg = await GET('/api/config/export');
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `ccproxy-config-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      toast('已导出');
    };
    $('#btnImport').onclick = () => $('#importFile').click();
    $('#importFile').onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      try {
        const cfg = JSON.parse(await f.text());
        if (!confirm('导入将覆盖现有上游、映射与 Key，确定继续？')) return;
        const r = await POST('/api/config/import', cfg);
        toast(`已导入：${r.upstreams} 个上游 / ${r.mappings} 条映射 / ${r.keys} 个 Key`);
        loadSettings();
      } catch (err) { toast('导入失败：' + err.message, 'err'); }
      e.target.value = '';
    };
    $('#modalClose').onclick = closeModal;
    $('#modalCancel').onclick = closeModal;
    $('#modalOk').onclick = () => modalSubmit && modalSubmit();
    $('#modalMask').onclick = (e) => { if (e.target.id === 'modalMask') closeModal(); };
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  }

  // ---------------------------------------------------------------- 启动
  bind();
  switchView('dashboard');
  ping();
  setInterval(ping, 15000);
})();
