/* ==========================================================================
   API 客户端：统一处理鉴权、JSON 编解码、错误提示
   同一套接口同时服务网页版与外部客户端，因此这里保持协议原样
   ========================================================================== */
(function () {
  'use strict';

  const BASE = '/api/v1';
  let TOKEN = '';

  function setToken(value) {
    TOKEN = value || '';
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function buildHeaders(extra) {
    const headers = Object.assign({ Accept: 'application/json' }, extra || {});
    if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
    return headers;
  }

  /** 由服务端错误结构或 HTTP 状态生成可读消息 */
  function describeError(status, payload) {
    if (payload && payload.error && payload.error.message) {
      return payload.error.message;
    }
    const map = {
      400: '请求参数有误',
      401: '登录状态已失效，请重新登录',
      403: '没有操作权限',
      404: '资源不存在',
      405: '请求方式不被支持',
      409: '操作冲突，请刷新后重试',
      413: '文件过大',
      500: '服务器内部错误',
    };
    return map[status] || `请求失败（${status}）`;
  }

  class ApiError extends Error {
    constructor(message, status, code, payload) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
      this.payload = payload;
    }
  }

  async function parseResponse(response) {
    const text = await response.text();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (err) {
      return { raw: text };
    }
  }

  async function request(path, options) {
    const opts = Object.assign({}, options || {});
    const headers = buildHeaders(opts.headers);
    if (opts.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    if (opts.method && opts.method !== 'GET') {
      headers['X-CSRFToken'] = csrfToken();
    }
    let response;
    try {
      response = await fetch(BASE + path, Object.assign(opts, { headers }));
    } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      throw new ApiError('网络连接失败，请检查服务是否在运行', 0, 'network_error');
    }
    const payload = await parseResponse(response);
    if (!response.ok || payload.ok === false) {
      const code = payload.error ? payload.error.code : 'http_error';
      throw new ApiError(describeError(response.status, payload), response.status, code, payload);
    }
    return payload.data !== undefined ? payload.data : payload;
  }

  const api = {
    setToken,
    ApiError,
    /** 供传输层读取当前令牌（WebSocket 握手也会用到） */
    __token: () => TOKEN,
    /** 供 XMLHttpRequest 上传路径复用同一份 CSRF 读取逻辑 */
    csrfToken,

    /** 认证 */
    register: (body) => request('/auth/register/', { method: 'POST', json: body }),
    login: (body) => request('/auth/login/', { method: 'POST', json: body }),
    logout: () => request('/auth/logout/', { method: 'POST' }),
    me: () => request('/auth/me/'),
    updateMe: (body) => request('/auth/me/', { method: 'PATCH', json: body }),
    searchUsers: (q) => request(`/users/search/?q=${encodeURIComponent(q)}`),
    userDetail: (id) => request(`/users/${id}/`),

    /** 联系人 */
    friends: () => request('/friends/'),
    removeFriend: (id) => request(`/friends/${id}/`, { method: 'DELETE' }),
    friendRequests: (direction) =>
      request(`/friend-requests/?direction=${direction || 'incoming'}`),
    sendFriendRequest: (userId, message) =>
      request('/friend-requests/', { method: 'POST', json: { user_id: userId, message: message || '' } }),
    handleFriendRequest: (id, action) =>
      request(`/friend-requests/${id}/${action}/`, { method: 'POST' }),

    /** 会话 */
    conversations: () => request('/conversations/'),
    createDirect: (peerId) =>
      request('/conversations/', { method: 'POST', json: { type: 'direct', peer_id: peerId } }),
    createGroup: (title, memberIds) =>
      request('/conversations/', {
        method: 'POST',
        json: { type: 'group', title, member_ids: memberIds },
      }),
    conversationDetail: (id) => request(`/conversations/${id}/`),
    updateConversation: (id, body) =>
      request(`/conversations/${id}/`, { method: 'PATCH', json: body }),
    addMembers: (id, memberIds) =>
      request(`/conversations/${id}/members/`, { method: 'POST', json: { member_ids: memberIds } }),
    removeMember: (id, memberId) =>
      request(`/conversations/${id}/members/${memberId}/`, { method: 'DELETE' }),
    leaveConversation: (id) =>
      request(`/conversations/${id}/leave/`, { method: 'POST' }),

    /** 消息 */
    messages: (id, params) => {
      const query = new URLSearchParams(params || {}).toString();
      return request(`/conversations/${id}/messages/${query ? `?${query}` : ''}`);
    },
    sendMessage: (id, body) =>
      request(`/conversations/${id}/messages/`, { method: 'POST', json: body }),
    revokeMessage: (id) => request(`/messages/${id}/revoke/`, { method: 'POST' }),
    reportMessage: (id, reason) =>
      request(`/messages/${id}/report/`, { method: 'POST', json: { reason: reason || '' } }),
    search: (params) => {
      const query = new URLSearchParams(params || {}).toString();
      return request(`/search/${query ? `?${query}` : ''}`);
    },
    preferences: () => request('/users/me/preferences/'),
    updatePreferences: (body) =>
      request('/users/me/preferences/', { method: 'PATCH', json: body }),
    uploadAvatar: (file) => {
      const form = new FormData();
      form.append('file', file);
      return request('/users/me/avatar/', { method: 'POST', body: form });
    },
    markRead: (id, upToSeq) =>
      request(`/conversations/${id}/read/`, {
        method: 'POST',
        json: upToSeq === undefined ? {} : { up_to_seq: upToSeq },
      }),
    sendTyping: (id) => request(`/conversations/${id}/typing/`, { method: 'POST' }),

    /** 附件 */
    uploadAttachment: (file) => {
      const form = new FormData();
      form.append('file', file);
      return request('/attachments/', { method: 'POST', body: form });
    },

    /** 实时：长轮询（signal 用于切页或切换会话时中断挂起请求） */
    updates: (cursor, signal) =>
      request('/updates/', {
        method: 'POST',
        json: { cursor, timeout: 25 },
        signal,
      }),

    downloadUrl: (attachmentId, thumb) =>
      `${BASE}/attachments/${attachmentId}/download/${thumb ? '?thumb=1' : ''}`,
  };

  window.Api = api;
})();
