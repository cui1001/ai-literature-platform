/* 版式感知阅读器：服务端页面图 + 结构化 bbox 覆盖层 + 引用双向跳转 */

import { api } from "./api.js";
import { isView, pushReaderMode, readerMode } from "./router.js";
import { finishTask, getTask, startTask, subscribe, updateTask } from "./tasks.js";
import { $, escapeHtml, openModal, pct, setBusy, toast } from "./ui.js";

const state = {
  paperId: "",
  doc: null,
  lang: "en",
  bilingual: false,
  activeBlock: null,
  translations: false,
};

let renderSeq = 0;
let unsubscribeTask = null;

export async function renderReader(host, paperId, params = null) {
  const seq = ++renderSeq;
  state.paperId = paperId;
  // 导航离开再回来时，旧的后台翻译可能仍在进行；这里重置，避免按钮被永久锁死
  state.translating = false;
  state.translationError = "";
  unsubscribeTask?.();
  unsubscribeTask = null;
  // P3：阅读器模式写进 URL（#reader/{id}?mode=zh|bilingual），刷新/分享/后退都能还原
  const mode = readerMode(params);
  state.lang = mode === "en" ? "en" : "zh";
  state.bilingual = mode === "bilingual";
  state.translations = mode !== "en";
  host.innerHTML = `<div class="empty"><span class="spinner"></span> 正在加载版式文档…</div>`;
  let doc;
  try {
    doc = await api.document(paperId, state.lang, state.translations);
  } catch (err) {
    if (seq !== renderSeq || !isView("reader", host) || state.paperId !== paperId) return;
    host.innerHTML = `<div class="empty">无法打开文献：${escapeHtml(err.message)}<br/><br/>
      <button class="btn" id="back-lib">返回文献库</button></div>`;
    $("#back-lib", host)?.addEventListener("click", () => (location.hash = "#library"));
    return;
  }
  // 路由已切到别的板块/别的论文时，丢弃这次迟到的响应，不能覆盖当前视图
  if (seq !== renderSeq || !isView("reader", host) || state.paperId !== paperId) return;
  state.doc = doc;
  paint(host);
  highlightEvidence(host, params);
  const taskId = `translate:${paperId}`;
  if (getTask(taskId)?.status === "running") {
    // P2：回到阅读器时翻译仍在后台跑，续显进度并在完成后自动刷新译文
    state.translating = true;
    paint(host);
    unsubscribeTask = subscribe(() => {
      const task = getTask(taskId);
      if (!task || task.status === "running") return;
      state.translating = false;
      if (seq === renderSeq && isView("reader", host) && state.paperId === paperId) reloadDoc(host);
    });
    return;
  }
  if (mode !== "en" && !doc.translation?.count) ensureTranslations(host);
}

function pageSrc(image) {
  if (!image) return "";
  // 阅读器显示宽度约 900px，请求服务端按需缩小的 WebP 派生图（约为原图 1/8 体积）
  const joiner = image.includes("?") ? "&" : "?";
  return `${image}${joiner}w=900`;
}

function paint(host) {
  // 关键：异步任务（翻译/重新加载）完成时用户可能已经切到别的板块，
  // 这里拒绝把阅读器旧内容重新写回 #view，否则会出现「导航高亮是文献库、内容却是阅读器」。
  if (!isView("reader", host) || !state.doc) return;
  const doc = state.doc;
  const paper = doc.paper || {};
  const stats = doc.stats || {};
  host.innerHTML = `
    <div class="reader-toolbar">
      <button class="btn small" data-nav="library">← 文献库</button>
      <b style="max-width:46ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(paper.title)}</b>
      <span class="badge">${paper.pages} 页</span>
      <span class="badge ${paper.ocr_used ? "warn" : ""}">${paper.ocr_used ? `OCR · ${escapeHtml(paper.ocr_engine || "engine")}` : "文本层"}</span>
      <span class="badge">${paper.layout === "double" ? "双栏" : "单栏"}</span>
      <span class="spacer"></span>
      <button class="btn small ${state.lang !== "zh" ? "primary" : ""}" id="btn-en">原文</button>
      <button class="btn small ${state.lang === "zh" && !state.bilingual ? "primary" : ""}" id="btn-zh">中文对照</button>
      <button class="btn small ${state.bilingual ? "primary" : ""}" id="btn-bilingual">双语并排</button>
      ${translationBadge(doc)}
      <span class="dim">导出</span>
      <button class="btn small" data-export="md">Markdown</button>
      <button class="btn small" data-export="html">HTML</button>
      <button class="btn small" data-export="pdf">原位 PDF</button>
      <a class="btn small ghost" href="/api/papers/${encodeURIComponent(paper.id)}/file" target="_blank" rel="noreferrer">原始 PDF</a>
    </div>
    <div class="reader-shell">
      <aside class="reader-side card">
        <div class="row" style="justify-content:space-between;margin-bottom:8px">
          <b style="font-size:12px">目录</b>
          <span class="dim mono">${countHeadings(doc.heading_tree)} 节</span>
        </div>
        <div id="outline">${outlineHtml(doc.heading_tree)}</div>
        <div class="row" style="justify-content:space-between;margin:14px 0 6px">
          <b style="font-size:12px">引用</b>
          <span class="dim mono">${stats.linked || 0}/${stats.citations || 0}</span>
        </div>
        <div class="dim" style="font-size:11.5px">正文 [n] ↔ References 双向可跳转</div>
        <div style="margin-top:10px;display:flex;flex-direction:column;gap:4px">
          <button class="btn small ghost" id="jump-refs">跳转到参考文献</button>
          <button class="btn small ghost" id="btn-translate">翻译全文</button>
        </div>
        ${translationHint(doc)}
        ${qualityHtml(paper, doc.stats)}
      </aside>
      <section id="reader-main">${state.bilingual ? bilingualHtml(doc) : state.lang === "zh" ? zhHtml(doc) : pagesHtml(doc)}</section>
    </div>
    <div id="inspector" class="inspector hidden"></div>`;

  $("#btn-en", host).addEventListener("click", async () => {
    pushReaderMode(state.paperId, "en");
    state.lang = "en";
    state.translations = false;
    state.bilingual = false;
    state.doc = await api.document(state.paperId, "en", false);
    paint(host);
  });
  $("#btn-zh", host).addEventListener("click", () => toggleZh(host));
  $("#btn-bilingual", host).addEventListener("click", async () => {
    if (state.bilingual) {
      state.bilingual = false;
      pushReaderMode(state.paperId, "zh");
      paint(host);
      return;
    }
    if (state.lang !== "zh") await toggleZh(host);
    state.bilingual = true;
    pushReaderMode(state.paperId, "bilingual");
    paint(host);
  });
  host.querySelectorAll("[data-retry]").forEach((btn) => {
    btn.addEventListener("click", () => ensureTranslations(host));
  });
  host.querySelectorAll("[data-export]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      const fmt = btn.dataset.export;
      const lang = state.lang === "zh" ? "zh" : "en";
      const taskId = `export:${state.paperId}:${fmt}`;
      startTask(taskId, { kind: "export", label: `导出 ${fmt.toUpperCase()}`, detail: "生成文件中…" });
      setBusy(event.currentTarget, true, "生成中…");
      try {
        const { blob, filename, translated, translatable, partial } = await api.exportFile(
          state.paperId,
          fmt,
          lang,
          state.translations
        );
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
        if (lang === "zh" && partial && translatable > translated) {
          toast(
            `已导出 ${filename}：仅 ${translated}/${translatable} 块已翻译，其余保留英文`,
            "warn"
          );
        } else {
          toast(`已导出 ${filename}`, "ok");
        }
        finishTask(taskId, "done", { detail: filename });
      } catch (err) {
        finishTask(taskId, "failed", { detail: err.message, error: err.message });
        toast(err.message || "导出失败", "err");
      } finally {
        setBusy(event.currentTarget, false);
      }
    });
  });
  $("#btn-translate", host).addEventListener("click", async () => {
    if (state.lang !== "zh") {
      state.lang = "zh";
      state.translations = true;
      state.bilingual = false;
      paint(host);
    }
    await ensureTranslations(host);
  });
  $("#jump-refs", host).addEventListener("click", () => {
    const node = document.querySelector(".refs-list");
    node?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  host.querySelectorAll("[data-nav]").forEach((btn) =>
    btn.addEventListener("click", () => (location.hash = `#${btn.dataset.nav}`))
  );
  host.querySelectorAll(".outline-item").forEach((item) => {
    item.addEventListener("click", () => {
      const target = document.getElementById(`page-${item.dataset.page}`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  host.querySelectorAll(".block, .zh-block").forEach((node) => {
    node.addEventListener("click", () => showBlock(node));
  });
  bindReferenceJumps(host);
  bindCitationHotspots(host);
  if (state.bilingual) bindScrollSync(host);
}

function countHeadings(tree) {
  if (!Array.isArray(tree)) return 0;
  return tree.reduce((sum, node) => sum + 1 + countHeadings(node.children), 0);
}

const GLOSSARY_PROVIDERS = new Set(["offline", "glossary"]);

function translationInfo(doc) {
  const meta = (doc && doc.translation) || {};
  const providers = (meta.providers || []).filter(Boolean);
  const count = meta.count || 0;
  const live = providers.filter((p) => !GLOSSARY_PROVIDERS.has(p));
  const glossaryOnly = count > 0 && live.length === 0;
  let translated = 0;
  let pending = 0;
  let metadata = 0;
  let skipped = 0;
  for (const page of doc.pages || []) {
    for (const block of page.blocks || []) {
      if (!["paragraph", "heading", "caption", "table_text"].includes(block.kind)) continue;
      if (block.translation) translated += 1;
      else if (block.skip_reason === "metadata") metadata += 1;
      else if (block.skip_reason) skipped += 1;
      else pending += 1;
    }
  }
  return {
    count,
    providers,
    live,
    glossaryOnly,
    translated,
    pending,
    metadata,
    skipped,
    translating: Boolean(state.translating),
    error: state.translationError || "",
  };
}

function translationBadge(doc) {
  const { count, providers, live, glossaryOnly } = translationInfo(doc);
  if (!count) return "";
  const label = (live.length ? live : providers).join("/") || "cache";
  return `<span class="badge ${glossaryOnly ? "warn" : "ok"}" title="译文来源">译文 ${escapeHtml(label)}${glossaryOnly ? " · 术语级" : ""}</span>`;
}

function translationHint(doc) {
  const info = translationInfo(doc);
  if (info.translating) {
    return `<div class="dim" style="font-size:11px;margin-top:8px">正在翻译…（可继续阅读，完成后自动刷新）</div>`;
  }
  const retry = info.pending || info.error
    ? `<button class="btn small ghost" data-retry style="margin-top:6px">${info.pending ? `重试未翻译的 ${info.pending} 块` : "重试翻译"}</button>`
    : "";
  if (info.error) {
    return `<div class="warn-box" style="font-size:11px;line-height:1.5">
      <b>翻译失败</b>：${escapeHtml(info.error)}${retry}
    </div>`;
  }
  if (!info.count && !info.pending) {
    return `<div class="dim" style="font-size:11px;margin-top:6px">本题录信息无需翻译；正文点「中文对照」按需生成。</div>`;
  }
  if (info.glossaryOnly) {
    return `<div class="warn-box" style="font-size:11px;line-height:1.5">
      当前仅有<b>离线术语辅助</b>（英文中夹少量中文词）。整句机器翻译需要服务器能访问外网，请稍后重试「翻译全文」。
      ${retry}
    </div>`;
  }
  const parts = [`已译 ${info.translated} 块`];
  if (info.pending) parts.push(`待译 ${info.pending} 块`);
  if (info.skipped) parts.push(`跳过 ${info.skipped} 块（非文本/过短）`);
  parts.push(`题录 ${info.metadata} 块保持原文`);
  return `<div class="dim" style="font-size:11px;margin-top:6px">${parts.join(" · ")}</div>${retry}`;
}

function outlineHtml(tree, depth = 1) {
  if (!Array.isArray(tree)) return `<div class="dim">未识别出章节标题</div>`;
  return tree
    .map(
      (node) => `
      <div class="outline-item l${Math.min(depth, 3)}" data-page="${node.page || 1}">
        ${node.number ? escapeHtml(node.number) + " " : ""}${escapeHtml(node.title || "未命名")}
      </div>
      ${node.children?.length ? outlineHtml(node.children, depth + 1) : ""}`
    )
    .join("");
}

function qualityHtml(paper, stats) {
  const q = paper.quality || {};
  const checks = q.checks || {};
  const items = Object.entries(checks);
  return `
    <div style="margin-top:14px;border-top:1px solid var(--line-soft);padding-top:10px">
      <div class="row" style="justify-content:space-between"><b style="font-size:12px">解析质量</b>
        <span class="mono ${q.score >= 0.7 ? "" : "dim"}">${pct(q.score)}</span></div>
      <div class="chips" style="margin-top:6px">
        ${items
          .map(([key, ok]) => `<span class="badge ${ok ? "ok" : ""}" title="${key}">${labelOf(key)} ${ok ? "✓" : "✗"}</span>`)
          .join("")}
      </div>
      <div class="dim" style="font-size:11px;margin-top:6px">图表 ${q.figures || 0} · 表格 ${q.tables || 0} · 公式 ${q.equations || 0} · 参考文献 ${q.references || 0}</div>
    </div>`;
}

function labelOf(key) {
  return (
    {
      has_title: "标题",
      has_authors: "作者",
      has_abstract: "摘要",
      has_sections: "章节",
      has_tables: "表格",
      has_figures: "图表",
      has_equations: "公式",
      has_references: "参考文献",
      text_layer: "文本层",
    }[key] || key
  );
}

/* 证据定位：URL 携带 page + bbox 时，滚动到对应页并高亮原文区域 */
function highlightEvidence(host, params) {
  const pageNo = Number(params?.get?.("page") || 0);
  const raw = params?.get?.("bbox") || "";
  if (!pageNo || !raw) return;
  const bbox = raw.split(",").map((v) => Number(v));
  if (bbox.length !== 4 || !bbox.every((v) => Number.isFinite(v)) || !bbox.some((v) => v > 0)) return;
  requestAnimationFrame(() => {
    const card = document.getElementById(`page-${pageNo}`);
    if (!card) return;
    card.scrollIntoView({ block: "center" });
    const layer = card.querySelector(".blocks-layer");
    const page = (state.doc?.pages || []).find((p) => p.number === pageNo) || { width: 595, height: 842 };
    if (!layer) return;
    layer.querySelectorAll(".evidence-hl").forEach((node) => node.remove());
    const w = page.width || 595;
    const h = page.height || 842;
    const hl = document.createElement("div");
    hl.className = "evidence-hl";
    hl.style.left = `${(bbox[0] / w) * 100}%`;
    hl.style.top = `${(bbox[1] / h) * 100}%`;
    hl.style.width = `${Math.max(((bbox[2] - bbox[0]) / w) * 100, 1)}%`;
    hl.style.height = `${Math.max(((bbox[3] - bbox[1]) / h) * 100, 0.6)}%`;
    hl.title = "证据原文位置";
    layer.appendChild(hl);
  });
}

function pagesHtml(doc) {
  const pages = doc.pages || [];
  const hotspots = citationHotspots(doc);
  return `
    ${pages
      .map(
        (page) => `
      <div class="card" id="page-${page.number}" style="margin-bottom:14px;padding:10px">
        <div class="row" style="margin-bottom:8px"><span class="mono dim">PAGE ${page.number}</span>
          <span class="badge">${page.columns === 2 ? "双栏" : "单栏"}</span>
          ${page.ocr ? `<span class="badge warn">OCR</span>` : ""}
          <span class="spacer"></span><span class="dim mono">${page.blocks?.length || 0} blocks</span></div>
        <div class="page-wrap" style="max-width:${Math.round((page.width || 595) * 1.05)}px;aspect-ratio:${page.width || 595}/${page.height || 842}">
          <img src="${escapeHtml(pageSrc(page.image))}" alt="page ${page.number}" loading="${page.number === 1 ? "eager" : "lazy"}" ${page.number === 1 ? 'fetchpriority="high"' : 'fetchpriority="low"'} />
          <div class="blocks-layer">${blocksHtml(page, hotspots)}</div>
        </div>
      </div>`
      )
      .join("")}
    ${referencesHtml(doc.references || [])}`;
}

function citationHotspots(doc) {
  const map = new Map();
  const occ = doc?.links?.occurrences || [];
  for (const item of occ) {
    const rect = item.rects?.[0];
    if (!rect || !item.block_id) continue;
    const list = map.get(item.block_id) || [];
    const key = item.marker || String(item.number);
    let group = list.find((g) => g.marker === key && g.rect.join() === rect.join());
    if (!group) {
      group = { marker: key, rect, numbers: [] };
      list.push(group);
      map.set(item.block_id, list);
    }
    if (Number.isFinite(item.number) && !group.numbers.includes(item.number)) group.numbers.push(item.number);
  }
  return map;
}

function blocksHtml(page, hotspots) {
  const w = page.width || 595;
  const h = page.height || 842;
  const blocks = (page.blocks || [])
    .filter((b) => b.bbox && b.text)
    .map((b) => {
      const [x0, y0, x1, y1] = b.bbox;
      const left = (x0 / w) * 100;
      const top = (y0 / h) * 100;
      const width = Math.max(((x1 - x0) / w) * 100, 0.6);
      const height = Math.max(((y1 - y0) / h) * 100, 0.5);
      const cite = b.citations?.length ? "cite" : "";
      return `<div class="block ${cite}" data-block="${escapeHtml(b.id)}" title="${escapeHtml((b.text || "").slice(0, 80))}"
        style="left:${left.toFixed(3)}%;top:${top.toFixed(3)}%;width:${width.toFixed(3)}%;height:${height.toFixed(3)}%"></div>`;
    })
    .join("");
  const layer = (page.blocks || [])
    .flatMap((b) => {
      const spots = hotspots?.get(b.id) || [];
      return spots.map((g) => {
        const [x0, y0, x1, y1] = g.rect;
        const left = (x0 / w) * 100;
        const top = (y0 / h) * 100;
        const width = Math.max(((x1 - x0) / w) * 100, 0.7);
        const height = Math.max(((y1 - y0) / h) * 100, 0.65);
        const nums = g.numbers.join(",");
        const label = nums ? `[${nums}]` : g.marker;
        return `<button class="cite-hot" data-block="${escapeHtml(b.id)}" data-jump-refs="${nums}" title="${escapeHtml(label)} → 跳转参考文献"
          style="left:${left.toFixed(3)}%;top:${top.toFixed(3)}%;width:${width.toFixed(3)}%;height:${height.toFixed(3)}%"></button>`;
      });
    })
    .join("");
  return blocks + layer;
}

function referencesHtml(refs) {
  if (!refs.length) return "";
  return `
    <div class="refs-list card" id="references">
      <div class="row" style="justify-content:space-between"><b>参考文献 · ${refs.length}</b>
        <span class="dim" style="font-size:12px">点击编号可查看正文引用位置</span></div>
      ${refs
        .map(
          (ref) => `
        <div class="ref-item" id="ref-${ref.idx}" data-ref="${ref.idx}">
          <span class="ref-idx">[${ref.idx}]</span>
          <div>
            <div>${escapeHtml(ref.raw || ref.title || "（未解析）")}</div>
            ${ref.title ? `<div class="dim" style="font-size:11.5px">${escapeHtml(ref.title)}${ref.year ? ` · ${ref.year}` : ""}${ref.arxiv_id ? ` · arXiv:${escapeHtml(ref.arxiv_id)}` : ""}</div>` : ""}
          </div>
          <span class="badge ${ref.cited_count ? "accent" : ""}">${ref.cited_count ? `被引 ${ref.cited_count}` : "未引用"}</span>
        </div>`
        )
        .join("")}
    </div>`;
}

function zhHtml(doc) {
  const pages = doc.pages || [];
  const wanted = new Set(["paragraph", "heading", "caption", "table_text"]);
  return `<div class="zh-reader">${pages
    .map((page) => {
      const blocks = (page.blocks || []).filter((b) => wanted.has(b.kind));
      const body = blocks
        .map((b) => {
          if (b.translation) {
            return `<div class="zh-block" data-block="${escapeHtml(b.id)}">${escapeHtml(b.translation)}</div>`;
          }
          if (b.skip_reason === "metadata") {
            return `<div class="zh-block info" data-block="${escapeHtml(b.id)}">${escapeHtml(b.text)}
                 <div class="dim" style="font-size:11px;margin-top:2px">题录信息（保留原文）</div></div>`;
          }
          if (b.skip_reason) {
            return `<div class="zh-block skipped" data-block="${escapeHtml(b.id)}">${escapeHtml(b.text)}
                 <div class="dim" style="font-size:11px;margin-top:2px">未翻译 · 非文本块（跳过）</div></div>`;
          }
          const note = state.translating ? "翻译中…" : "未翻译，点左侧「重试」";
          return `<div class="zh-block pending" data-block="${escapeHtml(b.id)}">${escapeHtml(b.text)}
                 <div class="dim" style="font-size:11px;margin-top:2px">${note}</div></div>`;
        })
        .join("") || `<div class="dim" style="font-size:12px">本页暂无可读正文。</div>`;
      return `<section class="card" style="margin-bottom:14px" id="page-${page.number}">
        <div class="row" style="justify-content:space-between;align-items:center;margin-bottom:8px">
          <b class="mono dim">PAGE ${page.number}</b>
          <a class="dim" style="font-size:11px" target="_blank" rel="noreferrer" href="${escapeHtml(pageSrc(page.image))}">查看原页</a>
        </div>
        ${body}
      </section>`;
    })
    .join("")}</div>`;
}

function bilingualHtml(doc) {
  const pages = doc.pages || [];
  return `
    <div class="bilingual">
      <div class="col" id="col-src">
        ${pages
          .map(
            (page) => `
          <div class="card" style="margin-bottom:12px;padding:8px" id="bipage-${page.number}">
            <div class="mono dim" style="margin-bottom:6px">PAGE ${page.number}</div>
            <div class="page-wrap"><img src="${escapeHtml(pageSrc(page.image))}" loading="${page.number === 1 ? "eager" : "lazy"}" fetchpriority="high" alt="page ${page.number}"/>
              <div class="blocks-layer">${blocksHtml(page)}</div></div>
          </div>`
          )
          .join("")}
      </div>
      <div class="col" id="col-dst">
        ${pages
          .map(
            (page) => `
          <div style="margin-bottom:16px" data-bipage="${page.number}">
            <div class="mono dim" style="margin-bottom:6px">PAGE ${page.number}</div>
            ${(page.blocks || [])
              .filter((b) => ["paragraph", "heading", "caption", "table_text"].includes(b.kind))
              .map((b) => {
                if (b.translation) {
                  return `<div class="pair"><div class="src">${escapeHtml(b.text)}</div>
                  <div class="dst">${escapeHtml(b.translation)}</div></div>`;
                }
                const note =
                  b.skip_reason === "metadata"
                    ? "题录信息（保留原文）"
                    : b.skip_reason
                    ? "未翻译 · 非文本块（跳过）"
                    : state.translating
                    ? "翻译中…"
                    : "未翻译（保留原文）";
                return `<div class="pair missing"><div class="src">${escapeHtml(b.text)}</div>
                  <div class="dst dim">${note}</div></div>`;
              })
              .join("") || `<div class="dim" style="font-size:12px">本页暂无可读正文。</div>`}
          </div>`
          )
          .join("")}
      </div>
    </div>`;
}

function bindScrollSync(host) {
  const src = $("#col-src", host);
  const dst = $("#col-dst", host);
  if (!src || !dst) return;
  let lock = 0;
  const sync = (from, to) => () => {
    if (lock) return;
    lock = 1;
    const ratio = from.scrollTop / Math.max(from.scrollHeight - from.clientHeight, 1);
    to.scrollTop = ratio * (to.scrollHeight - to.clientHeight);
    requestAnimationFrame(() => (lock = 0));
  };
  src.addEventListener("scroll", sync(src, dst));
  dst.addEventListener("scroll", sync(dst, src));
}

async function reloadDoc(host) {
  const paperId = state.paperId;
  const doc = await api.document(paperId, state.lang, state.translations);
  if (!isView("reader", host) || state.paperId !== paperId) return;
  state.doc = doc;
  paint(host);
}

async function ensureTranslations(host) {
  const paperId = state.paperId;
  const taskId = `translate:${paperId}`;
  // P2：任务中心已登记同名任务时直接复用，避免重复发起
  if (state.translating || getTask(taskId)?.status === "running" || !state.doc) return;
  const alive = () => isView("reader", host) && state.paperId === paperId;
  state.translating = true;
  state.translationError = "";
  startTask(taskId, {
    kind: "translate",
    label: `翻译《${(state.doc.paper?.title || paperId).slice(0, 18)}》`,
    detail: "准备中…",
    progress: 2,
  });
  if (alive()) paint(host);
  try {
    const info = translationInfo(state.doc);
    const page1 = (state.doc.pages || [])[0] || { blocks: [] };
    const ids = (page1.blocks || [])
      .filter((b) => !b.translation && !b.skip_reason)
      .map((b) => b.id);
    if (info.pending && ids.length) {
      // 先翻第 1 页，尽快出中文；失败则在下方提示重试
      updateTask(taskId, { progress: 8, detail: `翻译第 1 页（${ids.length} 块）` });
      const first = await api.translate(paperId, "zh", ids);
      updateTask(taskId, { progress: 35, detail: `第 1 页完成 ${first.count} 块` });
      if (alive()) await reloadDoc(host);
      if (first.failed) throw new Error(`第 1 页有 ${first.failed} 块翻译失败`);
    }
    updateTask(taskId, { progress: 40, detail: "翻译全文（后台继续，可切换板块）" });
    const full = await api.translate(paperId, "zh");
    updateTask(taskId, {
      progress: 92,
      detail: `已翻译 ${full.count} 块${full.skipped ? `，跳过 ${full.skipped} 块` : ""}`,
    });
    if (alive()) await reloadDoc(host);
    if (full.failed) {
      finishTask(taskId, "failed", {
        detail: `${full.failed} 块失败`,
        error: `${full.failed} 块翻译失败`,
      });
      if (alive()) {
        state.translationError = `${full.failed} 块翻译失败`;
        toast(`翻译完成 ${full.count} 块，${full.failed} 块失败，可点「重试」`, "err");
      }
    } else {
      finishTask(taskId, "done", { detail: `${full.count} 块已译` });
      if (alive()) {
        toast(
          `翻译完成：${full.count} 块${full.skipped ? `，跳过 ${full.skipped} 块（非文本/过短）` : ""}`,
          "ok"
        );
      }
    }
  } catch (err) {
    finishTask(taskId, "failed", { detail: err.message, error: err.message });
    if (alive()) {
      state.translationError = err.message || "翻译失败";
      toast(`${state.translationError}，可点「重试」`, "err");
    }
  } finally {
    state.translating = false;
    if (alive()) paint(host);
  }
}

async function toggleZh(host) {
  if (state.lang === "zh") {
    // P7：再次点击 = 重试缺失块，而不是切回原文（切回原文用「原文」按钮）
    if (state.bilingual) {
      state.bilingual = false;
      pushReaderMode(state.paperId, "zh");
      paint(host);
      return;
    }
    await ensureTranslations(host);
    return;
  }
  // P3：立即切到中文视图并逐块显示「翻译中」，再发起翻译，避免点击后长时间无反馈
  state.lang = "zh";
  state.translations = true;
  state.bilingual = false;
  pushReaderMode(state.paperId, "zh");
  paint(host);
  await ensureTranslations(host);
}


function showBlock(node) {
  const id = node.dataset.block;
  document.querySelectorAll(".block.active").forEach((n) => n.classList.remove("active"));
  node.classList.add("active");
  let block = null;
  let pageNo = 0;
  for (const page of state.doc.pages || []) {
    const found = (page.blocks || []).find((b) => b.id === id);
    if (found) {
      block = found;
      pageNo = page.number;
      break;
    }
  }
  if (!block) return;
  const inspector = document.getElementById("inspector");
  inspector.classList.remove("hidden");
  inspector.innerHTML = `
    <div class="row" style="justify-content:space-between">
      <h4>${escapeHtml(block.kind)} · p${pageNo}${block.section ? ` · ${escapeHtml(block.section)}` : ""}</h4>
      <button class="btn ghost small" id="close-inspector">✕</button>
    </div>
    <div class="quote">${escapeHtml(block.text)}</div>
    ${block.translation ? `<div class="quote" style="margin-top:8px;color:var(--accent-2)">${escapeHtml(block.translation)}</div>` : ""}
    ${
      block.citations?.length
        ? `<div class="chips" style="margin-top:10px">${block.citations
            .map((n) => `<button class="badge accent" data-jump-ref="${n}">[${n}]</button>`)
            .join("")}</div>`
        : `<div class="dim" style="margin-top:8px;font-size:12px">该块无引用标记</div>`
    }
    <div class="row" style="margin-top:10px">
      <button class="btn small" id="copy-quote">复制原文</button>
      <span class="dim mono" style="font-size:10.5px">bbox ${(block.bbox || []).map((v) => Math.round(v)).join(", ")}</span>
    </div>`;
  $("#close-inspector", inspector).onclick = () => inspector.classList.add("hidden");
  $("#copy-quote", inspector).onclick = async () => {
    await navigator.clipboard.writeText(block.text);
    toast("已复制", "ok");
  };
  inspector.querySelectorAll("[data-jump-ref]").forEach((btn) =>
    btn.addEventListener("click", () => focusReference(Number(btn.dataset.jumpRef)))
  );
}

function focusReference(number) {
  const node = document.getElementById(`ref-${number}`);
  if (!node) {
    toast("该引用未在参考文献中解析到", "err");
    return;
  }
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.style.outline = "1.5px solid var(--warn)";
  setTimeout(() => (node.style.outline = ""), 2200);
  openModal(
    `引用 [${number}]`,
    `<div class="kv"><dt>原文</dt><dd>${escapeHtml(node.querySelector("div")?.innerText || "")}</dd></div>`
  );
}

function bindCitationHotspots(host) {
  host.addEventListener("click", (event) => {
    const btn = event.target.closest?.(".cite-hot");
    if (!btn) return;
    event.preventDefault();
    event.stopPropagation();
    const numbers = (btn.dataset.jumpRefs || "")
      .split(",")
      .map((n) => Number(n))
      .filter((n) => Number.isFinite(n) && n > 0);
    if (numbers.length <= 1) {
      if (numbers.length) focusReference(numbers[0]);
      return;
    }
    openModal(
      `该处引用了 ${numbers.length} 篇文献`,
      `<div class="row" style="flex-wrap:wrap;gap:6px">${numbers
        .map((n) => `<button class="badge accent" data-pick-ref="${n}">[${n}]</button>`)
        .join("")}</div>`
    );
    document.querySelectorAll("[data-pick-ref]").forEach((node) =>
      node.addEventListener("click", () => {
        document.getElementById("modal").close();
        focusReference(Number(node.dataset.pickRef));
      })
    );
  });
}

function bindReferenceJumps(host) {
  host.querySelectorAll("[data-ref]").forEach((item) => {
    item.addEventListener("click", () => {
      const idx = Number(item.dataset.ref);
      const ref = (state.doc.references || []).find((r) => r.idx === idx);
      if (!ref) return;
      const list = (ref.cited_by || [])
        .map(
          (cite) => `<button class="btn small" data-goto="${escapeHtml(cite.block_id)}" data-page="${cite.page}">
            p${cite.page} · ${escapeHtml((cite.quote || "").slice(0, 46))}…</button>`
        )
        .join("") || `<div class="dim">正文中未检测到该文献的引用标记</div>`;
      openModal(`参考文献 [${idx}] 的正文引用位置`, `<div class="row" style="flex-direction:column;align-items:stretch;gap:6px">${list}</div>`);
      document.querySelectorAll("[data-goto]").forEach((btn) =>
        btn.addEventListener("click", () => {
          document.getElementById("modal").close();
          const target = document.querySelector(`[data-block="${btn.dataset.goto}"]`);
          target?.scrollIntoView({ behavior: "smooth", block: "center" });
          target?.classList.add("active");
          host.querySelectorAll(`.cite-hot[data-block="${btn.dataset.goto}"]`).forEach((hot) => {
            hot.classList.add("flash");
            setTimeout(() => hot.classList.remove("flash"), 1800);
          });
        })
      );
    });
  });
}
