/* 科研助手 Agent —— 前端路由与各视图 */

import { api } from "./api.js";
import { renderReader } from "./reader.js";
import { renderDataviz, renderDiscovery, renderGraph, renderWriting } from "./views.js";
import {
  $,
  closeModal,
  debounce,
  escapeHtml,
  fmtNumber,
  openModal,
  pct,
  setBusy,
  shortAuthors,
  toast,
} from "./ui.js";

const state = {
  view: "library",
  papers: [],
  selected: new Set(),
  filters: { q: "", sort: "recent", latest_only: false },
  status: null,
};

const VIEWS = {
  library: renderLibrary,
  reader: renderReaderView,
  qa: renderQA,
  discovery: renderDiscovery,
  graph: renderGraph,
  writing: renderWriting,
  dataviz: renderDataviz,
  system: renderSystem,
};

/* ---------------------------------------------------------------- router */

function parseHash() {
  const raw = (location.hash || "#library").replace(/^#/, "");
  const [path, query] = raw.split("?");
  const parts = path.split("/");
  const view = VIEWS[parts[0]] ? parts[0] : "library";
  return { view, id: parts[1] || "", params: new URLSearchParams(query || "") };
}

async function route() {
  const { view, id, params } = parseHash();
  state.view = view;
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  const host = $("#view");
  try {
    await VIEWS[view](host, id, params);
  } catch (err) {
    host.innerHTML = `<div class="empty">页面加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function go(view) {
  location.hash = `#${view}`;
}

/* --------------------------------------------------------------- library */

async function renderLibrary(host) {
  const data = await api.listPapers({ q: state.filters.q, sort: state.filters.sort, latest_only: state.filters.latest_only });
  state.papers = data.items || [];
  const stats = data.stats || {};
  host.innerHTML = `
    <div class="view-head">
      <div><h1>文献库</h1><p>上传 PDF → 细粒度解析 → 语义去重 → 自动索引，全程可回溯</p></div>
      <span class="spacer"></span>
      <button class="btn ghost" id="seed-btn">载入演示语料</button>
      <button class="btn primary" id="upload-btn">上传 PDF</button>
      <input type="file" id="file-input" accept="application/pdf" multiple hidden />
    </div>
    <div class="stats-strip">
      <span><b>${fmtNumber(stats.papers)}</b> 篇文献</span>
      <span><b>${fmtNumber(stats.pages)}</b> 页</span>
      <span><b>${fmtNumber(stats.chunks)}</b> 检索块</span>
      <span><b>${fmtNumber(stats.sections)}</b> 章节</span>
      <span><b>${fmtNumber(stats.references)}</b> 参考文献</span>
      <span><b>${fmtNumber(stats.ocr_papers)}</b> 篇 OCR</span>
    </div>
    <div class="toolbar">
      <input class="input" id="search" placeholder="搜索标题 / 作者 / 摘要…" value="${escapeHtml(state.filters.q)}" />
      <select class="select" id="sort" style="max-width:170px">
        <option value="recent">最近上传</option>
        <option value="title">按标题</option>
        <option value="year">按年份</option>
        <option value="pages">按页数</option>
      </select>
      <label class="badge" style="cursor:pointer"><input type="checkbox" id="latest-only" ${state.filters.latest_only ? "checked" : ""}/> 仅最新版本</label>
      <span class="spacer"></span>
      <span class="dim" style="font-size:12px">去重基于 sha256 + arXiv 版本号 + 正文 SimHash</span>
    </div>
    <div class="dropzone" id="dropzone">拖拽 PDF 到此处，或点击「上传 PDF」；支持批量与扫描件（Windows OCR 自动启用）</div>
    <div id="library-grid" style="margin-top:16px">${cardsHtml(state.papers)}</div>`;
  $("#sort").value = state.filters.sort;

  $("#search").addEventListener(
    "input",
    debounce((event) => {
      state.filters.q = event.target.value.trim();
      renderLibrary(host);
    }, 320)
  );
  $("#sort").addEventListener("change", (event) => {
    state.filters.sort = event.target.value;
    renderLibrary(host);
  });
  $("#latest-only").addEventListener("change", (event) => {
    state.filters.latest_only = event.target.checked;
    renderLibrary(host);
  });
  $("#upload-btn").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", (event) => uploadFiles(host, Array.from(event.target.files || [])));
  $("#seed-btn").addEventListener("click", async (event) => {
    setBusy(event.currentTarget, true, "导入中…");
    try {
      const result = await api.seed();
      toast(`演示语料 ${result.imported} 篇导入 / ${result.duplicates} 篇重复`, "ok");
      renderLibrary(host);
    } catch (err) {
      toast(err.message, "err");
      setBusy(event.currentTarget, false);
    }
  });
  bindDropzone(host);
  host.querySelectorAll("[data-open]").forEach((card) =>
    card.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      location.hash = `#reader/${card.dataset.open}`;
    })
  );
  host.querySelectorAll("[data-delete]").forEach((btn) =>
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const id = btn.dataset.delete;
      if (!confirm("确认从文献库删除这篇论文及其解析产物？")) return;
      try {
        await api.deletePaper(id);
        toast("已删除", "ok");
        renderLibrary(host);
      } catch (err) {
        toast(err.message, "err");
      }
    })
  );
  host.querySelectorAll("[data-versions]").forEach((btn) =>
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const id = btn.dataset.versions;
      try {
        const data = await api.versions(id);
        openModal(
          "版本关系",
          `<div class="dim" style="margin-bottom:10px">版本组：${escapeHtml(data.version_group)}</div>
           ${(data.items || [])
             .map(
               (item) => `<div class="ref-item"><span class="ref-idx">${escapeHtml(item.version_label || "v1")}</span>
                 <div>${escapeHtml(item.title)}<div class="dim" style="font-size:11.5px">${escapeHtml(item.id)} · ${item.pages} 页 · 质量 ${pct(item.quality_score)}</div></div>
                 <button class="btn small" data-open-version="${item.id}">打开</button></div>`
             )
             .join("")}`
        );
        document.querySelectorAll("[data-open-version]").forEach((node) =>
          node.addEventListener("click", () => {
            closeModal();
            location.hash = `#reader/${node.dataset.openVersion}`;
          })
        );
      } catch (err) {
        toast(err.message, "err");
      }
    })
  );
  host.querySelectorAll("[data-compare]").forEach((btn) =>
    btn.addEventListener("click", async (event) => {
      event.stopPropagation();
      const [a, b] = btn.dataset.compare.split(",");
      try {
        const diff = await api.compare(a, b);
        openModal(
          "版本差异",
          `<div class="kv">
             <dt>文本相似度</dt><dd>${pct(diff.diff.text_similarity, 1)}（${escapeHtml(diff.diff.verdict)}）</dd>
             <dt>页数</dt><dd>${diff.diff.pages.a} → ${diff.diff.pages.b}</dd>
             <dt>新增章节</dt><dd>${escapeHtml((diff.diff.sections_added || []).join(" / ") || "无")}</dd>
             <dt>移除章节</dt><dd>${escapeHtml((diff.diff.sections_removed || []).join(" / ") || "无")}</dd>
             <dt>新增参考文献</dt><dd>${diff.diff.references_added} 条</dd>
           </div>`
        );
      } catch (err) {
        toast(err.message, "err");
      }
    })
  );
}

function cardsHtml(items) {
  if (!items.length) {
    return `<div class="empty card">文献库为空。上传 PDF 或点击「载入演示语料」开始。</div>`;
  }
  return `<div class="grid-cards">${items
    .map((item) => {
      const tags = [];
      if (item.ocr_used) tags.push(`<span class="badge warn">OCR</span>`);
      if (item.layout === "double") tags.push(`<span class="badge">双栏</span>`);
      if (item.arxiv_id) tags.push(`<span class="badge accent">arXiv:${escapeHtml(item.arxiv_id)}</span>`);
      if (item.is_latest === 0) tags.push(`<span class="badge">旧版本</span>`);
      return `
        <article class="card paper-card" data-open="${escapeHtml(item.id)}">
          <div class="row" style="justify-content:space-between">
            <span class="badge">${escapeHtml(item.year || "年份未知")}</span>
            <span class="quality ${item.quality_score >= 0.7 ? "" : "dim"}">质量 ${pct(item.quality_score)}</span>
          </div>
          <h3>${escapeHtml(item.title)}</h3>
          <div class="authors">${escapeHtml(shortAuthors(item.authors))}</div>
          <div class="meta">${tags.join("")}<span>${item.pages} 页</span>
            <span class="mono dim">${escapeHtml((item.id || "").slice(0, 10))}</span></div>
          <div class="card-actions">
            <button class="btn small">阅读</button>
            <button class="btn small ghost" data-versions="${escapeHtml(item.id)}">版本</button>
            <button class="btn small danger" data-delete="${escapeHtml(item.id)}">删除</button>
          </div>
        </article>`;
    })
    .join("")}</div>`;
}

function bindDropzone(host) {
  const zone = $("#dropzone", host);
  if (!zone) return;
  zone.addEventListener("click", () => $("#file-input").click());
  ["dragenter", "dragover"].forEach((name) =>
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove("drag");
    })
  );
  zone.addEventListener("drop", (event) => uploadFiles(host, Array.from(event.dataTransfer?.files || [])));
}

async function uploadFiles(host, files) {
  const pdfs = files.filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  if (!pdfs.length) {
    toast("请选择 PDF 文件", "err");
    return;
  }
  if (pdfs.length === 1) {
    toast("正在解析，请稍候…");
    try {
      const result = await api.upload(pdfs[0]);
      showUploadResult(result, host);
    } catch (err) {
      toast(err.message, "err");
    }
    return;
  }
  toast(`批量上传 ${pdfs.length} 篇…`);
  try {
    const result = await api.uploadMany(pdfs);
    openModal(
      "批量导入结果",
      `<div class="kv"><dt>总数</dt><dd>${result.total}</dd><dt>成功</dt><dd>${result.imported}</dd>
        <dt>重复跳过</dt><dd>${result.duplicates}</dd><dt>失败</dt><dd>${result.failed}</dd></div>
       <pre class="code" style="margin-top:12px">${escapeHtml(
         result.items.map((item) => `${item.filename || item.paper?.title || "-"} → ${item.status}${item.error ? ": " + item.error : ""}`).join("\n")
       )}</pre>`
    );
    renderLibrary(host);
  } catch (err) {
    toast(err.message, "err");
  }
}

function showUploadResult(result, host) {
  if (result.status === "duplicate" && !result.imported) {
    const verdict = result.verdict || {};
    const comp = result.comparison || {};
    openModal(
      "检测到重复 / 版本关系",
      `<div class="badge ${verdict.action === "replace-existing" ? "ok" : "warn"}" style="margin-bottom:10px">
         ${escapeHtml(verdict.kind)} · ${verdict.action === "replace-existing" ? "建议保留本次上传" : "建议保留库中版本"}</div>
       <p>${escapeHtml(verdict.reason || "")}</p>
       <div class="kv">
         <dt>已存在</dt><dd>${escapeHtml(verdict.existing_title || "-")}</dd>
         <dt>已有版本</dt><dd>${escapeHtml(verdict.existing_version || "-")}</dd>
         <dt>本次版本</dt><dd>${escapeHtml(verdict.new_version || "-")}</dd>
         ${comp.text_similarity !== undefined ? `<dt>正文相似度</dt><dd>${pct(comp.text_similarity, 1)}（${escapeHtml(comp.verdict || "")}）</dd>` : ""}
         ${comp.references_added !== undefined ? `<dt>新增引用</dt><dd>${comp.references_added} 条</dd>` : ""}
       </div>
       <div class="row" style="margin-top:14px">
         <button class="btn primary" id="force-upload">仍然入库为新版本</button>
         <button class="btn ghost" id="view-existing">打开已有条目</button>
       </div>`
    );
    $("#force-upload").onclick = async () => {
      const file = $("#file-input")?.files?.[0];
      if (!file) return toast("请重新选择文件", "err");
      closeModal();
      try {
        showUploadResult(await api.upload(file, true), host);
      } catch (err) {
        toast(err.message, "err");
      }
    };
    $("#view-existing").onclick = () => {
      closeModal();
      if (verdict.existing_paper_id) location.hash = `#reader/${verdict.existing_paper_id}`;
    };
  } else {
    toast(`${result.status === "replaced" ? "已替换旧版本" : "导入成功"}：${result.paper?.title || ""}`, "ok");
    renderLibrary(host).then(() => {
      if (result.paper?.id && (result.status === "replaced" || result.status === "imported")) {
        // 停留在文献库，避免打断批量操作
      }
    });
  }
}

/* ---------------------------------------------------------------- reader */

function renderReaderView(host, id, params) {
  if (!id) {
    const last = localStorage.getItem("lastPaper");
    if (last) return renderReader(host, last);
    host.innerHTML = `<div class="empty">从文献库选择一篇论文开始阅读。<br/><br/><button class="btn" id="to-lib">前往文献库</button></div>`;
    $("#to-lib").addEventListener("click", () => go("library"));
    return Promise.resolve();
  }
  localStorage.setItem("lastPaper", id);
  return renderReader(host, id).then(() => {
    const page = params.get("page");
    if (page) {
      document.getElementById(`page-${page}`)?.scrollIntoView({ behavior: "smooth" });
    }
  });
}

/* -------------------------------------------------------------------- qa */

async function renderQA(host) {
  const data = await api.listPapers({ limit: 100 });
  const papers = data.items || [];
  host.innerHTML = `
    <div class="view-head"><div><h1>抗幻觉问答</h1>
      <p>每个结论都绑定原文页码与 bbox；无证据时明确拒答，而不是编造</p></div></div>
    <div class="qa-layout">
      <aside class="card">
        <div class="row" style="justify-content:space-between"><b>文献范围</b>
          <button class="btn small ghost" id="select-all">全选</button></div>
        <div class="paper-pick" id="paper-pick" style="margin-top:8px">
          ${papers
            .map(
              (p) => `<label class="pick-row"><input type="checkbox" value="${escapeHtml(p.id)}" />
                <span>${escapeHtml(p.title)}<br/><span class="dim" style="font-size:11px">${p.pages} 页 · ${escapeHtml(p.year || "—")}</span></span></label>`
            )
            .join("") || `<div class="dim">文献库为空</div>`}
        </div>
        <div class="dim" style="font-size:11.5px;margin-top:8px">不选则检索全库</div>
        <hr style="border:0;border-top:1px solid var(--line-soft);margin:14px 0"/>
        <b style="font-size:13px">跨文献对比</b>
        <div class="chips" id="dim-pills" style="margin-top:8px"></div>
        <button class="btn small" id="compare-btn" style="margin-top:10px">生成对比表</button>
      </aside>
      <section>
        <div class="card">
          <div class="row"><input class="input" id="question" placeholder="例如：训练使用了多少块 GPU？BLEU 分数是多少？" />
            <button class="btn primary" id="ask-btn">提问</button></div>
          <div class="dim" style="font-size:11.5px;margin-top:6px">支持作者/机构、超参数、硬件、指标、引用信息等细粒度问题</div>
        </div>
        <div id="qa-result" style="margin-top:14px"></div>
        <div id="compare-result" style="margin-top:14px"></div>
      </section>
    </div>`;

  const pillsHost = $("#dim-pills", host);
  const dims = [
    ["params", "参数量"], ["layers", "层数"], ["context", "上下文"], ["normalization", "归一化"],
    ["hardware", "硬件"], ["benchmark", "指标"], ["pretraining", "预训练数据"], ["code", "开源"],
  ];
  pillsHost.innerHTML = dims.map(([key, label]) => `<button class="pill" data-dim="${key}">${label}</button>`).join("");
  pillsHost.querySelectorAll(".pill").forEach((pill) =>
    pill.addEventListener("click", () => pill.classList.toggle("on"))
  );

  $("#select-all", host).addEventListener("click", () => {
    host.querySelectorAll("#paper-pick input").forEach((box) => (box.checked = true));
  });
  $("#ask-btn", host).addEventListener("click", async (event) => {
    const question = $("#question", host).value.trim();
    if (!question) return toast("请输入问题", "err");
    const ids = Array.from(host.querySelectorAll("#paper-pick input:checked")).map((box) => box.value);
    setBusy(event.currentTarget, true, "检索并回答…");
    try {
      const result = await api.ask({ question, paper_ids: ids.length ? ids : null });
      renderAnswer(result, host);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });
  $("#compare-btn", host).addEventListener("click", async (event) => {
    const ids = Array.from(host.querySelectorAll("#paper-pick input:checked")).map((box) => box.value);
    if (ids.length < 2) return toast("请至少选择两篇论文", "err");
    const dimensions = Array.from(host.querySelectorAll("#dim-pills .pill.on")).map((pill) => pill.dataset.dim);
    setBusy(event.currentTarget, true, "对比中…");
    try {
      const result = await api.compareQa({ paper_ids: ids, dimensions: dimensions.length ? dimensions : null });
      renderCompare(result, host);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });
}

function renderAnswer(result, host) {
  const citations = result.citations || [];
  const answer = String(result.answer || "")
    .replace(/\[E(\d+)\]/g, (_, n) => `<span class="cite-chip" data-cite="${n}">[E${n}]</span>`)
    .replace(/\[(\d+)\]/g, (_, n) => `<span class="cite-chip" data-refnum="${n}">[${n}]</span>`);
  const verification = result.verification || {};
  const verified = result.verified;
  $("#qa-result", host).innerHTML = `
    <div class="card">
      <div class="row" style="justify-content:space-between;margin-bottom:8px">
        <b>回答</b>
        <span class="badge ${verified ? "ok" : "warn"}">${verified ? "证据已校验" : result.refused ? "证据不足已拒答" : "未通过校验"}</span>
      </div>
      <div class="answer">${answer || escapeHtml(result.answer || "")}</div>
      <div class="verify" style="margin-top:12px">
        <span>置信度 <b>${pct(result.confidence || verification.confidence)}</b></span>
        <span>引用 <b>${citations.filter((c) => c.cited).length}</b> 处</span>
        <span>数值校验 <b>${verification.numbers_supported || 0}/${verification.numbers_total || 0}</b></span>
        <span>模型 <b>${escapeHtml(result.provider || "offline")}</b>${result.degraded ? "（降级）" : ""}</span>
        <span class="dim">${Math.round(result.latency_ms || 0)} ms</span>
      </div>
      ${result.notes?.length ? `<div class="dim" style="font-size:12px;margin-top:6px">${escapeHtml(result.notes.join("；"))}</div>` : ""}
    </div>
    <div class="card" style="margin-top:12px"><b>原文证据（点击定位）</b>
      <div class="evidence" style="margin-top:10px">
        ${citations
          .map(
            (cite, idx) => `<div class="ev" data-evidence="${idx}">
              <b>[E${cite.evidence_id?.replace(/^E/, "") || idx + 1}] ${escapeHtml(cite.paper_title || "")} · p${cite.page}${cite.section ? " · " + escapeHtml(cite.section) : ""}</b>
              <div>${escapeHtml((cite.quote || "").slice(0, 240))}</div>
              <div class="mono dim" style="font-size:10.5px">bbox ${(cite.bbox || []).map((v) => Math.round(v)).join(", ")} · ${escapeHtml(cite.chunk_id || "")}</div>
            </div>`
          )
          .join("") || `<div class="dim">无</div>`}
      </div>
    </div>`;
  host.querySelectorAll("[data-evidence]").forEach((node) => {
    node.addEventListener("click", () => {
      const cite = citations[Number(node.dataset.evidence)];
      if (cite) location.hash = `#reader/${cite.paper_id}?page=${cite.page}`;
    });
  });
  host.querySelectorAll("[data-cite]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const id = chip.dataset.cite;
      const cite = citations.find((item) => String(item.evidence_id || "").replace(/^E/, "") === id);
      if (cite) location.hash = `#reader/${cite.paper_id}?page=${cite.page}`;
    });
  });
}

function renderCompare(result, host) {
  const dimensions = result.dimensions || [];
  $("#compare-result", host).innerHTML = `
    <div class="card">
      <div class="row" style="justify-content:space-between;margin-bottom:10px">
        <b>跨文献对比</b>
        <span class="badge ${result.verified ? "ok" : "warn"}">覆盖 ${pct(result.coverage)} · ${result.verified ? "锚点完整" : "存在无锚点单元格"}</span>
      </div>
      <div style="overflow:auto">${markdownTable(result.markdown, result.citations)}</div>
      <div class="dim" style="font-size:12px;margin-top:8px">每个单元格都回挂了原文引用；「未提及」表示该文献没有给出对应信息，而不是模型臆测。</div>
    </div>`;
}

function markdownTable(markdown, citations) {
  const lines = String(markdown || "").split("\n").filter((line) => line.trim().startsWith("|"));
  if (!lines.length) return `<pre class="code">${escapeHtml(markdown || "无对比结果")}</pre>`;
  const rows = lines.map((line) =>
    line.split("|").slice(1, -1).map((cell) => cell.trim())
  );
  const [head, , ...body] = rows;
  return `<table class="compare-table"><thead><tr>${head
    .map((cell) => `<th>${escapeHtml(cell)}</th>`)
    .join("")}</tr></thead><tbody>${body
    .map(
      (row) => `<tr>${row
        .map((cell, index) => `<td>${escapeHtml(cell)}${index > 0 && cell !== "未提及" ? citeBadges(cell, citations) : ""}</td>`)
        .join("")}</tr>`
    )
    .join("")}</tbody></table>`;
}

function citeBadges(cell, citations) {
  const hits = (citations || []).filter((cite) => cite.quote && cell.includes(cite.quote.slice(0, 18))).slice(0, 2);
  return hits
    .map((cite) => `<button class="cite-chip" data-cite-open="${escapeHtml(cite.paper_id)}:${cite.page}">[${escapeHtml(cite.paper_title?.slice(0, 12) || "")}]</button>`)
    .join("");
}

/* --------------------------------------------------------------- system */

async function renderSystem(host) {
  const status = await api.status();
  state.status = status;
  setGlobalStatus(status);
  const providers = status.providers || {};
  host.innerHTML = `
    <div class="view-head"><div><h1>系统状态</h1><p>在线模型优先、离线引擎兜底；所有降级都显式标注，保证可复现</p></div>
      <span class="spacer"></span><button class="btn" id="refresh-status">刷新</button></div>
    <div class="split">
      ${Object.entries(providers)
        .map(([key, value]) => providerCard(key, value))
        .join("")}
    </div>
    <div class="card" style="margin-top:14px"><b>索引与库</b>
      <pre class="code" style="margin-top:10px">${escapeHtml(JSON.stringify({ library: status.library, index: status.index, counts: status.counts }, null, 2))}</pre>
    </div>`;
  $("#refresh-status").addEventListener("click", () => renderSystem(host));
}

function providerCard(name, value) {
  const active = value?.active || value?.mode || "-";
  const available = value?.available !== false;
  return `<div class="card">
    <div class="row" style="justify-content:space-between"><b>${escapeHtml(name)}</b>
      <span class="badge ${available ? "ok" : "warn"}">${escapeHtml(String(active))}</span></div>
    <pre class="code" style="margin-top:10px;max-height:170px">${escapeHtml(JSON.stringify(value, null, 2))}</pre>
  </div>`;
}

function setGlobalStatus(status) {
  const node = $("#global-status");
  if (!node) return;
  const llm = status?.providers?.llm || {};
  const ocr = status?.providers?.ocr || {};
  node.innerHTML = `<span class="dot"></span> v${escapeHtml(status?.version || "")}
    <span class="badge">LLM ${escapeHtml(llm.active || "-")}</span>
    <span class="badge">OCR ${escapeHtml(ocr.active || ocr.engine || "-")}</span>`;
}

/* ---------------------------------------------------------------- boot */

function boot() {
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.addEventListener("click", () => go(tab.dataset.view))
  );
  $("#modal-close").addEventListener("click", closeModal);
  document.getElementById("modal").addEventListener("click", (event) => {
    if (event.target.id === "modal") closeModal();
  });
  window.addEventListener("hashchange", route);
  api
    .status()
    .then(setGlobalStatus)
    .catch(() => {});
  route();
}

boot();
