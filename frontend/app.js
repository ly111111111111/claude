(() => {
  const $ = (id) => document.getElementById(id);

  const messagesEl = $("messages");
  const inputEl = $("input");
  const workspaceEl = $("workspace");
  const statusEl = $("ws-status");
  const activeEl = $("ws-active");
  const kbPathEl = $("kb-path");
  const usageSummaryEl = $("usage-summary");
  const sideModelEl = $("side-model");
  const browserEl = $("browser");
  const browserPathEl = $("browser-path");
  const browserListEl = $("browser-list");
  const browserMsgEl = $("browser-msg");
  const btnSend = $("btn-send");
  const btnStop = $("btn-stop");
  const btnNew = $("btn-new");
  const convListEl = $("conv-list");
  const convTitleEl = $("conv-title");
  const btnBrowse = $("btn-browse");
  const btnUpload = $("btn-upload");
  const fileUploadEl = $("file-upload");
  const btnUp = $("btn-up");
  const btnCloseBrowser = $("btn-close-browser");
  const caseDrawer = $("case-drawer");
  const caseTitle = $("case-title");
  const casePathEl = $("case-path");
  const caseMsgEl = $("case-msg");
  const caseExcelEl = $("case-excel");
  const caseExcelFields = $("case-excel-fields");
  const caseTypeList = $("case-type-list");
  const caseFileList = $("case-file-list");
  const casePdf = $("case-pdf");
  const casePdfName = $("case-pdf-name");
  const casePdfOpen = $("case-pdf-open");
  const casePdfEmpty = $("case-pdf-empty");
  const btnCloseCase = $("btn-close-case");
  const caseBackdrop = $("case-backdrop");
  const htmlPreviewEl = $("html-preview");
  const htmlPreviewFrame = $("html-preview-frame");
  const htmlPreviewTitle = $("html-preview-title");
  const htmlPreviewOpen = $("html-preview-open");
  const btnHtmlClose = $("btn-html-close");
  const htmlPreviewResizer = $("html-preview-resizer");
  const shellEl = document.querySelector(".shell");
  let previewBlobUrl = "";

  const LS_WORKSPACE = "claude_workspace";
  const LS_CONVERSATIONS = "claude_conversations";
  const MAX_CONVERSATIONS = 50;
  const MAX_MESSAGES = 200;

  let appliedWorkspace = "";
  let knowledgeRootPath = "";
  let browseParent = null;
  let browseCurrent = "";
  /** @type {Map<string, object>} 每个对话独立的生成任务，切换对话不中断 */
  const chatRuns = new Map();
  let convStore = { activeId: null, items: [] };
  let appBooted = false;

  function convStorageKey() {
    return `${LS_CONVERSATIONS}:anon`;
  }

  function uid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return `c-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function bootApp() {
    convStore = loadConvStore();
    appliedWorkspace = "";
    if (!appBooted) {
      appBooted = true;
      bindPreviewResizer();
    }
    renderHistory();
    refreshStatus();
    ensureActiveConversation();
    renderConversationList();
    renderActiveMessages();
    restoreHtmlPreviewForActive();
    refreshComposerBusy();
    loadHealth();
    loadUserWorkspace();
    loadUsageSummary();
  }

  function loadConvStore() {
    try {
      const raw = JSON.parse(localStorage.getItem(convStorageKey()) || "null");
      if (raw && Array.isArray(raw.items)) {
        return {
          activeId: raw.activeId || (raw.items[0] && raw.items[0].id) || null,
          items: raw.items,
        };
      }
    } catch {
      /* ignore */
    }
    return { activeId: null, items: [] };
  }

  function saveConvStore() {
    // 最多保留 50 个对话
    if (convStore.items.length > MAX_CONVERSATIONS) {
      convStore.items.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
      convStore.items = convStore.items.slice(0, MAX_CONVERSATIONS);
      if (!convStore.items.some((c) => c.id === convStore.activeId)) {
        convStore.activeId = convStore.items[0] ? convStore.items[0].id : null;
      }
    }
    localStorage.setItem(convStorageKey(), JSON.stringify(convStore));
  }

  function getActiveConv() {
    return convStore.items.find((c) => c.id === convStore.activeId) || null;
  }

  function getActiveSessionId() {
    const c = getActiveConv();
    return c && c.sessionId ? c.sessionId : null;
  }

  function setActiveSessionId(sid) {
    const c = getActiveConv();
    if (!c) return;
    c.sessionId = sid || null;
    c.updatedAt = Date.now();
    saveConvStore();
  }

  function truncateTitle(text) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    if (!t) return "新对话";
    return t.length > 20 ? `${t.slice(0, 20)}…` : t;
  }

  function formatConvTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (d.toDateString() === now.toDateString()) return hm;
    return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  }

  async function allocateServerConvId() {
    const res = await fetch("/api/conversation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !data.conv_id) throw new Error("服务未返回 conv_id");
    return data.conv_id;
  }

  function createConversation({ switchTo = true, id = null } = {}) {
    const conv = {
      id: id || uid(),
      title: "新对话",
      sessionId: null,
      workspace: appliedWorkspace || (workspaceEl && workspaceEl.value.trim()) || "",
      updatedAt: Date.now(),
      messages: [],
    };
    convStore.items.unshift(conv);
    if (switchTo) convStore.activeId = conv.id;
    saveConvStore();
    return conv;
  }

  async function startNewConversation({ switchTo = true } = {}) {
    let id = null;
    try {
      id = await allocateServerConvId();
    } catch {
      id = null;
    }
    return createConversation({ switchTo, id });
  }

  function ensureActiveConversation() {
    let c = getActiveConv();
    if (!c) {
      c = createConversation({ switchTo: true });
    }
    return c;
  }

  function getConvById(id) {
    return convStore.items.find((c) => c.id === id) || null;
  }

  function isViewingConv(convId) {
    return convStore.activeId === convId;
  }

  function isConvRunning(convId) {
    return !!convId && chatRuns.has(convId);
  }

  function refreshComposerBusy() {
    const busy = isConvRunning(convStore.activeId);
    btnSend.disabled = busy;
    if (btnUpload) btnUpload.disabled = busy;
    if (btnStop) {
      if (busy) btnStop.classList.remove("hidden");
      else btnStop.classList.add("hidden");
    }
  }

  function appendMessageToConv(convId, role, text, meta, usage) {
    const c = getConvById(convId);
    if (!c) return;
    const item = {
      role,
      text: text || "",
      meta: meta || undefined,
    };
    if (usage && (usage.total_tokens || usage.input_tokens || usage.output_tokens)) {
      item.usage = {
        input_tokens: Number(usage.input_tokens) || 0,
        output_tokens: Number(usage.output_tokens) || 0,
        total_tokens: Number(usage.total_tokens) || 0,
        num_turns: Number(usage.num_turns) || 0,
        model: usage.model || "",
      };
      c.tokenTotals = c.tokenTotals || {
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        turns: 0,
      };
      c.tokenTotals.input_tokens += item.usage.input_tokens;
      c.tokenTotals.output_tokens += item.usage.output_tokens;
      c.tokenTotals.total_tokens += item.usage.total_tokens;
      c.tokenTotals.turns += 1;
    }
    c.messages.push(item);
    if (c.messages.length > MAX_MESSAGES) {
      c.messages = c.messages.slice(-MAX_MESSAGES);
    }
    if (role === "user" && (c.title === "新对话" || !c.title)) {
      c.title = truncateTitle(text);
    }
    c.updatedAt = Date.now();
    c.workspace = appliedWorkspace || c.workspace || "";
    convStore.items = [c, ...convStore.items.filter((x) => x.id !== c.id)];
    saveConvStore();
    renderConversationList();
    if (isViewingConv(convId) && convTitleEl) {
      convTitleEl.textContent = c.title || "对话";
    }
  }

  function appendMessageToActive(role, text, meta, usage) {
    const c = ensureActiveConversation();
    appendMessageToConv(c.id, role, text, meta, usage);
  }

  function formatTokenCount(n) {
    const v = Number(n) || 0;
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
    if (v >= 10_000) return `${(v / 1000).toFixed(1)}k`;
    if (v >= 1000) return `${(v / 1000).toFixed(2)}k`;
    return String(v);
  }

  function formatUsageLine(usage) {
    if (!usage) return "";
    const inp = Number(usage.input_tokens) || 0;
    const out = Number(usage.output_tokens) || 0;
    const total = Number(usage.total_tokens) || inp + out;
    if (!total && !inp && !out) return "";
    const turns = Number(usage.num_turns) || 0;
    let line = `↑${formatTokenCount(inp)} ↓${formatTokenCount(out)} · 合计 ${formatTokenCount(total)}`;
    if (turns > 1) line += ` · ${turns} 轮工具`;
    return line;
  }

  function attachUsageBadge(bubbleEl, usage) {
    if (!bubbleEl || !usage) return;
    bubbleEl.querySelectorAll(".bubble-usage").forEach((el) => el.remove());
    const line = formatUsageLine(usage);
    if (!line) return;
    const el = document.createElement("div");
    el.className = "bubble-usage";
    el.textContent = line;
    bubbleEl.appendChild(el);
  }

  function renderUsageSummary(data) {
    if (!usageSummaryEl) return;
    if (!data || !data.ok) {
      usageSummaryEl.textContent = "暂无统计";
      return;
    }
    const totals = data.totals || {};
    const last = data.last_turn || {};
    usageSummaryEl.innerHTML =
      `<div class="usage-row"><span>本对话累计</span><span>${formatTokenCount(totals.total_tokens)} · ${Number(totals.turns) || 0} 轮</span></div>` +
      `<div class="usage-row"><span>输入 / 输出</span><span>↑${formatTokenCount(totals.input_tokens)} ↓${formatTokenCount(totals.output_tokens)}</span></div>` +
      `<div class="usage-row"><span>最近一轮</span><span>${last.total_tokens != null ? formatTokenCount(last.total_tokens) : "—"}</span></div>`;
  }

  async function loadUsageSummary() {
    if (!usageSummaryEl) return;
    const convId = convStore.activeId;
    if (!convId) {
      usageSummaryEl.textContent = "暂无对话";
      return;
    }
    try {
      const res = await fetch(`/api/usage/summary?conv_id=${encodeURIComponent(convId)}`);
      const data = await res.json().catch(() => ({}));
      renderUsageSummary(data);
    } catch {
      usageSummaryEl.textContent = "用量加载失败";
    }
  }

  function setSessionIdForConv(convId, sid) {
    const c = getConvById(convId);
    if (!c) return;
    c.sessionId = sid || null;
    c.updatedAt = Date.now();
    saveConvStore();
  }

  function renderConversationList() {
    if (!convListEl) return;
    convListEl.innerHTML = "";
    const items = [...convStore.items].sort(
      (a, b) => (b.updatedAt || 0) - (a.updatedAt || 0)
    );
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "conv-empty";
      empty.textContent = "暂无对话，点击「新建」开始";
      convListEl.appendChild(empty);
      return;
    }
    for (const c of items) {
      const row = document.createElement("div");
      const running = isConvRunning(c.id);
      row.className = `conv-item${c.id === convStore.activeId ? " active" : ""}${running ? " running" : ""}`;
      row.dataset.id = c.id;

      const main = document.createElement("button");
      main.type = "button";
      main.className = "conv-item-main";
      const timeOrStatus = running ? "生成中…" : formatConvTime(c.updatedAt);
      main.innerHTML = `<span class="conv-item-title">${escapeHtml(c.title || "新对话")}</span><span class="conv-item-time">${escapeHtml(timeOrStatus)}</span>`;
      main.addEventListener("click", () => switchConversation(c.id));

      const del = document.createElement("button");
      del.type = "button";
      del.className = "conv-item-del";
      del.title = "删除对话";
      del.setAttribute("aria-label", "删除对话");
      del.textContent = "×";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        void deleteConversation(c.id);
      });

      row.appendChild(main);
      row.appendChild(del);
      convListEl.appendChild(row);
    }
  }

  function renderActiveMessages() {
    messagesEl.innerHTML = "";
    const c = getActiveConv();
    if (convTitleEl) convTitleEl.textContent = (c && c.title) || "对话";
    if (!c || !(c.messages || []).length) {
      if (!c || !isConvRunning(c.id)) {
        showEmpty();
        return;
      }
    } else {
      for (const m of c.messages) {
        addBubble(m.role, m.text || "", m.meta, {
          markdown: m.role === "assistant",
          persist: false,
          usage: m.usage || null,
        });
      }
    }
    remountActiveRunUi();
  }

  function remountActiveRunUi() {
    const id = convStore.activeId;
    const run = id ? chatRuns.get(id) : null;
    if (!run) return;
    clearEmpty();
    const bubble = addBubble("assistant", "", "assistant", { persist: false });
    bubble.classList.add("streaming");
    run.assistantBubble = bubble;
    run.bodyEl = bubble.querySelector(".body");
    run.turn.toolPanel = null;
    run.turn.toolLog = null;
    run.turn.toolCount = 0;
    if (run.assistantText) {
      run.bodyEl.classList.remove("md");
      run.bodyEl.textContent = run.assistantText;
    }
    setStreamStatus(bubble, run.statusText || "生成中…");
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function restoreHtmlPreviewForActive() {
    const c = getActiveConv();
    const prev = c && c.htmlPreview;
    if (prev && (prev.src || prev.srcdoc)) {
      openHtmlPreview({
        title: prev.title || "页面预览",
        src: prev.src || "",
        srcdoc: prev.srcdoc || "",
        skipPersist: true,
      });
    } else {
      closeHtmlPreview({ skipPersist: true });
    }
  }

  function switchConversation(id) {
    if (!convStore.items.some((c) => c.id === id)) return;
    if (convStore.activeId === id) return;
    convStore.activeId = id;
    saveConvStore();
    renderConversationList();
    renderActiveMessages();
    restoreHtmlPreviewForActive();
    refreshStatus();
    refreshComposerBusy();
    void loadUsageSummary();
  }

  async function abortConvRun(convId) {
    const run = chatRuns.get(convId);
    if (!run) return;
    run.stopRequested = true;
    const c = getConvById(convId);
    const sid = c && c.sessionId;
    if (sid) {
      try {
        await fetch(`/api/session/${encodeURIComponent(sid)}/interrupt`, {
          method: "POST",
        });
      } catch {
        /* ignore */
      }
    }
    try {
      run.abort.abort();
    } catch {
      /* ignore */
    }
  }

  async function deleteConversation(id) {
    const target = convStore.items.find((c) => c.id === id);
    if (!target) return;
    const running = isConvRunning(id);
    const ok = window.confirm(
      running
        ? `对话「${target.title || "新对话"}」仍在生成，删除将停止任务。确定？`
        : `删除对话「${target.title || "新对话"}」？`
    );
    if (!ok) return;

    if (running) await abortConvRun(id);

    if (target.sessionId) {
      try {
        await fetch(`/api/session/${encodeURIComponent(target.sessionId)}`, {
          method: "DELETE",
        });
      } catch {
        /* ignore */
      }
    }

    convStore.items = convStore.items.filter((c) => c.id !== id);
    if (convStore.activeId === id) {
      convStore.activeId = convStore.items[0] ? convStore.items[0].id : null;
      if (!convStore.activeId) await startNewConversation({ switchTo: true });
    }
    saveConvStore();
    renderConversationList();
    renderActiveMessages();
    restoreHtmlPreviewForActive();
    refreshComposerBusy();
    void loadUsageSummary();
  }

  async function newSession() {
    await startNewConversation({ switchTo: true });
    renderConversationList();
    renderActiveMessages();
    restoreHtmlPreviewForActive();
    refreshStatus();
    refreshComposerBusy();
    void loadUsageSummary();
    inputEl.focus();
  }

  function historyList() {
    return [];
  }

  function saveHistory(_path) {
    /* 多人模式不再手动维护工作区历史 */
  }

  function renderHistory() {
    /* no-op */
  }

  function setStatus(kind, text) {
    statusEl.className = `ws-status ${kind}`;
    statusEl.textContent = text;
  }

  function refreshStatus() {
    // 服务器路径不对用户展示；仅维护内部状态
    if (statusEl) {
      statusEl.className = appliedWorkspace ? "ws-status ok" : "ws-status pending";
      statusEl.textContent = appliedWorkspace ? "就绪" : "加载中";
    }
    if (activeEl) activeEl.textContent = "";
    if (kbPathEl) kbPathEl.textContent = "";
  }

  function showEmpty() {
    if (messagesEl.children.length) return;
    const div = document.createElement("div");
    div.className = "empty";
    div.id = "empty-hint";
    div.innerHTML =
      '<div class="empty-brand">Claude</div>' +
      "<p>问案件看知识库 · 处理自己的表请上传 · 结果会写到你的工作区</p>";
    messagesEl.appendChild(div);
  }

  /** 浏览器用：把绝对路径收成对用户可见的友好标题 */
  function friendlyBrowseLabel(absPath) {
    const p = String(absPath || "").replace(/\\/g, "/");
    const ws = String(appliedWorkspace || "").replace(/\\/g, "/");
    const kb = String(knowledgeRootPath || "").replace(/\\/g, "/");
    if (!p) return "我的文件 / 知识库";
    if (ws && (p === ws || p.startsWith(ws + "/"))) {
      const rel = p === ws ? "" : p.slice(ws.length + 1);
      return rel ? `我的文件 / ${rel}` : "我的文件";
    }
    if (kb && (p === kb || p.startsWith(kb + "/"))) {
      const rel = p === kb ? "" : p.slice(kb.length + 1);
      return rel ? `知识库 / ${rel}` : "知识库";
    }
    // 兜底：只显示最后一段，避免泄露完整服务器路径
    const parts = p.split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "目录";
  }

  function clearEmpty() {
    $("empty-hint")?.remove();
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  let markedConfigured = false;
  function renderMarkdown(text) {
    const raw = text || "";
    if (!window.marked || !window.DOMPurify) {
      return `<pre class="plain">${escapeHtml(raw)}</pre>`;
    }
    try {
      if (!markedConfigured) {
        marked.setOptions({
          gfm: true,
          breaks: true,
        });
        markedConfigured = true;
      }
      const html = marked.parse(raw);
      return DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        ADD_ATTR: ["data-case", "target", "rel"],
      });
    } catch {
      return `<pre class="plain">${escapeHtml(raw)}</pre>`;
    }
  }

  // 完整案号 / 部门受案号数字
  const CASE_FULL_RE = /[\u4e00-\u9fffA-Za-z0-9·\-]*受\[[12]\d{3}\]\d{8,16}号/g;
  const CASE_LABELED_RE = /部门受案号\s*[：:]?\s*(\d{8,16})\s*号?/g;
  const CASE_DIGIT_RE = /(?<!\d)(1\d{9,15})(?!\d)/g;

  let caseFolderNames = null;
  let caseFolderDigits = null;
  let caseIndexMode = "folder"; // excel | folder
  let caseIndexPromise = null;

  function extractCaseDigit(query) {
    const q = String(query || "").trim();
    if (!q) return "";
    const full = /\[([12]\d{3})\](\d{8,16})/.exec(q);
    if (full) return full[2];
    if (/^\d{8,16}$/.test(q)) return q;
    const any = /(\d{8,16})/.exec(q);
    return any ? any[1] : "";
  }

  function extractCaseQueryFromMatch(raw) {
    const labeled = /部门受案号\s*[：:]?\s*(\d{8,16})/.exec(raw);
    if (labeled) return labeled[1];
    if (/受\[[12]\d{3}\]\d{8,16}号/.test(raw)) return raw;
    if (/^1\d{9,15}$/.test(raw)) return raw;
    return raw;
  }

  function caseQueryExists(query) {
    const q = String(query || "").trim();
    if (!q) return false;
    const digit = extractCaseDigit(q);
    const digits = caseFolderDigits;
    // Excel 模式：仅表内部门受案号可点击
    if (caseIndexMode === "excel") {
      return !!(digits && digit && digits.has(digit));
    }
    if (digits && digit && digits.has(digit)) return true;
    const names = caseFolderNames || [];
    return names.some((name) => name === q || name.includes(q) || (digit && name.includes(digit)));
  }

  async function loadCaseIndex({ force = false } = {}) {
    if (!force && caseFolderDigits) return caseFolderDigits;
    if (!force && caseIndexPromise) return caseIndexPromise;
    caseIndexPromise = (async () => {
      try {
        const res = await fetch("/api/cases/index");
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          caseFolderNames = caseFolderNames || [];
          caseFolderDigits = caseFolderDigits || new Set();
          return caseFolderDigits;
        }
        caseIndexMode = data.mode || "folder";
        caseFolderNames = data.names || [];
        caseFolderDigits = new Set(data.digits || []);
        return caseFolderDigits;
      } catch {
        caseFolderNames = caseFolderNames || [];
        caseFolderDigits = caseFolderDigits || new Set();
        return caseFolderDigits;
      } finally {
        caseIndexPromise = null;
      }
    })();
    return caseIndexPromise;
  }

  async function linkifyCaseNumbers(rootEl) {
    if (!rootEl) return;
    await loadCaseIndex();
    if (!rootEl.isConnected) return;

    const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const p = node.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        const tag = p.tagName;
        if (tag === "A" || tag === "CODE" || tag === "PRE" || tag === "SCRIPT") {
          return NodeFilter.FILTER_REJECT;
        }
        if (!node.nodeValue || !/[受号\d]/.test(node.nodeValue)) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    for (const textNode of nodes) {
      if (!textNode.isConnected) continue;
      const text = textNode.nodeValue;
      const matches = [];
      for (const re of [CASE_FULL_RE, CASE_LABELED_RE, CASE_DIGIT_RE]) {
        re.lastIndex = 0;
        let m;
        while ((m = re.exec(text))) {
          matches.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
        }
      }
      if (!matches.length) continue;
      matches.sort((a, b) => a.start - b.start || b.end - a.end);
      const picked = [];
      let lastEnd = -1;
      for (const m of matches) {
        if (m.start < lastEnd) continue;
        picked.push(m);
        lastEnd = m.end;
      }
      // 仅保留能匹配到案件文件夹的案号
      const linkable = picked.filter((m) => caseQueryExists(extractCaseQueryFromMatch(m.text)));
      if (!linkable.length) continue;

      const frag = document.createDocumentFragment();
      let cursor = 0;
      for (const m of linkable) {
        if (m.start > cursor) {
          frag.appendChild(document.createTextNode(text.slice(cursor, m.start)));
        }
        const a = document.createElement("a");
        a.href = "#";
        a.className = "case-link";
        a.dataset.case = extractCaseQueryFromMatch(m.text);
        a.title = `打开案件：${a.dataset.case}`;
        a.textContent = m.text;
        frag.appendChild(a);
        cursor = m.end;
      }
      if (cursor < text.length) {
        frag.appendChild(document.createTextNode(text.slice(cursor)));
      }
      textNode.parentNode.replaceChild(frag, textNode);
    }
  }

  function setBubbleContent(bodyEl, text, { markdown = false } = {}) {
    if (!bodyEl) return;
    if (markdown) {
      bodyEl.classList.add("md");
      bodyEl.innerHTML = renderMarkdown(text);
      bodyEl.querySelectorAll("table").forEach((table) => {
        if (table.parentElement?.classList.contains("table-wrap")) return;
        const wrap = document.createElement("div");
        wrap.className = "table-wrap";
        table.parentNode.insertBefore(wrap, table);
        wrap.appendChild(table);
      });
      // 仅高亮可匹配的案号（优先 Excel）
      void linkifyCaseNumbers(bodyEl);
    } else {
      bodyEl.classList.remove("md");
      bodyEl.textContent = text || "";
    }
    const bubble = bodyEl.closest(".bubble");
    if (bubble && bubble.classList.contains("assistant")) {
      attachFileDownloads(bubble, text || "");
      if (markdown) attachInlineHtmlPreviews(bubble);
    }
  }

  function sheetBasename(p) {
    const s = String(p || "").replace(/\\/g, "/");
    const i = s.lastIndexOf("/");
    return i >= 0 ? s.slice(i + 1) : s;
  }

  function extractSheetPaths(text) {
    const raw = String(text || "");
    if (!raw) return [];
    // 绝对路径（Windows / Unix）或相对路径文件名
    const re =
      /(?:[A-Za-z]:[\\/][^\s"'`<>|]+\.(?:xlsx|xls|csv)|\/[^\s"'`<>|]+\.(?:xlsx|xls|csv)|(?:\.\/)?[\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-()/\\]*\.(?:xlsx|xls|csv))/gi;
    const found = [];
    const seen = new Set();
    let m;
    while ((m = re.exec(raw))) {
      let p = m[0].replace(/^["'`]+|["'`]+$/g, "");
      p = p.replace(/[，。；;:）)\]}>]+$/g, "");
      if (!/\.(xlsx|xls|csv)$/i.test(p)) continue;
      const key = p.replace(/\\/g, "/").toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      found.push(p);
    }
    return found;
  }

  function buildSheetDownloadUrl(filePath) {
    return (
      `/api/workspace/download?path=${encodeURIComponent(filePath)}`
    );
  }

  function extractHtmlPaths(text) {
    const raw = String(text || "");
    if (!raw) return [];
    const re =
      /(?:[A-Za-z]:[\\/][^\s"'`<>|]+\.(?:html|htm)|\/[^\s"'`<>|]+\.(?:html|htm)|(?:\.\/)?[\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-()/\\]*\.(?:html|htm))/gi;
    const found = [];
    const seen = new Set();
    let m;
    while ((m = re.exec(raw))) {
      let p = m[0].replace(/^["'`]+|["'`]+$/g, "");
      p = p.replace(/[，。；;:）)\]}>]+$/g, "");
      if (!/\.(html|htm)$/i.test(p)) continue;
      const key = p.replace(/\\/g, "/").toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      found.push(p);
    }
    return found;
  }

  function looksLikeHtml(code) {
    const t = String(code || "").trim();
    if (t.length < 20) return false;
    return /<!doctype html|<html[\s>]|<(?:head|body|div|section|main|style|script)[\s>]/i.test(t);
  }

  function closeHtmlPreview({ skipPersist = false } = {}) {
    if (!htmlPreviewEl) return;
    htmlPreviewEl.classList.add("hidden");
    htmlPreviewEl.setAttribute("aria-hidden", "true");
    shellEl?.classList.remove("preview-open");
    if (htmlPreviewFrame) {
      htmlPreviewFrame.removeAttribute("srcdoc");
      htmlPreviewFrame.src = "about:blank";
    }
    if (previewBlobUrl) {
      URL.revokeObjectURL(previewBlobUrl);
      previewBlobUrl = "";
    }
    if (htmlPreviewOpen) {
      htmlPreviewOpen.href = "#";
      htmlPreviewOpen.classList.add("hidden");
    }
    if (!skipPersist) {
      const c = getActiveConv();
      if (c && c.htmlPreview) {
        delete c.htmlPreview;
        saveConvStore();
      }
    }
  }

  function openHtmlPreview({ title, src, srcdoc, skipPersist = false } = {}) {
    if (!htmlPreviewEl || !htmlPreviewFrame) return;
    if (htmlPreviewTitle) htmlPreviewTitle.textContent = title || "页面预览";
    htmlPreviewFrame.removeAttribute("srcdoc");
    htmlPreviewFrame.src = "about:blank";
    if (previewBlobUrl) {
      URL.revokeObjectURL(previewBlobUrl);
      previewBlobUrl = "";
    }
    if (srcdoc) {
      htmlPreviewFrame.srcdoc = srcdoc;
      if (htmlPreviewOpen) {
        const blob = new Blob([srcdoc], { type: "text/html;charset=utf-8" });
        previewBlobUrl = URL.createObjectURL(blob);
        htmlPreviewOpen.href = previewBlobUrl;
        htmlPreviewOpen.classList.remove("hidden");
      }
    } else if (src) {
      htmlPreviewFrame.src = src;
      if (htmlPreviewOpen) {
        htmlPreviewOpen.href = src;
        htmlPreviewOpen.classList.remove("hidden");
      }
    }
    htmlPreviewEl.classList.remove("hidden");
    htmlPreviewEl.setAttribute("aria-hidden", "false");
    shellEl?.classList.add("preview-open");

    if (!skipPersist) {
      const c = getActiveConv();
      if (c) {
        if (src) {
          c.htmlPreview = { title: title || "页面预览", src };
          saveConvStore();
        } else if (srcdoc && srcdoc.length <= 180000) {
          c.htmlPreview = { title: title || "页面预览", srcdoc };
          saveConvStore();
        }
      }
    }
  }

  function bindPreviewResizer() {
    if (!htmlPreviewResizer || !shellEl) return;
    let dragging = false;
    htmlPreviewResizer.addEventListener("mousedown", (e) => {
      e.preventDefault();
      dragging = true;
      htmlPreviewResizer.classList.add("dragging");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const rect = shellEl.getBoundingClientRect();
      const width = Math.min(Math.max(rect.right - e.clientX, 280), Math.floor(rect.width * 0.62));
      shellEl.style.setProperty("--preview-width", `${width}px`);
    });
    window.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      htmlPreviewResizer.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    });
  }

  function buildHtmlPreviewUrl(filePath) {
    return (
      `/api/workspace/preview?path=${encodeURIComponent(filePath)}`
    );
  }

  async function checkWorkspaceFile(filePath) {
    if (!filePath) return null;
    try {
      const res = await fetch(
        `/api/workspace/check?path=${encodeURIComponent(filePath)}`
      );
      const data = await res.json().catch(() => ({}));
      if (data && data.exists) return data;
    } catch {
      /* ignore */
    }
    return null;
  }

  async function checkWorkspaceDownloadable(filePath) {
    const data = await checkWorkspaceFile(filePath);
    if (data && data.downloadable) {
      return { path: data.path || filePath, name: data.name || sheetBasename(filePath) };
    }
    return null;
  }

  function attachInlineHtmlPreviews(bubbleEl) {
    if (!bubbleEl) return;
    bubbleEl.querySelectorAll("pre").forEach((pre) => {
      if (pre.dataset.htmlPreviewBound) return;
      const code = pre.querySelector("code") || pre;
      const raw = code.textContent || "";
      if (!looksLikeHtml(raw)) return;
      pre.dataset.htmlPreviewBound = "1";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "file-preview";
      btn.textContent = "预览页面";
      btn.addEventListener("click", () => {
        openHtmlPreview({ title: "代码预览", srcdoc: raw });
      });
      const wrap = document.createElement("div");
      wrap.className = "file-action-bar";
      wrap.style.borderTop = "0";
      wrap.style.marginTop = "8px";
      wrap.style.paddingTop = "0";
      wrap.appendChild(btn);
      pre.insertAdjacentElement("afterend", wrap);
    });
  }

  function attachFileDownloads(bubbleEl, text) {
    if (!bubbleEl) return;
    bubbleEl.querySelectorAll(".file-dl-bar, .file-action-bar.auto-files").forEach((el) => el.remove());
    const sheetPaths = extractSheetPaths(text);
    const htmlPaths = extractHtmlPaths(text);
    if (!sheetPaths.length && !htmlPaths.length) return;

    const bar = document.createElement("div");
    bar.className = "file-action-bar auto-files";
    bar.hidden = true;
    bubbleEl.appendChild(bar);

    void (async () => {
      const seen = new Set();
      for (const p of sheetPaths) {
        const hit = await checkWorkspaceDownloadable(p);
        if (!hit) continue;
        const key = `dl:${String(hit.path).replace(/\\/g, "/").toLowerCase()}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const a = document.createElement("a");
        a.className = "file-dl";
        a.href = buildSheetDownloadUrl(hit.path);
        a.setAttribute("download", hit.name);
        a.title = hit.path;
        a.textContent = `下载 ${hit.name}`;
        bar.appendChild(a);
      }
      for (const p of htmlPaths) {
        const data = await checkWorkspaceFile(p);
        if (!data || !data.previewable) continue;
        const key = `pv:${String(data.path || p).replace(/\\/g, "/").toLowerCase()}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "file-preview";
        btn.textContent = `预览 ${data.name || sheetBasename(p)}`;
        btn.title = data.path || p;
        btn.addEventListener("click", () => {
          openHtmlPreview({
            title: data.name || sheetBasename(p),
            src: buildHtmlPreviewUrl(data.path || p),
          });
        });
        bar.appendChild(btn);
      }
      if (!bar.childElementCount) {
        bar.remove();
        return;
      }
      bar.hidden = false;
    })();
  }

  function getHistoryForSend() {
    const c = getActiveConv();
    const msgs = (c && c.messages) || [];
    // 最后一条刚写入的当前用户消息不重复塞进 history
    const prior = msgs.slice(0, -1).filter(
      (m) => m && (m.role === "user" || m.role === "assistant") && (m.text || "").trim()
    );
    return prior.slice(-16).map((m) => ({
      role: m.role,
      text: String(m.text || "").slice(0, 3000),
    }));
  }

  function setCaseMsg(text, kind = "err") {
    if (!text) {
      caseMsgEl.className = "case-msg";
      caseMsgEl.textContent = "";
      return;
    }
    caseMsgEl.className = `case-msg show${kind === "info" ? " info" : ""}`;
    caseMsgEl.textContent = text;
  }

  function clearCaseExcel() {
    caseExcelFields.innerHTML = "";
    caseExcelEl.classList.add("hidden");
  }

  function renderCaseExcel(excel) {
    clearCaseExcel();
    const fields = (excel && excel.fields) || [];
    const visible = fields.filter((f) => String(f.value || "").trim() !== "");
    if (!visible.length) return;
    visible.forEach((f) => {
      const dt = document.createElement("dt");
      dt.textContent = f.key || "";
      const dd = document.createElement("dd");
      dd.textContent = f.value || "";
      caseExcelFields.appendChild(dt);
      caseExcelFields.appendChild(dd);
    });
    caseExcelEl.classList.remove("hidden");
  }

  function closeCaseDrawer() {
    caseDrawer.classList.add("hidden");
    caseDrawer.setAttribute("aria-hidden", "true");
    casePdf.removeAttribute("src");
    casePdfName.textContent = "PDF 预览";
    casePdfOpen.classList.add("hidden");
    casePdfOpen.removeAttribute("href");
    casePdfEmpty.classList.remove("hidden");
    clearCaseExcel();
  }

  function openCaseDrawer() {
    caseDrawer.classList.remove("hidden");
    caseDrawer.setAttribute("aria-hidden", "false");
  }

  function showPdf(filePath, fileName) {
    const url = `/api/cases/pdf?path=${encodeURIComponent(filePath)}`;
    casePdf.src = url;
    casePdfName.textContent = fileName || "PDF 预览";
    casePdfOpen.href = url;
    casePdfOpen.classList.remove("hidden");
    casePdfEmpty.classList.add("hidden");
  }

  function renderCaseDetail(detail) {
    caseTitle.textContent = detail.name || "—";
    casePathEl.textContent = detail.path || (detail.folder_missing ? "未找到对应文书目录" : "");
    caseTypeList.innerHTML = "";
    caseFileList.innerHTML = "";
    casePdf.removeAttribute("src");
    casePdfName.textContent = "PDF 预览";
    casePdfOpen.classList.add("hidden");
    casePdfOpen.removeAttribute("href");
    casePdfEmpty.classList.remove("hidden");
    renderCaseExcel(detail.excel);

    const types = [...(detail.types || [])];
    if ((detail.root_pdfs || []).length) {
      types.unshift({
        name: "根目录 PDF",
        path: detail.path,
        pdf_count: detail.root_pdfs.length,
        pdfs: detail.root_pdfs,
      });
    }
    if (!types.length) {
      const tip = detail.excel
        ? "已显示 Excel 案件信息；该案号下未找到文书 PDF 目录"
        : "该案件目录下未发现数据类型文件夹或 PDF";
      setCaseMsg(tip, "info");
      return;
    }
    setCaseMsg("");

    let activeBtn = null;
    const selectType = (type, btn) => {
      if (activeBtn) activeBtn.classList.remove("active");
      activeBtn = btn;
      btn.classList.add("active");
      caseFileList.innerHTML = "";
      const pdfs = type.pdfs || [];
      if (!pdfs.length) {
        caseFileList.innerHTML = `<div class="case-empty">该类型下暂无 PDF</div>`;
        return;
      }
      pdfs.forEach((pdf, idx) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "case-file-item";
        row.textContent = pdf.name;
        row.title = pdf.path;
        row.addEventListener("click", () => {
          caseFileList.querySelectorAll(".case-file-item").forEach((el) => el.classList.remove("active"));
          row.classList.add("active");
          showPdf(pdf.path, pdf.name);
        });
        caseFileList.appendChild(row);
        if (idx === 0) {
          row.classList.add("active");
          showPdf(pdf.path, pdf.name);
        }
      });
    };

    types.forEach((type, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "case-type-item";
      btn.innerHTML = `<span>${escapeHtml(type.name)}</span><em>${type.pdf_count || 0}</em>`;
      btn.addEventListener("click", () => selectType(type, btn));
      caseTypeList.appendChild(btn);
      if (idx === 0) selectType(type, btn);
    });
  }

  async function openCaseByQuery(query) {
    openCaseDrawer();
    caseTitle.textContent = query;
    casePathEl.textContent = "查找中…";
    setCaseMsg("正在匹配 Excel 与文书目录…", "info");
    clearCaseExcel();
    caseTypeList.innerHTML = "";
    caseFileList.innerHTML = "";
    try {
      const res = await fetch(`/api/cases/detail?q=${encodeURIComponent(query)}`);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        renderCaseDetail(data);
        return;
      }
      // 未配置 Excel 时：多个文件夹候选
      const searchRes = await fetch(`/api/cases/search?q=${encodeURIComponent(query)}`);
      const searchData = await searchRes.json().catch(() => ({}));
      if (!searchRes.ok || !(searchData.cases || []).length) {
        setCaseMsg(detailMessage(data) || `未找到案号：${query}`, "err");
        casePathEl.textContent = "";
        return;
      }
      if (searchData.cases.length === 1) {
        const detailRes = await fetch(
          `/api/cases/detail?path=${encodeURIComponent(searchData.cases[0].path)}&q=${encodeURIComponent(query)}`
        );
        const detail = await detailRes.json();
        if (!detailRes.ok) {
          setCaseMsg(detailMessage(detail), "err");
          return;
        }
        renderCaseDetail(detail);
        return;
      }
      setCaseMsg(`找到 ${searchData.cases.length} 个匹配，请选择：`, "info");
      casePathEl.textContent = searchData.root || "";
      caseTypeList.innerHTML = "";
      searchData.cases.forEach((c) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "case-type-item";
        btn.innerHTML = `<span>${escapeHtml(c.name)}</span>`;
        btn.addEventListener("click", async () => {
          setCaseMsg("加载中…", "info");
          const detailRes = await fetch(
            `/api/cases/detail?path=${encodeURIComponent(c.path)}&q=${encodeURIComponent(query)}`
          );
          const detail = await detailRes.json();
          if (!detailRes.ok) {
            setCaseMsg(detailMessage(detail), "err");
            return;
          }
          renderCaseDetail(detail);
        });
        caseTypeList.appendChild(btn);
      });
    } catch (err) {
      setCaseMsg(err.message || String(err), "err");
    }
  }

  function addBubble(role, text, meta, { markdown = false, persist = false, usage = null } = {}) {
    clearEmpty();
    const div = document.createElement("div");
    div.className = `bubble ${role}`;
    if (meta) {
      const m = document.createElement("span");
      m.className = "meta";
      m.textContent = meta;
      div.appendChild(m);
    }
    const body = document.createElement("div");
    body.className = "body";
    div.appendChild(body);
    // 必须先挂到 .bubble 上，setBubbleContent 才能找到气泡并挂预览/下载按钮
    const useMd = markdown || role === "assistant";
    setBubbleContent(body, text || "", { markdown: useMd && !!text });
    if (usage) attachUsageBadge(div, usage);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    if (persist) appendMessageToActive(role, text, meta, usage);
    return div;
  }

  function detailMessage(data) {
    if (!data) return "请求失败";
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
    }
    return data.message || "请求失败";
  }

  function setBrowserMsg(text, kind = "err") {
    if (!text) {
      browserMsgEl.className = "browser-msg";
      browserMsgEl.textContent = "";
      return;
    }
    browserMsgEl.className = `browser-msg show${kind === "info" ? " info" : ""}`;
    browserMsgEl.textContent = text;
  }

  async function loadHealth() {
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      if (data.knowledge_root) knowledgeRootPath = data.knowledge_root;
      if (data.model) {
        sideModelEl.textContent = `${data.provider || "模型"} · ${data.model}`;
      }
      refreshStatus();
      if (!data.has_api_key) addBubble("error", "未检测到 API Key，请检查 .env。");
      if (data.case_pdf_root && !data.case_pdf_root_ok) {
        addBubble("error", `案件 PDF 根目录不可用：${data.case_pdf_root}`);
      }
      if (data.case_excel_path) {
        if (!data.case_excel_ok) {
          addBubble(
            "error",
            `案件 Excel 不可用：${data.case_excel_path}${data.case_excel_error ? " — " + data.case_excel_error : ""}`
          );
        } else {
          void loadCaseIndex({ force: true });
        }
      } else if (data.case_pdf_root_ok) {
        void loadCaseIndex();
      }
    } catch (err) {
      addBubble("error", `无法连接后端: ${err.message}`);
    }
  }

  async function loadUserWorkspace() {
    try {
      const res = await fetch("/api/workspace");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        addBubble("error", detailMessage(data));
        return false;
      }
      appliedWorkspace = data.path || "";
      if (workspaceEl) workspaceEl.value = appliedWorkspace;
      localStorage.setItem(LS_WORKSPACE, appliedWorkspace);
      if (data.knowledge_root) knowledgeRootPath = data.knowledge_root;
      const active = getActiveConv();
      if (active) {
        active.workspace = appliedWorkspace;
        saveConvStore();
      }
      refreshStatus();
      return true;
    } catch (err) {
      addBubble("error", `无法分配工作区: ${err.message || err}`);
      return false;
    }
  }

  async function ensureWorkspaceApplied() {
    if (appliedWorkspace) return true;
    return loadUserWorkspace();
  }

  function formatBytes(n) {
    if (!n && n !== 0) return "";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function uploadLocalFile(file) {
    if (!file) return;
    if (!(await ensureWorkspaceApplied())) return;

    const allowed = [".xlsx", ".xls", ".csv"];
    const name = file.name || "";
    const lower = name.toLowerCase();
    if (!allowed.some((ext) => lower.endsWith(ext))) {
      addBubble("error", "仅支持上传 .xlsx / .xls / .csv");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      addBubble("error", "文件过大，上限 50MB");
      return;
    }

    const fd = new FormData();
    fd.append("file", file, name);

    try {
      const res = await fetch("/api/workspace/upload", {
        method: "POST",
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        addBubble("error", detailMessage(data));
        return;
      }
      if (data.workspace) {
        appliedWorkspace = data.workspace;
        if (workspaceEl) workspaceEl.value = appliedWorkspace;
        localStorage.setItem(LS_WORKSPACE, appliedWorkspace);
        refreshStatus();
      }
      addBubble(
        "assistant",
        `已上传「${data.name}」（${formatBytes(data.size)}）到你的工作区。\n\n可在对话中说：分析或修改 ${data.name}\n改完后回复里会出现「下载」按钮。`,
        "upload",
        { persist: false }
      );
    } catch (err) {
      addBubble("error", err.message || String(err));
    }
  }

  async function openBrowser(path) {
    browserEl.classList.remove("hidden");
    setBrowserMsg("加载中…", "info");
    browserListEl.innerHTML = "";
    try {
      const params = new URLSearchParams();
      if (path) params.set("path", path);
      const qs = params.toString();
      const res = await fetch(`/api/workspace/browse${qs ? `?${qs}` : ""}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setBrowserMsg(detailMessage(data), "err");
        return;
      }
      setBrowserMsg("");
      browseParent = data.parent;
      browseCurrent = data.path || "";
      browserPathEl.textContent = friendlyBrowseLabel(data.path || "");
      browserPathEl.title = browserPathEl.textContent;

      const children = data.children || [];
      if (!children.length) {
        setBrowserMsg("此目录为空或无可列内容", "info");
      }
      for (const item of children) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "browser-item";
        const tag = item.tag || (item.is_dir ? "目录" : "文件");
        // 根入口用友好名；子项只用文件名，不暴露绝对路径
        const label =
          item.tag ||
          (!data.path && (item.name === "我的工作区" || item.name === "知识库（只读）")
            ? item.name
            : item.name);
        row.innerHTML = `<span class="tag">${tag}</span>${escapeHtml(label)}`;
        row.title = item.is_dir ? friendlyBrowseLabel(item.path) : item.name;
        row.addEventListener("click", async () => {
          if (item.is_dir) {
            await openBrowser(item.path);
          } else {
            setBrowserMsg(`已选：${item.name}`, "info");
          }
        });
        browserListEl.appendChild(row);
      }
    } catch (err) {
      setBrowserMsg(err.message || String(err), "err");
    }
  }

  function ensureToolPanel(state, convId) {
    if (convId && !isViewingConv(convId)) return null;
    if (state.toolPanel && state.toolPanel.isConnected) return state.toolPanel;
    clearEmpty();
    const details = document.createElement("details");
    details.className = "tool-panel";
    details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "工具调用";
    const log = document.createElement("pre");
    log.className = "tool-log";
    details.appendChild(summary);
    details.appendChild(log);
    messagesEl.appendChild(details);
    state.toolPanel = details;
    state.toolLog = log;
    state.toolCount = 0;
    return details;
  }

  function appendToolLine(state, line, convId) {
    if (convId && !isViewingConv(convId)) {
      state.toolCount = (state.toolCount || 0) + 1;
      state.bufferedTools = (state.bufferedTools || "") + (state.bufferedTools ? "\n" : "") + line;
      return;
    }
    ensureToolPanel(state, convId);
    if (!state.toolPanel) return;
    state.toolCount += 1;
    const summary = state.toolPanel.querySelector("summary");
    summary.textContent = `工具调用（${state.toolCount}）`;
    state.toolLog.textContent += (state.toolLog.textContent ? "\n" : "") + line;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setStreamStatus(bubbleEl, text) {
    if (!bubbleEl || !bubbleEl.isConnected) return;
    let el = bubbleEl.querySelector(".stream-status");
    if (!text) {
      el?.remove();
      return;
    }
    if (!el) {
      el = document.createElement("div");
      el.className = "stream-status";
      bubbleEl.appendChild(el);
    }
    el.textContent = text;
  }

  async function stopChat() {
    const id = convStore.activeId;
    if (!isConvRunning(id)) return;
    await abortConvRun(id);
  }

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || isConvRunning(convStore.activeId)) return;
    if (!(await ensureWorkspaceApplied())) return;

    const userText = text;

    const conv = ensureActiveConversation();
    const convId = conv.id;
    const history = getHistoryForSend();
    const sessionIdAtStart = conv.sessionId || null;

    const abort = new AbortController();
    const run = {
      abort,
      stopRequested: false,
      assistantText: "",
      statusText: "思考中…",
      assistantBubble: null,
      bodyEl: null,
      turn: { toolPanel: null, toolLog: null, toolCount: 0, bufferedTools: "" },
      rafId: 0,
      pendingPaint: false,
      gotDelta: false,
      usage: null,
    };
    chatRuns.set(convId, run);
    renderConversationList();
    refreshComposerBusy();

    inputEl.value = "";
    autosizeInput();

    if (isViewingConv(convId)) {
      addBubble("user", userText, "you", { persist: true });
      run.assistantBubble = addBubble("assistant", "", "assistant", { persist: false });
      run.assistantBubble.classList.add("streaming");
      run.bodyEl = run.assistantBubble.querySelector(".body");
      setStreamStatus(run.assistantBubble, run.statusText);
    } else {
      appendMessageToConv(convId, "user", userText, "you");
    }

    const paintStreaming = () => {
      run.pendingPaint = false;
      run.rafId = 0;
      if (!isViewingConv(convId) || !run.bodyEl || !run.bodyEl.isConnected) return;
      run.bodyEl.classList.remove("md");
      run.bodyEl.textContent = run.assistantText;
      messagesEl.scrollTop = messagesEl.scrollHeight;
    };

    const paintAssistant = (final = false) => {
      if (final) {
        if (run.rafId) cancelAnimationFrame(run.rafId);
        run.rafId = 0;
        run.pendingPaint = false;
        if (isViewingConv(convId) && run.assistantBubble && run.assistantBubble.isConnected) {
          setStreamStatus(run.assistantBubble, "");
          setBubbleContent(run.bodyEl, run.assistantText, { markdown: true });
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        return;
      }
      if (run.pendingPaint) return;
      run.pendingPaint = true;
      run.rafId = requestAnimationFrame(paintStreaming);
    };

    const finishAssistant = (stopped) => {
      if (isViewingConv(convId) && run.assistantBubble && run.assistantBubble.isConnected) {
        run.assistantBubble.classList.remove("streaming");
        if (run.assistantText) {
          paintAssistant(true);
          appendMessageToConv(convId, "assistant", run.assistantText, "assistant", run.usage);
          if (run.usage) attachUsageBadge(run.assistantBubble, run.usage);
          if (stopped) setStreamStatus(run.assistantBubble, "已停止生成");
        } else {
          setStreamStatus(run.assistantBubble, "");
          if (stopped) {
            const body = run.assistantBubble.querySelector(".body");
            if (body) body.textContent = "（已停止）";
            appendMessageToConv(convId, "assistant", "（已停止）", "assistant", run.usage);
            if (run.usage) attachUsageBadge(run.assistantBubble, run.usage);
          } else {
            run.assistantBubble.remove();
          }
        }
        if (run.turn.toolPanel) run.turn.toolPanel.open = false;
      } else if (run.assistantText) {
        appendMessageToConv(convId, "assistant", run.assistantText, "assistant", run.usage);
      } else if (stopped) {
        appendMessageToConv(convId, "assistant", "（已停止）", "assistant", run.usage);
      }
    };

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: run.abort.signal,
        body: JSON.stringify({
          message: userText,
          session_id: sessionIdAtStart,
          conv_id: convId && String(convId).startsWith("c_") ? convId : null,
          history,
        }),
      });
      if (!res.ok || !res.body) throw new Error(`请求失败: HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          const line = part
            .split("\n")
            .map((l) => l.trim())
            .find((l) => l.startsWith("data:"));
          if (!line) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          let event;
          try {
            event = JSON.parse(raw);
          } catch {
            continue;
          }

          if (event.type === "session") {
            if (event.conv_id && event.conv_id !== convId) {
              const c = getConvById(convId);
              if (c) c.id = event.conv_id;
              if (convStore.activeId === convId) convStore.activeId = event.conv_id;
              const runObj = chatRuns.get(convId);
              if (runObj) {
                chatRuns.delete(convId);
                chatRuns.set(event.conv_id, runObj);
              }
              convId = event.conv_id;
              saveConvStore();
              renderConversationList();
            }
            setSessionIdForConv(convId, event.session_id);
            if (event.workspace && isViewingConv(convId)) {
              appliedWorkspace = event.workspace;
              localStorage.setItem(LS_WORKSPACE, appliedWorkspace);
              if (workspaceEl) workspaceEl.value = event.workspace;
              if (event.knowledge_root) knowledgeRootPath = event.knowledge_root;
              refreshStatus();
            } else if (event.workspace) {
              appliedWorkspace = event.workspace;
              localStorage.setItem(LS_WORKSPACE, appliedWorkspace);
            }
          } else if (event.type === "delta" && event.text) {
            run.gotDelta = true;
            run.assistantText += event.text;
            run.statusText = "生成中…";
            if (isViewingConv(convId)) {
              if (run.assistantText) setStreamStatus(run.assistantBubble, run.statusText);
              paintAssistant(false);
            }
          } else if (event.type === "assistant") {
            if (event.tools?.length) {
              run.statusText = "调用工具…";
              if (isViewingConv(convId)) setStreamStatus(run.assistantBubble, run.statusText);
              for (const t of event.tools) {
                const args = t.input ? JSON.stringify(t.input) : "";
                appendToolLine(run.turn, `→ ${t.name}${args ? " " + args : ""}`, convId);
              }
            }
            if (event.text) {
              if (!run.gotDelta) {
                run.assistantText = event.text;
                if (isViewingConv(convId)) paintAssistant(false);
              } else if (event.final && event.text.length > run.assistantText.length) {
                run.assistantText = event.text;
                if (isViewingConv(convId)) paintAssistant(false);
              }
            }
            if (event.error && isViewingConv(convId)) {
              addBubble("error", `模型错误: ${event.error}`, undefined, { persist: true });
            } else if (event.error) {
              appendMessageToConv(convId, "error", `模型错误: ${event.error}`, "error");
            }
          } else if (event.type === "tool_result" && event.text) {
            run.statusText = "调用工具…";
            if (isViewingConv(convId)) setStreamStatus(run.assistantBubble, run.statusText);
            appendToolLine(run.turn, `← ${event.text}`, convId);
          } else if (event.type === "error") {
            if (isViewingConv(convId)) {
              addBubble("error", event.message || "未知错误", undefined, { persist: true });
            } else {
              appendMessageToConv(convId, "error", event.message || "未知错误", "error");
            }
          } else if (event.type === "result") {
            if (event.usage) {
              run.usage = event.usage;
            }
            if (event.usage_summary) {
              renderUsageSummary(event.usage_summary);
            } else if (event.usage) {
              void loadUsageSummary();
            }
            if (event.is_error) {
              const detail = (event.errors && event.errors.join("\n")) || event.subtype || "";
              const isMaxTurns = /maximum number of turns|max_turns|轮次/i.test(
                `${detail} ${event.subtype || ""}`
              );
              const msg = isMaxTurns
                ? "工具轮次用尽。若要预览页面，请再说一次：把 HTML 复制/写入工作区 preview.html（不要用 Bash）。"
                : `执行结束但有错误: ${detail}`;
              if (isViewingConv(convId)) {
                addBubble("error", msg, undefined, { persist: true });
              } else {
                appendMessageToConv(convId, "error", msg, "error");
              }
            }
          }
        }
      }

      finishAssistant(false);
    } catch (err) {
      const aborted =
        run.stopRequested ||
        err?.name === "AbortError" ||
        /aborted|AbortError/i.test(String(err?.message || err));
      if (aborted) {
        finishAssistant(true);
      } else if (isViewingConv(convId)) {
        if (run.assistantBubble) {
          run.assistantBubble.classList.remove("streaming");
          setStreamStatus(run.assistantBubble, "");
        }
        addBubble("error", err.message || String(err), undefined, { persist: true });
        if (!run.assistantText && run.assistantBubble) run.assistantBubble.remove();
      } else {
        appendMessageToConv(convId, "error", err.message || String(err), "error");
      }
    } finally {
      chatRuns.delete(convId);
      renderConversationList();
      refreshComposerBusy();
      if (isViewingConv(convId)) inputEl.focus();
    }
  }

  messagesEl.addEventListener("click", (e) => {
    const link = e.target.closest("a.case-link");
    if (!link) return;
    e.preventDefault();
    const q = link.dataset.case || link.textContent.trim();
    if (q) openCaseByQuery(q);
  });

  btnCloseCase.addEventListener("click", closeCaseDrawer);
  caseBackdrop.addEventListener("click", closeCaseDrawer);
  if (btnHtmlClose) btnHtmlClose.addEventListener("click", closeHtmlPreview);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && htmlPreviewEl && !htmlPreviewEl.classList.contains("hidden")) {
      closeHtmlPreview();
      return;
    }
    if (e.key === "Escape" && !caseDrawer.classList.contains("hidden")) {
      closeCaseDrawer();
    }
  });

  btnSend.addEventListener("click", () => sendMessage().catch((e) => addBubble("error", e.message)));
  if (btnStop) btnStop.addEventListener("click", () => stopChat().catch(() => {}));
  btnNew.addEventListener("click", newSession);
  btnUpload.addEventListener("click", () => {
    if (isConvRunning(convStore.activeId)) return;
    fileUploadEl.value = "";
    fileUploadEl.click();
  });
  fileUploadEl.addEventListener("change", () => {
    const file = fileUploadEl.files && fileUploadEl.files[0];
    if (!file) return;
    uploadLocalFile(file)
      .catch((e) => addBubble("error", e.message || String(e)))
      .finally(() => {
        fileUploadEl.value = "";
      });
  });
  btnBrowse.addEventListener("click", () => {
    openBrowser("").catch((e) => {
      browserEl.classList.remove("hidden");
      setBrowserMsg(e.message || String(e), "err");
      addBubble("error", `浏览失败: ${e.message || e}`);
    });
  });
  btnUp.addEventListener("click", () => {
    if (browseParent != null && browseParent !== "") {
      openBrowser(browseParent).catch((e) => setBrowserMsg(e.message, "err"));
    } else {
      openBrowser("").catch((e) => setBrowserMsg(e.message, "err"));
    }
  });
  btnCloseBrowser.addEventListener("click", () => {
    browserEl.classList.add("hidden");
    setBrowserMsg("");
  });
  function autosizeInput() {
    inputEl.style.height = "auto";
    inputEl.style.height = `${Math.min(inputEl.scrollHeight, 180)}px`;
  }

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  inputEl.addEventListener("input", autosizeInput);

  void bootApp();
})();
