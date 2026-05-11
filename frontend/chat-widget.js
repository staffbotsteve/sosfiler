(function () {
  const WIDGET_ID = 'sosfilerChatWidget';
  const SESSION_KEY = 'sosfiler_chat_sid';

  function getSessionId() {
    let sid = sessionStorage.getItem(SESSION_KEY);
    if (!sid) {
      sid = 'web-' + Date.now() + '-' + Math.random().toString(16).slice(2);
      sessionStorage.setItem(SESSION_KEY, sid);
    }
    return sid;
  }

  function safeText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function readStorageJson(key) {
    try {
      return JSON.parse(localStorage.getItem(key) || '{}');
    } catch (_) {
      return {};
    }
  }

  function buildContext() {
    const params = new URLSearchParams(window.location.search);
    const storedUser = readStorageJson('sosfiler_user');
    const orderData = window.currentOrderData || {};
    const stateSelect = document.getElementById('stateSelect') || document.getElementById('formationState');
    const entitySelect = document.getElementById('entityType') || document.getElementById('formationEntityType');
    const state = (
      orderData.state ||
      params.get('state') ||
      (stateSelect ? stateSelect.value : '') ||
      ''
    ).toUpperCase();
    return {
      state,
      entity_type: orderData.entity_type || (entitySelect ? entitySelect.value : '') || 'LLC',
      product_type: params.get('product_type') || orderData.product_type || 'formation',
      order_id: window.orderId || params.get('order_id') || orderData.order_id || orderData.id || '',
      email: orderData.email || storedUser.email || '',
      page: window.location.pathname || '/'
    };
  }

  function addMessage(container, role, text) {
    const el = document.createElement('div');
    el.className = 'sos-chat-message ' + role;
    el.textContent = text;
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
    return el;
  }

  function addMeta(container, text) {
    if (!text) return;
    const el = document.createElement('div');
    el.className = 'sos-chat-meta';
    el.textContent = text;
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
  }

  function formatSources(sources) {
    if (!Array.isArray(sources) || !sources.length) return '';
    const labels = sources
      .map((source) => safeText(source.source || source.label || source.title || source.url || 'verified source'))
      .filter(Boolean)
      .slice(0, 3);
    return labels.length ? 'Sources: ' + labels.join(', ') : '';
  }

  async function sendMessage(input, messages, button) {
    const message = input.value.trim();
    if (!message) return;
    input.value = '';
    button.disabled = true;
    addMessage(messages, 'user', message);
    const pending = addMessage(messages, 'bot', 'Checking verified SOSFiler sources...');
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          session_id: getSessionId(),
          context: buildContext()
        })
      });
      const data = await res.json().catch(() => ({}));
      pending.textContent = data.response || data.detail || 'SOSFiler could not answer that yet.';
      const sourceLine = formatSources(data.sources);
      if (sourceLine) addMeta(messages, sourceLine);
      if (data.escalated && data.ticket_id) addMeta(messages, 'Ticket created: ' + data.ticket_id);
    } catch (_) {
      pending.textContent = 'SOSFiler chat is temporarily unavailable.';
    } finally {
      button.disabled = false;
      input.focus();
      messages.scrollTop = messages.scrollHeight;
    }
  }

  function initWidget() {
    if (document.getElementById(WIDGET_ID)) return;
    const widget = document.createElement('div');
    widget.id = WIDGET_ID;
    widget.className = 'sos-chat-widget';
    widget.innerHTML = [
      '<button class="sos-chat-button" type="button" aria-label="Open SOSFiler chat" title="Ask SOSFiler">?</button>',
      '<section class="sos-chat-panel" aria-label="SOSFiler chat">',
      '  <div class="sos-chat-header">',
      '    <div>',
      '      <div class="sos-chat-title">SOSFiler Assistant</div>',
      '      <div class="sos-chat-subtitle">Verified answers, operator escalation when needed</div>',
      '    </div>',
      '    <button class="sos-chat-close" type="button" aria-label="Close SOSFiler chat">&times;</button>',
      '  </div>',
      '  <div class="sos-chat-messages" role="log" aria-live="polite"></div>',
      '  <form class="sos-chat-form">',
      '    <input class="sos-chat-input" type="text" autocomplete="off" placeholder="Ask a filing question">',
      '    <button class="sos-chat-send" type="submit">Send</button>',
      '  </form>',
      '</section>'
    ].join('');
    document.body.appendChild(widget);

    const toggle = widget.querySelector('.sos-chat-button');
    const close = widget.querySelector('.sos-chat-close');
    const panel = widget.querySelector('.sos-chat-panel');
    const form = widget.querySelector('.sos-chat-form');
    const input = widget.querySelector('.sos-chat-input');
    const button = widget.querySelector('.sos-chat-send');
    const messages = widget.querySelector('.sos-chat-messages');

    addMessage(messages, 'bot', 'Hi. Ask me about SOSFiler pricing, filing steps, status, or document access. If I cannot verify the answer, I will create an operator ticket.');

    toggle.addEventListener('click', () => {
      panel.classList.toggle('open');
      if (panel.classList.contains('open')) input.focus();
    });
    close.addEventListener('click', () => panel.classList.remove('open'));
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      sendMessage(input, messages, button);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWidget);
  } else {
    initWidget();
  }
})();
