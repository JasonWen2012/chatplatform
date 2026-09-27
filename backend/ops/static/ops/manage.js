/* ==========================================================================
   管理后台交互脚本
   - 全部数据经 /api/v1/ops/* 获取，与协议 API 完全一致
   - 无第三方依赖，原生 JS
   ========================================================================== */
(function () {
  'use strict';

  const BASE = '/api/v1';
  const PAGE_SIZE = 20;

  // ---------------------------------------------------------------- 工具
  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  function fmtDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtDay(iso) {
    if (!iso) return '';
    const parts = String(iso).split('-');
    return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : iso;
  }

  function fileSize(bytes) {
    const v = Number(bytes) || 0;
    if (v < 1024) return `${v} B`;
    if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
    return `${(v / 1024 / 1024).toFixed(1)} MB`;
  }

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  let toastTimer = null;
  function toast(message) {
    const el = document.getElementById('opsToast');
    if (!el) return;
    el.textContent = message;
    el.classList.remove('hidden');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add('hidden'), 2600);
  }

  async function request(path, options) {
    const opts = Object.assign({}, options || {});
    const headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
    if (opts.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    if (opts.method && opts.method !== 'GET') headers['X-CSRFToken'] = csrfToken();

    let response;
    try {
      response = await fetch(BASE + path, Object.assign(opts, { headers }));
    } catch (err) {
      throw new Error('网络连接失败');
    }
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch (err) { payload = {}; }

    if (!response.ok || payload.ok === false) {
      if (response.status === 401) {
        toast('登录已失效，正在跳转…');
        setTimeout(() => { window.location.href = '/login/'; }, 800);
        throw new Error('未登录');
      }
      if (response.status === 403) throw new Error('需要管理员权限');
      throw new Error((payload.error && payload.error.message) || `请求失败（${response.status}）`);
    }
    return payload.data !== undefined ? payload.data : payload;
  }

  function qs(params) {
    const sp = new URLSearchParams();
    Object.keys(params || {}).forEach((k) => {
      const v = params[k];
      if (v !== undefined && v !== null && v !== '') sp.set(k, v);
    });
    const s = sp.toString();
    return s ? `?${s}` : '';
  }

  function el(id) { return document.getElementById(id); }

  function badge(text, kind) {
    return `<span class="ops-tag ops-tag-${kind}">${escapeHtml(text)}</span>`;
  }

  function pagerHtml(meta) {
    if (!meta) return '';
    return `<div class="ops-pager">
      <button class="ops-btn ops-btn-mini" data-page="prev" ${meta.has_prev ? '' : 'disabled'}>上一页</button>
      <span class="ops-pager-info">第 ${meta.page} / ${meta.total_pages || 1} 页 · 共 ${meta.total} 条</span>
      <button class="ops-btn ops-btn-mini" data-page="next" ${meta.has_next ? '' : 'disabled'}>下一页</button>
    </div>`;
  }

  function bindPager(container, state, reload) {
    container.querySelectorAll('[data-page]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.page += btn.dataset.page === 'next' ? 1 : -1;
        if (state.page < 1) state.page = 1;
        reload();
      });
    });
  }

  // ---------------------------------------------------------------- 概览
  const dashboard = {
    async load() {
      const root = el('overviewStats');
      if (!root) return;
      root.innerHTML = '<div class="ops-loading">加载中…</div>';
      try {
        const data = await request('/ops/overview/');
        const u = data.users;
        root.innerHTML = `
          <div class="ops-stat"><div class="ops-stat-label">用户总数</div><div class="ops-stat-value">${u.total}</div></div>
          <div class="ops-stat"><div class="ops-stat-label">当前在线</div><div class="ops-stat-value ops-stat-accent">${u.online}</div></div>
          <div class="ops-stat"><div class="ops-stat-label">已禁用</div><div class="ops-stat-value ${u.banned ? 'ops-stat-danger' : ''}">${u.banned}</div></div>
          <div class="ops-stat"><div class="ops-stat-label">管理员</div><div class="ops-stat-value">${u.staff}</div></div>
          <div class="ops-stat"><div class="ops-stat-label">会话数</div><div class="ops-stat-value">${data.conversations}</div></div>
          <div class="ops-stat"><div class="ops-stat-label">今日消息</div><div class="ops-stat-value">${data.messages.today}</div></div>
          <div class="ops-stat"><div class="ops-stat-label">消息总数</div><div class="ops-stat-value">${data.messages.total}</div></div>
          <div class="ops-stat"><div class="ops-stat-label">待处理举报</div><div class="ops-stat-value ${data.reports.pending ? 'ops-stat-danger' : ''}">${data.reports.pending}</div></div>
        `;

        const max = Math.max(1, ...data.trend.map((t) => t.count));
        el('trendChart').innerHTML = data.trend.map((t) => `
          <div class="ops-chart-col">
            <span class="ops-chart-value">${t.count}</span>
            <div class="ops-chart-bar" style="height:${Math.max(2, (t.count / max) * 100)}%"></div>
            <span class="ops-chart-label">${fmtDay(t.date)}</span>
          </div>`).join('');

        el('topConversations').innerHTML = data.top_conversations.length
          ? `<table class="ops-table"><thead><tr>
               <th>会话</th><th>类型</th><th>成员</th><th>消息</th><th>最后活动</th>
             </tr></thead><tbody>${data.top_conversations.map((c) => `
               <tr>
                 <td data-label="会话">${escapeHtml(c.title)}</td>
                 <td data-label="类型">${c.type === 'group' ? '群聊' : '单聊'}</td>
                 <td data-label="成员">${c.member_count}</td>
                 <td data-label="消息">${c.message_count}</td>
                 <td data-label="最后活动">${fmtDateTime(c.last_message_at)}</td>
               </tr>`).join('')}</tbody></table>`
          : '<div class="ops-empty">暂无会话</div>';
      } catch (err) {
        root.innerHTML = `<div class="ops-empty">加载失败：${escapeHtml(err.message)}</div>`;
      }
    },
  };

  // ------------------------------------------------------------ 用户管理
  const users = {
    state: { page: 1, q: '', status: '' },
    async load() {
      const tbody = el('usersBody');
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="7" class="ops-loading">加载中…</td></tr>';
      try {
        const data = await request(`/ops/users/${qs({
          page: this.state.page, page_size: PAGE_SIZE, q: this.state.q, status: this.state.status,
        })}`);
        const myId = Number(document.body.dataset.meId || 0);
        tbody.innerHTML = data.users.length ? data.users.map((u) => {
          const isSelf = u.id === myId;
          const stateTag = !u.is_active
            ? badge('已禁用', 'danger')
            : (u.is_online ? badge('在线', 'ok') : badge('离线', 'muted'));
          return `<tr data-uid="${u.id}">
            <td data-label="ID">${u.id}</td>
            <td data-label="账号">
              <div><strong>${escapeHtml(u.username)}</strong></div>
              <div class="muted">${escapeHtml(u.nickname)}${u.is_staff ? ' · 管理员' : ''}</div>
            </td>
            <td data-label="状态">${stateTag}</td>
            <td data-label="消息数">${u.message_count}</td>
            <td data-label="会话数">${u.conversation_count}</td>
            <td data-label="注册时间">${fmtDateTime(u.date_joined)}</td>
            <td data-label="操作"><div class="ops-actions">
              ${isSelf
                ? '<span class="muted">当前账号</span>'
                : (u.is_active
                  ? `<button class="ops-btn ops-btn-mini ops-btn-danger" data-ban="${u.id}">禁用</button>`
                  : `<button class="ops-btn ops-btn-mini ops-btn-success" data-unban="${u.id}">解禁</button>`)}
              <button class="ops-btn ops-btn-mini" data-revoke="${u.id}">强制下线</button>
            </div></td>
          </tr>`;
        }).join('') : '<tr><td colspan="7" class="ops-empty">没有匹配的用户</td></tr>';

        el('usersPager').innerHTML = pagerHtml(data.pagination);
        bindPager(el('usersPager'), this.state, () => this.load());
        this.bindActions();
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" class="ops-empty">加载失败：${escapeHtml(err.message)}</td></tr>`;
      }
    },
    bindActions() {
      const tbody = el('usersBody');
      tbody.querySelectorAll('[data-ban]').forEach((b) => b.addEventListener('click', () => this.setActive(Number(b.dataset.ban), false)));
      tbody.querySelectorAll('[data-unban]').forEach((b) => b.addEventListener('click', () => this.setActive(Number(b.dataset.unban), true)));
      tbody.querySelectorAll('[data-revoke]').forEach((b) => b.addEventListener('click', () => this.revoke(Number(b.dataset.revoke))));
    },
    async setActive(userId, active) {
      const verb = active ? '解禁' : '禁用';
      if (!window.confirm(`确定${verb}该用户吗？${active ? '' : '对方将被立即踢下线。'}`)) return;
      try {
        await request(`/ops/users/${userId}/`, { method: 'PATCH', json: { is_active: active } });
        toast(`已${verb}`);
        this.load();
      } catch (err) { toast(err.message); }
    },
    async revoke(userId) {
      if (!window.confirm('确定强制该用户下线吗？其所有设备将需要重新登录。')) return;
      try {
        const data = await request(`/ops/users/${userId}/revoke-tokens/`, { method: 'POST' });
        toast(`已强制下线（撤销 ${data.revoked_tokens} 个令牌）`);
      } catch (err) { toast(err.message); }
    },
  };

  // ------------------------------------------------------------ 消息检索
  const messages = {
    state: { page: 1, q: '', conversation: '', sender: '' },
    async load() {
      const tbody = el('messagesBody');
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="6" class="ops-loading">加载中…</td></tr>';
      try {
        const data = await request(`/ops/messages/${qs({
          page: this.state.page, page_size: PAGE_SIZE, q: this.state.q,
          conversation: this.state.conversation, sender: this.state.sender,
        })}`);
        tbody.innerHTML = data.messages.length ? data.messages.map((m) => {
          const stateTag = m.removed ? badge('已移除', 'danger')
            : (m.revoked ? badge('已撤回', 'warn') : badge('正常', 'ok'));
          const content = m.removed
            ? '<span class="muted">（内容已被移除）</span>'
            : (m.kind === 'image' ? '[图片]'
              : (m.kind === 'file' ? `[文件] ${escapeHtml(m.attachment ? m.attachment.name : '')}`
                : escapeHtml(m.body)));
          return `<tr>
            <td data-label="ID">${m.id}</td>
            <td data-label="会话">${escapeHtml(m.conversation_title)}<div class="muted">#${m.conversation_id}</div></td>
            <td data-label="发送者">${m.sender ? escapeHtml(m.sender.nickname) : '—'}</td>
            <td data-label="内容"><div class="ops-truncate">${content}</div></td>
            <td data-label="状态">${stateTag}<div class="muted">${fmtDateTime(m.created_at)}</div></td>
            <td data-label="操作"><div class="ops-actions">
              ${m.removed || m.revoked ? '<span class="muted">不可操作</span>'
                : `<button class="ops-btn ops-btn-mini ops-btn-danger" data-remove="${m.id}">移除</button>`}
            </div></td>
          </tr>`;
        }).join('') : '<tr><td colspan="6" class="ops-empty">没有匹配的消息</td></tr>';

        el('messagesPager').innerHTML = pagerHtml(data.pagination);
        bindPager(el('messagesPager'), this.state, () => this.load());
        tbody.querySelectorAll('[data-remove]').forEach((b) => {
          b.addEventListener('click', () => this.remove(Number(b.dataset.remove)));
        });
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="ops-empty">加载失败：${escapeHtml(err.message)}</td></tr>`;
      }
    },
    async remove(messageId) {
      const reason = window.prompt('移除理由（可留空）：', '违规内容');
      if (reason === null) return;
      try {
        await request(`/ops/messages/${messageId}/remove/`, { method: 'POST', json: { reason } });
        toast('消息已移除，会话成员将实时收到通知');
        this.load();
      } catch (err) { toast(err.message); }
    },
  };

  // ------------------------------------------------------------ 会话浏览
  const conversations = {
    state: { page: 1, q: '', type: '' },
    async load() {
      const tbody = el('conversationsBody');
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="6" class="ops-loading">加载中…</td></tr>';
      try {
        const data = await request(`/ops/conversations/${qs({
          page: this.state.page, page_size: PAGE_SIZE, q: this.state.q, type: this.state.type,
        })}`);
        tbody.innerHTML = data.conversations.length ? data.conversations.map((c) => `<tr>
          <td data-label="ID">${c.id}</td>
          <td data-label="名称">${escapeHtml(c.title)}</td>
          <td data-label="类型">${c.type === 'group' ? badge('群聊', 'ok') : badge('单聊', 'muted')}</td>
          <td data-label="成员">${c.member_count}</td>
          <td data-label="消息">${c.message_count}</td>
          <td data-label="操作"><div class="ops-actions">
            <button class="ops-btn ops-btn-mini" data-detail="${c.id}">查看</button>
          </div></td>
        </tr>`).join('') : '<tr><td colspan="6" class="ops-empty">没有匹配的会话</td></tr>';

        el('conversationsPager').innerHTML = pagerHtml(data.pagination);
        bindPager(el('conversationsPager'), this.state, () => this.load());
        tbody.querySelectorAll('[data-detail]').forEach((b) => {
          b.addEventListener('click', () => this.detail(Number(b.dataset.detail)));
        });
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="ops-empty">加载失败：${escapeHtml(err.message)}</td></tr>`;
      }
    },
    async detail(conversationId) {
      try {
        const data = await request(`/ops/conversations/${conversationId}/`);
        const c = data.conversation;
        const members = data.members.map((m) =>
          `${escapeHtml(m.nickname)}${m.role === 'owner' ? '（群主）' : (m.role === 'admin' ? '（管理员）' : '')}`
        ).join('、') || '无';
        const msgs = data.messages.slice(-20).map((m) => {
          const content = m.removed ? '（已被移除）'
            : (m.revoked ? '（已撤回）'
              : (m.kind === 'image' ? '[图片]' : (m.kind === 'file' ? '[文件]' : escapeHtml(m.body))));
          return `<div class="ops-detail-row">
            <span class="k">#${m.seq} ${m.sender ? escapeHtml(m.sender.nickname) : '系统'}</span>
            <span class="ops-truncate">${content}</span>
          </div>`;
        }).join('') || '<div class="ops-empty">暂无消息</div>';

        showModal(`会话详情 #${c.id}`, `
          <div class="ops-detail-row"><span class="k">名称</span><span>${escapeHtml(c.title)}</span></div>
          <div class="ops-detail-row"><span class="k">类型</span><span>${c.type === 'group' ? '群聊' : '单聊'}</span></div>
          <div class="ops-detail-row"><span class="k">成员数</span><span>${c.member_count}</span></div>
          <div class="ops-detail-row"><span class="k">消息数</span><span>${c.message_count}</span></div>
          <div class="ops-detail-row"><span class="k">成员</span><span>${members}</span></div>
          <h3 style="margin-top:16px">最近消息</h3>
          ${msgs}
        `);
      } catch (err) { toast(err.message); }
    },
  };

  // ------------------------------------------------------------ 举报处理
  const reports = {
    state: { page: 1, status: 'pending' },
    async load() {
      const tbody = el('reportsBody');
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="5" class="ops-loading">加载中…</td></tr>';
      try {
        const data = await request(`/ops/reports/${qs({
          page: this.state.page, page_size: PAGE_SIZE, status: this.state.status,
        })}`);
        if (el('pendingReports')) el('pendingReports').textContent = data.pending_total;

        tbody.innerHTML = data.reports.length ? data.reports.map((r) => {
          const statusTag = r.status === 'pending' ? badge('待处理', 'warn')
            : (r.status === 'resolved' ? badge('已处理', 'ok') : badge('已驳回', 'muted'));
          const m = r.message;
          const content = m.removed ? '<span class="muted">（已被移除）</span>'
            : (m.kind === 'image' ? '[图片]' : (m.kind === 'file' ? '[文件]' : escapeHtml(m.body)));
          return `<tr>
            <td data-label="ID">${r.id}</td>
            <td data-label="被举报内容">
              <div class="ops-truncate">${content}</div>
              <div class="muted">会话 #${m.conversation_id} · ${m.sender ? escapeHtml(m.sender.nickname) : '—'}</div>
            </td>
            <td data-label="举报人">${escapeHtml(r.reporter.nickname)}<div class="muted">${escapeHtml(r.reason || '未填理由')}</div></td>
            <td data-label="状态">${statusTag}<div class="muted">${fmtDateTime(r.created)}</div></td>
            <td data-label="操作"><div class="ops-actions">
              ${r.status === 'pending' ? `
                <button class="ops-btn ops-btn-mini ops-btn-danger" data-resolve="${r.id}">移除并结案</button>
                <button class="ops-btn ops-btn-mini" data-dismiss="${r.id}">驳回</button>` : '<span class="muted">已处理</span>'}
            </div></td>
          </tr>`;
        }).join('') : '<tr><td colspan="5" class="ops-empty">没有举报记录</td></tr>';

        el('reportsPager').innerHTML = pagerHtml(data.pagination);
        bindPager(el('reportsPager'), this.state, () => this.load());
        tbody.querySelectorAll('[data-resolve]').forEach((b) => {
          b.addEventListener('click', () => this.act(Number(b.dataset.resolve), 'resolve'));
        });
        tbody.querySelectorAll('[data-dismiss]').forEach((b) => {
          b.addEventListener('click', () => this.act(Number(b.dataset.dismiss), 'dismiss'));
        });
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="ops-empty">加载失败：${escapeHtml(err.message)}</td></tr>`;
      }
    },
    async act(reportId, action) {
      const isResolve = action === 'resolve';
      if (!window.confirm(isResolve ? '结案并移除该消息？' : '驳回该举报？消息将保持原样。')) return;
      try {
        await request(`/ops/reports/${reportId}/${action}/`, {
          method: 'POST', json: { remove_message: isResolve },
        });
        toast(isResolve ? '已结案并移除消息' : '已驳回');
        this.load();
      } catch (err) { toast(err.message); }
    },
  };

  // ------------------------------------------------------------ 操作日志
  const audit = {
    state: { page: 1, action: '' },
    async load() {
      const tbody = el('auditBody');
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="5" class="ops-loading">加载中…</td></tr>';
      try {
        const data = await request(`/ops/audit/${qs({
          page: this.state.page, page_size: PAGE_SIZE, action: this.state.action,
        })}`);
        tbody.innerHTML = data.logs.length ? data.logs.map((l) => `<tr>
          <td data-label="时间">${fmtDateTime(l.created)}</td>
          <td data-label="操作人">${l.actor ? escapeHtml(l.actor.nickname) : '（已删除）'}</td>
          <td data-label="动作">${escapeHtml(l.action_label)}</td>
          <td data-label="对象">${escapeHtml(l.target_type || '—')} ${l.target_id ? '#' + l.target_id : ''}</td>
          <td data-label="说明">${escapeHtml(l.detail || '—')}</td>
        </tr>`).join('') : '<tr><td colspan="5" class="ops-empty">暂无操作记录</td></tr>';

        el('auditPager').innerHTML = pagerHtml(data.pagination);
        bindPager(el('auditPager'), this.state, () => this.load());
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="ops-empty">加载失败：${escapeHtml(err.message)}</td></tr>`;
      }
    },
  };

  // ---------------------------------------------------------------- 弹窗
  function showModal(title, html) {
    const modal = document.createElement('div');
    modal.className = 'ops-modal';
    modal.innerHTML = `<div class="ops-modal-card">
      <h3>${escapeHtml(title)}</h3>
      ${html}
      <div class="ops-modal-actions"><button class="ops-btn" data-close>关闭</button></div>
    </div>`;
    modal.addEventListener('click', (e) => {
      if (e.target === modal || e.target.hasAttribute('data-close')) modal.remove();
    });
    document.body.appendChild(modal);
  }

  // ---------------------------------------------------------------- 启动
  function bindToolbar(config) {
    const search = el(config.searchId);
    if (search) {
      let timer = null;
      search.addEventListener('input', () => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          config.state.q = search.value.trim();
          config.state.page = 1;
          config.load();
        }, 320);
      });
    }
    const status = el(config.filterId);
    if (status) {
      status.addEventListener('change', () => {
        const key = config.filterKey || 'status';
        config.state[key] = status.value;
        config.state.page = 1;
        config.load();
      });
    }
  }

  function boot() {
    const menuBtn = el('opsMenuBtn');
    const sidebar = el('opsSidebar');
    if (menuBtn && sidebar) {
      menuBtn.addEventListener('click', () => sidebar.classList.toggle('open'));
    }

    const page = document.body.dataset.page;
    if (page === 'dashboard') {
      dashboard.load();
    } else if (page === 'users') {
      bindToolbar({ searchId: 'usersSearch', filterId: 'usersStatus', state: users.state, load: () => users.load() });
      users.load();
    } else if (page === 'messages') {
      bindToolbar({ searchId: 'messagesSearch', state: messages.state, load: () => messages.load() });
      const convInput = el('messagesConversation');
      if (convInput) {
        convInput.addEventListener('change', () => {
          messages.state.conversation = convInput.value.trim();
          messages.state.page = 1;
          messages.load();
        });
      }
      messages.load();
    } else if (page === 'conversations') {
      bindToolbar({
        searchId: 'conversationsSearch', filterId: 'conversationsType',
        filterKey: 'type', state: conversations.state, load: () => conversations.load(),
      });
      conversations.load();
    } else if (page === 'reports') {
      const filter = el('reportsStatus');
      if (filter) {
        filter.addEventListener('change', () => {
          reports.state.status = filter.value;
          reports.state.page = 1;
          reports.load();
        });
      }
      reports.load();
    } else if (page === 'audit') {
      const filter = el('auditAction');
      if (filter) {
        filter.addEventListener('change', () => {
          audit.state.action = filter.value;
          audit.state.page = 1;
          audit.load();
        });
      }
      audit.load();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
