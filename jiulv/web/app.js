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
  stats: null,
  taxonomy: null,
  hooks: [],
  hookType: "",
  readerUid: null,
  lastPack: null,
  graphFocus: "",
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
    // 没有关键词、但点了侧栏标签时要直接列出这批文档。
    // 这里以前无条件回首页，于是点标签只有变色，列表不动。
    if (hasFilters()) {
      await runBrowse();
      return;
    }
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

/* -------------------------------------------------------------- 浏览模式 */

// 只按标签浏览：不带关键词，直接列符合条件的文档，可继续翻页。
const BROWSE_PAGE = 30;

function hasFilters() {
  return Object.values(state.filters).some((value) => value);
}

function filterLabels() {
  const out = [];
  for (const [key, value] of Object.entries(state.filters)) {
    if (!value) continue;
    out.push(key === "doc_type" ? TYPE_LABEL[value] || value : value);
  }
  return out;
}

async function runBrowse() {
  setView("search");
  window.scrollTo(0, 0);
  await loadBrowsePage(0, false);
}

async function loadBrowsePage(offset, append) {
  const view = $("#view");
  const params = new URLSearchParams({
    limit: String(BROWSE_PAGE),
    offset: String(offset),
    // 按等级排而不是按最近索引：索引时间排序会把杂项文件顶到最前面，
    // 点开「云贵川 452」看到的全是 sleep 日志之类的东西，等于没用。
    order: "grade",
  });
  for (const [key, value] of Object.entries(state.filters)) if (value) params.set(key, value);

  if (!append) {
    view.innerHTML = "";
    const head = el("div", "view-head");
    head.appendChild(el("h2", null, filterLabels().join(" · ") || "全部文档"));
    head.appendChild(el("span", "hint", "读取中…"));
    view.appendChild(head);
  }

  let data;
  try {
    data = await api("/api/v1/docs?" + params.toString());
  } catch (error) {
    if (!append) view.innerHTML = `<div class="empty"><strong>读取失败</strong>${escapeHtml(error.message)}</div>`;
    else toast("加载失败：" + error.message);
    return;
  }

  if (!append) {
    view.innerHTML = "";
    const head = el("div", "view-head");
    head.appendChild(el("h2", null, filterLabels().join(" · ") || "全部文档"));
    // 数字是去重后的：侧栏那个数含各来源的副本，这里只算不同内容
    head.appendChild(el("span", "hint", `${data.total} 篇不同内容 · 按等级排`));
    view.appendChild(head);
  }

  const stale = view.querySelector(".more-row");
  if (stale) stale.remove();

  if (!data.items.length) {
    if (!append) {
      const empty = el("div", "empty");
      empty.innerHTML =
        "<strong>这个组合下没有文档</strong>去掉一两个标签再看，或者直接搜关键词。";
      view.appendChild(empty);
    }
    return;
  }

  for (const doc of data.items) view.appendChild(resultCard({ doc, duplicates: doc.duplicates }));

  const shown = offset + data.items.length;
  const rest = data.total - shown;
  if (rest > 0) {
    const row = el("div", "more-row");
    const btn = el("button", "chip", `继续加载（还有 ${rest} 篇）`);
    btn.onclick = () => {
      btn.disabled = true;
      btn.textContent = "读取中…";
      loadBrowsePage(shown, true);
    };
    row.appendChild(btn);
    view.appendChild(row);
  } else if (append) {
    const done = el("div", "more-row");
    done.appendChild(el("span", "meta", `已显示全部 ${data.total} 篇`));
    view.appendChild(done);
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
    // 收录条数会虚高一倍（同一篇被几个来源各收一份）；并列给出不同内容数
    const unique = state.stats?.unique_docs || 0;
    const collected = state.stats?.docs || 0;
    stats.innerHTML = `<span><b>${unique}</b> 篇不同内容</span><span><b>${
      collected
    }</b> 条收录</span><span><b>${(state.facets.regions || []).length}</b> 个地区</span><span><b>${
      (state.facets.hotels || []).length
    }</b> 家酒店</span>`;
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
    renderNotes($("#reader-notes"), doc.annotations || []);
    renderReaderSide($("#reader-related"), doc);
  } catch (error) {
    $("#reader-body").innerHTML = `<div class="empty"><strong>打不开</strong>${escapeHtml(error.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ 注解 */

const NOTE_TONE = {
  takeaway: "key",
  review: "key",
  con: "warn",
  booking: "info",
  pro: "info",
  voice: "mute",
  structure: "mute",
  core: "mute",
  visual: "mute",
  note: "mute",
  signal: "derived",
  title: "derived",
};

const NOTE_OPEN = 3;

function noteCard(note) {
  const tone = NOTE_TONE[note.kind] || "mute";
  const card = el("div", "note-card tone-" + tone);
  const top = el("div", "note-top");
  top.appendChild(el("span", "note-kind", note.kind_label || note.label || "注解"));
  // 推算出来的要跟原文摘录分开，不然读者分不清哪句是资料里写的
  top.appendChild(
    el("span", "note-origin", note.origin === "derived" ? "规则推算" : "原文摘录")
  );
  card.appendChild(top);
  card.appendChild(el("p", "note-text", note.text));

  // 引文跟正文是同一句时就别重复占一行了
  const quote = (note.evidence || "").replace(/^原文引用：/, "").trim();
  if (quote && !note.text.includes(quote.slice(0, 12))) {
    card.appendChild(el("p", "note-evidence", "原文：" + quote));
  }
  return card;
}

function renderNotes(box, notes) {
  box.innerHTML = "";
  if (!notes.length) return;

  const head = el("div", "notes-head");
  head.appendChild(el("h4", null, "为什么这么写"));
  head.appendChild(el("span", "hint", `${notes.length} 条注解`));
  box.appendChild(head);

  const list = el("div", "notes-list");
  const shown = notes.slice(0, NOTE_OPEN);
  shown.forEach((note) => list.appendChild(noteCard(note)));
  box.appendChild(list);

  if (notes.length > NOTE_OPEN) {
    const rest = el("div", "notes-list");
    rest.hidden = true;
    notes.slice(NOTE_OPEN).forEach((note) => rest.appendChild(noteCard(note)));
    box.appendChild(rest);

    const more = el("button", "ghost notes-more", `展开其余 ${notes.length - NOTE_OPEN} 条`);
    more.onclick = () => {
      const open = rest.hidden;
      rest.hidden = !open;
      more.textContent = open ? "收起" : `展开其余 ${notes.length - NOTE_OPEN} 条`;
    };
    box.appendChild(more);
  }
}

function renderReaderSide(box, doc) {
  box.innerHTML = "";

  const concepts = doc.concepts || [];
  if (concepts.length) {
    box.appendChild(el("h4", null, "这篇连到"));
    const chips = el("div", "concept-chips");
    for (const concept of concepts) {
      const chip = el("button", "concept-chip kind-" + concept.kind);
      chip.appendChild(el("span", "chip-kind", concept.kind_label));
      chip.appendChild(el("span", "chip-label", concept.label));
      chip.onclick = () => goGraph(concept.ref || concept.kind + ":" + concept.key);
      chips.appendChild(chip);
    }
    box.appendChild(chips);
  }

  const backlinks = doc.backlinks || [];
  if (backlinks.length) {
    box.appendChild(el("h4", null, "反向链接 · 谁还讲过同一个东西"));
    for (const item of backlinks) {
      const row = el("a", "backlink");
      row.href = "#";
      const name = el("span", "backlink-title", item.title || "（无标题）");
      row.appendChild(name);
      row.appendChild(
        el("span", "backlink-via", `共 ${item.shared} 个概念 · ${(item.via || []).join("、")}`)
      );
      row.onclick = (event) => {
        event.preventDefault();
        openDoc(item.uid);
      };
      box.appendChild(row);
    }
  }

  if ((doc.related || []).length) {
    box.appendChild(el("h4", null, "同酒店 / 同地区"));
    for (const item of doc.related) {
      const link = el("a", null, item.title);
      link.href = "#";
      link.onclick = (event) => {
        event.preventDefault();
        openDoc(item.uid);
      };
      box.appendChild(link);
    }
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

/* ------------------------------------------------------------------ 来源 */

// 资料从哪来：两个 agent 的资料库各自连在哪、各多少篇、上次什么时候同步。
// 这一页存在的意义是让「库」不是一个黑盒——看不到来源，就不知道答案的出处。

const GROUP_LABEL = {
  workbuddy: "WorkBuddy",
  openclaw: "OpenCLaw",
  inbox: "回写",
  default: "自定义",
};

function sourceRow(item, tag) {
  const row = el("div", "src-row" + (item.exists ? "" : " missing"));
  const name = el("span", "src-name");
  if (tag) name.appendChild(el("em", "src-tag", tag));
  name.appendChild(document.createTextNode(item.name));
  row.appendChild(name);
  row.appendChild(el("span", "src-count", item.exists ? fmtNum(item.doc_count) || "0" : "断开"));
  row.appendChild(el("code", "src-path", item.path));
  return row;
}

async function renderSources() {
  setView("sources");
  const view = $("#view");
  view.innerHTML = '<p class="loading">载入中…</p>';

  let data;
  let pool;
  try {
    [data, pool] = await Promise.all([
      api("/api/v1/sources"),
      api("/api/v1/inbox?limit=8").catch(() => ({ items: [] })),
    ]);
  } catch (error) {
    view.innerHTML = `<div class="empty"><strong>读不到来源</strong>${escapeHtml(error.message)}</div>`;
    return;
  }

  view.innerHTML = "";
  const head = el("div", "view-head");
  const left = el("div");
  left.appendChild(el("h2", null, "资料从哪来"));
  const when = (data.last_sync || "").slice(0, 16).replace("T", " ");
  left.appendChild(
    el("p", "meta", `${data.items.length} 个来源 · ${fmtNum(data.total_docs)} 篇 · 最近同步 ${when || "还没同步过"}`)
  );
  head.appendChild(left);

  const btn = el("button", "ghost", "同步一次");
  btn.onclick = async () => {
    btn.disabled = true;
    btn.textContent = "同步中…";
    try {
      const result = await api("/api/v1/sync", {
        method: "POST",
        body: JSON.stringify({}),
      });
      toast(
        result.skipped
          ? "资料库没有变化"
          : `已同步 +${result.report.added} 篇 / ~${result.report.updated} 篇`
      );
      renderSources();
    } catch (error) {
      toast("同步失败：" + error.message);
      btn.disabled = false;
      btn.textContent = "同步一次";
    }
  };
  head.appendChild(btn);
  view.appendChild(head);

  const groups = { workbuddy: [], openclaw: [], inbox: [], other: [] };
  for (const item of data.items) {
    (groups[item.group] || groups.other).push(item);
  }
  for (const key of ["workbuddy", "openclaw", "inbox", "other"]) {
    const items = groups[key];
    if (!items.length) continue;
    const section = el("div", "bars");
    const title = el("h3", null, GROUP_LABEL[key] || "自定义");
    title.style.cssText =
      "font-size:12px;letter-spacing:.1em;color:var(--ink-3);font-weight:500;margin:0 0 8px";
    section.appendChild(title);
    const list = el("div", "src-list");
    for (const item of items) list.appendChild(sourceRow(item));
    section.appendChild(list);
    view.appendChild(section);
  }

  if ((pool.items || []).length) {
    const section = el("div", "bars");
    section.style.marginTop = "26px";
    const title = el("h3", null, "回写池 · 最近 8 条");
    title.style.cssText =
      "font-size:12px;letter-spacing:.1em;color:var(--ink-3);font-weight:500;margin:0 0 8px";
    section.appendChild(title);
    const list = el("div", "src-list");
    for (const item of pool.items) {
      list.appendChild(sourceRow({ name: item.title, path: item.path, exists: true, doc_count: 0 }, item.kind || "note"));
    }
    section.appendChild(list);
    view.appendChild(section);
  } else {
    view.appendChild(
      el("p", "meta", "回写池还是空的：agent 写完后 POST /api/v1/inbox，或调 MCP 的 kb_save。")
    );
  }
}

/* ------------------------------------------------------------------ 图谱 */

// 概念类型 → 颜色。用的是纸感的低饱和色，跟正文不打架。
const GRAPH_KINDS = {
  region: { color: "#2f5d50", label: "地区" },
  hotel: { color: "#9a5232", label: "酒店" },
  pain: { color: "#8a7420", label: "痛点" },
  audience: { color: "#3f6a8a", label: "人群" },
  technique: { color: "#6a5a8f", label: "手法" },
  series: { color: "#2f7a6a", label: "系列" },
  author: { color: "#6f6a5e", label: "作者" },
  hook: { color: "#9a5252", label: "钩子" },
  doc: { color: "#9c958a", label: "笔记" },
};

const G = {
  nodes: [],
  edges: [],
  focus: null,
  view: { scale: 1, x: 0, y: 0 },
  canvas: null,
  ctx: null,
  drag: null,
  hover: null,
  moved: false,
};

function goGraph(ref) {
  state.graphFocus = ref || "";
  if (state.view === "graph") renderGraph();
  else location.hash = "#/graph";
}

function nodeRadius(node) {
  if (node.kind === "doc") return 5.5;
  return 4.5 + Math.min(8.5, Math.log2(1 + (node.size || 1)) * 1.35);
}

function layoutGraph(nodes, edges, width, height) {
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const links = [];
  for (const edge of edges) {
    const a = index.get(edge.source);
    const b = index.get(edge.target);
    if (a != null && b != null && a !== b) links.push([a, b, Math.max(0.2, Math.min(1, (edge.weight || 1) / 6))]);
  }

  nodes.forEach((node, i) => {
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    const radius = Math.min(width, height) * 0.34 * (0.55 + ((i * 37) % 100) / 180);
    node.x = width / 2 + Math.cos(angle) * radius;
    node.y = height / 2 + Math.sin(angle) * radius;
    node.vx = 0;
    node.vy = 0;
    node.r = nodeRadius(node);
  });

  // 节点少的时候（聚焦视图）力要收着点，否则十来个点会被推得铺满整屏、拉出长线
  const compact = nodes.length <= 24;
  const repel = compact ? 1500 : 3400;
  const steps = nodes.length > 45 ? 320 : 420;
  for (let step = 0; step < steps; step += 1) {
    const alpha = 1 - step / steps;

    // 斥力：节点互相推开，避免标签压在一起
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        // 距离下限卡在两者半径之和：不卡的话两个点贴近时斥力炸到无穷，
        // 整张图会被甩成一圈孤点
        const d = Math.max(Math.hypot(dx, dy), a.r + b.r + 6);
        const force = (repel * alpha) / (d * d);
        const fx = (dx / d) * force;
        const fy = (dy / d) * force;
        a.vx -= fx;
        a.vy -= fy;
        b.vx += fx;
        b.vy += fy;
      }
    }

    // 弹簧：有边的往标准距离上拉
    for (const [a, b, strength] of links) {
      const na = nodes[a];
      const nb = nodes[b];
      const dx = nb.x - na.x;
      const dy = nb.y - na.y;
      const d = Math.hypot(dx, dy) || 1;
      const target = (compact ? 78 : 108) - 22 * strength;
      const force = (d - target) * (compact ? 0.02 : 0.012) * alpha * (0.5 + strength);
      const fx = (dx / d) * force;
      const fy = (dy / d) * force;
      na.vx += fx;
      na.vy += fy;
      nb.vx -= fx;
      nb.vy -= fy;
    }

    for (const node of nodes) {
      node.vx += (width / 2 - node.x) * 0.0026 * alpha;
      node.vy += (height / 2 - node.y) * 0.0026 * alpha;
      node.vx *= 0.84;
      node.vy *= 0.84;
      node.x += node.vx;
      node.y += node.vy;
      node.x = Math.max(node.r + 8, Math.min(width - node.r - 8, node.x));
      node.y = Math.max(node.r + 8, Math.min(height - node.r - 8, node.y));
    }
  }

  // 收尾：把整张图等比缩放进画布，别缩在中间一小块
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = 56;
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  const k = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY, 1.6);
  const offsetX = (width - spanX * k) / 2;
  const offsetY = (height - spanY * k) / 2;
  for (const node of nodes) {
    node.x = (node.x - minX) * k + offsetX;
    node.y = (node.y - minY) * k + offsetY;
  }

  // 标签只给度数高的常显，其余悬停才出，否则一片糊
  const ranked = [...nodes].sort((a, b) => (b.size || 0) - (a.size || 0));
  const show = new Set(ranked.slice(0, 30).map((n) => n.id));
  for (const node of nodes) node.showLabel = node.kind !== "doc" && show.has(node.id);
}

function drawGraph() {
  const { ctx, canvas, nodes, edges, view, hover } = G;
  if (!ctx || !canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.width / dpr;
  const height = canvas.height / dpr;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const pos = (node) => ({ x: node.x * view.scale + view.x, y: node.y * view.scale + view.y });
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // 边
  for (const edge of edges) {
    const a = byId.get(edge.source);
    const b = byId.get(edge.target);
    if (!a || !b) continue;
    const pa = pos(a);
    const pb = pos(b);
    const strong = (edge.weight || 1) >= 3;
    const lit = hover && (hover.id === a.id || hover.id === b.id);
    ctx.strokeStyle = lit
      ? "rgba(47,93,80,.6)"
      : strong
      ? "rgba(126,119,106,.34)"
      : "rgba(126,119,106,.17)";
    ctx.lineWidth = lit ? 1.7 : strong ? 1.05 : 0.75;
    ctx.beginPath();
    ctx.moveTo(pa.x, pa.y);
    ctx.lineTo(pb.x, pb.y);
    ctx.stroke();
  }

  // 节点
  for (const node of nodes) {
    const p = pos(node);
    const color = (GRAPH_KINDS[node.kind] || GRAPH_KINDS.doc).color;
    const r = node.r * view.scale;
    const active = hover && hover.id === node.id;
    ctx.beginPath();
    ctx.arc(p.x, p.y, Math.max(2.5, r), 0, Math.PI * 2);
    ctx.fillStyle = node.kind === "doc" ? "rgba(156,149,138,.55)" : color;
    ctx.globalAlpha = active ? 1 : node.kind === "doc" ? 0.75 : 0.92;
    ctx.fill();
    ctx.globalAlpha = 1;
    if (node.kind !== "doc") {
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = "rgba(255,253,249,.85)";
      ctx.stroke();
    }
    // 焦点节点加一圈，一眼知道现在站在哪儿
    if (G.focus && node.id === G.focus.id) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, Math.max(2.5, r) + 4.5, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.globalAlpha = 0.7;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  // 标签：概念常显，笔记只在不拥挤时显示。标签之间做避让，
  // 宁可少显示几个，也不要叠成一坨看不清
  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const labelBg = dark ? "rgba(22,22,17,.86)" : "rgba(255,253,249,.86)";
  const placed = [];
  ctx.textBaseline = "middle";
  // 节点少的（聚焦视图）全都给标签 —— 点进一家酒店就是想看有哪几篇
  const roomy = nodes.length <= 24;
  const ordered = [...nodes].sort((a, b) => (b.size || 0) - (a.size || 0));
  for (const node of ordered) {
    const p = pos(node);
    const r = Math.max(2.5, node.r * view.scale);
    const active = hover && hover.id === node.id;
    if (node.kind === "doc" && !active && !roomy && view.scale < 1.5) continue;
    if (!active && !node.showLabel && !roomy && view.scale < 1.5) continue;
    const limit = node.kind === "doc" ? 15 : 14;
    const text = node.label.length > limit ? node.label.slice(0, limit) + "…" : node.label;
    ctx.font = active
      ? "600 12.5px -apple-system, 'PingFang SC', sans-serif"
      : "11.5px -apple-system, 'PingFang SC', sans-serif";
    const w = ctx.measureText(text).width;
    const box = { x: p.x + r + 3, y: p.y - 8, w: w + 4, h: 16 };
    if (!active) {
      const clash = placed.some(
        (o) => box.x < o.x + o.w && box.x + box.w > o.x && box.y < o.y + o.h && box.y + box.h > o.y
      );
      if (clash) continue;
    }
    placed.push(box);
    ctx.fillStyle = labelBg;
    ctx.fillRect(box.x, box.y, box.w, box.h);
    ctx.fillStyle = node.kind === "doc" ? (dark ? "#b8b2a5" : "#6d675c") : GRAPH_KINDS[node.kind].color;
    ctx.fillText(text, box.x + 2, p.y);
  }
}

function graphHitTest(clientX, clientY) {
  const rect = G.canvas.getBoundingClientRect();
  const x = (clientX - rect.left - G.view.x) / G.view.scale;
  const y = (clientY - rect.top - G.view.y) / G.view.scale;
  let best = null;
  let bestDist = Infinity;
  for (const node of G.nodes) {
    const d = Math.hypot(node.x - x, node.y - y);
    if (d <= node.r + 6 && d < bestDist) {
      best = node;
      bestDist = d;
    }
  }
  return best;
}

function bindGraphEvents(canvas) {
  let panning = false;
  let last = null;

  canvas.addEventListener("mousedown", (event) => {
    const node = graphHitTest(event.clientX, event.clientY);
    G.moved = false;
    if (node) {
      G.drag = node;
    } else {
      panning = true;
      last = { x: event.clientX, y: event.clientY };
      canvas.style.cursor = "grabbing";
    }
  });

  window.addEventListener("mousemove", (event) => {
    if (G.drag) {
      const rect = canvas.getBoundingClientRect();
      G.drag.x = (event.clientX - rect.left - G.view.x) / G.view.scale;
      G.drag.y = (event.clientY - rect.top - G.view.y) / G.view.scale;
      G.moved = true;
      drawGraph();
      return;
    }
    if (panning && last) {
      G.view.x += event.clientX - last.x;
      G.view.y += event.clientY - last.y;
      last = { x: event.clientX, y: event.clientY };
      G.moved = true;
      drawGraph();
      return;
    }
    if (canvas.contains(event.target) || event.target === canvas) {
      const node = graphHitTest(event.clientX, event.clientY);
      const next = node ? node.id : null;
      if (next !== (G.hover && G.hover.id)) {
        G.hover = node;
        canvas.style.cursor = node ? "pointer" : "grab";
        drawGraph();
      }
    }
  });

  window.addEventListener("mouseup", (event) => {
    if (G.drag && !G.moved) {
      const node = G.drag;
      if (node.kind === "doc" && node.uid) openDoc(node.uid);
      else if (node.kind !== "doc") goGraph(node.id);
    }
    G.drag = null;
    panning = false;
    last = null;
    canvas.style.cursor = "grab";
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const factor = event.deltaY < 0 ? 1.12 : 0.89;
    const next = Math.max(0.45, Math.min(2.6, G.view.scale * factor));
    G.view.x = mx - (mx - G.view.x) * (next / G.view.scale);
    G.view.y = my - (my - G.view.y) * (next / G.view.scale);
    G.view.scale = next;
    drawGraph();
  }, { passive: false });

  canvas.addEventListener("mouseleave", () => {
    G.hover = null;
    drawGraph();
  });
}

async function renderGraph() {
  setView("graph");
  const view = $("#view");
  const focus = state.graphFocus || "";
  view.innerHTML = "";

  const head = el("div", "view-head");
  head.appendChild(el("h2", null, "知识图谱"));
  head.appendChild(el("span", "hint", "概念和笔记是同一种节点，点一下顺着走"));
  view.appendChild(head);

  const bar = el("div", "graph-bar");
  const legend = el("div", "graph-legend");
  for (const [kind, meta] of Object.entries(GRAPH_KINDS)) {
    const item = el("span", "legend-item");
    const dot = el("i");
    dot.style.background = meta.color;
    item.appendChild(dot);
    item.appendChild(document.createTextNode(meta.label));
    legend.appendChild(item);
  }
  bar.appendChild(legend);
  const actions = el("div", "graph-actions");
  if (focus) {
    const back = el("button", "ghost", "返回全景");
    back.onclick = () => goGraph("");
    actions.appendChild(back);
  }
  bar.appendChild(actions);
  view.appendChild(bar);

  const stage = el("div", "graph-stage");
  const canvas = document.createElement("canvas");
  canvas.className = "graph-canvas";
  stage.appendChild(canvas);
  const tip = el("div", "graph-tip");
  tip.hidden = true;
  stage.appendChild(tip);
  view.appendChild(stage);

  const dpr = window.devicePixelRatio || 1;
  const width = stage.clientWidth || 860;
  const height = Math.max(420, Math.min(660, window.innerHeight - 260));
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  canvas.style.width = width + "px";
  canvas.style.height = height + "px";

  G.canvas = canvas;
  G.ctx = canvas.getContext("2d");
  G.view = { scale: 1, x: 0, y: 0 };
  G.hover = null;

  try {
    const url = "/api/v1/graph?limit=" + (focus ? 30 : 26) + (focus ? "&focus=" + encodeURIComponent(focus) : "");
    const data = await api(url);
    G.nodes = data.nodes || [];
    G.edges = data.edges || [];
    G.focus = data.focus;

    if (data.focus) {
      const crumb = el("div", "graph-focus");
      crumb.appendChild(el("span", "crumb-kind", data.focus.kind_label));
      crumb.appendChild(el("strong", null, data.focus.label));
      if (data.focus.note) crumb.appendChild(el("span", "crumb-note", data.focus.note));
      crumb.appendChild(el("span", "crumb-count", `${data.node_count} 个节点 · ${data.edge_count} 条连接`));
      bar.insertBefore(crumb, legend);
    } else if (!G.nodes.length) {
      const empty = el("div", "empty");
      empty.innerHTML = "<strong>还没有图谱</strong>先运行 <code>jiulv ingest</code> 收录资料。";
      view.appendChild(empty);
      return;
    }

    layoutGraph(G.nodes, G.edges, width, height);
    drawGraph();
    bindGraphEvents(canvas);
  } catch (error) {
    view.appendChild(el("div", "empty", "图谱加载失败：" + error.message));
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
  else if (hash.startsWith("sources")) renderSources();
  else if (hash.startsWith("graph")) renderGraph();
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
    state.stats = stats;
    state.taxonomy = taxonomy;
    renderFacets();
    const unique = stats.unique_docs || stats.docs;
    $("#db-meta").textContent = `${unique} 篇不同内容 · 索引于 ${(stats.indexed_at || "")
      .slice(0, 16)
      .replace("T", " ")}`;
    $("#tagline").textContent = `${unique} 篇资料 · ${stats.hooks} 条钩子 · 可接 Agent`;
  } catch (error) {
    $("#view").innerHTML = `<div class="empty"><strong>连不上服务</strong>${escapeHtml(
      error.message
    )}<br /><br />先运行 <code>jiulv serve</code>，再确认资料已经 <code>jiulv ingest</code>。</div>`;
    return;
  }
  route();
}

boot();
