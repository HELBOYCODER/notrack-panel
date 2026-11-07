/* NoTrack Panel — frontend */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const el = {
    sidebar: $('sidebar'), closeSide: $('closeSide'), openSide: $('openSide'),
    flow: $('flow'), empty: $('emptyState'),
    input: $('input'), composer: $('composer'),
    sendBtn: $('sendBtn'), stopBtn: $('stopBtn'),
    speakerSel: $('speakerSel'), personaSel: $('personaSel'),
    systemIn: $('systemIn'), sysCount: $('sysCount'),
    newChat: $('newChat'), probeBtn: $('probeBtn'), probeOut: $('probeOut'),
    sessionList: $('sessionList'), refreshSessions: $('refreshSessions'),
    statusPill: $('statusPill'), sessionPill: $('sessionPill'), clock: $('clock'),
    poolCount: $('poolCount'), poolReady: $('poolReady'), poolPace: $('poolPace'),
    poolReqs: $('poolReqs'), poolErrs: $('poolErrs'), poolList: $('poolList'),
    refreshPool: $('refreshPool'), rotatePool: $('rotatePool'),
    apiSnippet: $('apiSnippet'), copySnippet: $('copySnippet'), langToggle: $('langToggle'),
  };

  const state = {
    sessionId: null,
    chatId: null,
    speaker: 'C',
    persona: 'normal',
    system: '',
    streaming: false,
    abort: null,
    lang: localStorage.getItem('nt.lang') || 'fa',
  };

  /* ═════════════════ i18n ═════════════════ */
  const I18N = {
    fa: {
      brand_sub: 'پنل کنترل', tab_chat: 'گفتگو', tab_pool: 'استخر', tab_api: 'API',
      lbl_speaker: 'مدل', lbl_persona: 'شخصیت', lbl_system: 'پرامپت سیستم',
      p_normal: 'عادی', p_concise: 'مختصر', p_detailed: 'مفصل', p_creative: 'خلاق',
      sys_hint: 'فقط روی چت جدید اعمال می‌شود',
      btn_new: 'چت جدید', btn_probe: 'تست زنده‌بودن مدل‌ها',
      pool_ids: 'هویت‌ها', pool_ready: 'آماده', pool_pace: 'فاصله ارسال',
      pool_reqs: 'درخواست‌ها', pool_errs: 'خطاها',
      btn_refresh: 'بروزرسانی', btn_rotate: 'چرخش همه هویت‌ها',
      api_hint: 'این پنل یک اندپوینت سازگار با OpenAI هم ارائه می‌دهد.',
      btn_copy: 'کپی', sessions: 'نشست‌ها',
      empty_h: 'آماده', empty_p: 'پیامت را بنویس. فیلتری نیست.', ph_msg: 'پیام…',
      you: 'شما', ready: 'متصل', connecting: 'اتصال…', error: 'خطا',
      thinking: 'در حال فکر کردن', stopped: 'متوقف شد',
      lang_btn: 'English',
    },
    en: {
      brand_sub: 'Control panel', tab_chat: 'Chat', tab_pool: 'Pool', tab_api: 'API',
      lbl_speaker: 'Model', lbl_persona: 'Persona', lbl_system: 'System prompt',
      p_normal: 'Normal', p_concise: 'Concise', p_detailed: 'Detailed', p_creative: 'Creative',
      sys_hint: 'Applies to new chats only',
      btn_new: 'New chat', btn_probe: 'Probe model liveness',
      pool_ids: 'Identities', pool_ready: 'Ready', pool_pace: 'Send interval',
      pool_reqs: 'Requests', pool_errs: 'Errors',
      btn_refresh: 'Refresh', btn_rotate: 'Rotate all identities',
      api_hint: 'This panel also exposes an OpenAI-compatible endpoint.',
      btn_copy: 'Copy', sessions: 'Sessions',
      empty_h: 'Ready', empty_p: 'Type your message. No filter.', ph_msg: 'Message…',
      you: 'You', ready: 'connected', connecting: 'connecting…', error: 'error',
      thinking: 'thinking', stopped: 'stopped',
      lang_btn: 'فارسی',
    },
  };
  const T = (k) => (I18N[state.lang] && I18N[state.lang][k]) || I18N.en[k] || k;

  function applyLang() {
    document.documentElement.lang = state.lang;
    document.documentElement.dir = state.lang === 'fa' ? 'rtl' : 'ltr';
    document.querySelectorAll('[data-i18n]').forEach((n) => {
      const k = n.getAttribute('data-i18n');
      if (I18N[state.lang][k]) n.textContent = I18N[state.lang][k];
    });
    document.querySelectorAll('[data-i18n-ph]').forEach((n) => {
      const k = n.getAttribute('data-i18n-ph');
      if (I18N[state.lang][k]) n.placeholder = I18N[state.lang][k];
    });
    el.langToggle.textContent = T('lang_btn');
    localStorage.setItem('nt.lang', state.lang);
  }

  /* ═════════════════ minimal markdown ═════════════════ */
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function mdToHtml(src) {
    const blocks = [];
    let text = String(src ?? '');
    text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (m, lang, code) => {
      blocks.push(`<pre><code>${esc(code.replace(/\n$/, ''))}</code></pre>`);
      return `\u0000B${blocks.length - 1}\u0000`;
    });

    text = esc(text);

    text = text.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    text = text.replace(/^######\s+(.*)$/gm, '<h3>$1</h3>');
    text = text.replace(/^#{1,5}\s+(.*)$/gm, '<h3>$1</h3>');
    text = text.replace(/^&gt;\s?(.*)$/gm, '<blockquote>$1</blockquote>');
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s).,!?:;])/g, '$1<em>$2</em>');
    text = text.replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,!?:;])/g, '$1<em>$2</em>');
    text = text.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // lists
    text = text.replace(/(?:^|\n)((?:[-*+]\s+.*\n?)+)/g, (m, block) => {
      const items = block.trim().split('\n')
        .map((l) => `<li>${l.replace(/^[-*+]\s+/, '')}</li>`).join('');
      return `\n<ul>${items}</ul>\n`;
    });
    text = text.replace(/(?:^|\n)((?:\d+\.\s+.*\n?)+)/g, (m, block) => {
      const items = block.trim().split('\n')
        .map((l) => `<li>${l.replace(/^\d+\.\s+/, '')}</li>`).join('');
      return `\n<ol>${items}</ol>\n`;
    });

    // paragraphs
    text = text.split(/\n{2,}/).map((chunk) => {
      const t = chunk.trim();
      if (!t) return '';
      if (/^<(pre|ul|ol|blockquote|h3)/.test(t)) return t;
      // chunks made only of block placeholders or stray tags never become a <p>
      if (/^(?:\u0000B\d+\u0000\s*)+$/.test(t)) return t;
      return `<p>${t.replace(/\n/g, '<br>')}</p>`;
    }).join('');

    // drop paragraphs that ended up empty after substitution
    text = text.replace(/<p>\s*<\/p>/g, '');

    text = text.replace(/\u0000B(\d+)\u0000/g, (m, i) => blocks[Number(i)] || '');
    return text;
  }

  /* ═════════════════ message rendering ═════════════════ */
  function hideEmpty() { if (el.empty) el.empty.classList.add('hidden'); }

  function labelFor(sp) {
    return { C: 'NoTrack', A: 'Minimax', B: 'ChatGPT' }[sp] || 'NoTrack';
  }

  function addMessage(role, text, speaker) {
    hideEmpty();
    const wrap = document.createElement('div');
    wrap.className = `msg ${role === 'user' ? 'user' : 'ai'}`;

    const av = document.createElement('div');
    av.className = 'avatar';
    av.textContent = role === 'user' ? 'You' : (speaker || 'C');

    const bubble = document.createElement('div');
    bubble.className = 'bubble';

    if (role !== 'user') {
      const r = document.createElement('div');
      r.className = 'role';
      r.textContent = labelFor(speaker || state.speaker);
      bubble.appendChild(r);
    }

    const body = document.createElement('div');
    body.className = 'body';
    // let each block pick its own direction: Persian RTL, English/code LTR
    body.setAttribute('dir', 'auto');
    body.innerHTML = role === 'user'
      ? `<p>${esc(text).replace(/\n/g, '<br>')}</p>`
      : mdToHtml(text);

    bubble.appendChild(body);
    wrap.appendChild(av);
    wrap.appendChild(bubble);
    el.flow.appendChild(wrap);
    scrollDown();
    return { wrap, body, bubble };
  }

  function addError(code, msg) {
    hideEmpty();
    const wrap = document.createElement('div');
    wrap.className = 'msg ai';
    wrap.innerHTML = `<div class="avatar">!</div><div class="bubble">
      <div class="errbox"><b>${esc(code || 'error')}</b>${esc(msg || '')}</div></div>`;
    el.flow.appendChild(wrap);
    scrollDown();
  }

  function scrollDown(force) {
    const near = el.flow.scrollHeight - el.flow.scrollTop - el.flow.clientHeight < 220;
    if (near || force) el.flow.scrollTop = el.flow.scrollHeight;
  }

  /* ═════════════════ send ═════════════════ */
  async function send() {
    const text = el.input.value.trim();
    if (!text || state.streaming) return;

    el.input.value = '';
    el.input.style.height = 'auto';
    state.system = el.systemIn.value.trim();
    state.persona = el.personaSel.value;
    state.speaker = el.speakerSel.value;

    addMessage('user', text);

    state.streaming = true;
    el.sendBtn.disabled = true;
    el.sendBtn.classList.add('hidden');
    el.stopBtn.classList.remove('hidden');

    // streaming AI bubble
    const node = addMessage('ai', '', state.speaker);
    node.body.innerHTML = '<div class="thinking"><i></i><i></i><i></i></div>';

    let acc = '';
    let gotDelta = false;
    const ctrl = new AbortController();
    state.abort = ctrl;

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          message: text,
          session_id: state.sessionId,
          speaker: state.speaker,
          persona: state.persona,
          system: state.system,
        }),
      });

      if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) !== -1) {
          const block = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const line = block.split('\n').find((l) => l.startsWith('data: '));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }

          if (ev.type === 'session') {
            state.sessionId = ev.session_id;
            if (ev.chat_id) state.chatId = ev.chat_id;
            el.sessionPill.textContent = (state.sessionId || '').slice(0, 12);
          } else if (ev.type === 'delta') {
            if (!gotDelta) { node.body.innerHTML = ''; gotDelta = true; }
            acc += ev.text;
            node.body.innerHTML = mdToHtml(acc) + '<span class="cursor"></span>';
            scrollDown();
          } else if (ev.type === 'message') {
            acc = ev.text;
            gotDelta = true;
            node.body.innerHTML = mdToHtml(acc);
            scrollDown();
          } else if (ev.type === 'error') {
            node.body.innerHTML = '';
            node.wrap.remove();
            addError(ev.code, ev.text);
          } else if (ev.type === 'done') {
            if (ev.chat_id) state.chatId = ev.chat_id;
            if (ev.session_id) state.sessionId = ev.session_id;
          }
        }
      }

      if (gotDelta) node.body.innerHTML = mdToHtml(acc);
      else if (!acc) node.body.innerHTML = '<p style="color:#6b7480">(empty)</p>';
      refreshSessions();

    } catch (e) {
      if (e.name === 'AbortError') {
        node.body.innerHTML = mdToHtml(acc) +
          `<p style="color:#6b7480;font-size:11.5px">— ${T('stopped')}</p>`;
      } else {
        node.wrap.remove();
        addError('network', e.message);
      }
    } finally {
      state.streaming = false;
      state.abort = null;
      el.stopBtn.classList.add('hidden');
      el.sendBtn.classList.remove('hidden');
      el.sendBtn.disabled = false;
      el.input.focus();
    }
  }

  /* ═════════════════ sessions ═════════════════ */
  async function refreshSessions() {
    try {
      const r = await fetch('/api/sessions');
      const d = await r.json();
      el.sessionList.innerHTML = '';
      for (const s of d.sessions) {
        const b = document.createElement('button');
        b.className = 'session-item' + (s.id === state.sessionId ? ' active' : '');
        b.innerHTML = `<span class="t">${esc(s.title)}</span>
          <span class="m">${s.speaker} · ${s.turns}t</span>`;
        b.onclick = () => loadSession(s.id);
        el.sessionList.appendChild(b);
      }
    } catch { /* silent */ }
  }

  async function loadSession(sid) {
    try {
      const r = await fetch('/api/sessions/' + encodeURIComponent(sid));
      if (!r.ok) return;
      const s = await r.json();
      state.sessionId = s.id;
      state.chatId = s.chat_id;
      state.speaker = s.speaker;
      state.persona = s.persona;
      el.speakerSel.value = s.speaker;
      el.personaSel.value = s.persona;
      el.sessionPill.textContent = s.id.slice(0, 12);

      el.flow.innerHTML = '';
      if (!s.turns.length) el.flow.appendChild(el.empty), el.empty.classList.remove('hidden');
      for (const t of s.turns) {
        addMessage(t.role, t.content, t.speaker || s.speaker);
      }
      scrollDown(true);
      refreshSessions();
      closeSidebar();
    } catch { /* silent */ }
  }

  function newChat() {
    state.sessionId = null;
    state.chatId = null;
    el.flow.innerHTML = '';
    el.empty.classList.remove('hidden');
    el.flow.appendChild(el.empty);
    el.sessionPill.textContent = '';
    refreshSessions();
    el.input.focus();
  }

  /* ═════════════════ pool ═════════════════ */
  async function refreshPool() {
    try {
      const [h, p] = await Promise.all([
        fetch('/api/health').then((r) => r.json()),
        fetch('/api/pool').then((r) => r.json()),
      ]);
      el.poolCount.textContent = p.count;
      el.poolReady.textContent = p.ready;
      el.poolPace.textContent = p.pace + 's';
      el.poolReqs.textContent = h.requests;
      el.poolErrs.textContent = h.errors;

      el.statusPill.textContent = `${p.ready}/${p.count} · ${p.pace}s`;
      el.statusPill.className = 'pill ' + (p.ready > 0 ? 'good' : 'warn');

      el.poolList.innerHTML = '';
      for (const i of p.identities) {
        const row = document.createElement('div');
        row.className = 'pool-row' + (i.ready ? '' : ' cool');
        row.innerHTML = `<span class="u">${i.short}</span>
          <span class="n">${i.requests}req ${i.ready ? '✓' : i.cooldown_in + 's'}</span>`;
        el.poolList.appendChild(row);
      }
    } catch { /* silent */ }
  }

  /* ═════════════════ probe ═════════════════ */
  async function probe() {
    el.probeBtn.disabled = true;
    el.probeOut.innerHTML = '<div class="probe-row" style="color:#9aa4b2">…</div>';
    try {
      const r = await fetch('/api/probe', { method: 'POST' });
      const d = await r.json();
      el.probeOut.innerHTML = '';
      for (const p of d.results) {
        const row = document.createElement('div');
        row.className = 'probe-row';
        row.innerHTML = `<span class="${p.alive ? 'ok' : 'bad'}">${p.alive ? '●' : '○'}</span>
          <span>${p.speaker}</span>
          <span style="color:#6b7480">${p.alive ? esc((p.sample || '').slice(0, 40)) : esc(p.error || 'down')}</span>`;
        el.probeOut.appendChild(row);
      }
    } catch (e) {
      el.probeOut.innerHTML = `<div class="probe-row bad">${esc(e.message)}</div>`;
    } finally { el.probeBtn.disabled = false; }
  }

  /* ═════════════════ wiring ═════════════════ */
  function closeSidebar() { el.sidebar.classList.remove('open'); }

  el.composer.addEventListener('submit', (e) => { e.preventDefault(); send(); });
  el.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  el.input.addEventListener('input', () => {
    el.input.style.height = 'auto';
    el.input.style.height = Math.min(el.input.scrollHeight, 200) + 'px';
  });
  el.stopBtn.addEventListener('click', () => state.abort && state.abort.abort());
  el.newChat.addEventListener('click', newChat);
  el.probeBtn.addEventListener('click', probe);
  el.refreshPool.addEventListener('click', refreshPool);
  el.refreshSessions.addEventListener('click', refreshSessions);
  el.rotatePool.addEventListener('click', async () => {
    el.rotatePool.disabled = true;
    try { await fetch('/api/pool/rotate', { method: 'POST' }); await refreshPool(); }
    finally { el.rotatePool.disabled = false; }
  });
  el.openSide.addEventListener('click', () => el.sidebar.classList.add('open'));
  el.closeSide.addEventListener('click', closeSidebar);
  el.langToggle.addEventListener('click', () => {
    state.lang = state.lang === 'fa' ? 'en' : 'fa';
    applyLang();
  });
  el.copySnippet.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(el.apiSnippet.textContent); 
      el.copySnippet.textContent = '✓'; setTimeout(() => el.copySnippet.textContent = T('btn_copy'), 1200);
    } catch { /* ignore */ }
  });

  el.systemIn.addEventListener('input', () => {
    el.sysCount.textContent = el.systemIn.value.length;
  });

  document.querySelectorAll('.tab').forEach((t) => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
      t.classList.add('active');
      const p = t.dataset.panel;
      el.settingsPanel.classList.toggle('hidden', p !== 'chat');
      $('poolPanel').classList.toggle('hidden', p !== 'pool');
      $('apiPanel').classList.toggle('hidden', p !== 'api');
      if (p === 'pool') refreshPool();
      closeSidebar();
    });
  });

  setInterval(() => {
    el.clock.textContent = new Date().toLocaleTimeString(state.lang === 'fa' ? 'fa-IR' : 'en-GB');
  }, 1000);

  setInterval(refreshPool, 12000);

  // init
  const origin = location.origin;
  el.apiSnippet.textContent =
`from openai import OpenAI
c = OpenAI(base_url="${origin}/v1",
           api_key="panel")

r = c.chat.completions.create(
    model="notrack-uncensored",
    messages=[{"role":"user","content":"hi"}])
print(r.choices[0].message.content)`;

  applyLang();
  refreshSessions();
  refreshPool();
  el.input.focus();
})();