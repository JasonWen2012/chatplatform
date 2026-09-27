/* ==========================================================================
   UI 工具：转义、时间格式化、头像、提示条、图片预览
   暴露为全局 window.UI，避免引入构建工具
   ========================================================================== */
(function () {
  'use strict';

  const EMOJIS = [
    '😀', '😃', '😄', '😁', '😆', '😅', '😂', '🤣', '😊', '😇',
    '🙂', '🙃', '😉', '😌', '😍', '🥰', '😘', '😗', '😋', '😛',
    '😜', '🤪', '😝', '🤗', '🤔', '🤨', '😐', '😑', '😶', '🙄',
    '😏', '😴', '😪', '😢', '😭', '😤', '😠', '😡', '🥺', '😱',
    '😳', '🤯', '😎', '🤓', '🧐', '🥳', '😷', '🤒', '🤝', '🙏',
    '👍', '👎', '👌', '✌️', '🤞', '💪', '👏', '🙌', '👋', '🤙',
    '❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '💔', '💕', '💖',
    '🎉', '🎊', '🎁', '🔥', '✨', '⭐', '🌟', '💡', '✅', '❌',
    '🍎', '🍌', '🍉', '🍇', '🍓', '🍔', '🍕', '🍜', '🍚', '☕',
    '🍺', '🍻', '🥂', '🎂', '🍰', '🐱', '🐶', '🐼', '🐰', '🦊',
    '🌸', '🌺', '🌈', '⛅', '🌙', '⚡', '💧', '🚀', '🎵', '⚽',
  ];

  /** HTML 转义：所有来自 API 的文本都必须经过此函数再插入 DOM */
  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** 会话列表用的简短时间：今天显示 HH:MM，本周显示星期，更早显示日期 */
  function shortTime(iso) {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    if (sameDay) {
      return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }
    const diffDays = Math.floor((now - date) / 86400000);
    if (diffDays < 7) {
      return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
    }
    return `${date.getMonth() + 1}/${date.getDate()}`;
  }

  /** 消息时间戳：今天只显示时刻，跨天带上日期 */
  function messageTime(iso) {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const now = new Date();
    const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    if (date.toDateString() === now.toDateString()) return time;
    return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
  }

  function pad(num) {
    return String(num).padStart(2, '0');
  }

  /** 字节数转可读大小 */
  function fileSize(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }

  /** 生成头像：有 URL 用图片，否则用昵称首字绘制彩色方块 */
  function avatarHtml(user, size) {
    const cls = size === 'small' ? 'avatar avatar-text' : 'avatar';
    const name = (user && (user.nickname || user.username)) || '?';
    if (user && user.avatar) {
      return `<img class="${cls}" src="${escapeHtml(user.avatar)}" alt="${escapeHtml(name)}" data-name="${escapeHtml(name)}">`;
    }
    const color = colorFor(name);
    return `<span class="${cls}" style="background:${color}" data-name="${escapeHtml(name)}">${escapeHtml(name.slice(0, 1).toUpperCase())}</span>`;
  }

  /** 由名字稳定散列出颜色，保证同一用户颜色固定 */
  function colorFor(name) {
    let hash = 0;
    for (let i = 0; i < name.length; i += 1) {
      hash = (hash * 31 + name.charCodeAt(i)) % 360;
    }
    return `hsl(${hash}, 42%, 58%)`;
  }

  /** 兜底：头像图片加载失败时降级为文字头像 */
  function bindAvatarFallback(root) {
    (root || document).querySelectorAll('img.avatar').forEach((img) => {
      img.addEventListener('error', () => {
        const name = img.dataset.name || '?';
        const span = document.createElement('span');
        span.className = img.className + ' avatar-text';
        span.style.background = colorFor(name);
        span.textContent = name.slice(0, 1).toUpperCase();
        img.replaceWith(span);
      }, { once: true });
    });
  }

  let toastTimer = null;
  function toast(message, duration) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.remove('hidden');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add('hidden'), duration || 2600);
  }

  /** 点击图片放大预览 */
  function openLightbox(src) {
    const box = document.createElement('div');
    box.className = 'lightbox';
    box.innerHTML = `<img src="${escapeHtml(src)}" alt="预览">`;
    box.addEventListener('click', () => box.remove());
    document.body.appendChild(box);
  }

  window.UI = {
    EMOJIS,
    escapeHtml,
    shortTime,
    messageTime,
    fileSize,
    avatarHtml,
    bindAvatarFallback,
    toast,
    openLightbox,
  };
})();
