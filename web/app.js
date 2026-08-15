// 原作者: LBG617 (https://gitee.com/LBG617/era-terminal)
// 许可证: AGPL-3.0 (见 LICENSE)

(function () {
  'use strict';

  const msgbox = document.getElementById('msgbox');
  const inp = document.getElementById('inp');
  const sendBtn = document.getElementById('send');
  let busy = false;
  let webMode = false;       // 是否 web 模式（SSE 流式）
  let es = null;             // EventSource

  // ===== 主题（深色/浅色）=====
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('themeBtn');
    if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
  }
  function toggleTheme() {
    const cur = document.documentElement.getAttribute('data-theme') || 'light';
    const next = cur === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    try { localStorage.setItem('era-theme', next); } catch (_) {}
  }
  // 暴露给 HTML onclick 使用
  window.toggleTheme = toggleTheme;
  window.applyTheme = applyTheme;

  // 初始化：localStorage 优先，否则跟随系统
  (function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem('era-theme'); } catch (_) {}
    if (saved === 'dark' || saved === 'light') {
      applyTheme(saved);
    } else {
      const preferDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      applyTheme(preferDark ? 'dark' : 'light');
    }
  })();

  function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  // ===== Markdown 渲染器（块级占位符隔离 + 行内全语法）=====
  function mdInline(s, ctx) {
    // ctx 含 refMap(引用链接) / fnMap(脚注定义)
    if (!ctx) ctx = {};
    const refMap = ctx.refMap || {};
    const fnMap = ctx.fnMap || {};
    let out = s;

    // 1. 行内代码：最先处理，防止其中内容被当作 markdown
    out = out.replace(/`([^`]+)`/g, function (_, c) { return '<code class="inline-code">' + c + '</code>'; });

    // 2. 图片：![alt](url "title") / ![alt][ref]
    out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g,
      function (_, alt, url, title) {
        const t = title ? ' title="' + title + '"' : '';
        return '<img src="' + url + '" alt="' + alt + '"' + t + ' style="max-width:100%;border-radius:6px;">';
      });
    out = out.replace(/!\[([^\]]*)\]\[([^\]]*)\]/g, function (_, alt, ref) {
      const r = refMap[ref] || refMap[(ref || '').toLowerCase()];
      if (!r) return _;
      const t = r.title ? ' title="' + r.title + '"' : '';
      return '<img src="' + r.url + '" alt="' + alt + '"' + t + ' style="max-width:100%;border-radius:6px;">';
    });

    // 3. 引用式链接 [text][ref] / 简写 [ref][]
    out = out.replace(/\[([^\]]+)\]\[([^\]]*)\]/g, function (_, text, ref) {
      const key = ref || text;
      const r = refMap[key] || refMap[key.toLowerCase()];
      if (!r) return _;
      const t = r.title ? ' title="' + r.title + '"' : '';
      return '<a href="' + r.url + '" target="_blank" rel="noopener"' + t + '>' + text + '</a>';
    });

    // 4. 行内链接 [text](url "title")
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g,
      function (_, text, url, title) {
        const t = title ? ' title="' + title + '"' : '';
        return '<a href="' + url + '" target="_blank" rel="noopener"' + t + '>' + text + '</a>';
      });

    // 5. 自动链接：<http://xxx>  <mailto:xx@xx> <xx@xx>
    out = out.replace(/&lt;((?:https?|ftp):\/\/[^&;\s]+?)&gt;/g,
      '<a href="$1" target="_blank" rel="noopener">$1</a>');
    out = out.replace(/&lt;mailto:([^&;\s]+?)&gt;/g, '<a href="mailto:$1">$1</a>');
    out = out.replace(/&lt;([^&;\s<]+?@[^&;\s<]+?)&gt;/g, '<a href="mailto:$1">$1</a>');

    // 6. 裸 URL 自动识别（不在已生成的 a 标签里）
    out = out.replace(/(^|[>\s(\[])((?:https?|ftp):\/\/[^\s<)\]]+)/g,
      function (_, pre, url) { return pre + '<a href="' + url + '" target="_blank" rel="noopener">' + url + '</a>'; });

    // 7. 强调（粗体/斜体/删除线/高亮）
    out = out.replace(/\*\*\*([^*]+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    out = out.replace(/___([^_]+?)___/g, '<strong><em>$1</em></strong>');
    out = out.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/__([^_]+?)__/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*])\*([^*<\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
    out = out.replace(/(^|[^_])_([^_<\n]+?)_(?!_)/g, '$1<em>$2</em>');
    out = out.replace(/~~([^~]+?)~~/g, '<del>$1</del>');
    out = out.replace(/==([^=]+?)==/g, '<mark>$1</mark>');

    // 8. 上标/下标（^...^ / ~...~）
    out = out.replace(/\^([^\s<^]+?)\^/g, '<sup>$1</sup>');
    out = out.replace(/~([^\s<~]+?)~(?!~)/g, '<sub>$1</sub>');

    // 9. 脚注引用 [^id]
    out = out.replace(/\[\^([^\]]+)\](?::)?/g, function (_, id) {
      if (fnMap && fnMap[id]) {
        return '<sup id="fnref-' + id + '"><a href="#fn-' + id + '">[' + id + ']</a></sup>';
      }
      return _;
    });

    // 10. 行尾硬换行（两个空格）
    out = out.replace(/  $/gm, '<br>');

    return out;
  }

  function md2html(md) {
    if (!md) return '';
    const blocks = [];
    const pushBlock = (html) => { const idx = blocks.length; blocks.push(html); return '@@BLK_' + idx + '@@'; };

    // Step 1: 提取 fence code / 缩进 code / math 块，避免 escape 破坏
    let raw = md;
    // 1.1 围栏代码块 ```lang ... ```
    raw = raw.replace(/^```([ \t]*\S+)?[ \t]*\n([\s\S]*?)^```[ \t]*$/gm, function (_, lang, code) {
      const langClass = (lang && lang.trim()) ? ' class="language-' + lang.trim() + '"' : '';
      const safeCode = escapeHtml(code.replace(/\n$/, ''));
      return pushBlock('<pre class="code-block"><code' + langClass + '>' + safeCode + '</code></pre>') + '\n';
    });

    // 1.2 四空格缩进代码块（连续行首 4 空格或 1 tab）
    raw = raw.replace(/(^|\n)((?:(?:    |\t).*\n?)+)/g, function (_, pre, code) {
      const lines = code.replace(/^\n/, '').replace(/\n$/, '').split('\n');
      const stripped = lines.map(function (l) { return l.replace(/^(    |\t)/, ''); }).join('\n');
      return (pre ? pre : '') + pushBlock('<pre class="code-block"><code>' + escapeHtml(stripped) + '</code></pre>') + '\n';
    });

    // 1.3 数学公式块 $$...$$
    raw = raw.replace(/^\$\$\n([\s\S]*?)^\$\$$/gm, function (_, content) {
      return pushBlock('<div class="math-block">$$\n' + escapeHtml(content) + '\n$$</div>') + '\n';
    });

    // Step 2: escapeHtml 剩余文本
    raw = escapeHtml(raw);

    // Step 3: 提取引用式链接 / 脚注定义
    const lines = raw.split(/\r?\n/);
    const refMap = {};
    const fnMap = {};
    for (let j = 0; j < lines.length; j++) {
      const l = lines[j];
      let m = l.match(/^\s*\[([^\]]+)\]:\s*(\S+)(?:\s+"([^"]*)")?\s*$/);
      if (m) {
        refMap[m[1]] = { url: m[2], title: m[3] || '' };
        refMap[m[1].toLowerCase()] = refMap[m[1]];
        lines[j] = '';
        continue;
      }
      m = l.match(/^\s*\[\^([^\]]+)\]:\s*(.*)$/);
      if (m) {
        let id = m[1], content = m[2];
        while (j + 1 < lines.length && /^  \S/.test(lines[j + 1])) {
          content += ' ' + lines[j + 1].replace(/^  /, '');
          lines[j + 1] = '';
          j++;
        }
        fnMap[id] = content;
        lines[j] = '';
      }
    }

    // Step 4: 表格块提取
    for (let j = 0; j < lines.length - 1; j++) {
      const l = lines[j];
      if (!/@@BLK_\d+@@/.test(l) && l.indexOf('|') !== -1 &&
          /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(lines[j + 1]) && lines[j + 1].indexOf('-') !== -1) {
        const splitRow = function (row) {
          let r = row.trim();
          if (r.charAt(0) === '|') r = r.slice(1);
          if (r.charAt(r.length - 1) === '|') r = r.slice(0, -1);
          return r.split('|').map(function (c) { return c.trim(); });
        };
        const headerCells = splitRow(l);
        const sepCells = splitRow(lines[j + 1]);
        const aligns = sepCells.map(function (s) {
          s = s.trim();
          const L = s.charAt(0) === ':';
          const R = s.charAt(s.length - 1) === ':';
          if (L && R) return 'center';
          if (R) return 'right';
          if (L) return 'left';
          return '';
        });
        const bodyRows = [];
        let k = j + 2;
        while (k < lines.length && lines[k].indexOf('|') !== -1 && !/^\s*$/.test(lines[k]) && !/@@BLK_\d+@@/.test(lines[k])) {
          bodyRows.push(splitRow(lines[k]));
          lines[k] = '';
          k++;
        }
        let tbl = '<table class="md-table"><thead><tr>';
        headerCells.forEach(function (c, idx) {
          const a = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : '';
          tbl += '<th' + a + '>' + mdInline(c, { refMap: refMap }) + '</th>';
        });
        tbl += '</tr></thead><tbody>';
        bodyRows.forEach(function (row) {
          tbl += '<tr>';
          headerCells.forEach(function (_, idx) {
            const a = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : '';
            const cell = (row[idx] !== undefined) ? row[idx] : '';
            tbl += '<td' + a + '>' + mdInline(cell, { refMap: refMap }) + '</td>';
          });
          tbl += '</tr>';
        });
        tbl += '</tbody></table>';
        lines[j] = pushBlock(tbl);
        lines[j + 1] = '';
      }
    }

    const ctx = { refMap: refMap, fnMap: fnMap };
    const out = [];

    // 解析列表（支持嵌套 + 任务列表）
    function parseListFrom(startIdx, ordered) {
      const listTag = ordered ? 'ol' : 'ul';
      const markerRe = ordered ? /^(\s*)\d+\.\s+(.*)$/ : /^(\s*)[-*+]\s+(.*)$/;
      const toggleMarkerRe = ordered ? /^(\s*)\d+\.\s+/ : /^(\s*)[-*+]\s+/;
      const baseIndent = (function () {
        const m = lines[startIdx].match(/^\s*/);
        return m ? m[0].length : 0;
      })();
      const items = [];
      let cur = null;
      let j = startIdx;
      while (j < lines.length) {
        const line = lines[j];
        if (/^\s*$/.test(line)) { j++; continue; }
        if (/@@BLK_\d+@@/.test(line)) break;
        const ind = ((line.match(/^\s*/) || [])[0] || '').length;
        if (ind < baseIndent) break;
        const mm = line.match(toggleMarkerRe);
        if (mm && mm[1].length <= baseIndent) {
          // 另一种列表标记：停止
          if (!ordered && /^\s*\d+\.\s+/.test(line)) break;
          if (ordered && /^\s*[-*+]\s+/.test(line)) break;
          cur = { texts: [line.replace(toggleMarkerRe, '')], sub: null };
          items.push(cur);
          j++;
          continue;
        }
        if (mm && mm[1].length > baseIndent) {
          if (!cur) { j++; continue; }
          const childOrdered = /^\s*\d+\.\s+/.test(line);
          const sub = parseListFrom(j, childOrdered);
          cur.sub = sub.html;
          j += sub.consumed;
          continue;
        }
        if (cur && ind > baseIndent) {
          cur.texts.push(line.replace(/^\s+/, ''));
          j++;
          continue;
        }
        break;
      }
      let html = '<' + listTag + '>';
      for (const it of items) {
        const first = it.texts[0] || '';
        let task = null;
        const tm = first.match(/^\[([ xX])\]\s+(.*)$/);
        if (tm) {
          task = (tm[1].toLowerCase() === 'x');
          it.texts[0] = tm[2];
        }
        let innerHtml = mdInline(it.texts.join(' '), ctx);
        if (task !== null) {
          innerHtml = '<input type="checkbox" disabled' + (task ? ' checked' : '') + '> ' + innerHtml;
        }
        html += '<li>' + innerHtml;
        if (it.sub) html += it.sub;
        html += '</li>';
      }
      html += '</' + listTag + '>';
      return { html: html, consumed: j - startIdx };
    }

    let i = 0;
    while (i < lines.length) {
      let line = lines[i];
      const pm = line.match(/^(@@BLK_\d+@@)\s*$/);
      if (pm) {
        out.push(blocks[parseInt(pm[1].slice(6, -2), 10)]);
        i++;
        continue;
      }
      if (/@@BLK_\d+@@/.test(line)) {
        line = line.replace(/@@BLK_(\d+)@@/g, function (_, k) { return blocks[parseInt(k, 10)]; });
        out.push(line); i++; continue;
      }
      if (/^\s*$/.test(line)) { i++; continue; }

      // 分割线
      if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push('<hr>'); i++; continue; }

      // 标题
      let m;
      m = line.match(/^(#{1,6})\s+(.*)$/);
      if (m) {
        const lvl = m[1].length + 1;
        out.push('<h' + lvl + '>' + mdInline(m[2], ctx) + '</h' + lvl + '>');
        i++; continue;
      }

      // 引用块
      if (/^&gt;\s?/.test(line)) {
        const quoteLines = [];
        while (i < lines.length && /^&gt;\s?/.test(lines[i])) {
          let ql = lines[i];
          ql = ql.replace(/^&gt;/, '');
          if (ql.charAt(0) === ' ') ql = ql.slice(1);
          quoteLines.push(ql);
          i++;
        }
        const inner = md2htmlInner(quoteLines.join('\n'));
        out.push('<blockquote>' + inner + '</blockquote>');
        continue;
      }

      // 列表
      if (/^\s*[-*+]\s+/.test(line)) {
        const r = parseListFrom(i, false);
        out.push(r.html); i += r.consumed; continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        const r = parseListFrom(i, true);
        out.push(r.html); i += r.consumed; continue;
      }

      // 普通段落
      const paraLines = [line];
      let k = i + 1;
      while (k < lines.length) {
        const nl = lines[k];
        if (/^\s*$/.test(nl)) break;
        if (/@@BLK_\d+@@/.test(nl)) break;
        if (/^(#{1,6})\s+/.test(nl)) break;
        if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(nl)) break;
        if (/^&gt;\s?/.test(nl)) break;
        if (/^\s*[-*+]\s+/.test(nl)) break;
        if (/^\s*\d+\.\s+/.test(nl)) break;
        paraLines.push(nl);
        k++;
      }
      let paraText = paraLines.join('\n');
      paraText = paraText.replace(/\$([^$\n]+?)\$/g, '<span class="math-inline">$$1</span>');
      out.push('<p>' + mdInline(paraText, ctx) + '</p>');
      i = k;
    }

    const fnIds = Object.keys(fnMap);
    if (fnIds.length) {
      out.push('<hr><div class="footnotes"><h6>脚注</h6><ol>');
      for (const id of fnIds) {
        out.push('<li id="fn-' + id + '">' + mdInline(fnMap[id], ctx) + ' <a href="#fnref-' + id + '">↩</a></li>');
      }
      out.push('</ol></div>');
    }

    return out.join('\n');
  }

  // 引用块内部递归
  function md2htmlInner(text) {
    return md2html(text);
  }

  // ===== 消息渲染 =====
  function addMsg(text, who, reasoning) {
    const div = document.createElement('div');
    div.className = 'msg ' + who;
    if (who === 'ai') {
      let html = '';
      if (reasoning) {
        html += '<details class="reasoning" open><summary>思考过程</summary><div>' + escapeHtml(reasoning) + '</div></details>';
      }
      html += '<div class="ai-content">' + md2html(text) + '</div>';
      div.innerHTML = html;
    } else {
      div.textContent = text;
    }
    msgbox.appendChild(div);
    msgbox.scrollTop = msgbox.scrollHeight;
    return div;
  }

  function setBusy(b) {
    busy = b;
    sendBtn.disabled = b;
    inp.disabled = b;
    if (!b) inp.focus();
  }

  function showTyping() {
    return addMsg('思考中...', 'ai typing');
  }
  function removeTyping() {
    const last = msgbox.lastChild;
    if (last && last.classList.contains('typing')) last.remove();
  }

  function loadHistoryMessages(messages) {
    if (!messages || !messages.length) return;
    for (const m of messages) {
      if (m.role === 'user') {
        addMsg(m.content, 'user');
      } else if (m.role === 'assistant') {
        const tcs = m.tool_calls || [];
        if (tcs.length || m.reasoning) {
          addAssistantBlock(m.reasoning || '', m.content || '(空)', tcs);
        } else {
          addMsg(m.content || '(空)', 'ai');
        }
      }
    }
    addMsg('（以上为历史记录）', 'sys');
  }

  async function loadHistory() {
    try {
      const r = await fetch('/api/history');
      const d = await r.json();
      if (!d.loaded || !d.messages || !d.messages.length) return;
      loadHistoryMessages(d.messages);
    } catch (e) {}
  }

  // 当前正在填充的 AI 气泡
  let currentAi = null;
  function replaceCurrentAiWithFolded(reasoning, content) {
    const newNode = document.createElement('div');
    newNode.className = 'msg ai';
    let html = '';
    if (reasoning) {
      html += '<details class="reasoning" open><summary>思考过程</summary><div>' + escapeHtml(reasoning) + '</div></details>';
    }
    html += '<div class="ai-content">' + md2html(content || '(空)') + '</div>';
    newNode.innerHTML = html;
    if (currentAi && currentAi.parentNode) {
      currentAi.parentNode.replaceChild(newNode, currentAi);
    } else {
      msgbox.appendChild(newNode);
    }
    currentAi = null;
    msgbox.scrollTop = msgbox.scrollHeight;
  }

  function addAssistantBlock(reasoning, content, toolCalls) {
    const div = document.createElement('div');
    div.className = 'msg ai';
    let html = '';
    if (reasoning) {
      html += '<details class="reasoning" open><summary>思考过程</summary><div>' + escapeHtml(reasoning) + '</div></details>';
    }
    if (content) {
      html += '<div class="ai-content">' + md2html(content) + '</div>';
    }
    if (toolCalls && toolCalls.length) {
      html += '<div class="ai-tools">';
      for (const tc of toolCalls) {
        const argsStr = (function () {
          try { return JSON.stringify(tc.args); }
          catch (_) { return String(tc.args); }
        })();
        html +=
          '<details class="toolcall">' +
            '<summary>' +
              '<span class="tc-arrow" style="display:inline-block;width:12px;">▶</span> ' +
              '调用工具: <b>' + escapeHtml(tc.name) + '</b>' +
            '</summary>' +
            '<div class="tc-body">' +
              '<div class="tc-cmd">$ ' + escapeHtml(tc.name) + '(' + escapeHtml(argsStr) + ')</div>' +
              '<div class="tc-res">&gt; ' + escapeHtml(tc.result) + '</div>' +
            '</div>' +
          '</details>';
      }
      html += '</div>';
    }
    div.innerHTML = html;
    div.querySelectorAll('details.toolcall').forEach(function (det) {
      const arrow = det.querySelector('.tc-arrow');
      if (arrow) det.addEventListener('toggle', function () {
        arrow.textContent = det.open ? '▼' : '▶';
      });
    });
    msgbox.appendChild(div);
    msgbox.scrollTop = msgbox.scrollHeight;
  }

  function handleStreamChunk(rawChunk) {
    let chunk = rawChunk;
    try {
      const o = JSON.parse(rawChunk);
      if (o && typeof o === 'object' && typeof o.__sse_type === 'string') {
        if (o.__sse_type === 'done') {
          if (currentAi) {
            replaceCurrentAiWithFolded(o.reasoning || '', o.content || '');
          }
        } else if (o.__sse_type === 'block') {
          if (currentAi && currentAi.parentNode) currentAi.parentNode.removeChild(currentAi);
          addAssistantBlock(o.reasoning || '', o.content || '', o.tool_calls || []);
          currentAi = null;
        }
        return;
      }
      if (o && typeof o.text === 'string') {
        chunk = o.text;
      }
    } catch (_) {}

    if (!currentAi) currentAi = addMsg('思考中…', 'ai typing');
    if (!currentAi._touched) {
      currentAi.textContent = '';
      currentAi._touched = true;
      currentAi.classList.remove('typing');
    }
    currentAi.textContent += chunk;
    msgbox.scrollTop = msgbox.scrollHeight;
  }

  function connectStream() {
    try { es.close(); } catch (e) {}
    es = new EventSource('/api/stream');
    es.onopen = function () {
      webMode = true;
      addMsg('已连接到终端输出流（web 模式）', 'sys');
    };
    es.onmessage = function (ev) {
      handleStreamChunk(ev.data);
    };
    es.onerror = function () {
      es.close();
      if (webMode) { addMsg('输出流已断开，切换为同步模式', 'sys'); }
      webMode = false;
    };
  }

  let _busyWatchdog = null;
  function setBusySafe(b) {
    setBusy(b);
    if (b) {
      clearTimeout(_busyWatchdog);
      _busyWatchdog = setTimeout(function () {
        console.warn('发送超时，强制解锁');
        if (busy) setBusy(false);
      }, 600000);
    } else {
      clearTimeout(_busyWatchdog);
      _busyWatchdog = null;
    }
  }

  async function send() {
    if (busy) return;
    try { inp.focus(); } catch (_) {}
    const text = inp.value.trim();
    if (!text) return;
    addMsg(text, 'user');
    inp.value = '';
    inp.style.height = 'auto';
    setBusySafe(true);
    if (webMode) {
      currentAi = null;
      try {
        const res = await fetch('/api/input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        if (data.error) {
          if (currentAi && !currentAi._touched) currentAi.remove();
          addMsg('❌ ' + data.error, 'ai');
          setBusySafe(false);
        } else if (data.done) {
          if (currentAi && !currentAi._touched) { currentAi.textContent = '(已完成)'; }
          setBusySafe(false);
          refreshBalance();
        } else if (data.reply !== undefined) {
          if (currentAi && !currentAi._touched) currentAi.remove();
          addMsg(data.reply || '(空)', 'ai');
          setBusySafe(false);
          refreshBalance();
        } else {
          setBusySafe(false);
        }
      } catch (e) {
        if (currentAi && !currentAi._touched) currentAi.remove();
        addMsg('❌ ' + e, 'ai');
        setBusySafe(false);
      }
    } else {
      showTyping();
      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text })
        });
        removeTyping();
        const data = await res.json();
        if (data.error) addMsg('❌ ' + data.error, 'ai');
        else addMsg(data.reply || '(空)', 'ai', data.reasoning || '');
      } catch (e) {
        removeTyping();
        addMsg('❌ ' + e, 'ai');
      }
      setBusySafe(false);
      refreshBalance();
    }
  }
  window.send = send;

  async function sendCmd(cmd) {
    if (busy) return;
    setBusySafe(true);
    addMsg('执行命令: ' + cmd, 'sys');
    try {
      const res = await fetch('/', {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: cmd
      });
      const text = await res.text();
      addMsg(text, 'sys');
    } catch (e) { addMsg('❌ ' + e, 'sys'); }
    setBusySafe(false);
  }
  window.sendCmd = sendCmd;

  inp.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      e.stopPropagation();
      send();
    }
  });
  sendBtn.addEventListener('click', function (e) { e.preventDefault(); send(); });
  inp.addEventListener('input', function () {
    inp.style.height = 'auto';
    inp.style.height = Math.min(inp.scrollHeight, 120) + 'px';
  });

  // ===== 侧边栏（会话管理）=====
  const sidebar = document.getElementById('sidebar');
  const convListEl = document.getElementById('convList');
  const sidebarToggleBtn = document.getElementById('sidebarToggle');
  const sidebarToggleHeaderBtn = document.getElementById('sidebarToggleHeader');
  const newConvBtn = document.getElementById('newConvBtn');
  const renameModalEl = document.getElementById('renameModal');
  const renameInputEl = document.getElementById('renameInput');
  let _renameConvId = null;
  let _currentConvId = null;

  // 侧边栏展开/收起（使用 localStorage 记住）
  function applySidebar(collapsed) {
    if (collapsed) sidebar.classList.add('collapsed');
    else sidebar.classList.remove('collapsed');
    try { localStorage.setItem('era-sidebar-collapsed', collapsed ? '1' : '0'); } catch (_) {}
  }
  function initSidebarPref() {
    let saved = null;
    try { saved = localStorage.getItem('era-sidebar-collapsed'); } catch (_) {}
    applySidebar(saved === '1');
  }
  // toggle 按钮有两个：侧边栏头部一个（展开时可见）+ header 一个（始终可见，折叠后也能点开）
  function toggleSidebar() {
    applySidebar(!sidebar.classList.contains('collapsed'));
  }
  if (sidebarToggleBtn) sidebarToggleBtn.addEventListener('click', toggleSidebar);
  if (sidebarToggleHeaderBtn) sidebarToggleHeaderBtn.addEventListener('click', toggleSidebar);

  // 格式化时间（仅显示日期或"今天 HH:mm"，简单处理）
  function fmtTime(s) {
    if (!s) return '';
    const t = new Date(s);
    if (isNaN(t.getTime())) return '';
    const now = new Date();
    const sameDay = t.toDateString() === now.toDateString();
    const pad = n => n < 10 ? '0' + n : '' + n;
    const hhmm = pad(t.getHours()) + ':' + pad(t.getMinutes());
    if (sameDay) return hhmm;
    return (t.getFullYear() === now.getFullYear() ? '' : (t.getFullYear() + '/'))
         + pad(t.getMonth() + 1) + '/' + pad(t.getDate());
  }

  // 渲染会话列表
  function renderConvList(conversations) {
    convListEl.innerHTML = '';
    if (!conversations || !conversations.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding:20px 10px;text-align:center;font-size:12px;color:var(--sidebar-muted);';
      empty.textContent = '暂无对话';
      convListEl.appendChild(empty);
      return;
    }
    for (const c of conversations) {
      const row = document.createElement('div');
      row.className = 'conv-item' + (c.current ? ' active' : '');
      row.dataset.id = c.id;
      const title = document.createElement('div');
      title.className = 'conv-title';
      title.textContent = c.title || '未命名对话';
      title.title = c.title || '';
      const time = document.createElement('div');
      time.className = 'conv-time';
      time.textContent = fmtTime(c.updated_at || c.created_at);
      const actions = document.createElement('div');
      actions.className = 'conv-actions';
      // 重命名
      const btnRename = document.createElement('button');
      btnRename.title = '重命名';
      btnRename.innerHTML = '✎';
      btnRename.addEventListener('click', (e) => {
        e.stopPropagation();
        openRenameModal(c.id, c.title || '');
      });
      // 删除
      const btnDel = document.createElement('button');
      btnDel.title = '删除';
      btnDel.innerHTML = '🗑';
      btnDel.addEventListener('click', (e) => {
        e.stopPropagation();
        if (confirm('确定删除此对话吗？')) deleteConv(c.id);
      });
      actions.appendChild(btnRename);
      actions.appendChild(btnDel);
      row.appendChild(title);
      row.appendChild(time);
      row.appendChild(actions);
      row.addEventListener('click', () => {
        if (!c.current) switchConv(c.id);
      });
      convListEl.appendChild(row);
    }
  }

  async function loadConvList() {
    try {
      const r = await fetch('/api/convs');
      if (!r.ok) return;
      const data = await r.json();
      if (data && data.conversations) {
        renderConvList(data.conversations);
        const cur = data.conversations.find(x => x.current);
        if (cur) _currentConvId = cur.id;
      }
    } catch (_) {}
  }

  // 新建会话
  newConvBtn.addEventListener('click', async () => {
    if (busy) return;
    try {
      setBusySafe(true);
      const r = await fetch('/api/convs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title: '新对话'})
      });
      if (!r.ok) throw new Error((await r.json()).error || '创建失败');
      const data = await r.json();
      _currentConvId = data.id;
      // 清空当前消息并载入新会话
      msgbox.innerHTML = '';
      if (data.messages) loadHistoryMessages(data.messages);
      else addMsg('终端未进入 web 模式，当前为同步请求模式（在终端输入 web 可切换）', 'sys');
      await loadConvList();
    } catch (e) { addMsg('❌ 新建对话失败：' + e, 'sys'); }
    finally { setBusySafe(false); }
  });

  // 切换会话
  async function switchConv(id) {
    if (busy) return;
    if (id === _currentConvId) return;
    try {
      setBusySafe(true);
      const r = await fetch(`/api/convs/${encodeURIComponent(id)}/load`, {method: 'POST'});
      if (!r.ok) throw new Error((await r.json()).error || '切换失败');
      const data = await r.json();
      _currentConvId = data.id;
      msgbox.innerHTML = '';
      if (data.messages && data.messages.length) {
        loadHistoryMessages(data.messages);
      } else {
        addMsg('终端未进入 web 模式，当前为同步请求模式（在终端输入 web 可切换）', 'sys');
      }
      await loadConvList();
    } catch (e) { addMsg('❌ 切换对话失败：' + e, 'sys'); }
    finally { setBusySafe(false); }
  }

  // 删除会话
  async function deleteConv(id) {
    if (busy) return;
    try {
      setBusySafe(true);
      const r = await fetch(`/api/convs/${encodeURIComponent(id)}/delete`, {method: 'POST'});
      if (!r.ok) throw new Error((await r.json()).error || '删除失败');
      const data = await r.json();
      _currentConvId = data.current_id;
      msgbox.innerHTML = '';
      if (data.messages) loadHistoryMessages(data.messages);
      if (data.conversations) renderConvList(data.conversations);
      else await loadConvList();
    } catch (e) { addMsg('❌ 删除对话失败：' + e, 'sys'); }
    finally { setBusySafe(false); }
  }

  // 重命名弹窗
  function openRenameModal(convId, currentTitle) {
    _renameConvId = convId;
    renameInputEl.value = currentTitle || '';
    renameModalEl.style.display = 'flex';
    setTimeout(() => renameInputEl.focus(), 0);
  }
  window.closeRenameModal = async function (submit) {
    if (!submit) {
      renameModalEl.style.display = 'none';
      _renameConvId = null;
      return;
    }
    if (!_renameConvId) { renameModalEl.style.display = 'none'; return; }
    const title = (renameInputEl.value || '').trim();
    if (!title) { renameInputEl.focus(); return; }
    try {
      const r = await fetch(`/api/convs/${encodeURIComponent(_renameConvId)}/rename`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title: title})
      });
      if (!r.ok) throw new Error((await r.json()).error || '重命名失败');
      await loadConvList();
    } catch (e) { addMsg('❌ 重命名失败：' + e, 'sys'); }
    renameModalEl.style.display = 'none';
    _renameConvId = null;
  };
  renameInputEl && renameInputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); closeRenameModal(true); }
    if (e.key === 'Escape') { e.preventDefault(); closeRenameModal(false); }
  });

  // ===== 设置弹窗（读写 AIconfig.json）=====
  const balanceBarEl = document.getElementById('balanceBar');
  const balanceValueEl = document.getElementById('balanceValue');
  const settingsModalEl = document.getElementById('settingsModal');
  const settingsFormEl = document.getElementById('settingsForm');
  const settingsNoteEl = document.getElementById('settingsNote');
  const saveSettingsBtn = document.getElementById('saveSettingsBtn');
  const settingsBtn = document.getElementById('settingsBtn');

  function setSettingsNote(msg, type) {
    if (!settingsNoteEl) return;
    settingsNoteEl.textContent = msg || '';
    settingsNoteEl.className = 'settings-note' + (type ? ' ' + type : '');
  }

  function fillSettingsForm(cfg) {
    if (!cfg || typeof cfg !== 'object') cfg = {};
    const inputs = settingsFormEl.querySelectorAll('[data-key]');
    inputs.forEach(function (el) {
      const key = el.getAttribute('data-key');
      const val = cfg[key];
      if (el.type === 'checkbox') {
        el.checked = val === true || val === 'true' || val === 'y' || val === 1;
      } else if (el.tagName === 'SELECT') {
        // open_voice / check_update 用 y/n
        let v = (val === undefined || val === null) ? '' : String(val).toLowerCase();
        if (v === 'yes' || v === 'true' || v === '1' || v === 'on') v = 'y';
        if (v === 'no' || v === 'false' || v === '0' || v === 'off') v = 'n';
        if (v !== 'y' && v !== 'n') v = el.options.length ? el.options[0].value : '';
        el.value = v;
      } else {
        el.value = (val === undefined || val === null) ? '' : String(val);
      }
    });
  }

  function collectSettingsForm() {
    const cfg = {};
    const inputs = settingsFormEl.querySelectorAll('[data-key]');
    inputs.forEach(function (el) {
      const key = el.getAttribute('data-key');
      if (el.type === 'checkbox') {
        cfg[key] = el.checked;
      } else if (el.type === 'number') {
        cfg[key] = el.value === '' ? '' : Number(el.value);
      } else {
        cfg[key] = el.value;
      }
    });
    return cfg;
  }

  async function openSettings() {
    setSettingsNote('加载中…', '');
    settingsModalEl.style.display = 'flex';
    try {
      const r = await fetch('/api/config');
      const d = await r.json();
      if (d.ok) {
        fillSettingsForm(d.config || {});
        setSettingsNote('已加载当前配置，修改后点保存。接口信息保存后立即生效。', '');
      } else {
        fillSettingsForm({});
        setSettingsNote('加载失败：' + (d.error || '未知错误'), 'err');
      }
    } catch (e) {
      fillSettingsForm({});
      setSettingsNote('加载失败：' + e, 'err');
    }
  }

  window.closeSettings = function (submit) {
    settingsModalEl.style.display = 'none';
    setSettingsNote('', '');
  };

  async function saveSettings() {
    const cfg = collectSettingsForm();
    setSettingsNote('保存中…', '');
    if (saveSettingsBtn) { saveSettingsBtn.classList.add('saving'); saveSettingsBtn.disabled = true; }
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({config: cfg})
      });
      const d = await r.json();
      if (d.ok) {
        setSettingsNote(d.note || '配置已保存并应用', 'ok');
        // 接口信息已热更新到 AI 实例，后续对话自动使用新配置
        refreshBalance();  // API 地址/密钥可能变化，重新判断并查询
      } else {
        setSettingsNote('保存失败：' + (d.error || '未知错误'), 'err');
      }
    } catch (e) {
      setSettingsNote('保存失败：' + e, 'err');
    } finally {
      if (saveSettingsBtn) { saveSettingsBtn.classList.remove('saving'); saveSettingsBtn.disabled = false; }
    }
  }
  window.saveSettings = saveSettings;
  if (settingsBtn) settingsBtn.addEventListener('click', openSettings);

  // ===== 余额查询（仅 DeepSeek 显示；每次 AI 输出结束刷新）=====
  async function refreshBalance() {
    if (!balanceBarEl || !balanceValueEl) return;
    try {
      const r = await fetch('/api/balance');
      const d = await r.json();
      if (d.supported === false) {
        // 非 DeepSeek：隐藏余额条
        balanceBarEl.style.display = 'none';
        return;
      }
      balanceBarEl.style.display = 'flex';
      balanceBarEl.classList.remove('err');
      if (d.ok) {
        const bal = d.balance || '0';
        const cur = d.currency || '';
        balanceValueEl.textContent = bal + ' ' + cur;
      } else {
        balanceBarEl.classList.add('err');
        balanceValueEl.textContent = '查询失败';
        balanceValueEl.title = d.error || '';
      }
    } catch (e) {
      // 查询异常不强制显示
    }
  }
  window.refreshBalance = refreshBalance;

  // 启动
  (async function init() {
    initSidebarPref();
    await loadConvList();
    await loadHistory();
    refreshBalance();  // 启动时查一次（非 DeepSeek 会自动隐藏）
    try {
      const r = await fetch('/api/status');
      const s = await r.json();
      if (s.web_mode) connectStream();
      else addMsg('终端未进入 web 模式，当前为同步请求模式（在终端输入 web 可切换）', 'sys');
    } catch (e) {}
  })();
})();
