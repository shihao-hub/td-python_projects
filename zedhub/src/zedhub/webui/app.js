"use strict";
/* zedhub 会话检索页：只调 /api/v1（薄客户端，零业务逻辑、零依赖）。 */

const API = "/api/v1";
const MAX_MESSAGES = 50;        // 详情里最多渲染的消息条数（正文按需加载）
const MAX_TEXT_PER_MSG = 4000;  // 单条消息渲染上限，避免长正文压垮页面

const el = {
  form: document.getElementById("filters"),
  q: document.getElementById("q"),
  agent: document.getElementById("agent"),
  project: document.getElementById("project"),
  archived: document.getElementById("archived"),
  since: document.getElementById("since"),
  until: document.getElementById("until"),
  scope: document.getElementById("scope"),
  list: document.getElementById("list"),
  detail: document.getElementById("detail"),
  status: document.getElementById("status"),
  notice: document.getElementById("notice"),
};

/* -- 与 daemon 的唯一通道 ---------------------------------------------------- */

async function api(path, params) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== "" && v !== null && v !== undefined && v !== false) url.searchParams.set(k, v);
  }
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => null);
  if (!res.ok || !body || body.ok === false) {
    const err = (body && body.error) || { code: `http_${res.status}`, message: res.statusText };
    throw new Error(`${err.code}: ${err.message}`);
  }
  return body;
}

/* -- 渲染辅助 ---------------------------------------------------------------- */

function highlight(text, tokens) {
  const frag = document.createDocumentFragment();
  if (!tokens.length) {
    frag.append(text);
    return frag;
  }
  const lower = text.toLowerCase();
  let i = 0;
  while (i < text.length) {
    let at = -1;
    let len = 0;
    for (const t of tokens) {
      const p = lower.indexOf(t, i);
      if (p !== -1 && (at === -1 || p < at)) {
        at = p;
        len = t.length;
      }
    }
    if (at === -1) {
      frag.append(text.slice(i));
      break;
    }
    if (at > i) frag.append(text.slice(i, at));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(at, at + len);
    frag.append(mark);
    i = at + len;
  }
  return frag;
}

function badge(text, cls) {
  const span = document.createElement("span");
  span.className = `badge ${cls || ""}`.trim();
  span.textContent = text;
  return span;
}

function shortTime(iso) {
  if (!iso) return "-";
  return String(iso).slice(0, 16).replace("T", " ");
}

function basename(p) {
  return String(p).split(/[\\/]/).filter(Boolean).pop() || p;
}

function normPath(p) {
  if (!p) return "";
  let s = String(p).trim().replace(/\\/g, "/");
  while (s.length > 1 && s.endsWith("/")) {
    s = s.slice(0, -1);
  }
  return s.toLowerCase();
}

function setStatus(text) {
  el.status.textContent = text || "";
}

function showNotice(text) {
  el.notice.textContent = text || "";
  el.notice.hidden = !text;
}

/* -- 表单状态 <-> URL（刷新/分享保持检索条件） -------------------------------- */

let seq = 0;
let externalAgents = new Set();
let zedAgents = new Set();

function setAgentValue(agentId, scope) {
  if (!agentId) {
    el.agent.value = "";
    return;
  }
  const targetScope = scope || el.scope.value;
  const options = Array.from(el.agent.options);
  const matched = options.find((o) => o.value === agentId && o.dataset.management === targetScope)
    || options.find((o) => o.value === agentId);
  if (matched) {
    matched.selected = true;
  } else {
    el.agent.value = "";
  }
}

function setProjectValue(projVal) {
  if (!projVal) {
    el.project.value = "";
    return;
  }
  const normVal = normPath(projVal);
  const options = Array.from(el.project.options);
  const matched = options.find((o) => normPath(o.value) === normVal);
  if (matched) {
    matched.selected = true;
  } else {
    el.project.value = projVal;
  }
}

function restoreFromUrl() {
  const p = new URLSearchParams(location.search);
  el.q.value = p.get("q") || "";
  el.archived.value = p.get("archived") || "no";
  el.since.value = p.get("since") || "";
  el.until.value = p.get("until") || "";
  el.scope.value = p.get("scope") || "zed";
  const requestedAgent = p.get("agent") || "";
  setAgentValue(requestedAgent, el.scope.value);
  const requestedProj = p.get("project") || "";
  setProjectValue(requestedProj);
}

function readState() {
  return {
    q: el.q.value.trim(),
    agent: el.agent.value,
    project: el.project.value,
    archived: el.archived.value,
    since: el.since.value,
    until: el.until.value,
    scope: el.scope.value,
  };
}

function syncUrl(state) {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  if (el.agent.value) p.set("agent", el.agent.value);
  if (el.project.value) p.set("project", el.project.value);
  if (state.archived && state.archived !== "no") p.set("archived", state.archived);
  if (state.since) p.set("since", state.since);
  if (state.until) p.set("until", state.until);
  if (state.scope && state.scope !== "zed") p.set("scope", state.scope);
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

/* -- 候选值（agent / 项目，来自既有只读端点） -------------------------------- */

async function loadOptions() {
  const zedAgentCounts = new Map();
  const extAgentCounts = new Map();
  try {
    const body = await api(`${API}/stats`);
    for (const [agent, count] of Object.entries((body.data && body.data.agents) || {})) {
      zedAgentCounts.set(agent, count);
    }
  } catch (e) {
    showNotice(`读取 Zed agent 列表失败：${e.message}`);
  }

  // 读取 Zed 索引中的项目
  const zedProjectsList = [];
  try {
    const body = await api(`${API}/projects`);
    const seenZed = new Set();
    for (const p of body.data || []) {
      if (!p.path) continue;
      const key = normPath(p.path);
      if (seenZed.has(key)) continue;
      seenZed.add(key);
      zedProjectsList.push({
        path: p.path,
        norm: key,
        active: p.active || 0,
        total: p.total || 0,
      });
    }
  } catch (e) {
    showNotice(`读取 Zed 项目列表失败：${e.message}`);
  }

  try {
    const body = await api(`${API}/search`, { scope: "all", archived: "all", limit: 0 });
    const hits = (body.data && body.data.hits) || [];
    const searchZedCounts = new Map();
    const searchExtCounts = new Map();
    const extProjectStats = new Map(); // key -> { path, norm, count }
    const zedNormKeys = new Set(zedProjectsList.map((p) => p.norm));

    for (const hit of hits) {
      const aid = hit.agent_id;
      if (aid) {
        if (hit.management === "zed") {
          searchZedCounts.set(aid, (searchZedCounts.get(aid) || 0) + 1);
        } else if (hit.management === "external") {
          searchExtCounts.set(aid, (searchExtCounts.get(aid) || 0) + 1);
        }
      }

      for (const project of hit.projects || []) {
        if (!project) continue;
        const key = normPath(project);
        if (!zedNormKeys.has(key)) {
          let stat = extProjectStats.get(key);
          if (!stat) {
            stat = { path: project, norm: key, count: 0 };
            extProjectStats.set(key, stat);
          }
          stat.count += 1;
        }
      }
    }

    for (const [aid, count] of searchZedCounts.entries()) {
      zedAgentCounts.set(aid, count);
    }
    for (const [aid, count] of searchExtCounts.entries()) {
      extAgentCounts.set(aid, count);
    }

    zedAgents = new Set(zedAgentCounts.keys());
    externalAgents = new Set(extAgentCounts.keys());

    el.agent.replaceChildren();
    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = "全部";
    el.agent.append(allOpt);

    const sortAgentsByCount = (map) => {
      return [...map.keys()].sort((a, b) => {
        const diff = (map.get(b) || 0) - (map.get(a) || 0);
        return diff !== 0 ? diff : a.localeCompare(b);
      });
    };

    if (zedAgentCounts.size > 0) {
      const groupZed = document.createElement("optgroup");
      groupZed.label = "Zed 管理 (ACP)";
      for (const agent of sortAgentsByCount(zedAgentCounts)) {
        const opt = document.createElement("option");
        opt.value = agent;
        opt.dataset.management = "zed";
        opt.textContent = `${agent} (${zedAgentCounts.get(agent)})`;
        groupZed.append(opt);
      }
      el.agent.append(groupZed);
    }

    if (extAgentCounts.size > 0) {
      const groupExt = document.createElement("optgroup");
      groupExt.label = "外部发现";
      for (const agent of sortAgentsByCount(extAgentCounts)) {
        const opt = document.createElement("option");
        opt.value = agent;
        opt.dataset.management = "external";
        opt.textContent = `${agent} (${extAgentCounts.get(agent)})`;
        groupExt.append(opt);
      }
      el.agent.append(groupExt);
    }

    // 组织项目下拉框：全部 / Zed 管理项目 / 外部发现项目，按数量降序
    el.project.replaceChildren();
    const allProjOpt = document.createElement("option");
    allProjOpt.value = "";
    allProjOpt.textContent = "全部";
    el.project.append(allProjOpt);

    if (zedProjectsList.length > 0) {
      const groupZedProj = document.createElement("optgroup");
      groupZedProj.label = "Zed 管理项目";
      const sortedZedProj = [...zedProjectsList].sort((a, b) => {
        const diff = b.total - a.total;
        return diff !== 0 ? diff : basename(a.path).localeCompare(basename(b.path));
      });
      for (const p of sortedZedProj) {
        const opt = document.createElement("option");
        opt.value = p.path;
        opt.textContent = `${basename(p.path)} — ${p.active} 活跃 / ${p.total} 总计`;
        groupZedProj.append(opt);
      }
      el.project.append(groupZedProj);
    }

    if (extProjectStats.size > 0) {
      const groupExtProj = document.createElement("optgroup");
      groupExtProj.label = "外部发现项目";
      const sortedExtProj = [...extProjectStats.values()].sort((a, b) => {
        const diff = b.count - a.count;
        return diff !== 0 ? diff : basename(a.path).localeCompare(basename(b.path));
      });
      for (const p of sortedExtProj) {
        const opt = document.createElement("option");
        opt.value = p.path;
        opt.textContent = `${basename(p.path)} — (${p.count} 会话)`;
        groupExtProj.append(opt);
      }
      el.project.append(groupExtProj);
    }
  } catch (e) {
    showNotice(`读取全部会话筛选项失败：${e.message}`);
  }
  restoreFromUrl();
}

/* -- 检索 -------------------------------------------------------------------- */

async function runSearch() {
  const state = readState();
  syncUrl(state);
  const tokens = state.q.toLowerCase().split(/\s+/).filter(Boolean);
  const mine = ++seq;

  setStatus("检索中…");
  let body;
  try {
    body = await api(`${API}/search`, {
      q: state.q,
      agent: el.agent.value,
      project: el.project.value,
      archived: state.archived,
      since: state.since,
      until: state.until,
      limit: 200,
      scope: state.scope,
    });
  } catch (e) {
    if (mine !== seq) return;
    setStatus("检索失败");
    showNotice(e.message);
    return;
  }
  if (mine !== seq) return;

  const data = body.data || {};
  const hits = data.hits || [];
  showNotice((data.degraded || []).join("；"));
  const elapsed = body.meta && body.meta.elapsed_ms != null ? ` · ${body.meta.elapsed_ms}ms` : "";
  const capped = data.total > data.count ? `（共 ${data.total} 条，显示前 ${data.count} 条）` : "";
  setStatus(`${data.count} / ${data.total} 条命中${capped}${elapsed}`);

  el.list.replaceChildren();
  if (!hits.length) {
    const p = document.createElement("p");
    p.className = "placeholder";
    p.textContent = "没有命中。试试减少关键词、把「归档」改为「含归档」，或切换管理范围。";
    el.list.append(p);
    return;
  }
  for (const hit of hits) el.list.append(renderHit(hit, tokens));
}

function renderHit(hit, tokens) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "hit";
  btn.setAttribute("aria-selected", "false");

  const title = document.createElement("div");
  title.className = "title";
  title.append(highlight(hit.title || "(无标题)", tokens));
  btn.append(title);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.append(badge(hit.agent_id, "agent"));
  meta.append(badge(hit.management === "zed" ? "Zed 管理" : "外部发现", hit.management === "zed" ? "zed" : "external"));
  if (hit.source_id) meta.append(badge(hit.source_id, "source"));
  if (hit.archived) meta.append(badge("已归档", "arch"));
  if (hit.model && hit.model.model_id) meta.append(badge(`${hit.model.provider || "?"}/${hit.model.model_id}`));
  const time = document.createElement("span");
  time.textContent = `更新 ${shortTime(hit.updated_at || hit.created_at)}`;
  meta.append(time);
  if (hit.matched_fields && hit.matched_fields.length) {
    const m = document.createElement("span");
    m.textContent = `命中字段 ${hit.matched_fields.join("/")}`;
    meta.append(m);
  }
  btn.append(meta);

  if (hit.projects && hit.projects.length) {
    const paths = document.createElement("div");
    paths.className = "paths";
    paths.append(highlight(hit.projects.join("  ·  "), tokens));
    btn.append(paths);
  }

  btn.addEventListener("click", () => {
    for (const other of el.list.querySelectorAll(".hit")) other.setAttribute("aria-selected", "false");
    btn.setAttribute("aria-selected", "true");
    openHit(hit).catch((e) => {
      el.detail.replaceChildren();
      const p = document.createElement("p");
      p.className = "placeholder";
      p.textContent = `加载详情失败：${e.message}`;
      el.detail.append(p);
    });
  });
  return btn;
}

/* -- 详情面板 ---------------------------------------------------------------- */

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function mdToHtml(src) {
  // 轻量 markdown：转义后处理围栏代码块/行内 code/标题/粗体/列表/换行
  const fenced = [];
  let text = esc(src || "");
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) => {
    fenced.push(`<pre><code>${code}</code></pre>`);
    return `\u0000${fenced.length - 1}\u0000`;
  });
  const lines = text.split("\n");
  let html = "";
  let inList = false;
  for (const ln of lines) {
    const m = ln.match(/^\u0000(\d+)\u0000$/);
    if (m) { if (inList) { html += "</ul>"; inList = false; } html += fenced[+m[1]]; continue; }
    if (/^#{1,4}\s/.test(ln)) {
      if (inList) { html += "</ul>"; inList = false; }
      const lvl = ln.match(/^#+/)[0].length;
      html += `<h${lvl}>${ln.replace(/^#+\s*/, "")}</h${lvl}>`;
    } else if (/^\s*[-*]\s+/.test(ln)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${ln.replace(/^\s*[-*]\s+/, "")}</li>`;
    } else if (!ln.trim()) {
      if (inList) { html += "</ul>"; inList = false; }
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<p>${ln}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderDetail(hit, rows, actions) {
  el.detail.replaceChildren();
  const h2 = document.createElement("h2");
  h2.textContent = hit.title || "(无标题)";
  el.detail.append(h2);

  const tabs = document.createElement("div");
  tabs.className = "tabs";
  tabs.setAttribute("role", "tablist");
  const panes = {};
  for (const name of ["元数据", "轨迹"]) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "tab"; b.textContent = name;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", name === "元数据" ? "true" : "false");
    const pane = document.createElement("div");
    pane.className = "tabpane";
    pane.hidden = name !== "元数据";
    panes[name] = pane;
    b.addEventListener("click", () => {
      for (const n of Object.keys(panes)) panes[n].hidden = n !== name;
      for (const o of tabs.querySelectorAll(".tab")) o.setAttribute("aria-selected", String(o === b));
    });
    tabs.append(b);
  }
  el.detail.append(tabs);

  const meta = panes["元数据"];
  const dl = document.createElement("dl");
  for (const [k, v, mono] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    if (mono) dd.className = "mono";
    dd.textContent = v == null || v === "" ? "-" : String(v);
    dl.append(dt, dd);
  }
  meta.append(dl);
  if (actions && actions.length) {
    const bar = document.createElement("div");
    bar.className = "toolbar";
    for (const a of actions) bar.append(a);
    meta.append(bar);
  }
  const tmeta = document.createElement("p");
  tmeta.className = "placeholder";
  tmeta.textContent = "点「查看轨迹」加载；加载后显示在这里。";
  panes["轨迹"].append(tmeta);
  el.detail.append(meta, panes["轨迹"]);
  el.detail._panes = panes;
  return el.detail;
}

function actionButton(label, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "action";
  b.textContent = label;
  b.addEventListener("click", () => onClick(b));
  return b;
}

async function openHit(hit) {
  const actions = [];
  const rows = [
    ["类型", hit.kind === "zed_thread" ? "Zed 索引线程" : "OpenCode 会话（未进 Zed 索引）"],
    ["Agent", hit.agent_id],
    ["项目", (hit.projects || []).join("  ·  ")],
    ["创建", shortTime(hit.created_at)],
    ["更新", shortTime(hit.updated_at)],
    ["归档", hit.archived ? "是" : "否"],
    ["线程 id", hit.thread_id, true],
    ["会话 id", hit.session_id, true],
  ];

  let agentSource = hit.source_id || "";
  const aid = (hit.agent_id || "").toLowerCase();
  if (!agentSource) {
    if (aid.includes("codex")) agentSource = "codex";
    else if (aid.includes("claude")) agentSource = "claude-code";
    else if (aid.includes("pi")) agentSource = "pi";
    else if (aid.includes("antigravity")) agentSource = "antigravity";
    else agentSource = "opencode";
  }
  const hasContentSupport = ["opencode", "claude-code", "codex", "pi", "antigravity"].includes(agentSource);

  let contentLoader = null;
  if (hasContentSupport && hit.session_id) {
    contentLoader = actionButton("加载正文", async (b) => {
      b.disabled = true;
      b.textContent = "加载中…";
      try {
        const body = await api(`${API}/sessions/${encodeURIComponent(hit.session_id)}/content`, { source: agentSource });
        renderMessages(body.data, hit);
      } catch (e) {
        b.disabled = false;
        b.textContent = "加载正文";
        showNotice(`加载正文失败：${e.message}`);
      }
    });
    actions.push(contentLoader);
  }
  if (hit.kind === "opencode_session" && hit.session_id) {
    actions.push(actionButton("查看会话元数据", async () => {
      const body = await api(`${API}/sessions/${encodeURIComponent(hit.session_id)}`, { source: "opencode" });
      const s = body.data;
      renderDetail(hit, rows.concat([
        ["目录", s.directory],
        ["模型", s.model ? `${s.model.provider || "?"}/${s.model.model_id || "?"}` : "-"],
      ]), actions);
    }));
  }

  if (hit.session_id || hit.thread_id) {
    const sid = hit.session_id || hit.thread_id;
    const src = agentSource;
    actions.push(actionButton("查看轨迹", async (b) => {
      b.disabled = true;
      b.textContent = "轨迹加载中…";
      try {
        const body = await api(`${API}/trajectory/${encodeURIComponent(sid)}`,
          { source: src, thread_id: hit.thread_id || "" });
        renderTrajectory(body.data);
      } catch (e) {
        renderTrajectory({ source: src, count: 0, file: "", events: [], error: e.message });
      } finally {
        b.disabled = false;
        b.textContent = "查看轨迹";
      }
    }));
  }
  renderDetail(hit, rows, actions);

  if (hit.kind === "zed_thread" && hit.thread_id) {
    const body = await api(`${API}/threads/${encodeURIComponent(hit.thread_id)}`);
    const t = body.data;
    renderDetail(hit, [
      ["类型", "Zed 索引线程"],
      ["标题", t.title || "(无标题)"],
      ["Agent", t.agent_id],
      ["项目", (t.projects || []).join("  ·  ")],
      ["创建", shortTime(t.created_at)],
      ["更新", shortTime(t.updated_at)],
      ["最后交互", shortTime(t.interacted_at)],
      ["归档", t.archived ? "是" : "否"],
      ["线程 id", t.id, true],
      ["会话 id", t.session_id, true],
      hasContentSupport
        ? ["正文", `点上方「加载正文」（${agentSource} 对话气泡）`]
        : ["正文", `该会话属 ${hit.agent_id}，无结构化正文源（仅 Zed 索引可见）`],
    ], actions);
  }
}

function renderMessages(content, hit) {
  const wrap = document.createElement("div");
  wrap.className = "messages";
  const messages = content.messages || [];
  const shown = messages.slice(0, MAX_MESSAGES);
  for (const m of shown) {
    const box = document.createElement("div");
    box.className = `msg ${m.role === "user" ? "user" : "assistant"}`;
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = `${m.role || "?"} · ${shortTime(m.created_at)}${m.model_id ? ` · ${m.model_id}` : ""}`;
    box.append(who);
    const text = (m.parts || [])
      .filter((p) => p.type === "text" && p.text)
      .map((p) => p.text)
      .join("\n");
    const pre = document.createElement("pre");
    if (!text) {
      pre.textContent = "(无文本内容)";
    } else if (text.length > MAX_TEXT_PER_MSG) {
      pre.textContent = `${text.slice(0, MAX_TEXT_PER_MSG)}\n…（本条已截断，共 ${text.length} 字符；完整内容用 zedhub sessions content ${hit.session_id} --format markdown 导出）`;
    } else {
      pre.textContent = text;
    }
    box.append(pre);
    wrap.append(box);
  }
  if (messages.length > shown.length) {
    const p = document.createElement("p");
    p.className = "placeholder";
    p.textContent = `仅显示前 ${shown.length} 条消息（共 ${messages.length} 条）。`;
    wrap.append(p);
  }
  const existing = el.detail.querySelector(".messages");
  if (existing) existing.remove();
  el.detail.append(wrap);
}

function renderTrajectory(data) {
  const panes = el.detail._panes;
  const host = (panes && panes["轨迹"]) || el.detail;
  const old = host.querySelector(".trajectory");
  if (old) old.remove();
  const ph = host.querySelector(".placeholder");
  if (ph) ph.remove();
  const wrap = document.createElement("div");
  wrap.className = "trajectory";
  if (/antigravity/i.test(data.source || "")
      && (data.events || []).some((e) => (e.text || "").includes("<undecoded"))) {
    const note = document.createElement("div");
    note.className = "degraded-note";
    note.textContent = "部分步骤未能解开（无公开 schema 的 protobuf 新字段），显示为占位；其余步骤为真实文本。";
    wrap.append(note);
  }
  const h3 = document.createElement("h3");
  h3.textContent = `轨迹 · ${data.source} · ${data.count} 步 · ${data.file || ""}`;
  wrap.append(h3);
  if (data.mapped_to) {
    const note = document.createElement("div");
    note.className = "degraded-note";
    note.textContent = `经目录映射找到本地文件（Zed 会话 ${data.mapped_from} → 源文件 ${data.mapped_to}）。`;
    wrap.append(note);
  }
  if (data.error) {
    const note = document.createElement("div");
    note.className = "degraded-note";
    note.textContent = `未能加载轨迹：${data.error}`;
    wrap.append(note);
  }
  for (const e of data.events || []) {
    const cls = e.role === "tool_call" ? "tool" : e.role === "tool_result" ? "result"
      : e.role === "thinking" ? "think" : e.role === "user" ? "user" : "sys";
    const box = document.createElement("details");
    box.className = `tstep ${cls}`;
    if (e.role === "tool_result" || e.role === "assistant") box.open = true;
    const sum = document.createElement("summary");
    sum.textContent = `${e.role}${e.name ? ` · ${e.name}` : ""}${e.ts ? ` · ${e.ts}` : ""} — ${(e.text || "").slice(0, 80)}`;
    box.append(sum);
    const md = document.createElement("div");
    md.className = "md";
    try {
      md.innerHTML = mdToHtml(e.text || "(空)");
    } catch (_) {
      const pre = document.createElement("pre");
      pre.textContent = e.text || "(空)";
      md.append(pre);
    }
    box.append(md);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "action";
    copy.textContent = "复制";
    copy.addEventListener("click", () => navigator.clipboard.writeText(e.text || ""));
    box.append(copy);
    wrap.append(box);
  }
  host.append(wrap);
  const tabs = el.detail.querySelectorAll(".tab");
  if (tabs.length === 2) tabs[1].click();
}

/* -- 事件绑定 ---------------------------------------------------------------- */

let timer = null;
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(() => runSearch().catch(() => {}), 250);
}

el.q.addEventListener("input", schedule);
el.form.addEventListener("submit", (e) => {
  e.preventDefault();
  runSearch().catch(() => {});
});
el.agent.addEventListener("change", () => {
  const selectedOpt = el.agent.selectedOptions[0];
  const m = selectedOpt && selectedOpt.dataset.management;
  if (m === "external" && el.scope.value === "zed") {
    el.scope.value = "external";
  } else if (m === "zed" && el.scope.value === "external") {
    el.scope.value = "zed";
  }
  runSearch().catch(() => {});
});

el.scope.addEventListener("change", () => {
  const selectedOpt = el.agent.selectedOptions[0];
  const m = selectedOpt && selectedOpt.dataset.management;
  if (el.scope.value === "zed" && m === "external") {
    el.agent.value = "";
  } else if (el.scope.value === "external" && m === "zed") {
    el.agent.value = "";
  }
  runSearch().catch(() => {});
});

for (const node of [el.project, el.archived, el.since, el.until]) {
  node.addEventListener("change", () => runSearch().catch(() => {}));
}
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (e.key === "/" && !typing) {
    e.preventDefault();
    el.q.focus();
    el.q.select();
  } else if (e.key === "Escape" && document.activeElement === el.q) {
    el.q.value = "";
    runSearch().catch(() => {});
  }
});

/* -- 启动 -------------------------------------------------------------------- */

loadOptions()
  .then(() => {
    restoreFromUrl();
    return runSearch();
  })
  .catch((e) => {
    setStatus("初始化失败");
    showNotice(e.message);
  });
