/* 酒旅爆款知识库 · 前端（零构建，原生 JS） */

const API = "";
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const TYPE_LABEL = {
  note: "爆款笔记",
  rule: "写作规则",
  hook: "钩子",
  guide: "选房攻略",
  fact: "硬资料",
  ops: "代运营",
  daily: "每日采集",
  index: "索引",
  misc: "其他",
};

const state = {
  view: "search",
  query: "",
  filters: { region: "", doc_type: "", grade: "", group: "" },
  results: null,
  facets: null,
  taxonomy: null,
  hooks: [],
  hookType: "",
  readerUid: null,
  lastPack: null,
};

/* ------------------------------------------------------------------ 基础 */

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (node.hidden = true), 2200);
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function fmtNum(n) {
  if (!n) return "";
  if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, "") + "万";
  return String(Math.round(n));
}

function metricLine(metrics) {
  if (!metrics) return "";
  const parts = [];
  if (metrics.likes) parts.push(fmtNum(metrics.likes) + "赞");
  if (metrics.saves) parts.push(fmtNum(metrics.saves) + "藏");
  if (metrics.comments) parts.push(fmtNum(metrics.comments) + "评");
  return parts.join(" / ");
}

function mark(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/«/g, "<mark>")
    .replace(/»/g, "</mark>");
}

/* ------------------------------------------------------------------ 极简 Markdown */

function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function renderMarkdown(md) {
  const lines = String(md || "").split("\n");
  const out = [];
  let list = null;
  let quote = null;

  const closeAll = () => {
    if (list) { out.push(`</${list}>`); list = null; }
    if (quote) { out.push("</blockquote>"); quote = null; }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) { closeAll(); continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeAll();
      const level = Math.min(heading[1].length + 1, 6);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { closeAll(); out.push("<hr />"); continue; }

    const bq = line.match(/^>\s?(.*)$/);
    if (bq) {
      if (list) { out.push(`</${list}>`); list = null; }
      if (!quote) { out.push("<blockquote>"); quote = true; }
      out.push(`<p>${inline(bq[1])}</p>`);
      continue;
    }
    if (quote) { out.push("</blockquote>"); quote = null; }

    if (/^\|/.test(line)) {
      const cells = line.split("|").slice(1, -1);
      if (cells.every((c) => /^[\s:-]+$/.test(c))) continue;
      closeAll();
      out.push(`<p class="table-row">${cells.map((c) => inline(c.trim())).join(" · ")}</p>`);
      continue;
    }

    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      if (list !== "ul") { closeAll(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) {
      if (list !== "ol") { closeAll(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    out.push(`<p>${inline(line)}</p>`);
  }
  closeAll();
  return out.join("\n");
}

/* ------------------------------------------------------------------ 筛选栏 */

function renderFacets() {
  const facets = state.facets;
  if (!facets) return;
  const map = [
    ["facet-region", "regions", "region"],
    ["facet-type", "doc_types", "doc_type"],
    ["facet-grade", "grades", "grade"],
    ["facet-group", "groups", "group"],
  ];
  for (const [target, key, filterKey] of map) {
    const box = document.getElementById(target);
    box.innerHTML = "";
    const items = (facets[key] || []).slice(0, key === "regions" ? 12 : 10);
    for (const item of items) {
      const label = key === "doc_types" ? TYPE_LABEL[item.value] || item.value : item.value;
      const btn = el("button", "chip");
      btn.innerHTML = `${escapeHtml(label)}<span class="n">${item.count}</span>`;
      if (state.filters[filterKey] === item.value) btn.classList.add("active");
      btn.onclick = () => {
        state.filters[filterKey] = state.filters[filterKey] === item.value ? "" : item.value;
        renderFacets();
        runSearch();
      };
      box.appendChild(btn);
    }
    if (!items.length) box.appendChild(el("span", "meta", "—"));
  }
}

/* ------------------------------------------------------------------ 检索 */

function resultCard(hit) {
  const doc = hit.doc;
  const card = el("div", "result");
  const top = el("div", "result-top");

  if (doc.grade) {
    const badge = el("span", "badge " + doc.grade.toLowerCase(), doc.grade + "级");
    top.appendChild(badge);
  }
  const type = el("span", "badge type", TYPE_LABEL[doc.doc_type] || doc.doc_type || "文档");
  top.appendChild(type);
  if (hit.duplicates) top.appendChild(el("span", "badge", `另有 ${hit.duplicates} 份副本`));

  const title = el("h3", null, doc.title || "（无标题）");
  top.appendChild(title);
  card.appendChild(top);

  if (hit.snippet) {
    const snippet = el("p", "snippet");
    snippet.innerHTML = mark(hit.snippet);
    card.appendChild(snippet);
  }

  const meta = el("p", "meta");
  const bits = [];
  if (doc.hotel) bits.push(doc.hotel);
  else if (doc.region) bits.push(doc.region);
  const metrics = metricLine(doc.metrics);
  if (metrics) bits.push(metrics);
  bits.push(doc.source);
  meta.innerHTML = bits.map(escapeHtml).join('<span class="dot"></span>');
  card.appendChild(meta);

  card.onclick = () => openDoc(doc.uid);
  return card;
}

async function runSearch() {
  setView("search");
  const view = $("#view");
  const query = state.query.trim();
  if (!query) {
    renderLanding(view);
    return;
  }
  view.innerHTML = "";
  const head = el("div", "view-head");
  head.appendChild(el("h2", null, "检索结果"));
  head.appendChild(el("span", "hint", "查询中…"));
  view.appendChild(head);

  const params = new URLSearchParams({ q: query, limit: "20" });
  for (const [key, value] of Object.entries(state.filters)) if (value) params.set(key, value);

  try {
    const data = await api("/api/v1/search?" + params.toString());
    state.results = data;
    view.innerHTML = "";
    const head2 = el("div", "view-head");
    head2.appendChild(el("h2", null, `「${query}」`));
    const modeLabel = { keyword: "关键词", hybrid: "混合", semantic: "语义" }[data.mode] || data.mode;
    head2.appendChild(el("span", "hint", `${data.total} 篇 · ${modeLabel}检索`));
    view.appendChild(head2);

    if (!data.hits.length) {
      const empty = el("div", "empty");
      empty.innerHTML =
        "<strong>没有命中</strong>换更短的词试试，比如只留酒店名或「三亚 亲子」。";
      view.appendChild(empty);
      return;
    }
    for (const hit of data.hits) view.appendChild(resultCard(hit));
  } catch (error) {
    view.innerHTML = `<div class="empty"><strong>检索失败</strong>${escapeHtml(error.message)}</div>`;
  }
}

function renderLanding(view) {
  view.innerHTML = "";
  const head = el("div", "view-head");
  head.appendChild(el("h2", null, "从一句话开始"));
  view.appendChild(head);

  const intro = el("p", "meta");
  intro.style.marginBottom = "18px";
  intro.textContent =
    "这里收着爆款笔记、写作规则、钩子和房型资料。检索用于查事实，组稿台用于出整篇。";
  view.appendChild(intro);

  const samples = [
    ["三亚 亲子 选房", "同地区亲子选房怎么写"],
    ["柏悦 黄金房号", "具体房号事实"],
    ["民宿 避雷", "踩坑结构"],
    ["一价全包 算账", "算账型钩子"],
    ["长白山 滑雪 住宿", "雪季选店"],
  ];
  const box = el("div", "chips");
  for (const [query, note] of samples) {
    const btn = el("button", "chip", query);
    btn.title = note;
    btn.onclick = () => {
      $("#q").value = query;
      state.query = query;
      runSearch();
    };
    box.appendChild(btn);
  }
  view.appendChild(box);

  if (state.facets) {
    const stats = el("div", "summary-row");
    stats.style.marginTop = "26px";
    const total = (state.facets.groups || []).reduce((sum, g) => sum + g.count, 0);
    stats.innerHTML = `<span>共 <b>${total}</b> 篇文档</span><span><b>${
      (state.facets.regions || []).length
    }</b> 个地区</span><span><b>${(state.facets.hotels || []).length}</b> 家酒店</span>`;
    view.appendChild(stats);
  }
}

/* ------------------------------------------------------------------ 阅读 */

async function openDoc(uid) {
  state.readerUid = uid;
  const reader = $("#reader");
  reader.hidden = false;
  $("#app").classList.add("with-reader");
  $("#reader-body").innerHTML = '<p class="loading">载入中…</p>';
  try {
    const doc = await api(`/api/v1/docs/${encodeURIComponent(uid)}`);
    $("#reader-grade").textContent = doc.grade ? doc.grade + "级" : TYPE_LABEL[doc.doc_type] || "";
    $("#reader-grade").className = "badge " + (doc.grade || "").toLowerCase();
    $("#reader-title").textContent = doc.title || "（无标题）";
    const meta = [];
    if (doc.hotel) meta.push(doc.hotel);
    if (doc.region) meta.push(doc.region);
    if (doc.author) meta.push(doc.author);
    const metrics = metricLine(doc.metrics);
    if (metrics) meta.push(metrics);
    meta.push(doc.path);
    $("#reader-meta").textContent = meta.join(" · ");

    $("#reader-body").innerHTML = renderMarkdown(doc.content || "");

    const related = $("#reader-related");
    related.innerHTML = "";
    if ((doc.related || []).length) {
      related.appendChild(el("h4", null, "同酒店 / 同地区"));
      for (const item of doc.related) {
        const link = el("a", null, item.title);
        link.href = "#";
        link.onclick = (event) => {
          event.preventDefault();
          openDoc(item.uid);
        };
        related.appendChild(link);
      }
    }
  } catch (error) {
    $("#reader-body").innerHTML = `<div class="empty"><strong>打不开</strong>${escapeHtml(error.message)}</div>`;
  }
}

function closeReader() {
  $("#reader").hidden = true;
  $("#app").classList.remove("with-reader");
  state.readerUid = null;
}

/* ------------------------------------------------------------------ 组稿台 */

function field(label, node) {
  const wrap = el("div", "field");
  wrap.appendChild(el("label", null, label));
  wrap.appendChild(node);
  return wrap;
}

function selectFrom(options, placeholder) {
  const select = document.createElement("select");
  const first = el("option", null, placeholder);
  first.value = "";
  select.appendChild(first);
  for (const [value, label] of options) {
    const option = el("option", null, label);
    option.value = value;
    select.appendChild(option);
  }
  return select;
}

function renderCompose() {
  setView("compose");
  const view = $("#view");
  view.innerHTML = "";

  const head = el("div", "view-head");
  head.appendChild(el("h2", null, "组稿台"));
  head.appendChild(el("span", "hint", "拼好素材再交给任意大模型写"));
  view.appendChild(head);

  const grid = el("div", "compose-grid");
  const card = el("div", "form-card");

  const hotel = document.createElement("input");
  hotel.placeholder = "例如 三亚仁恒皇冠";
  hotel.setAttribute("list", "hotel-list");
  const region = document.createElement("input");
  region.placeholder = "例如 海南";
  region.setAttribute("list", "region-list");

  const hotelList = document.createElement("datalist");
  hotelList.id = "hotel-list";
  for (const item of (state.facets?.hotels || [])) {
    const option = document.createElement("option");
    option.value = item.value;
    hotelList.appendChild(option);
  }

  const datalist = document.createElement("datalist");
  datalist.id = "region-list";
  for (const item of (state.facets?.regions || [])) {
    const option = document.createElement("option");
    option.value = item.value;
    datalist.appendChild(option);
  }

  const audience = selectFrom(
    Object.entries(state.taxonomy?.audience || {}).map(([code, item]) => [code, `${code} ${item.label}`]),
    "人群（可不填）"
  );
  const pain = selectFrom(
    Object.entries(state.taxonomy?.pain || {}).map(([code, item]) => [code, `${code} ${item.label}`]),
    "痛点（可不填）"
  );
  const hookType = selectFrom(
    Object.keys(state.taxonomy?.hook_types || {}).map((key) => [key, key]),
    "钩子类型（默认反常识）"
  );
  const goal = selectFrom(
    [["save", "收藏"], ["comment", "评论"], ["consult", "私信咨询"], ["share", "转发"]],
    "期望动作"
  );
  goal.value = "save";

  const angle = document.createElement("textarea");
  angle.placeholder = "切入角度，例如「写给第一次带娃去三亚的人」";
  const extra = document.createElement("textarea");
  extra.placeholder = "你手上另有、库里没有的事实，一行一条";

  card.appendChild(field("酒店", hotel));
  card.appendChild(field("地区", region));
  card.appendChild(hotelList);
  card.appendChild(datalist);
  card.appendChild(field("人群", audience));
  card.appendChild(field("痛点", pain));
  card.appendChild(field("钩子类型", hookType));
  card.appendChild(field("期望动作", goal));
  card.appendChild(field("切入角度", angle));
  card.appendChild(field("补充事实", extra));

  const submit = el("button", "primary", "生成上下文包");
  submit.onclick = async () => {
    submit.textContent = "生成中…";
    submit.disabled = true;
    try {
      const pack = await api("/api/v1/compose", {
        method: "POST",
        body: JSON.stringify({
          hotel: hotel.value,
          region: region.value,
          persona: audience.value,
          pain: pain.value,
          hook_type: hookType.value,
          goal: goal.value,
          angle: angle.value,
          extra_facts: extra.value.split("\n").map((s) => s.trim()).filter(Boolean),
        }),
      });
      state.lastPack = pack;
      renderPack(pack);
    } catch (error) {
      toast("生成失败：" + error.message);
    } finally {
      submit.textContent = "生成上下文包";
      submit.disabled = false;
    }
  };
  card.appendChild(submit);
  grid.appendChild(card);

  const packBox = el("div");
  packBox.id = "pack-box";
  grid.appendChild(packBox);
  view.appendChild(grid);

  if (state.lastPack) renderPack(state.lastPack);
  else {
    packBox.innerHTML =
      '<div class="pack"><div class="pack-head"><h3>上下文包</h3></div><pre>填左边几项，点「生成上下文包」。\n\n出来的是一段完整提示词：任务、硬规则、风格规则、钩子、同地区爆款结构、可核对事实、输出要求。\n\n粘贴给 DeepSeek、Claude、GPT 或任何一个 Agent 都能直接写，不用它再猜你的标准。</pre></div>';
  }
}

function renderPack(pack) {
  const box = $("#pack-box");
  if (!box) return;
  box.innerHTML = "";

  for (const warning of pack.warnings || []) {
    const notice = el("div", "notice", warning);
    box.appendChild(notice);
  }

  const cover = el("div", "summary-row");
  cover.innerHTML = `<span>参考 <b>${pack.coverage.references}</b></span><span>规则 <b>${pack.coverage.rules}</b></span><span>钩子 <b>${pack.coverage.hooks}</b></span><span>事实 <b>${pack.coverage.facts}</b></span>`;
  box.appendChild(cover);

  const pane = el("div", "pack");
  const head = el("div", "pack-head");
  head.appendChild(el("h3", null, "可直接使用的提示词"));
  const copy = el("button", "ghost", "复制");
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(pack.prompt);
      toast("已复制");
    } catch (_) {
      const range = document.createRange();
      const pre = box.querySelector("pre");
      range.selectNodeContents(pre);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      toast("已选中，按 Cmd+C 复制");
    }
  };
  head.appendChild(copy);
  pane.appendChild(head);
  const pre = el("pre", null, pack.prompt);
  pane.appendChild(pre);
  box.appendChild(pane);

  if ((pack.references || []).length) {
    const refs = el("div");
    refs.style.marginTop = "14px";
    refs.appendChild(el("h3", null, "本次用到的资料"));
    refs.querySelector("h3").style.cssText = "font-size:12px;letter-spacing:.1em;color:var(--ink-3);font-weight:500;margin:14px 0 8px";
    for (const ref of pack.references) {
      const card = el("div", "hook-card");
      const top = el("div", "hook-head");
      if (ref.grade) top.appendChild(el("span", "badge " + ref.grade.toLowerCase(), ref.grade + "级"));
      top.appendChild(el("span", "badge", ref.reason || ""));
      const title = el("a", null, ref.title);
      title.href = "#";
      title.style.cssText = "font-size:14px;color:var(--ink);text-decoration:none";
      title.onclick = (event) => {
        event.preventDefault();
        openDoc(ref.uid);
      };
      top.appendChild(title);
      card.appendChild(top);
      if (ref.snippet) card.appendChild(el("p", "mech", ref.snippet));
      refs.appendChild(card);
    }
    box.appendChild(refs);
  }
}

/* ------------------------------------------------------------------ 钩子库 */

async function renderHooks() {
  setView("hooks");
  const view = $("#view");
  view.innerHTML = "";

  const head = el("div", "view-head");
  head.appendChild(el("h2", null, "钩子库"));
  view.appendChild(head);

  const chips = el("div", "chips");
  chips.style.marginBottom = "16px";
  const types = ["", ...Object.keys(state.taxonomy?.hook_types || {})];
  for (const type of types) {
    const btn = el("button", "chip" + (state.hookType === type ? " active" : ""), type || "全部");
    btn.onclick = () => {
      state.hookType = type;
      renderHooks();
    };
    chips.appendChild(btn);
  }
  view.appendChild(chips);

  const list = el("div");
  view.appendChild(list);
  list.innerHTML = '<p class="loading">载入中…</p>';
  try {
    const params = new URLSearchParams({ limit: "60" });
    if (state.hookType) params.set("hook_type", state.hookType);
    const data = await api("/api/v1/hooks?" + params.toString());
    list.innerHTML = "";
    if (!data.items.length) {
      list.innerHTML = '<div class="empty"><strong>还没有钩子</strong>钩子库目录下按七字段写条目，收录后自动出现在这里。</div>';
      return;
    }
    for (const hook of data.items) {
      const card = el("div", "hook-card");
      const head2 = el("div", "hook-head");
      head2.appendChild(el("span", "hook-code", hook.code));
      if (hook.hook_type) head2.appendChild(el("span", "badge type", hook.hook_type));
      if (hook.audience) head2.appendChild(el("span", "badge", hook.audience));
      if (hook.pain) head2.appendChild(el("span", "badge", hook.pain));
      const status = hook.status || "";
      if (status) head2.appendChild(el("span", "badge", status));
      card.appendChild(head2);
      card.appendChild(el("p", "quote", hook.text));
      if (hook.mechanism) {
        const mech = el("p", "mech");
        mech.innerHTML = "<b>机制</b> " + escapeHtml(hook.mechanism);
        card.appendChild(mech);
      }
      list.appendChild(card);
    }
  } catch (error) {
    list.innerHTML = `<div class="empty"><strong>载入失败</strong>${escapeHtml(error.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ 数据 */

async function renderStats() {
  setView("stats");
  const view = $("#view");
  view.innerHTML = '<p class="loading">载入中…</p>';
  try {
    const data = await api("/api/v1/stats");
    view.innerHTML = "";

    const head = el("div", "view-head");
    head.appendChild(el("h2", null, "库的现状"));
    view.appendChild(head);

    const grid = el("div", "stat-grid");
    const stats = [
      ["文档", data.docs],
      ["段落", data.chunks],
      ["钩子", data.hooks],
      ["组稿", data.composes],
      ["回填", data.outcomes],
      ["总字数", data.chars],
    ];
    for (const [key, value] of stats) {
      const box = el("div", "stat");
      box.appendChild(el("div", "k", key));
      box.appendChild(el("div", "v", fmtNum(value) || "0"));
      grid.appendChild(box);
    }
    view.appendChild(grid);

    const byType = el("div", "bars");
    byType.appendChild(el("h3", null, "类型分布"));
    byType.querySelector("h3").style.cssText = "font-size:12px;letter-spacing:.1em;color:var(--ink-3);font-weight:500;margin:0 0 12px";
    const entries = Object.entries(data.by_type || {}).sort((a, b) => b[1] - a[1]);
    const max = Math.max(...entries.map((entry) => entry[1]), 1);
    for (const [key, value] of entries) {
      const row = el("div", "bar-row");
      row.appendChild(el("span", null, TYPE_LABEL[key] || key));
      const bar = el("div", "bar");
      const fill = el("i");
      fill.style.width = (value / max) * 100 + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("span", "num", String(value)));
      byType.appendChild(row);
    }
    view.appendChild(byType);

    if ((data.by_source || []).length) {
      const src = el("div", "bars");
      src.style.marginTop = "14px";
      const title = el("h3", null, "来源");
      title.style.cssText = "font-size:12px;letter-spacing:.1em;color:var(--ink-3);font-weight:500;margin:0 0 10px";
      src.appendChild(title);
      for (const item of data.by_source) {
        const row = el("div", "bar-row");
        row.appendChild(el("span", null, item.source));
        const bar = el("div", "bar");
        const fill = el("i");
        fill.style.width = (item.count / Math.max(data.docs, 1)) * 100 + "%";
        bar.appendChild(fill);
        row.appendChild(bar);
        row.appendChild(el("span", "num", String(item.count)));
        src.appendChild(row);
      }
      view.appendChild(src);
    }
  } catch (error) {
    view.innerHTML = `<div class="empty"><strong>载入失败</strong>${escapeHtml(error.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ 路由 */

function setView(view) {
  if (state.view !== view) state.view = view;
  for (const tab of document.querySelectorAll("#tabs a")) {
    tab.classList.toggle("active", tab.dataset.view === view);
  }
  // 筛选项只服务检索；组稿台和数据页把空间让出来
  const filters = document.getElementById("filters");
  if (filters) filters.style.display = view === "search" ? "" : "none";
  document.getElementById("app").classList.toggle("wide", view !== "search");
  if (view !== "search") closeReader();
}

function route() {
  const hash = (location.hash || "#/search").replace("#/", "");
  if (hash.startsWith("compose")) renderCompose();
  else if (hash.startsWith("hooks")) renderHooks();
  else if (hash.startsWith("stats")) renderStats();
  else runSearch();
}

async function boot() {
  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.query = $("#q").value;
    location.hash = "#/search";
    if (state.view === "search") runSearch();
  });
  $("#reader-close").onclick = closeReader;
  $("#clear-filters").onclick = () => {
    state.filters = { region: "", doc_type: "", grade: "", group: "" };
    renderFacets();
    runSearch();
  };
  window.addEventListener("hashchange", route);

  try {
    const [stats, taxonomy] = await Promise.all([api("/api/v1/stats"), api("/api/v1/taxonomy")]);
    state.facets = stats.facets;
    state.taxonomy = taxonomy;
    renderFacets();
    $("#db-meta").textContent = `${stats.docs} 篇 · 索引于 ${(stats.indexed_at || "").slice(0, 16).replace("T", " ")}`;
    $("#tagline").textContent = `${stats.docs} 篇资料 · ${stats.hooks} 条钩子 · 可接 Agent`;
  } catch (error) {
    $("#view").innerHTML = `<div class="empty"><strong>连不上服务</strong>${escapeHtml(
      error.message
    )}<br /><br />先运行 <code>jiulv serve</code>，再确认资料已经 <code>jiulv ingest</code>。</div>`;
    return;
  }
  route();
}

boot();
