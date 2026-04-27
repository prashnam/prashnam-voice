// In-app docs viewer.
//
// Loads the curated doc index from the server, lets the user pick one,
// fetches the raw markdown, renders it with a small homegrown converter
// (deliberately not pulling marked / showdown — we have a known set of
// docs and only a small markdown subset to support).
//
// Routing: hash is `#<doc-id>`, e.g. `#rest-api.md`. Default = first doc.

const $ = (sel, root = document) => root.querySelector(sel);

const state = { docs: [], current: null };

async function loadIndex() {
  const r = await fetch("/api/docs");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function loadDoc(id) {
  const r = await fetch("/api/docs/" + encodeURI(id));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
}

function renderRail() {
  const ol = $("#docs-list");
  ol.innerHTML = "";
  for (const d of state.docs) {
    const li = document.createElement("li");
    const href = "#" + encodeURIComponent(d.id);
    const a = document.createElement("a");
    a.href = href;
    a.innerHTML = `<strong>${escapeHtml(d.title)}</strong><span class="summary">${escapeHtml(d.summary || "")}</span>`;
    if (d.id === state.current) a.classList.add("current");
    li.appendChild(a);
    ol.appendChild(li);
  }
}

async function renderDoc(id) {
  const out = $("#doc-content");
  if (!id) { out.innerHTML = '<p class="muted">Pick a doc on the left.</p>'; return; }
  out.innerHTML = `<p class="muted">Loading ${escapeHtml(id)}…</p>`;
  let md;
  try { md = await loadDoc(id); }
  catch (e) {
    out.innerHTML = `<p class="error">Couldn't load ${escapeHtml(id)}: ${escapeHtml(e.message)}</p>`;
    return;
  }
  out.innerHTML = mdToHtml(md);
  // Anchor inline links so a click on `[REST](rest-api.md)` switches docs.
  out.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (/\.md$/.test(href) || /\.md#/.test(href)) {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const target = href.replace(/^.*\//, "").replace(/#.*$/, "");
        location.hash = "#" + encodeURIComponent(target);
      });
    }
  });
  // Scroll to top whenever we switch.
  out.scrollTop = 0;
}

// --------------------------------------------------------------------------
// Tiny markdown → HTML
//
// Subset:
//   #..###### headers, paragraphs, bold (**), italic (* / _), inline code (`),
//   fenced code blocks (```), unordered lists (- or *), ordered lists (1.),
//   blockquotes (>), horizontal rules (---), tables ( | | with header sep),
//   links [text](url), and HTML escapes.
//
// Not supported: nested lists, task lists, footnotes, images, autolinks,
// inline HTML. Our docs don't use those.
// --------------------------------------------------------------------------

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function inlineMd(s) {
  // Inline code first so we don't touch its contents.
  const placeholders = [];
  s = s.replace(/`([^`]+)`/g, (_, code) => {
    placeholders.push(`<code>${escapeHtml(code)}</code>`);
    return `${placeholders.length - 1}`;
  });
  s = escapeHtml(s);
  // Links — [text](url)
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
    (_, text, url) => `<a href="${url}">${text}</a>`);
  // Bold (** / __)
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  // Italic (* / _) — guarded so it doesn't eat list bullets.
  s = s.replace(/(^|[^\w*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^\w_])_([^_\n]+)_/g, "$1<em>$2</em>");
  // Restore inline code placeholders.
  s = s.replace(/(\d+)/g, (_, i) => placeholders[+i]);
  return s;
}

function mdToHtml(md) {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  let listType = null;       // "ul" | "ol" | null
  let inBlockquote = false;
  let inTable = false;
  let tableRows = [];

  const closeList = () => {
    if (listType) { out.push(`</${listType}>`); listType = null; }
  };
  const closeBlockquote = () => {
    if (inBlockquote) { out.push("</blockquote>"); inBlockquote = false; }
  };
  const closeTable = () => {
    if (!inTable) return;
    if (tableRows.length === 0) { inTable = false; return; }
    const html = ["<table>"];
    const [header, ...rest] = tableRows;
    html.push("<thead><tr>");
    for (const cell of header) html.push(`<th>${inlineMd(cell)}</th>`);
    html.push("</tr></thead>");
    if (rest.length) {
      html.push("<tbody>");
      for (const row of rest) {
        html.push("<tr>");
        for (const cell of row) html.push(`<td>${inlineMd(cell)}</td>`);
        html.push("</tr>");
      }
      html.push("</tbody>");
    }
    html.push("</table>");
    out.push(html.join(""));
    tableRows = [];
    inTable = false;
  };
  const closeAll = () => { closeList(); closeBlockquote(); closeTable(); };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    const fence = line.match(/^```(.*)$/);
    if (fence) {
      closeAll();
      const lang = fence[1].trim();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      const cls = lang ? ` class="lang-${escapeHtml(lang)}"` : "";
      out.push(`<pre><code${cls}>${escapeHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }

    // Horizontal rule
    if (/^---+\s*$/.test(line) || /^\*\*\*+\s*$/.test(line)) {
      closeAll();
      out.push("<hr />");
      i++;
      continue;
    }

    // Header
    const h = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (h) {
      closeAll();
      const level = h[1].length;
      out.push(`<h${level}>${inlineMd(h[2])}</h${level}>`);
      i++;
      continue;
    }

    // Table — recognize a header row followed by a separator row.
    if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*[-:]+(\s*\|\s*[-:]+)+\s*\|?\s*$/.test(lines[i + 1])) {
      closeList();
      closeBlockquote();
      inTable = true;
      tableRows = [];
      tableRows.push(line.split("|").map((c) => c.trim()).filter((_, idx, arr) => idx > 0 && idx < arr.length - 1 || (idx > 0 && idx === arr.length - 1 && arr[idx]) || (idx === 0 && arr[idx])));
      // Cleaner cell-split: trim leading/trailing pipes then split.
      const splitRow = (s) => s.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      tableRows = [splitRow(line)];
      i += 2;     // skip header + separator
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") {
        tableRows.push(splitRow(lines[i]));
        i++;
      }
      closeTable();
      continue;
    }

    // Blockquote
    if (/^>\s?/.test(line)) {
      closeList();
      closeTable();
      if (!inBlockquote) { out.push("<blockquote>"); inBlockquote = true; }
      out.push(`<p>${inlineMd(line.replace(/^>\s?/, ""))}</p>`);
      i++;
      continue;
    }
    closeBlockquote();

    // Unordered list
    const ul = line.match(/^[-*]\s+(.+)$/);
    if (ul) {
      closeTable();
      if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; }
      out.push(`<li>${inlineMd(ul[1])}</li>`);
      i++;
      continue;
    }

    // Ordered list
    const ol = line.match(/^\d+\.\s+(.+)$/);
    if (ol) {
      closeTable();
      if (listType !== "ol") { closeList(); out.push("<ol>"); listType = "ol"; }
      out.push(`<li>${inlineMd(ol[1])}</li>`);
      i++;
      continue;
    }
    closeList();

    // Blank line
    if (line.trim() === "") { i++; continue; }

    // Paragraph: collect consecutive non-special lines.
    closeTable();
    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== ""
           && !/^(#{1,6}\s|```|>\s?|-\s|\*\s|\d+\.\s|---+\s*$)/.test(lines[i])
           && !(/\|/.test(lines[i]) && i + 1 < lines.length && /^\s*\|?\s*[-:]+/.test(lines[i + 1]))) {
      buf.push(lines[i]);
      i++;
    }
    out.push(`<p>${inlineMd(buf.join(" "))}</p>`);
  }
  closeAll();
  return out.join("\n");
}

// --------------------------------------------------------------------------
// Routing
// --------------------------------------------------------------------------

function currentFromHash() {
  const h = decodeURIComponent((location.hash || "").replace(/^#/, ""));
  return h || null;
}

async function show() {
  const id = currentFromHash() || (state.docs[0] && state.docs[0].id);
  state.current = id;
  renderRail();
  await renderDoc(id);
}

window.addEventListener("hashchange", show);

// --------------------------------------------------------------------------
// Bootstrap
// --------------------------------------------------------------------------

(async () => {
  try { state.docs = await loadIndex(); }
  catch (e) {
    $("#doc-content").innerHTML = `<p class="error">Couldn't load docs index: ${escapeHtml(e.message)}</p>`;
    return;
  }
  await show();
})();
