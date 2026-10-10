/* Repurposes the starter's chat interaction and Swagger message envelope. */
(() => {
  'use strict';
  const messages = document.querySelector('.messages-content');
  const input = document.querySelector('.message-input');
  const send = document.querySelector('.message-submit');
  const status = document.getElementById('connection-status');
  const reset = document.getElementById('new-chat');
  const key = 'dining-concierge-session';
  const uuid = () => {
    // S3 website endpoints use HTTP, where crypto.randomUUID may be unavailable.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  };
  let sessionId;
  try { sessionId = localStorage.getItem(key) || uuid(); localStorage.setItem(key, sessionId); }
  catch (_) { sessionId = uuid(); }
  function add(text, personal = false, error = false) {
    const node = document.createElement('div');
    node.className = `message${personal ? ' message-personal' : ''}${error ? ' message-error' : ''}`;
    node.textContent = text; // User/bot content is never interpreted as HTML.
    messages.append(node);
    node.scrollIntoView({block: 'nearest'});
  }
  add("Hi there, how can I help? I can suggest restaurants in Manhattan. Tell me you’d like restaurant suggestions to get started.");
  const endpoint = (window.CONCIERGE_CONFIG?.apiBaseUrl || '').replace(/\/$/, '');
  if (!endpoint) status.textContent = 'Deployment required: the API endpoint has not been configured.';
  let busy = false;
  async function submit(event) {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    if (!endpoint) { status.textContent = 'The API is not deployed yet. Follow the project setup instructions.'; return; }
    busy = true; send.disabled = true; reset.disabled = true; input.disabled = true;
    add(text, true); input.value = ''; status.textContent = 'Concierge is typing…';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 28000);
    try {
      const response = await fetch(`${endpoint}/chatbot`, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Session-Id': sessionId},
        body: JSON.stringify({messages: [{type: 'unstructured', unstructured: {text}}]}),
        signal: controller.signal
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || 'Unable to reach the concierge.');
      if (!body.messages?.length) throw new Error('The concierge returned an empty response.');
      body.messages.forEach(message => add(message.unstructured.text));
      status.textContent = '';
    } catch (error) {
      add(error.name === 'AbortError' ? 'The request timed out. Please try again.' : error.message, false, true);
      input.value = text; status.textContent = 'Your message is ready to retry.';
    } finally {
      clearTimeout(timeout); busy = false; send.disabled = false; reset.disabled = false; input.disabled = false; input.focus();
    }
  }
  document.getElementById('chat-form').addEventListener('submit', submit);
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) submit(event);
  });
  reset.addEventListener('click', () => {
    sessionId = uuid();
    try { localStorage.setItem(key, sessionId); } catch (_) {}
    messages.replaceChildren(); add('Let’s start a new conversation. What would you like to eat?');
    input.focus();
  });
})();
