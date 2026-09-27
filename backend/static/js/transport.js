/* ==========================================================================
   传输层抽象：把「如何接收实时事件」与「业务如何处理事件」解耦
   --------------------------------------------------------------------------
   一期实现：HTTP 长轮询（longpoll）
   二期实现：WebSocket（新增 WsTransport，事件信封与游标语义完全一致）

   统一接口（二期实现必须满足）：
     Transport.mode                       -> 'longpoll' | 'websocket'
     Transport.start({ cursor, onEvents, onStatus, onError })
     Transport.stop()
     Transport.setCursor(cursor)           // 断线续传用
     Transport.getCursor()

   事件信封（与后端 /api/v1/updates/ 返回的一致）：
     { event_id, type, conversation_id, payload, ts }

   因此二期只需：
     1. 新增 static/js/ws-transport.js，实现同一接口；
     2. 在 app.js 的 boot() 里按 capabilities.transport 选择实现。
   业务代码（事件处理、会话渲染）完全不用改。
   ========================================================================== */
(function () {
  'use strict';

  const BASE = '/api/v1';

  class LongPollTransport {
    constructor() {
      this.mode = 'longpoll';
      this.cursor = 0;
      this.running = false;
      this.controller = null;
      this.handlers = {};
      this.retryDelay = 3000;
    }

    setCursor(cursor) {
      this.cursor = Number(cursor) || 0;
      return this;
    }

    getCursor() {
      return this.cursor;
    }

    /** 启动事件流循环；返回一个 stop 函数便于调用方直接取消 */
    start(options) {
      this.handlers = options || {};
      this.running = true;
      this.loop();
      return () => this.stop();
    }

    stop() {
      this.running = false;
      if (this.controller) {
        try { this.controller.abort(); } catch (err) { /* 忽略 */ }
        this.controller = null;
      }
    }

    /** 立即中断当前挂起请求并重连（切会话、页面重新可见时使用） */
    restart() {
      if (!this.running) return;
      if (this.controller) {
        try { this.controller.abort(); } catch (err) { /* 忽略 */ }
      }
    }

    async loop() {
      while (this.running) {
        const controller = new AbortController();
        this.controller = controller;
        try {
          const response = await fetch(`${BASE}/updates/`, {
            method: 'POST',
            headers: this._headers(),
            body: JSON.stringify({ cursor: this.cursor, timeout: 25 }),
            signal: controller.signal,
          });

          if (response.status === 401) {
            this._emitStatus('unauthorized');
            this.running = false;
            return;
          }
          const text = await response.text();
          let payload = {};
          try { payload = text ? JSON.parse(text) : {}; } catch (err) { payload = {}; }
          if (!response.ok || payload.ok === false) {
            this._emitStatus('error', payload);
            await this._sleep(this.retryDelay);
            continue;
          }

          const data = payload.data || {};
          this.cursor = Number(data.cursor) || this.cursor;
          this._emitStatus('connected');
          const events = data.events || [];
          if (events.length && typeof this.handlers.onEvents === 'function') {
            await this.handlers.onEvents(events, this);
          }
        } catch (err) {
          if (err && err.name === 'AbortError') {
            // 主动中断：立刻重连，不进入退避
            continue;
          }
          this._emitStatus('disconnected', err);
          await this._sleep(this.retryDelay);
        }
      }
    }

    _headers() {
      const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
      const token = window.Api && window.Api.__token ? window.Api.__token() : '';
      if (token) headers.Authorization = `Bearer ${token}`;
      return headers;
    }

    _emitStatus(status, detail) {
      if (typeof this.handlers.onStatus === 'function') {
        try { this.handlers.onStatus(status, detail); } catch (err) { /* 忽略 */ }
      }
      if (status === 'error' && typeof this.handlers.onError === 'function') {
        try { this.handlers.onError(detail); } catch (err) { /* 忽略 */ }
      }
    }

    _sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }
  }

  /**
   * 按能力声明选择传输实现。
   * @param {string[]} transports 服务端 capabilities.transport，如 ['longpoll']
   */
  function createTransport(transports) {
    const list = transports || ['longpoll'];
    // 二期：此处优先判断 'websocket' 并返回 WsTransport
    if (list.includes('longpoll')) return new LongPollTransport();
    throw new Error(`没有可用的实时传输方式：${list.join(', ')}`);
  }

  window.Transport = {
    LongPollTransport,
    createTransport,
    /** 二期实现的参照接口说明 */
    requiredMethods: ['start', 'stop', 'setCursor', 'getCursor'],
  };
})();
