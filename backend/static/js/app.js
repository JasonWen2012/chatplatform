/* ==========================================================================
   聊天前端主逻辑
   - 所有数据经 /api/v1/ 获取，与外部客户端共享同一套协议
   - 长轮询消费事件流，游标单调递增，切换会话时中断并重启
   ========================================================================== */
(function () {
  'use strict';

  const api = window.Api;
  const UI = window.UI;

  // ------------------------------------------------------------------ 状态
  const state = {
    me: null,
    cursor: 0,
    conversations: [],        // 会话列表（含最后一条消息与未读）
    friendRequests: [],       // 收到的好友申请
    friends: [],              // 好友列表
    users: {},                // user_id -> 用户信息，用于渲染昵称与头像
    activeId: null,           // 当前会话 id
    messages: [],             // 当前会话消息（升序）
    pending: new Set(),       // 本地乐观插入的消息 id
    typing: {},               // conversation_id -> 过期时间戳
    typingTimers: {},         // conversation_id -> 定时器
    replyTo: null,            // 当前引用的消息
    hasMore: false,
    poller: null,             // 长轮询 AbortController
    polling: false,
    sending: false,
    preferences: null,        // 服务端偏好（主题、字号等）
    isAdmin: false,           // 当前用户是否服务器管理员
    capabilities: null,       // 服务端能力声明（可用传输方式等）
    transport: null,          // 实时传输实例（一期长轮询，二期 WebSocket）
  };

  // ------------------------------------------------------------------ 元素
  const el = {};

  function cacheElements() {
    const ids = [
      'chatApp', 'myAvatar', 'myName', 'conversationSearch', 'mainTabs',
      'paneConversations', 'paneContacts', 'paneRequests',
      'conversationList', 'conversationEmpty', 'friendList', 'friendEmpty',
      'requestList', 'requestEmpty', 'friendReqBadge', 'newFriendBadge',
      'userSearch', 'userSearchResults', 'btnNewGroup',
      'chatPlaceholder', 'chatView', 'chatTitle', 'chatSub', 'typingHint',
      'messageScroll', 'messageList', 'btnLoadMore', 'btnBackToList',
      'btnConversationInfo', 'btnEmoji', 'btnImage', 'btnFile', 'imageInput',
      'fileInput', 'messageInput', 'btnSend', 'emojiPanel', 'replyPreview',
      'btnCancelReply', 'uploadStatus',
      'groupModal', 'groupTitle', 'groupMemberPicker', 'btnGroupCancel', 'btnGroupCreate',
      'infoModal', 'infoTitle', 'infoBody', 'btnInfoClose', 'btnInfoLeave',
      'btnOpenSettings', 'settingsModal', 'profileNickname', 'profileBio', 'btnChangeAvatar',
      'prefTheme', 'prefFont', 'prefBubble', 'prefSound', 'prefDesktop',
      'globalSearchInput', 'btnGlobalSearch', 'globalSearchResults',
      'btnSettingsClose', 'btnSettingsSave',
    ];
    ids.forEach((id) => { el[id] = document.getElementById(id); });
  }

  // ================================================================== 启动
  function readBootData() {
    const node = document.getElementById('boot-data');
    try {
      return JSON.parse(node.textContent);
    } catch (err) {
      return { me: null, token: '' };
    }
  }

  async function boot() {
    cacheElements();
    const boot0 = readBootData();
    state.me = boot0.me;
    state.preferences = boot0.preferences || null;
    state.isAdmin = Boolean(boot0.is_admin);
    state.capabilities = boot0.capabilities || null;
    api.setToken(boot0.token);
    applyPreferences(state.preferences);
    if (state.me) {
      state.users[state.me.id] = state.me;
      el.myName.textContent = state.me.nickname;
      renderMyAvatar();
      UI.bindAvatarFallback(document);
      setupMyAvatar();
    }

    bindEvents();
    buildEmojiPanel();
    await Promise.all([loadConversations(), loadFriends(), loadFriendRequests()]);
    if (!state.preferences) loadPreferences();
    startEventStream();
    setInterval(refreshTypingHints, 1000);
  }

  /** 用当前 state.me 重绘侧栏头像（换头像后需要刷新） */
  function renderMyAvatar() {
    const current = document.querySelector('.sidebar-top .avatar');
    if (!current) return;
    const holder = document.createElement('div');
    holder.innerHTML = UI.avatarHtml(state.me);
    current.replaceWith(holder.firstElementChild);
    UI.bindAvatarFallback(document);
  }

  /** 应用主题与字号偏好到根元素 */
  function applyPreferences(prefs) {
    if (!prefs) return;
    const root = document.documentElement;
    let theme = prefs.theme || 'system';
    if (theme === 'system') {
      theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark' : 'light';
    }
    root.setAttribute('data-theme', theme);
    root.setAttribute('data-font', prefs.font_size || 'normal');
    root.setAttribute('data-bubble', prefs.bubble_style || 'classic');
    state.preferences = prefs;
  }

  async function loadPreferences() {
    try {
      const data = await api.preferences();
      applyPreferences(data.preferences);
    } catch (err) {
      // 偏好读取失败不影响聊天
    }
  }

  function setupMyAvatar() {
    const avatar = document.querySelector('.sidebar-top .avatar');
    if (!avatar) return;
    avatar.style.cursor = 'pointer';
    avatar.title = '点击更换头像';
    avatar.addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.addEventListener('change', async () => {
        const file = input.files && input.files[0];
        if (!file) return;
        if (file.size > 20 * 1024 * 1024) {
          UI.toast('头像文件超过 20MB 上限');
          return;
        }
        UI.toast('正在上传头像…');
        try {
          const data = await api.uploadAvatar(file);
          state.me = data.user;
          state.users[state.me.id] = state.me;
          renderMyAvatar();
          setupMyAvatar();
          el.myName.textContent = state.me.nickname;
          UI.toast('头像已更新');
          // 头像变化会影响会话列表里自己的展示，刷新一次
          loadConversations();
        } catch (err) {
          UI.toast(`头像更新失败：${err.message}`);
        }
      });
      input.click();
    });
  }

  // =============================================================== 事件绑定
  function bindEvents() {
    el.btnSend.addEventListener('click', sendCurrentMessage);
    el.messageInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendCurrentMessage();
      }
    });
    el.messageInput.addEventListener('input', () => {
      autoGrow(el.messageInput);
      notifyTyping();
    });

    el.btnEmoji.addEventListener('click', (event) => {
      event.stopPropagation();
      el.emojiPanel.classList.toggle('hidden');
    });
    document.addEventListener('click', (event) => {
      if (!el.emojiPanel.classList.contains('hidden')
        && !el.emojiPanel.contains(event.target)
        && event.target !== el.btnEmoji) {
        el.emojiPanel.classList.add('hidden');
      }
    });

    el.btnImage.addEventListener('click', () => el.imageInput.click());
    el.btnFile.addEventListener('click', () => el.fileInput.click());
    el.imageInput.addEventListener('change', () => uploadFrom(el.imageInput));
    el.fileInput.addEventListener('change', () => uploadFrom(el.fileInput));

    // 粘贴上传：截图 / 复制的图片可直接 Ctrl+V 发送
    el.messageInput.addEventListener('paste', (event) => {
      const items = (event.clipboardData && event.clipboardData.items) || [];
      for (const item of items) {
        if (item.kind === 'file') {
          const file = item.getAsFile();
          if (file) {
            event.preventDefault();
            uploadFile(file);
            return;
          }
        }
      }
    });

    // 拖拽上传：拖到消息区松手即上传
    ['dragenter', 'dragover'].forEach((name) => {
      el.messageScroll.addEventListener(name, (event) => {
        event.preventDefault();
        el.messageScroll.classList.add('drag-over');
      });
    });
    ['dragleave', 'drop'].forEach((name) => {
      el.messageScroll.addEventListener(name, (event) => {
        event.preventDefault();
        el.messageScroll.classList.remove('drag-over');
      });
    });
    el.messageScroll.addEventListener('drop', (event) => {
      const files = (event.dataTransfer && event.dataTransfer.files) || [];
      if (files.length) uploadFile(files[0]);
    });

    el.btnLoadMore.addEventListener('click', () => loadMessages(state.activeId, true));
    el.btnBackToList.addEventListener('click', () => el.chatApp.classList.remove('show-chat'));
    el.btnConversationInfo.addEventListener('click', openConversationInfo);
    el.btnCancelReply.addEventListener('click', clearReply);
    el.btnNewGroup.addEventListener('click', openGroupModal);
    el.btnGroupCancel.addEventListener('click', () => el.groupModal.classList.add('hidden'));
    el.btnGroupCreate.addEventListener('click', createGroup);
    el.btnInfoClose.addEventListener('click', () => el.infoModal.classList.add('hidden'));
    el.btnInfoLeave.addEventListener('click', leaveConversation);

    // 设置弹窗：个人资料 + 偏好 + 全局搜索
    el.btnOpenSettings.addEventListener('click', openSettings);
    el.btnSettingsClose.addEventListener('click', () => el.settingsModal.classList.add('hidden'));
    el.btnSettingsSave.addEventListener('click', saveSettings);
    el.btnChangeAvatar.addEventListener('click', pickAvatarFromSettings);
    el.btnGlobalSearch.addEventListener('click', runGlobalSearch);
    el.globalSearchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        runGlobalSearch();
      }
    });

    el.mainTabs.addEventListener('click', (event) => {
      const tab = event.target.closest('.tab');
      if (tab) switchView(tab.dataset.view);
    });

    el.conversationSearch.addEventListener('input', renderConversationList);
    el.userSearch.addEventListener('input', debounce(searchUsers, 300));

    // 鼠标滚轮滚动到顶部时自动加载更早消息
    el.messageScroll.addEventListener('scroll', () => {
      if (el.messageScroll.scrollTop < 40 && state.hasMore) {
        loadMessages(state.activeId, true);
      }
    });

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        loadConversations();
        if (state.activeId) loadMessages(state.activeId, false);
        // 回到前台时立即重连，避免等待当前挂起请求超时
        if (state.transport && typeof state.transport.restart === 'function') {
          state.transport.restart();
        }
      }
    });

    // 退出前尽量把已读状态落库
    window.addEventListener('beforeunload', () => {
      if (state.activeId) markRead(state.activeId, true);
    });
  }

  function debounce(fn, wait) {
    let timer = null;
    return function debounced(...args) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function autoGrow(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 130)}px`;
  }

  function switchView(view) {
    Array.from(el.mainTabs.querySelectorAll('.tab')).forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.view === view);
    });
    el.paneConversations.classList.toggle('hidden', view !== 'conversations');
    el.paneContacts.classList.toggle('hidden', view !== 'contacts');
    el.paneRequests.classList.toggle('hidden', view !== 'requests');
    if (view === 'contacts') loadFriends();
    if (view === 'requests') loadFriendRequests();
  }

  // ================================================================ 数据加载
  function learnUsers(list) {
    (list || []).forEach((user) => {
      if (user && user.id) state.users[user.id] = user;
    });
  }

  async function loadConversations() {
    try {
      const data = await api.conversations();
      state.conversations = data.conversations || [];
      state.conversations.forEach((conv) => {
        if (conv.peer) learnUsers([conv.peer]);
        if (conv.last_message && conv.last_message.sender) learnUsers([conv.last_message.sender]);
      });
      renderConversationList();
      // 服务端返回的 unread_total 与本地汇总可能略有差异（当前会话已乐观清零），
      // 以本地为准，保证进入会话后角标立刻消失
      refreshUnreadTitle();
    } catch (err) {
      if (err.status === 401) return;
      UI.toast(`加载会话失败：${err.message}`);
    }
  }

  async function loadFriends() {
    try {
      const data = await api.friends();
      state.friends = data.friends || [];
      learnUsers(state.friends);
      renderFriendList();
    } catch (err) {
      if (err.status !== 401) UI.toast(`加载好友失败：${err.message}`);
    }
  }

  async function loadFriendRequests() {
    try {
      const data = await api.friendRequests('incoming');
      state.friendRequests = data.requests || [];
      state.friendRequests.forEach((item) => learnUsers([item.from_user]));
      renderRequestList();
      const count = state.friendRequests.length;
      el.newFriendBadge.textContent = count;
      el.newFriendBadge.classList.toggle('hidden', count === 0);
      el.friendReqBadge.textContent = count;
      el.friendReqBadge.classList.toggle('hidden', count === 0);
    } catch (err) {
      if (err.status !== 401) UI.toast(`加载好友申请失败：${err.message}`);
    }
  }

  async function searchUsers() {
    const keyword = el.userSearch.value.trim();
    if (!keyword) {
      el.userSearchResults.classList.add('hidden');
      return;
    }
    try {
      const data = await api.searchUsers(keyword);
      const results = data.results || [];
      learnUsers(results);
      const friendIds = new Set(state.friends.map((f) => f.id));
      el.userSearchResults.innerHTML = results.length
        ? results.map((user) => {
          const isFriend = friendIds.has(user.id);
          return `<li class="conv-item">
            ${UI.avatarHtml(user)}
            <div class="conv-body">
              <div class="conv-name">${UI.escapeHtml(user.nickname)}</div>
              <div class="conv-preview">@${UI.escapeHtml(user.username)}</div>
            </div>
            <div class="list-actions">
              ${isFriend
    ? `<button class="btn btn-mini" data-chat="${user.id}">发消息</button>`
    : `<button class="btn btn-mini btn-primary" data-add="${user.id}">加好友</button>`}
            </div>
          </li>`;
        }).join('')
        : '<li class="empty">没有找到匹配的用户</li>';
      el.userSearchResults.classList.remove('hidden');
      el.userSearchResults.querySelectorAll('[data-add]').forEach((btn) => {
        btn.addEventListener('click', () => addFriend(Number(btn.dataset.add)));
      });
      el.userSearchResults.querySelectorAll('[data-chat]').forEach((btn) => {
        btn.addEventListener('click', () => startDirect(Number(btn.dataset.chat)));
      });
    } catch (err) {
      UI.toast(`搜索失败：${err.message}`);
    }
  }

  // ================================================================== 渲染
  function renderConversationList() {
    const keyword = el.conversationSearch.value.trim().toLowerCase();
    const list = state.conversations.filter(
      (conv) => !keyword || (conv.title || '').toLowerCase().includes(keyword),
    );
    el.conversationEmpty.classList.toggle('hidden', state.conversations.length > 0);
    el.conversationList.innerHTML = list.map(conversationItemHtml).join('');
    el.conversationList.querySelectorAll('[data-conv]').forEach((node) => {
      node.addEventListener('click', () => openConversation(Number(node.dataset.conv)));
    });
    UI.bindAvatarFallback(el.conversationList);
  }

  function conversationItemHtml(conv) {
    const last = conv.last_message;
    let preview = '暂无消息';
    if (last) {
      if (last.revoked) {
        preview = '消息已撤回';
      } else if (last.kind === 'image') {
        preview = '[图片]';
      } else if (last.kind === 'file') {
        preview = `[文件] ${last.attachment ? last.attachment.name : ''}`;
      } else if (last.kind === 'system') {
        preview = last.body || '';
      } else {
        preview = last.body || '';
      }
      if (conv.type === 'group' && last.sender) {
        preview = `${last.sender.nickname}: ${preview}`;
      }
    }
    const unread = conv.unread_count > 0 && !conv.is_muted
      ? `<span class="badge">${conv.unread_count > 99 ? '99+' : conv.unread_count}</span>`
      : '';
    const muted = conv.unread_count > 0 && conv.is_muted ? '<span class="mute-mark">🔕</span>' : '';
    const pinned = conv.is_pinned ? '<span class="pin-mark">📌</span>' : '';
    const groupMark = conv.type === 'group' ? '👥 ' : '';
    return `<li class="conv-item ${conv.id === state.activeId ? 'active' : ''}" data-conv="${conv.id}">
      ${UI.avatarHtml(conv.peer || { nickname: conv.title, avatar: null })}
      <div class="conv-body">
        <div class="conv-line1">
          <span class="conv-name">${groupMark}${UI.escapeHtml(conv.title)}</span>
          <span class="conv-time">${UI.shortTime(conv.last_message_at)}</span>
        </div>
        <div class="conv-line2">
          <span class="conv-preview">${UI.escapeHtml(preview)}</span>
          <span class="conv-right">${pinned}${muted}${unread}</span>
        </div>
      </div>
    </li>`;
  }

  function renderFriendList() {
    el.friendEmpty.classList.toggle('hidden', state.friends.length > 0);
    el.friendList.innerHTML = state.friends.map((friend) => `<li class="conv-item">
      ${UI.avatarHtml(friend)}
      <div class="conv-body">
        <div class="conv-name">${UI.escapeHtml(friend.nickname)}</div>
        <div class="conv-preview">${friend.is_online ? '在线' : '离线'} · @${UI.escapeHtml(friend.username)}</div>
      </div>
      <div class="list-actions">
        <button class="btn btn-mini" data-chat="${friend.id}">发消息</button>
      </div>
    </li>`).join('');
    el.friendList.querySelectorAll('[data-chat]').forEach((btn) => {
      btn.addEventListener('click', () => startDirect(Number(btn.dataset.chat)));
    });
    UI.bindAvatarFallback(el.friendList);
  }

  function renderRequestList() {
    el.requestEmpty.classList.toggle('hidden', state.friendRequests.length > 0);
    el.requestList.innerHTML = state.friendRequests.map((item) => `<li class="conv-item">
      ${UI.avatarHtml(item.from_user)}
      <div class="conv-body">
        <div class="conv-name">${UI.escapeHtml(item.from_user.nickname)}</div>
        <div class="conv-preview">${UI.escapeHtml(item.message || '请求添加你为好友')}</div>
      </div>
      <div class="list-actions">
        <button class="btn btn-mini btn-primary" data-accept="${item.id}">接受</button>
        <button class="btn btn-mini" data-reject="${item.id}">拒绝</button>
      </div>
    </li>`).join('');
    el.requestList.querySelectorAll('[data-accept]').forEach((btn) => {
      btn.addEventListener('click', () => handleRequest(Number(btn.dataset.accept), 'accept'));
    });
    el.requestList.querySelectorAll('[data-reject]').forEach((btn) => {
      btn.addEventListener('click', () => handleRequest(Number(btn.dataset.reject), 'reject'));
    });
    UI.bindAvatarFallback(el.requestList);
  }

  function renderMessages() {
    const myId = state.me ? state.me.id : 0;
    const activeConv = state.conversations.find((item) => item.id === state.activeId);
    const isGroup = activeConv && activeConv.type === 'group';
    el.messageList.innerHTML = state.messages.map((msg) => messageHtml(msg, myId, isGroup)).join('');
    bindMessageActions();
    UI.bindAvatarFallback(el.messageList);
  }

  function messageHtml(msg, myId, isGroup) {
    // 系统消息居中显示
    if (msg.kind === 'system') {
      return `<div class="msg-row system"><div class="msg-col">
        <div class="bubble system">${UI.escapeHtml(msg.body)}</div>
      </div></div>`;
    }

    const mine = msg.sender && msg.sender.id === myId;
    const sender = msg.sender || { nickname: '未知用户' };
    const hidden = msg.revoked || msg.removed;

    let content = '';
    if (msg.removed) {
      // 管理员移除：文案与用户撤回区分开，避免误解
      const detail = msg.removed_by ? '已由管理员移除' : '消息已被移除';
      content = `<div class="bubble removed">${UI.escapeHtml(detail)}</div>`;
    } else if (msg.revoked) {
      const who = mine ? '你' : (sender.nickname || '对方');
      content = `<div class="bubble revoked">${UI.escapeHtml(who)}撤回了一条消息</div>`;
    } else if (msg.kind === 'image' && msg.attachment) {
      const src = api.downloadUrl(msg.attachment.id);
      const thumb = api.downloadUrl(msg.attachment.id, true);
      content = `<div class="bubble"><img class="msg-image" src="${UI.escapeHtml(thumb)}"
        alt="${UI.escapeHtml(msg.attachment.name)}" data-full="${UI.escapeHtml(src)}"></div>`;
    } else if (msg.kind === 'file' && msg.attachment) {
      const href = api.downloadUrl(msg.attachment.id);
      content = `<div class="bubble"><a class="file-card" href="${UI.escapeHtml(href)}" download>
        <span class="file-icon">📄</span>
        <span class="file-meta">
          <span class="file-name">${UI.escapeHtml(msg.attachment.name)}</span>
          <span class="file-size">${UI.fileSize(msg.attachment.size)}</span>
        </span>
      </a></div>`;
    } else {
      content = `<div class="bubble">${linkify(msg.body || '')}</div>`;
    }

    // 引用摘要：展示被引用消息的实际内容，被撤回/移除时不泄露原文
    let replyQuote = '';
    if (msg.reply_to) {
      const quoted = msg.reply_to.hidden
        ? '消息已不可见'
        : `${msg.reply_to.sender ? UI.escapeHtml(msg.reply_to.sender.nickname) + '：' : ''}${UI.escapeHtml(msg.reply_to.excerpt || '')}`;
      replyQuote = `<div class="reply-quote" data-goto-seq="${msg.reply_to.seq || ''}">${quoted}</div>`;
    } else if (msg.reply_to_id) {
      replyQuote = '<div class="reply-quote">引用了历史消息</div>';
    }

    let footer = '';
    const parts = [];
    if (isGroup && !mine) {
      parts.push(`<span class="msg-sender">${UI.escapeHtml(sender.nickname)}</span>`);
    }
    parts.push(`<span>${UI.messageTime(msg.created_at)}</span>`);
    if (!hidden) {
      if (mine) {
        if (msg.pending) {
          parts.push('<span class="read-state">发送中…</span>');
        } else if (msg.kind !== 'system') {
          const readCount = (msg.reads || []).filter((row) => row.user_id !== myId).length;
          if (readCount > 0) {
            parts.push('<span class="read-state read">已读</span>');
          } else if (msg.reader_count > 0) {
            parts.push(`<span class="read-state read">${msg.reader_count} 人已读</span>`);
          }
        }
        if (!msg.pending && canRevoke(msg)) {
          parts.push(`<span class="revoke-link" data-revoke="${msg.id}">撤回</span>`);
        }
      } else if (!msg.pending) {
        parts.push(`<span class="revoke-link" data-report="${msg.id}">举报</span>`);
      }
      if (!msg.pending) {
        parts.push(`<span class="revoke-link" data-reply="${msg.id}">引用</span>`);
      }
    }
    footer = `<div class="msg-foot">${parts.join('')}</div>`;

    return `<div class="msg-row ${mine ? 'mine' : 'other'} ${msg.pending ? 'pending' : ''} ${hidden ? 'hidden-content' : ''}" data-mid="${msg.id}">
      ${UI.avatarHtml(sender)}
      <div class="msg-col">
        ${content}
        ${replyQuote}
        ${footer}
      </div>
    </div>`;
  }

  /** 2 分钟内发送的消息才显示撤回入口（与后端规则一致） */
  function canRevoke(msg) {
    if (!msg.created_at) return false;
    const created = new Date(msg.created_at).getTime();
    return Date.now() - created < 2 * 60 * 1000;
  }

  /** 把纯文本中的 URL 变成可点击链接（先转义，再替换） */
  function linkify(text) {
    const escaped = UI.escapeHtml(text);
    return escaped.replace(/(https?:\/\/[^\s<]+)/g, (url) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  }

  function bindMessageActions() {
    el.messageList.querySelectorAll('[data-revoke]').forEach((node) => {
      node.addEventListener('click', () => revokeMessage(Number(node.dataset.revoke)));
    });
    el.messageList.querySelectorAll('[data-report]').forEach((node) => {
      node.addEventListener('click', () => reportMessage(Number(node.dataset.report)));
    });
    el.messageList.querySelectorAll('[data-reply]').forEach((node) => {
      node.addEventListener('click', () => setReply(Number(node.dataset.reply)));
    });
    el.messageList.querySelectorAll('[data-full]').forEach((node) => {
      node.addEventListener('click', () => UI.openLightbox(node.dataset.full));
      // 缩略图先上屏，原图加载完后替换，兼顾速度与清晰度
      if (node.dataset.loaded !== '1') {
        const full = new Image();
        full.onload = () => {
          node.src = node.dataset.full;
          node.dataset.loaded = '1';
        };
        full.src = node.dataset.full;
      }
    });
    // 点击引用条跳到被引用的消息（仅当它还在当前已加载范围内）
    el.messageList.querySelectorAll('[data-goto-seq]').forEach((node) => {
      node.addEventListener('click', () => gotoSeq(node.dataset.gotoSeq));
    });
  }

  /** 滚动定位到指定 seq 的消息并短暂高亮 */
  function gotoSeq(seq) {
    const target = Number(seq);
    if (!target) return;
    const message = state.messages.find((m) => m.seq === target);
    if (!message) {
      UI.toast('被引用的消息不在当前加载范围内');
      return;
    }
    const row = el.messageList.querySelector(`[data-mid="${message.id}"]`);
    if (!row) {
      UI.toast('被引用的消息不在当前加载范围内');
      return;
    }
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.add('flash');
    setTimeout(() => row.classList.remove('flash'), 1500);
  }

  /** 举报消息：仅做记录，由管理员在管理后台判断 */
  async function reportMessage(messageId) {
    const reason = window.prompt('举报理由（可留空）：', '');
    if (reason === null) return;
    try {
      await api.reportMessage(messageId, reason);
      UI.toast('举报已提交，管理员会尽快处理');
    } catch (err) {
      UI.toast(err.message);
    }
  }

  function buildEmojiPanel() {
    el.emojiPanel.innerHTML = UI.EMOJIS
      .map((emoji) => `<button type="button" class="emoji-btn">${emoji}</button>`)
      .join('');
    el.emojiPanel.querySelectorAll('.emoji-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        insertAtCursor(el.messageInput, btn.textContent);
        el.messageInput.focus();
      });
    });
  }

  function insertAtCursor(textarea, text) {
    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || 0;
    const value = textarea.value;
    textarea.value = value.slice(0, start) + text + value.slice(end);
    const caret = start + text.length;
    textarea.setSelectionRange(caret, caret);
    autoGrow(textarea);
  }

  // ============================================================== 会话操作
  async function openConversation(id) {
    if (!id) return;
    state.activeId = id;
    state.replyTo = null;
    state.messages = [];
    clearReply();
    el.chatPlaceholder.classList.add('hidden');
    el.chatView.classList.remove('hidden');
    el.chatApp.classList.add('show-chat');
    el.emojiPanel.classList.add('hidden');

    const conv = state.conversations.find((item) => item.id === id);
    // 进入会话即乐观清零未读并同步标题角标，避免等接口返回才消失
    if (conv && conv.unread_count > 0) {
      conv.unread_count = 0;
      refreshUnreadTitle();
    }
    renderActiveHeader(conv);
    renderConversationList();
    renderMessages();
    await loadMessages(id, false);
    markRead(id);
    el.messageInput.focus();
  }

  /** 依据本地会话数据重算浏览器标题的未读总数 */
  function refreshUnreadTitle() {
    const total = state.conversations.reduce(
      (sum, conv) => sum + (conv.is_muted ? 0 : (conv.unread_count || 0)), 0,
    );
    document.title = total > 0
      ? `(${total}) 聊天 · 微信风即时通信`
      : '聊天 · 微信风即时通信';
  }

  function renderActiveHeader(conv) {
    if (!conv) return;
    el.chatTitle.textContent = conv.title;
    if (conv.type === 'group') {
      el.chatSub.textContent = `${conv.summary ? conv.summary.member_count : ''} 位成员`;
    } else if (conv.peer) {
      el.chatSub.textContent = conv.peer.is_online ? '在线' : '离线';
    } else {
      el.chatSub.textContent = '';
    }
  }

  async function loadMessages(id, older) {
    if (!id) return;
    try {
      const params = { limit: 50 };
      if (older && state.messages.length) {
        params.before_seq = state.messages[0].seq;
      }
      const data = await api.messages(id, params);
      if (state.activeId !== id) return; // 期间切换了会话，丢弃结果
      const incoming = data.messages || [];
      // 记录消息发送者资料，供头像与昵称渲染使用
      incoming.forEach((msg) => { if (msg.sender) learnUsers([msg.sender]); });
      if (older) {
        state.messages = incoming.concat(state.messages);
      } else {
        // 保留本地尚未确认的乐观消息
        const pending = state.messages.filter((msg) => state.pending.has(msg.id));
        state.messages = incoming.concat(pending);
      }
      state.hasMore = Boolean(data.has_more);
      el.btnLoadMore.classList.toggle('hidden', !state.hasMore);
      renderMessages();
      if (!older) scrollToBottom();
    } catch (err) {
      if (err.status === 404) {
        UI.toast('该会话已不存在');
        state.activeId = null;
        el.chatView.classList.add('hidden');
        el.chatPlaceholder.classList.remove('hidden');
        el.chatApp.classList.remove('show-chat');
        await loadConversations();
      } else if (err.status !== 401) {
        UI.toast(`加载消息失败：${err.message}`);
      }
    }
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      el.messageScroll.scrollTop = el.messageScroll.scrollHeight;
    });
  }

  async function startDirect(peerId) {
    try {
      const data = await api.createDirect(peerId);
      await loadConversations();
      await openConversation(data.conversation.id);
      switchView('conversations');
    } catch (err) {
      UI.toast(err.message);
    }
  }

  async function sendCurrentMessage() {
    const text = el.messageInput.value.trim();
    if (!text || !state.activeId || state.sending) return;
    const conversationId = state.activeId;
    const clientMsgId = makeClientId();

    // 乐观上屏：先展示「发送中」，服务端返回后替换
    const optimistic = {
      id: `tmp-${clientMsgId}`,
      seq: Number.MAX_SAFE_INTEGER,
      kind: 'text',
      body: text,
      sender: state.me,
      created_at: new Date().toISOString(),
      pending: true,
      reply_to_id: state.replyTo ? state.replyTo.id : null,
      reads: [],
    };
    state.pending.add(optimistic.id);
    state.messages.push(optimistic);
    renderMessages();
    scrollToBottom();

    el.messageInput.value = '';
    autoGrow(el.messageInput);
    state.sending = true;
    try {
      const data = await api.sendMessage(conversationId, {
        body: text,
        client_msg_id: clientMsgId,
        reply_to_id: state.replyTo ? state.replyTo.id : undefined,
      });
      state.pending.delete(optimistic.id);
      state.messages = state.messages.filter((msg) => msg.id !== optimistic.id);
      upsertMessage(data.message);
      clearReply();
      renderMessages();
      scrollToBottom();
      loadConversations();
    } catch (err) {
      state.pending.delete(optimistic.id);
      state.messages = state.messages.filter((msg) => msg.id !== optimistic.id);
      renderMessages();
      el.messageInput.value = text; // 失败回填，避免用户重新打字
      UI.toast(`发送失败：${err.message}`);
    } finally {
      state.sending = false;
    }
  }

  function makeClientId() {
    return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function upsertMessage(message) {
    if (!message) return;
    if (message.sender) learnUsers([message.sender]);
    const index = state.messages.findIndex((msg) => msg.id === message.id);
    if (index >= 0) {
      state.messages[index] = message;
    } else if (message.conversation_id === state.activeId) {
      state.messages.push(message);
      state.messages.sort((a, b) => a.seq - b.seq);
    }
  }

  const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

  /** 从 file input 取文件并上传（点击按钮路径） */
  function uploadFrom(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    uploadFile(file).finally(() => { input.value = ''; });
  }

  /**
   * 上传并发送附件。
   *
   * 用 XMLHttpRequest 而不是 fetch：只有 xhr.upload 能拿到上传进度，
   * fetch 无法报告发送阶段进度。
   */
  function uploadFile(file) {
    if (!state.activeId) {
      UI.toast('请先选择一个会话');
      return Promise.resolve();
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      UI.toast('文件超过 20MB 上限');
      return Promise.resolve();
    }

    const conversationId = state.activeId;
    showUploadStatus(`正在上传 ${file.name}…`, 0);

    const form = new FormData();
    form.append('file', file);

    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/v1/attachments/');
      const token = api.__token ? api.__token() : '';
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      xhr.setRequestHeader('Accept', 'application/json');
      if (typeof api.csrfToken === 'function') xhr.setRequestHeader('X-CSRFToken', api.csrfToken());

      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.round((event.loaded / event.total) * 100);
        showUploadStatus(`正在上传 ${file.name}… ${percent}%`, percent);
      };

      xhr.onload = async () => {
        let payload = {};
        try { payload = JSON.parse(xhr.responseText || '{}'); } catch (err) { payload = {}; }
        if (xhr.status < 200 || xhr.status >= 300 || payload.ok === false) {
          hideUploadStatus();
          UI.toast(`上传失败：${(payload.error && payload.error.message) || xhr.status}`);
          resolve();
          return;
        }
        showUploadStatus('正在发送…', 100);
        try {
          await api.sendMessage(conversationId, {
            attachment_id: payload.data.attachment.id,
            client_msg_id: makeClientId(),
          });
          await loadMessages(conversationId, false);
          loadConversations();
        } catch (err) {
          UI.toast(`发送失败：${err.message}`);
        } finally {
          hideUploadStatus();
          resolve();
        }
      };

      xhr.onerror = () => {
        hideUploadStatus();
        UI.toast('上传失败：网络错误');
        resolve();
      };
      xhr.send(form);
    });
  }

  function showUploadStatus(text, percent) {
    if (!el.uploadStatus) return;
    el.uploadStatus.classList.remove('hidden');
    el.uploadStatus.innerHTML = `<div class="upload-bar"><div class="upload-bar-fill"
      style="width:${Math.max(0, Math.min(100, percent))}%"></div></div>
      <span class="upload-text">${UI.escapeHtml(text)}</span>`;
  }

  function hideUploadStatus() {
    if (!el.uploadStatus) return;
    el.uploadStatus.classList.add('hidden');
    el.uploadStatus.innerHTML = '';
  }

  async function revokeMessage(messageId) {
    try {
      const data = await api.revokeMessage(messageId);
      upsertMessage(data.message);
      renderMessages();
      loadConversations();
    } catch (err) {
      UI.toast(err.message);
    }
  }

  function setReply(messageId) {
    const target = state.messages.find((msg) => msg.id === messageId);
    if (!target) return;
    state.replyTo = target;
    const text = target.kind === 'text'
      ? (target.body || '').slice(0, 40)
      : (target.kind === 'image' ? '[图片]' : '[文件]');
    el.replyPreview.querySelector('.reply-text').textContent = `引用：${text}`;
    el.replyPreview.classList.remove('hidden');
    el.messageInput.focus();
  }

  function clearReply() {
    state.replyTo = null;
    el.replyPreview.classList.add('hidden');
  }

  async function markRead(id, useBeacon) {
    const conv = state.conversations.find((item) => item.id === id);
    if (!conv) return;
    const latest = state.messages.length
      ? state.messages[state.messages.length - 1].seq
      : conv.last_seq;
    if (latest <= (conv.last_read_seq || 0) && conv.unread_count === 0) return;
    if (useBeacon && navigator.sendBeacon) {
      // 页面卸载时用 sendBeacon，避免请求被取消
      try {
        const payload = new Blob([JSON.stringify({ up_to_seq: latest })], { type: 'application/json' });
        navigator.sendBeacon(`/api/v1/conversations/${id}/read/`, payload);
        return;
      } catch (err) {
        // 回退到普通请求
      }
    }
    try {
      const data = await api.markRead(id, latest);
      conv.unread_count = 0;
      conv.last_read_seq = data.last_read_seq;
      renderConversationList();
      refreshUnreadTitle();
    } catch (err) {
      // 已读上报失败不打扰用户
    }
  }

  /** 输入中状态：合并短时间内的多次输入，减少请求 */
  function notifyTyping() {
    if (!state.activeId) return;
    const now = Date.now();
    const last = state.typing[`self-${state.activeId}`] || 0;
    if (now - last < 2500) return;
    state.typing[`self-${state.activeId}`] = now;
    api.sendTyping(state.activeId).catch(() => {});
  }

  function refreshTypingHints() {
    const now = Date.now();
    Object.keys(state.typingTimers).forEach((key) => {
      if (state.typingTimers[key] < now) {
        delete state.typingTimers[key];
        delete state.typing[key];
      }
    });
    const until = state.typing[state.activeId];
    if (until && until > now) {
      el.typingHint.textContent = '对方正在输入…';
    } else {
      el.typingHint.textContent = '';
    }
  }

  // ============================================================== 好友操作
  async function addFriend(userId) {
    try {
      const data = await api.sendFriendRequest(userId, '');
      UI.toast(data.auto_accepted ? '对方已向你发起申请，已互为好友' : '好友申请已发送');
      await Promise.all([loadFriends(), loadFriendRequests()]);
    } catch (err) {
      UI.toast(err.message);
    }
  }

  async function handleRequest(requestId, action) {
    try {
      await api.handleFriendRequest(requestId, action);
      UI.toast(action === 'accept' ? '已添加为好友' : '已拒绝');
      await Promise.all([loadFriendRequests(), loadFriends()]);
    } catch (err) {
      UI.toast(err.message);
    }
  }

  // ============================================================== 群聊操作
  async function openGroupModal() {
    await loadFriends();
    el.groupTitle.value = '';
    el.groupMemberPicker.innerHTML = state.friends.length
      ? state.friends.map((friend) => `<li>
          <label>
            <input type="checkbox" value="${friend.id}">
            ${UI.avatarHtml(friend, 'small')}
            <span>${UI.escapeHtml(friend.nickname)}</span>
          </label>
        </li>`).join('')
      : '<li class="empty">还没有好友，先添加好友再建群</li>';
    UI.bindAvatarFallback(el.groupMemberPicker);
    el.groupModal.classList.remove('hidden');
  }

  async function createGroup() {
    const title = el.groupTitle.value.trim();
    const memberIds = Array.from(el.groupMemberPicker.querySelectorAll('input:checked'))
      .map((input) => Number(input.value));
    if (!title) {
      UI.toast('请填写群名称');
      return;
    }
    try {
      const data = await api.createGroup(title, memberIds);
      el.groupModal.classList.add('hidden');
      await loadConversations();
      await openConversation(data.conversation.id);
      switchView('conversations');
    } catch (err) {
      UI.toast(err.message);
    }
  }

  async function openConversationInfo() {
    if (!state.activeId) return;
    try {
      const data = await api.conversationDetail(state.activeId);
      const conv = data.conversation;
      const members = data.members || [];
      learnUsers(members);
      const isGroup = conv.type === 'group';
      // 群主与群管理员才有管理权限；后端同样会校验，这里只做展示收敛
      const canManage = isGroup && (conv.my_role === 'owner' || conv.my_role === 'admin');
      const meId = state.me ? state.me.id : 0;

      el.infoTitle.textContent = isGroup ? '群聊信息' : '聊天信息';

      const memberChips = members.map((member) => {
        const roleLabel = member.role === 'owner' ? '（群主）' : (member.role === 'admin' ? '（管理员）' : '');
        const canRemove = canManage && member.role !== 'owner' && member.id !== meId;
        return `<span class="member-chip">
          ${UI.avatarHtml(member, 'small')}
          <span>${UI.escapeHtml(member.nickname)}${roleLabel}</span>
          ${canRemove ? `<button class="chip-remove" data-remove-member="${member.id}"
             title="移出群聊">✕</button>` : ''}
        </span>`;
      }).join('');

      const renameRow = canManage
        ? `<div class="modal-section-title">群管理</div>
           <div class="settings-row">
             <input type="text" id="infoGroupTitle" maxlength="64"
                    value="${UI.escapeHtml(conv.title)}" placeholder="群名称">
             <button class="btn btn-mini" id="btnRenameGroup">改名</button>
           </div>
           <div class="settings-row">
             <select id="infoAddMemberSelect"><option value="">选择好友加入…</option></select>
             <button class="btn btn-mini" id="btnAddMember">添加</button>
           </div>`
        : '';

      el.infoBody.innerHTML = `
        <div class="info-row"><span class="k">名称</span><span>${UI.escapeHtml(conv.title)}</span></div>
        <div class="info-row"><span class="k">类型</span><span>${isGroup ? '群聊' : '单聊'}</span></div>
        <div class="info-row"><span class="k">成员数</span><span>${conv.summary ? conv.summary.member_count : members.length}</span></div>
        <div class="info-row"><span class="k">我的角色</span><span>${UI.escapeHtml(conv.my_role)}</span></div>
        <div class="info-row"><span class="k">免打扰</span><span>
          <label><input type="checkbox" id="infoMuted" ${conv.is_muted ? 'checked' : ''}> 开启</label></span></div>
        <div class="info-row"><span class="k">置顶</span><span>
          <label><input type="checkbox" id="infoPinned" ${conv.is_pinned ? 'checked' : ''}> 开启</label></span></div>
        ${renameRow}
        <div class="modal-section-title">成员</div>
        <div class="member-grid">${memberChips || '<span class="muted">无</span>'}</div>
      `;
      UI.bindAvatarFallback(el.infoBody);
      el.infoBody.querySelector('#infoMuted').addEventListener('change', (event) => {
        updateMyConversation({ is_muted: event.target.checked });
      });
      el.infoBody.querySelector('#infoPinned').addEventListener('change', (event) => {
        updateMyConversation({ is_pinned: event.target.checked });
      });

      if (canManage) {
        const memberIds = new Set(members.map((m) => m.id));
        el.infoBody.querySelector('#btnRenameGroup').addEventListener('click', renameGroup);
        const select = el.infoBody.querySelector('#infoAddMemberSelect');
        state.friends
          .filter((friend) => !memberIds.has(friend.id))
          .forEach((friend) => {
            const option = document.createElement('option');
            option.value = friend.id;
            option.textContent = friend.nickname;
            select.appendChild(option);
          });
        el.infoBody.querySelector('#btnAddMember').addEventListener('click', () => {
          addGroupMembers(select.value);
        });
      }

      el.infoBody.querySelectorAll('[data-remove-member]').forEach((btn) => {
        btn.addEventListener('click', () => removeGroupMember(Number(btn.dataset.removeMember)));
      });

      el.infoModal.classList.remove('hidden');
    } catch (err) {
      UI.toast(err.message);
    }
  }

  async function renameGroup() {
    const input = el.infoBody.querySelector('#infoGroupTitle');
    const title = (input.value || '').trim();
    if (!title) {
      UI.toast('群名称不能为空');
      return;
    }
    try {
      await api.updateConversation(state.activeId, { title });
      await loadConversations();
      const conv = state.conversations.find((item) => item.id === state.activeId);
      renderActiveHeader(conv);
      UI.toast('群名称已更新');
      openConversationInfo();
    } catch (err) {
      UI.toast(err.message);
    }
  }

  async function addGroupMembers(memberId) {
    const id = Number(memberId);
    if (!id) {
      UI.toast('请先选择要添加的好友');
      return;
    }
    try {
      await api.addMembers(state.activeId, [id]);
      await loadConversations();
      UI.toast('已添加成员');
      openConversationInfo();
    } catch (err) {
      UI.toast(err.message);
    }
  }

  async function removeGroupMember(memberId) {
    if (!window.confirm('确定将该成员移出群聊吗？')) return;
    try {
      await api.removeMember(state.activeId, memberId);
      await loadConversations();
      UI.toast('已移出群聊');
      openConversationInfo();
    } catch (err) {
      UI.toast(err.message);
    }
  }

  async function updateMyConversation(patch) {
    try {
      await api.updateConversation(state.activeId, patch);
      await loadConversations();
    } catch (err) {
      UI.toast(err.message);
    }
  }

  // ============================================================== 设置与搜索
  function openSettings() {
    if (!state.me) return;
    el.profileNickname.value = state.me.nickname || '';
    el.profileBio.value = state.me.bio || '';
    const prefs = state.preferences || {};
    el.prefTheme.value = prefs.theme || 'system';
    el.prefFont.value = prefs.font_size || 'normal';
    el.prefBubble.value = prefs.bubble_style || 'classic';
    el.prefSound.checked = prefs.notify_sound !== false;
    el.prefDesktop.checked = Boolean(prefs.notify_desktop);
    el.globalSearchResults.classList.add('hidden');
    el.globalSearchResults.innerHTML = '';
    el.settingsModal.classList.remove('hidden');
  }

  async function saveSettings() {
    const nickname = (el.profileNickname.value || '').trim();
    const bio = (el.profileBio.value || '').trim();

    try {
      // 1) 资料
      const profile = await api.updateMe({ nickname, bio });
      state.me = profile.user;
      state.users[state.me.id] = state.me;
      el.myName.textContent = state.me.nickname;

      // 2) 偏好
      const prefData = await api.updatePreferences({
        theme: el.prefTheme.value,
        font_size: el.prefFont.value,
        bubble_style: el.prefBubble.value,
        notify_sound: el.prefSound.checked,
        notify_desktop: el.prefDesktop.checked,
      });
      applyPreferences(prefData.preferences);

      el.settingsModal.classList.add('hidden');
      UI.toast('设置已保存');
    } catch (err) {
      UI.toast(err.message);
    }
  }

  function pickAvatarFromSettings() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      try {
        const data = await api.uploadAvatar(file);
        state.me = data.user;
        state.users[state.me.id] = state.me;
        renderMyAvatar();
        setupMyAvatar();
        el.myName.textContent = state.me.nickname;
        UI.toast('头像已更新');
        loadConversations();
      } catch (err) {
        UI.toast(err.message);
      }
    });
    input.click();
  }

  /** 全局搜索：消息（限本人会话）、用户、会话 */
  async function runGlobalSearch() {
    const keyword = (el.globalSearchInput.value || '').trim();
    if (!keyword) {
      UI.toast('请输入搜索关键词');
      return;
    }
    const box = el.globalSearchResults;
    box.classList.remove('hidden');
    box.innerHTML = '<li class="empty">搜索中…</li>';
    try {
      const data = await api.search({ q: keyword, type: 'all', limit: 20 });
      const parts = [];

      (data.messages || []).forEach((row) => {
        const msg = row.message;
        const sender = msg.sender ? msg.sender.nickname : '系统';
        const preview = msg.kind === 'image' ? '[图片]'
          : (msg.kind === 'file' ? '[文件]' : (msg.body || ''));
        parts.push(`<li class="search-item" data-open-conv="${row.conversation.id}" data-seq="${msg.seq}">
          <span class="search-kind">消息</span>
          <span class="search-main">${UI.escapeHtml(sender)}：${UI.escapeHtml(preview.slice(0, 50))}</span>
          <span class="search-sub">${UI.escapeHtml(row.conversation.title)}</span>
        </li>`);
      });

      (data.conversations || []).forEach((conv) => {
        parts.push(`<li class="search-item" data-open-conv="${conv.id}">
          <span class="search-kind">会话</span>
          <span class="search-main">${UI.escapeHtml(conv.title)}</span>
          <span class="search-sub">${conv.type === 'group' ? '群聊' : '单聊'}</span>
        </li>`);
      });

      (data.users || []).forEach((user) => {
        parts.push(`<li class="search-item" data-chat-user="${user.id}">
          <span class="search-kind">用户</span>
          <span class="search-main">${UI.escapeHtml(user.nickname)}</span>
          <span class="search-sub">@${UI.escapeHtml(user.username)}</span>
        </li>`);
      });

      box.innerHTML = parts.length ? parts.join('') : '<li class="empty">没有匹配结果</li>';
      box.querySelectorAll('[data-open-conv]').forEach((node) => {
        node.addEventListener('click', () => {
          el.settingsModal.classList.add('hidden');
          openConversation(Number(node.dataset.openConv)).then(() => {
            if (node.dataset.seq) gotoTargetSeq(Number(node.dataset.seq));
          });
        });
      });
      box.querySelectorAll('[data-chat-user]').forEach((node) => {
        node.addEventListener('click', () => {
          el.settingsModal.classList.add('hidden');
          startDirect(Number(node.dataset.chatUser));
        });
      });
    } catch (err) {
      box.innerHTML = `<li class="empty">搜索失败：${UI.escapeHtml(err.message)}</li>`;
    }
  }

  /** 搜索命中后，等消息加载完再定位 */
  function gotoTargetSeq(seq) {
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      const message = state.messages.find((m) => m.seq === seq);
      if (message) {
        clearInterval(timer);
        gotoSeq(seq);
      } else if (tries > 20) {
        clearInterval(timer);
        UI.toast('该消息不在最近加载范围内');
      }
    }, 150);
  }

  async function leaveConversation() {
    if (!state.activeId) return;
    if (!window.confirm('确定退出该会话吗？单聊退出后将删除全部历史消息。')) return;
    try {
      await api.leaveConversation(state.activeId);
      el.infoModal.classList.add('hidden');
      state.activeId = null;
      state.messages = [];
      el.chatView.classList.add('hidden');
      el.chatPlaceholder.classList.remove('hidden');
      el.chatApp.classList.remove('show-chat');
      await loadConversations();
      UI.toast('已退出会话');
    } catch (err) {
      UI.toast(err.message);
    }
  }

  // ============================================================ 实时事件流
  /**
   * 启动事件流。
   *
   * 具体传输方式由 Transport 层决定（当前长轮询，二期 WebSocket），
   * 本函数只关心「收到事件后怎么处理」，因此二期换传输不需要改这里。
   */
  function startEventStream() {
    if (state.transport) return;
    const capabilities = state.capabilities || { transport: ['longpoll'] };
    state.transport = window.Transport.createTransport(capabilities.transport);
    state.transport.setCursor(state.cursor);
    state.transport.start({
      onEvents: (events) => handleEvents(events),
      onStatus: (status) => {
        if (status === 'unauthorized') {
          window.location.href = '/login/';
        } else if (status === 'disconnected') {
          UI.toast('与服务器连接中断，正在重试…');
        }
      },
    });
  }

  function stopEventStream() {
    if (state.transport) {
      state.transport.stop();
      state.transport = null;
    }
  }

  async function handleEvents(events) {
    if (!events.length) return;
    let needConversations = false;
    let needMessages = false;
    let needRefresh = false;
    let activeHasNew = false;

    events.forEach((event) => {
      const payload = event.payload || {};
      switch (event.type) {
        case 'message.new':
          if (payload.sender) learnUsers([payload.sender]);
          if (payload.conversation_id === state.activeId) {
            upsertMessage(payload);
            needMessages = true;
            if (payload.sender && state.me && payload.sender.id !== state.me.id) {
              activeHasNew = true;
            }
          }
          needConversations = true;
          break;
        case 'message.revoked':
          if (payload.message) {
            upsertMessage(payload.message);
            needMessages = true;
          }
          needConversations = true;
          break;
        case 'message.removed':
          // 管理员移除：本地立即脱敏，避免等下一次刷新才隐藏内容
          if (payload.message) {
            const incoming = payload.message;
            const index = state.messages.findIndex((m) => m.id === incoming.id);
            if (index >= 0) {
              state.messages[index] = incoming;
            } else if (incoming.conversation_id === state.activeId) {
              upsertMessage(incoming);
            }
            needMessages = true;
          }
          needConversations = true;
          break;
        case 'read.receipt':
          // 已读回执的 reads 字段来自服务端，必须重新拉取才能看到「已读」
          if (payload.conversation_id === state.activeId) needRefresh = true;
          break;
        case 'typing':
          if (payload.conversation_id) {
            state.typing[payload.conversation_id] = Date.now() + (payload.ttl || 5) * 1000;
            state.typingTimers[payload.conversation_id] = Date.now() + (payload.ttl || 5) * 1000;
          }
          break;
        case 'conversation.updated':
          needConversations = true;
          break;
        case 'friend.request':
          UI.toast(`${payload.from_user ? payload.from_user.nickname : '有人'} 请求添加你为好友`);
          loadFriendRequests();
          break;
        case 'friend.accepted':
          UI.toast(`${payload.friend ? payload.friend.nickname : '对方'} 已同意你的好友申请`);
          loadFriends();
          break;
        default:
          break;
      }
    });

    if (needConversations) await loadConversations();
    if (needMessages) renderMessages();
    if (needRefresh) {
      // 合并短时间内的多次回执，避免频繁请求
      scheduleRefresh();
    }
    if (activeHasNew) {
      scrollToBottom();
      markRead(state.activeId);
    }
  }

  let refreshTimer = null;
  /** 延迟合并刷新：已读回执可能连续到达 */
  function scheduleRefresh() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      if (state.activeId) loadMessages(state.activeId, false);
    }, 300);
  }

  // ------------------------------------------------------------------ 入口
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
