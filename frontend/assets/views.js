/* 四个业务视图：arXiv 追踪、引用图谱、写作 Copilot、数据可视化 */

import { api } from "./api.js";
import { $, escapeHtml, openModal, pct, setBusy, toast } from "./ui.js";

/* ------------------------------------------------------------- utilities */

function paperPicker(papers, id = "pick", note = "不选则使用全库") {
  return `<div class="paper-pick" id="${id}">
    ${papers
      .map(
        (p) => `<label class="pick-row"><input type="checkbox" value="${escapeHtml(p.id)}" />
          <span>${escapeHtml(p.title)}<br/><span class="dim" style="font-size:11px">${p.pages} 页 · ${escapeHtml(p.year || "—")} · ${escapeHtml(p.arxiv_id || "")}</span></span></label>`
      )
      .join("") || `<div class="dim">文献库为空</div>`}
  </div><div class="dim" style="font-size:11px;margin-top:6px">${note}</div>`;
}

function picked(host, selector) {
  return Array.from(host.querySelectorAll(`${selector} input:checked`)).map((box) => box.value);
}

function miniMarkdown(markdown) {
  const lines = String(markdown || "").replace(/\r/g, "").split("\n");
  const out = [];
  let inList = false;
  const closeList = () => {
    if (inList) out.push("</ul>");
    inList = false;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const inline = (text) =>
      escapeHtml(text)
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\[(\d+)\]/g, '<span class="cite-chip">[$1]</span>');
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      out.push(`<h${Math.min(4, heading[1].length + 1)}>${inline(heading[2])}</h${Math.min(4, heading[1].length + 1)}>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  return out.join("");
}

function scriptBlock(text, label) {
  return `<div class="row" style="justify-content:space-between;margin-top:10px">
      <b style="font-size:13px">${escapeHtml(label)}</b>
      <button class="btn small ghost" data-copy="${encodeURIComponent(text)}">复制</button>
    </div>
    <pre class="code" style="max-height:320px">${escapeHtml(text)}</pre>`;
}

function bindCopy(host) {
  host.querySelectorAll("[data-copy]").forEach((button) =>
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(decodeURIComponent(button.dataset.copy));
        toast("已复制", "ok");
      } catch {
        toast("复制失败，请手动选择", "err");
      }
    })
  );
}

/* ------------------------------------------------------------- discovery */

export async function renderDiscovery(host) {
  const data = await api.listPapers({ limit: 100 });
  const papers = data.items || [];
  host.innerHTML = `
    <div class="view-head"><div><h1>arXiv 检索与追踪</h1>
      <p>检索 → 相关性/代码加成排序 → 一键入库；推荐与追踪都基于本地文献库画像</p></div>
      <span class="spacer"></span>
      <button class="btn ghost" id="run-track">运行追踪</button></div>
    <div class="split two">
      <section>
        <div class="card">
          <div class="row"><input class="input" id="d-query" placeholder="论文标题 / arXiv id / 关键词" />
            <button class="btn primary" id="d-search">检索</button></div>
          <div class="row" style="margin-top:8px">
            <label class="dim" style="font-size:12px"><input type="checkbox" id="d-code" checked /> 优先代码可复现</label>
            <input class="input" id="d-cat" placeholder="分类（可选，如 cs.CL）" style="max-width:180px" />
          </div>
          <div class="dim" style="font-size:11.5px;margin-top:6px">离线模式检索内置 arXiv 镜像；在线模式直连 export.arxiv.org</div>
        </div>
        <div id="d-results" style="margin-top:12px"></div>
        <div class="card" style="margin-top:12px">
          <div class="row" style="justify-content:space-between"><b>自动追踪主题</b>
            <span class="dim" style="font-size:11px">Sickle 风格定时刷新</span></div>
          <div class="row" style="margin-top:8px"><input class="input" id="d-topic" placeholder="如：retrieval augmented generation" />
            <button class="btn small" id="d-track-add">添加</button></div>
          <div id="d-topics" style="margin-top:8px"></div>
        </div>
      </section>
      <section>
        <div class="card"><b>为你推荐</b>
          <div class="dim" style="font-size:11.5px;margin-top:4px">按本地库相关性 + 代码可复现 + 新鲜度排序，点赞/踩会实时影响下一次</div>
          <div id="d-reco" style="margin-top:10px"></div>
        </div>
        <div class="card" style="margin-top:12px"><b>本地库</b>
          <div class="dim" style="font-size:11.5px;margin-top:4px">当前 ${papers.length} 篇；从 References 批量入库会自动跳过已有文献</div>
          <div id="d-refs" style="margin-top:10px"></div>
        </div>
      </section>
    </div>`;

  const renderItems = (items) => `
    <div class="discovery-list">${(items || [])
      .map(
        (item) => `<article class="disc-item">
          <div class="row" style="justify-content:space-between;gap:8px">
            <b>${escapeHtml(item.title || item.arxiv_id || "")}</b>
            <span class="badge ${item.has_code ? "ok" : ""}">${item.has_code ? "有代码" : "无代码"}</span>
          </div>
          <div class="dim" style="font-size:12px;margin-top:4px">
            ${escapeHtml((item.authors || []).slice(0, 3).join(", "))}${item.year ? " · " + item.year : ""}
            ${item.arxiv_id ? ` · <a href="https://arxiv.org/abs/${escapeHtml(item.arxiv_id)}" target="_blank" rel="noreferrer">${escapeHtml(item.arxiv_id)}</a>` : ""}
          </div>
          <div style="font-size:12.5px;margin-top:6px">${escapeHtml((item.abstract || "").slice(0, 260))}${(item.abstract || "").length > 260 ? "…" : ""}</div>
          <div class="dim" style="font-size:11.5px;margin-top:6px">${escapeHtml(item.reason || "")}</div>
          <div class="row" style="margin-top:8px">
            <span class="dim mono" style="font-size:11px">相关性 ${pct(item.relevance ?? item.score)}</span>
            <span class="spacer"></span>
            <button class="btn small" data-import="${escapeHtml(item.arxiv_id || item.title)}">入库</button>
            <button class="btn small ghost" data-like="${escapeHtml(item.arxiv_id || item.title)}">赞</button>
            <button class="btn small ghost" data-dislike="${escapeHtml(item.arxiv_id || item.title)}">踩</button>
          </div>
        </article>`
      )
      .join("") || `<div class="empty">暂无结果</div>`}</div>`;

  const search = async (event) => {
    const query = $("#d-query", host).value.trim();
    if (!query) return toast("请输入检索词", "err");
    setBusy(event?.currentTarget || $("#d-search", host), true, "检索中…");
    try {
      const result = await api.discoverSearch({
        query,
        max_results: 12,
        category: $("#d-cat", host).value.trim() || null,
        prefer_code: $("#d-code", host).checked,
      });
      $("#d-results", host).innerHTML = `
        <div class="row" style="justify-content:space-between"><b>检索结果 ${result.items?.length || 0} 条</b>
          <span class="dim" style="font-size:11px">${escapeHtml(result.provider || "")}${result.degraded ? " · 降级" : ""}</span></div>
        ${renderItems(result.items)}`;
      bindActions(host);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy($("#d-search", host), false);
    }
  };

  const loadReco = async () => {
    try {
      const result = await api.discoverRecommend();
      $("#d-reco", host).innerHTML = renderItems(result.items);
      bindActions(host);
    } catch (err) {
      $("#d-reco", host).innerHTML = `<div class="dim">${escapeHtml(err.message)}</div>`;
    }
  };

  const loadTopics = async () => {
    const result = await api.discoverTrack({ action: "list" });
    const topics = result.topics || result.items || [];
    $("#d-topics", host).innerHTML = topics.length
      ? topics
          .map(
            (topic) => `<div class="row" style="justify-content:space-between;padding:6px 0;border-top:1px solid var(--line-soft)">
              <span>${escapeHtml(topic.query || topic.topic || "")}<br/><span class="dim" style="font-size:11px">${escapeHtml(topic.category || "")} · ${escapeHtml(topic.last_run || "尚未运行")}</span></span>
              <button class="btn small ghost" data-remove-topic="${escapeHtml(topic.id || topic.topic_id || topic.query)}">移除</button></div>`
          )
          .join("")
      : `<div class="dim">尚未添加追踪主题</div>`;
    $("#d-topics", host)
      .querySelectorAll("[data-remove-topic]")
      .forEach((button) =>
        button.addEventListener("click", async () => {
          await api.discoverTrack({ action: "remove", query: button.dataset.removeTopic });
          loadTopics();
        })
      );
  };

  const bindActions = (scope) => {
    scope.querySelectorAll("[data-import]").forEach((button) =>
      button.addEventListener("click", async () => {
        setBusy(button, true, "入库中…");
        try {
          const result = await api.discoverImport({ identifier: button.dataset.import });
          toast(result.status === "failed" ? result.reason || "入库失败" : "已入库", result.status === "failed" ? "err" : "ok");
          if (result.paper?.id) {
            $("#d-refs", host).innerHTML = `<div>刚入库：<a href="#reader/${result.paper.id}">${escapeHtml(result.paper.title)}</a>
              <button class="btn small ghost" id="d-import-refs">导入其参考文献</button></div>`;
            $("#d-import-refs", host)?.addEventListener("click", async (event) => {
              setBusy(event.currentTarget, true, "导入中…");
              const refs = await api.discoverRefs(result.paper.id);
              $("#d-refs", host).innerHTML = `<div class="dim">尝试 ${refs.attempted || 0} 条，导入 ${refs.imported || 0} 条，跳过 ${refs.skipped || 0} 条</div>`;
            });
          }
        } catch (err) {
          toast(err.message, "err");
        } finally {
          setBusy(button, false);
        }
      })
    );
    scope.querySelectorAll("[data-like],[data-dislike]").forEach((button) =>
      button.addEventListener("click", async () => {
        const target = button.dataset.like || button.dataset.dislike;
        await api.discoverFeedback({ target, action: button.dataset.like ? "like" : "dislike" });
        toast("反馈已记录", "ok");
        loadReco();
      })
    );
  };

  $("#d-search", host).addEventListener("click", search);
  $("#d-query", host).addEventListener("keydown", (event) => {
    if (event.key === "Enter") search(event);
  });
  $("#d-track-add", host).addEventListener("click", async () => {
    const query = $("#d-topic", host).value.trim();
    if (!query) return toast("请输入主题", "err");
    await api.discoverTrack({ action: "add", query });
    $("#d-topic", host).value = "";
    loadTopics();
  });
  $("#run-track", host).addEventListener("click", async (event) => {
    setBusy(event.currentTarget, true, "刷新中…");
    try {
      const result = await api.discoverTrack({ action: "run", force: true });
      toast(`抓取 ${result.fetched || 0} 条，新增 ${result.new_items || 0} 条`, "ok");
      loadReco();
      loadTopics();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });

  loadReco();
  loadTopics();
}

/* ----------------------------------------------------------------- graph */

const ROLE_COLOR = { foundation: "#7fe0b0", bridge: "#8ab4ff", peripheral: "#f0b46a", normal: "#8b96a8" };
const ROLE_LABEL = { foundation: "基石论文", bridge: "桥接论文", peripheral: "边缘衍生", normal: "常规节点" };

export async function renderGraph(host, id) {
  const data = await api.listPapers({ limit: 100 });
  const papers = data.items || [];
  host.innerHTML = `
    <div class="view-head"><div><h1>引用图谱与综述</h1>
      <p>解析文献间引用关系 → PageRank / 介数中心性 → 挖掘基石与边缘论文 → 自动综述</p></div>
      <span class="spacer"></span>
      <button class="btn ghost" id="g-export-mmd">导出 Mermaid</button>
      <button class="btn ghost" id="g-export-dot">导出 DOT</button></div>
    <div class="split two">
      <aside class="card">
        <div class="row" style="justify-content:space-between"><b>图谱范围</b>
          <button class="btn small ghost" id="g-all">全选</button></div>
        <div style="margin-top:8px;max-height:320px;overflow:auto">${paperPicker(papers, "g-pick", "建议选择 6–20 篇同一主题文献")}</div>
        <button class="btn primary" id="g-build" style="margin-top:10px">构建引用图谱</button>
        <hr style="border:0;border-top:1px solid var(--line-soft);margin:14px 0"/>
        <div class="row" style="justify-content:space-between"><b>领域综述</b></div>
        <input class="input" id="g-title" placeholder="综述标题（可选）" style="margin-top:8px" />
        <button class="btn" id="g-survey" style="margin-top:8px">生成综述 + Future Work</button>
      </aside>
      <section>
        <div class="card" id="g-canvas-wrap"><div class="row" style="justify-content:space-between"><b>引用拓扑</b>
          <span class="dim" id="g-metrics" style="font-size:11.5px">尚未构建</span></div>
          <div id="g-canvas" style="margin-top:10px"></div>
          <div class="legend">${Object.entries(ROLE_LABEL)
            .map(([role, label]) => `<span><i style="background:${ROLE_COLOR[role]}"></i>${label}</span>`)
            .join("")}</div>
        </div>
        <div id="g-insights" style="margin-top:12px"></div>
        <div id="g-survey-result" style="margin-top:12px"></div>
      </section>
    </div>`;

  let current = null;

  const draw = (graph) => {
    current = graph;
    const nodes = graph.nodes || [];
    const links = graph.links || graph.edges || [];
    const width = 760;
    const height = Math.max(420, Math.min(620, 300 + nodes.length * 14));
    const cx = width / 2;
    const cy = height / 2;
    const maxRank = Math.max(...nodes.map((node) => node.pagerank || 0), 1e-9);
    const ordered = [...nodes].sort((a, b) => (b.pagerank || 0) - (a.pagerank || 0));
    const positions = new Map();
    ordered.forEach((node, index) => {
      const angle = (index / Math.max(1, ordered.length)) * Math.PI * 2 - Math.PI / 2;
      const ring = node.role === "foundation" ? 0.52 : node.role === "bridge" ? 0.72 : 0.9;
      positions.set(node.id, {
        x: cx + Math.cos(angle) * width * 0.36 * ring,
        y: cy + Math.sin(angle) * height * 0.34 * ring,
      });
    });
    const radius = (node) => 7 + 15 * Math.sqrt((node.pagerank || 0) / maxRank);
    const label = (node) => {
      const text = String(node.title || node.id || "");
      return text.length > 22 ? text.slice(0, 21) + "…" : text;
    };
    const edges = links
      .map((edge) => {
        const a = positions.get(edge.source);
        const b = positions.get(edge.target);
        if (!a || !b) return "";
        return `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"
          stroke="#4a5568" stroke-width="${(0.7 + (edge.weight || 1) * 0.7).toFixed(2)}" marker-end="url(#arrow)" opacity="0.55">
          <title>${escapeHtml(edge.evidence || "")}</title></line>`;
      })
      .join("");
    const circles = nodes
      .map((node) => {
        const pos = positions.get(node.id);
        const r = radius(node);
        return `<g class="gnode" data-node="${escapeHtml(node.id)}" style="cursor:pointer">
          <circle cx="${pos.x.toFixed(1)}" cy="${pos.y.toFixed(1)}" r="${r.toFixed(1)}"
            fill="${ROLE_COLOR[node.role] || ROLE_COLOR.normal}" stroke="#0d1117" stroke-width="1.2" opacity="0.92">
            <title>${escapeHtml(label(node))}\nPageRank ${(node.pagerank || 0).toFixed(4)} · 入度 ${node.indegree || 0} · ${ROLE_LABEL[node.role] || node.role}</title>
          </circle>
          <text x="${(pos.x > cx ? pos.x - r - 4 : pos.x + r + 4).toFixed(1)}" y="${(pos.y + 3).toFixed(1)}" text-anchor="${pos.x > cx ? "end" : "start"}" font-size="10.5" fill="#c8d2e0">${escapeHtml(label(node))}</text>
        </g>`;
      })
      .join("");
    $("#g-canvas", host).innerHTML = `<svg class="graph-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#6b7688"/></marker></defs>
      ${edges}${circles}</svg>`;
    const metrics = graph.metrics || {};
    $("#g-metrics", host).textContent = `${metrics.nodes || nodes.length} 节点 · ${metrics.edges || links.length} 边 · 密度 ${metrics.density ?? "—"} · 基石 ${metrics.foundation_count ?? 0}`;
    $("#g-insights", host).innerHTML = `
      <div class="card"><b>图论洞察</b>
        <ul style="margin-top:8px">${(graph.insights || [])
          .map((item) => `<li>${escapeHtml(typeof item === "string" ? item : item.text || item.summary || JSON.stringify(item))}</li>`)
          .join("") || `<li class="dim">暂无洞察</li>`}</ul>
        <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">
          ${(graph.foundation || [])
            .map((pid) => {
              const node = nodes.find((item) => item.id === pid);
              return `<button class="pill" data-open-paper="${escapeHtml(pid)}">基石 · ${escapeHtml((node?.title || pid).slice(0, 26))}</button>`;
            })
            .join("")}
        </div></div>`;
    host.querySelectorAll("[data-node]").forEach((group) =>
      group.addEventListener("click", () => {
        const node = nodes.find((item) => item.id === group.dataset.node);
        if (!node) return;
        openModal(`<h3>${escapeHtml(node.title || node.id)}</h3>
          <p class="dim">${ROLE_LABEL[node.role] || node.role} · 分组 ${escapeHtml(node.group_label || node.group || "")}${node.year ? " · " + node.year : ""}</p>
          <table class="compare-table"><tbody>
            <tr><th>PageRank</th><td>${(node.pagerank || 0).toFixed(5)}</td></tr>
            <tr><th>入度 / 出度</th><td>${node.indegree || 0} / ${node.outdegree || 0}</td></tr>
            <tr><th>介数中心性</th><td>${(node.betweenness || 0).toFixed(5)}</td></tr>
            <tr><th>聚类系数</th><td>${node.clustering ?? 0}</td></tr>
          </tbody></table>
          <div class="row" style="margin-top:12px"><button class="btn primary" onclick="location.hash='#reader/${escapeHtml(node.id)}'">打开阅读器</button></div>`);
      })
    );
    host.querySelectorAll("[data-open-paper]").forEach((button) =>
      button.addEventListener("click", () => (location.hash = `#reader/${button.dataset.openPaper}`))
    );
  };

  $("#g-all", host).addEventListener("click", () => {
    host.querySelectorAll("#g-pick input").forEach((box) => (box.checked = true));
  });
  $("#g-build", host).addEventListener("click", async (event) => {
    const ids = picked(host, "#g-pick");
    if (ids.length < 2) return toast("请至少选择两篇论文", "err");
    setBusy(event.currentTarget, true, "解析引用关系…");
    try {
      const graph = await api.graph(ids);
      draw(graph);
      history.replaceState(null, "", `#graph/${graph.graph_id}`);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });
  $("#g-survey", host).addEventListener("click", async (event) => {
    const ids = picked(host, "#g-pick");
    if (ids.length < 2) return toast("请至少选择两篇论文", "err");
    setBusy(event.currentTarget, true, "生成综述…");
    try {
      const result = await api.survey({ paper_ids: ids, title: $("#g-title", host).value.trim() || null });
      $("#g-survey-result", host).innerHTML = `
        <div class="card"><div class="row" style="justify-content:space-between"><b>${escapeHtml(result.title || "领域综述")}</b>
          <span class="badge ${result.degraded ? "warn" : "ok"}">${escapeHtml(result.provider || "offline")}${result.degraded ? " · 降级" : ""}</span></div>
          <div class="survey-body" style="margin-top:10px">${miniMarkdown(result.markdown || "")}</div>
          <div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">${(result.future_directions || [])
            .map(
              (item) => `<span class="badge ${item.status === "solved" ? "" : "ok"}" title="${escapeHtml(item.evidence || "")}">
                ${escapeHtml(item.direction || item.text || "")}${item.status === "solved" ? "（已解决）" : "（待探索）"}</span>`
            )
            .join("")}</div>
          ${(result.excluded_ideas || []).length ? `<div class="dim" style="font-size:11.5px;margin-top:8px">已排除 ${result.excluded_ideas.length} 个已被解决的旧方向</div>` : ""}
        </div>`;
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });
  const exportGraph = async (fmt) => {
    if (!current) return toast("请先构建图谱", "err");
    try {
      const result = await api.exportGraph(current.graph_id || id, fmt);
      openModal(`<h3>${fmt.toUpperCase()} 导出</h3>${scriptBlock(result.content || "", fmt)}`);
      bindCopy(document.getElementById("modal"));
    } catch (err) {
      toast(err.message, "err");
    }
  };
  $("#g-export-mmd", host).addEventListener("click", () => exportGraph("mermaid"));
  $("#g-export-dot", host).addEventListener("click", () => exportGraph("dot"));

  if (id) {
    try {
      const graph = await api.getGraph(id);
      draw(graph);
    } catch {
      /* 忽略历史链接失效 */
    }
  } else if (location.hash.includes("auto=1") && papers.length >= 2) {
    host.querySelectorAll("#g-pick input").forEach((box) => (box.checked = true));
    setTimeout(() => $("#g-build", host)?.click(), 80);
  }
}

/* --------------------------------------------------------------- writing */

export async function renderWriting(host) {
  const data = await api.listPapers({ limit: 100 });
  const papers = data.items || [];
  host.innerHTML = `
    <div class="view-head"><div><h1>写作 Copilot</h1>
      <p>参考文献只来自本地真实文献库；引用编号双向校验，模型无法编造条目</p></div></div>
    <div class="split two">
      <section>
        <div class="card">
          <b>论文框架</b>
          <textarea class="input" id="w-idea" rows="4" placeholder="研究 idea，例如：把 LoRA 用到视觉 Transformer 的注意力层，降低微调显存"
            style="margin-top:8px;resize:vertical"></textarea>
          <div class="row" style="margin-top:8px"><input class="input" id="w-title" placeholder="标题（可选）" />
            <select class="input" id="w-lang" style="max-width:120px"><option value="zh">中文</option><option value="en">英文</option></select></div>
          <div class="dim" style="font-size:11.5px;margin-top:6px">文献范围（不选则按 idea 相关性自动挑选最多 12 篇）</div>
          <div style="max-height:150px;overflow:auto;margin-top:6px">${paperPicker(papers, "w-pick", "")}</div>
          <button class="btn primary" id="w-draft" style="margin-top:10px">生成框架</button>
        </div>
        <div class="card" style="margin-top:12px">
          <b>架构拓扑图脚本</b>
          <div class="row" style="margin-top:8px">
            <select class="input" id="w-kind" style="max-width:150px">
              <option value="mermaid">Mermaid</option><option value="graphviz">Graphviz</option>
              <option value="tikz">TikZ</option><option value="matplotlib">Matplotlib</option></select>
            <button class="btn" id="w-diagram">生成脚本</button></div>
          <textarea class="input" id="w-edges" rows="4" placeholder="每行一条边，例如：Input -> Encoder&#10;Encoder -> Decoder&#10;Decoder -> Output"
            style="margin-top:8px;resize:vertical"></textarea>
        </div>
        <div class="card" style="margin-top:12px">
          <b>学术翻译</b>
          <div class="row" style="margin-top:8px"><select class="input" id="w-source" style="max-width:120px">
              <option value="zh">中 → 英</option><option value="en">英 → 中</option></select>
            <button class="btn" id="w-translate">翻译</button></div>
          <textarea class="input" id="w-text" rows="4" placeholder="粘贴摘要或正文，公式/引用/数字会被保护"
            style="margin-top:8px;resize:vertical"></textarea>
        </div>
      </section>
      <section>
        <div id="w-result"></div>
        <div id="w-diagram-result" style="margin-top:12px"></div>
        <div id="w-translate-result" style="margin-top:12px"></div>
      </section>
    </div>`;

  $("#w-draft", host).addEventListener("click", async (event) => {
    const idea = $("#w-idea", host).value.trim();
    if (idea.length < 4) return toast("请写清楚研究 idea", "err");
    setBusy(event.currentTarget, true, "生成框架…");
    try {
      const ids = picked(host, "#w-pick");
      const result = await api.draftOutline({
        idea,
        paper_ids: ids.length ? ids : null,
        title: $("#w-title", host).value.trim() || null,
        language: $("#w-lang", host).value,
      });
      $("#w-result", host).innerHTML = `
        <div class="card">
          <div class="row" style="justify-content:space-between"><b>${escapeHtml(result.title || "论文框架")}</b>
            <span class="badge ${result.checks?.reference_check ? "ok" : "warn"}">引用校验 ${result.checks?.reference_check ? "通过" : "需检查"}</span></div>
          <div class="dim" style="font-size:11.5px;margin-top:4px">References ${result.references?.length || 0} 条 · 全部来自本地库${result.degraded ? " · 离线引擎" : ""}</div>
          <div class="survey-body" style="margin-top:10px">${miniMarkdown(result.markdown || "")}</div>
          <div class="row" style="margin-top:10px;gap:8px">
            <button class="btn small" id="w-export-md">导出 Markdown</button>
            <button class="btn small ghost" id="w-export-txt">导出 TXT</button>
            <button class="btn small ghost" data-copy="${encodeURIComponent(result.markdown || "")}">复制全文</button>
          </div>
        </div>`;
      bindCopy(host);
      ["md", "txt"].forEach((fmt) =>
        $(`#w-export-${fmt}`, host)?.addEventListener("click", async (event) => {
          setBusy(event.currentTarget, true, "导出…");
          try {
            const file = await api.exportOutline(result.outline_id, fmt);
            toast(`已导出：${file.path || file.filename || ""}`, "ok");
          } catch (err) {
            toast(err.message, "err");
          } finally {
            setBusy(event.currentTarget, false);
          }
        })
      );
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });

  $("#w-diagram", host).addEventListener("click", async (event) => {
    const raw = $("#w-edges", host).value.trim();
    if (!raw) return toast("请填写至少一条边", "err");
    const nodes = new Map();
    const edges = raw
      .split(/\n+/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const parts = line.split(/->|→/).map((part) => part.trim()).filter(Boolean);
        if (parts.length < 2) return null;
        parts.forEach((part) => nodes.set(part, { id: part, label: part }));
        return { source: parts[0], target: parts[1] };
      })
      .filter(Boolean);
    if (!edges.length) return toast("边格式应为 A -> B", "err");
    setBusy(event.currentTarget, true, "生成…");
    try {
      const result = await api.diagram({
        kind: $("#w-kind", host).value,
        nodes: Array.from(nodes.values()),
        edges,
      });
      $("#w-diagram-result", host).innerHTML = `<div class="card"><b>${escapeHtml(result.kind || "")} 脚本</b>
        <div class="dim" style="font-size:11.5px">${result.nodes || 0} 节点 / ${result.edges || 0} 边 · ${escapeHtml(result.filename || "")}</div>
        ${scriptBlock(result.script || "", "可直接粘贴到论文")}</div>`;
      bindCopy(host);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });

  $("#w-translate", host).addEventListener("click", async (event) => {
    const text = $("#w-text", host).value.trim();
    if (!text) return toast("请输入文本", "err");
    setBusy(event.currentTarget, true, "翻译…");
    try {
      const result = await api.translateAcademic({ text, source: $("#w-source", host).value, style: "paper" });
      $("#w-translate-result", host).innerHTML = `<div class="card"><div class="row" style="justify-content:space-between">
          <b>${escapeHtml(result.source || "")} → ${escapeHtml(result.target || "")}</b>
          <span class="badge ${result.degraded ? "warn" : "ok"}">${escapeHtml(result.provider || "offline")}${result.degraded ? " · 降级" : ""}</span></div>
        <p style="margin-top:10px;line-height:1.7">${escapeHtml(result.text || result.translation || "")}</p>
        <div class="dim" style="font-size:11.5px">术语覆盖 ${pct(result.coverage)}${result.notes?.length ? " · " + escapeHtml(result.notes.join("；")) : ""}</div></div>`;
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy(event.currentTarget, false);
    }
  });
}

/* --------------------------------------------------------------- dataviz */

export async function renderDataviz(host) {
  const datasets = await api.datasets();
  host.innerHTML = `
    <div class="view-head"><div><h1>数据可视化</h1>
      <p>CSV → 确定性 SVG 图表 + 学术 Figure Caption + 可复现 matplotlib 脚本</p></div></div>
    <div class="split two">
      <aside>
        <div class="card">
          <b>CSV 数据集</b>
          <label class="drop" id="v-drop" style="margin-top:10px">
            <input type="file" id="v-file" accept=".csv,text/csv" hidden />
            <span>拖拽 CSV 到此处，或点击选择</span>
            <span class="dim" style="font-size:11px">支持 utf-8 / gbk，自动推断数值/文本/日期</span>
          </label>
          <div id="v-datasets" style="margin-top:10px"></div>
        </div>
      </aside>
      <section>
        <div class="card" id="v-preview"><div class="dim">上传或选择数据集后，在这里配置图表</div></div>
        <div id="v-chart" style="margin-top:12px"></div>
      </section>
    </div>`;

  let current = null;

  const renderDatasets = (items) => {
    $("#v-datasets", host).innerHTML = (items || [])
      .map(
        (item) => `<button class="pill" data-dataset="${escapeHtml(item.id)}" style="display:block;width:100%;text-align:left;margin-bottom:6px">
          ${escapeHtml(item.name || item.id)}<br/><span class="dim" style="font-size:11px">${item.row_count || 0} 行 × ${(item.columns || []).length} 列</span></button>`
      )
      .join("") || `<div class="dim">暂无数据集</div>`;
    $("#v-datasets", host)
      .querySelectorAll("[data-dataset]")
      .forEach((button) =>
        button.addEventListener("click", async () => {
          const dataset = await api.getDataset(button.dataset.dataset);
          selectDataset(dataset);
        })
      );
  };

  const selectDataset = (dataset) => {
    current = dataset;
    const columns = dataset.columns || [];
    const header = columns.map((col) => (typeof col === "string" ? col : col.name));
    const preview = dataset.preview || [];
    const rows = preview.map((row) => header.map((name) => (row && typeof row === "object" ? row[name] : "")));
    $("#v-preview", host).innerHTML = `
      <div class="row" style="justify-content:space-between"><b>${escapeHtml(dataset.name || dataset.id)}</b>
        <span class="badge">${dataset.row_count || 0} 行 × ${header.length} 列</span></div>
      <div style="overflow:auto;margin-top:10px"><table class="compare-table">
        <thead><tr>${header.map((name) => `<th>${escapeHtml(name)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell ?? "")}</td>`).join("")}</tr>`).join("")}</tbody>
      </table></div>
      <div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">
        <select class="input" id="v-kind" style="max-width:150px">
          <option value="line">折线图</option><option value="bar">柱状图</option><option value="scatter">散点图</option>
          <option value="radar3d">三维雷达</option><option value="table">表格</option></select>
        <select class="input" id="v-x" style="max-width:150px">${header.map((name) => `<option>${escapeHtml(name)}</option>`).join("")}</select>
        <select class="input" id="v-y" style="max-width:150px">${header.slice(1).map((name) => `<option>${escapeHtml(name)}</option>`).join("")}</select>
        <button class="btn primary" id="v-build">生成图表</button>
      </div>`;
    $("#v-build", host).addEventListener("click", async (event) => {
      setBusy(event.currentTarget, true, "渲染…");
      try {
        const result = await api.buildChart({
          dataset_id: dataset.id,
          kind: $("#v-kind", host).value,
          x: $("#v-x", host).value,
          y: $("#v-y", host).value,
        });
        const svg = result.svg || "";
        $("#v-chart", host).innerHTML = `
          <div class="card">
            <div class="row" style="justify-content:space-between"><b>${escapeHtml(result.title || "图表")}</b>
              <span class="badge">${escapeHtml(result.kind || "")} · ${escapeHtml(result.provider || "")}</span></div>
            <div class="chart-wrap" style="margin-top:10px">${svg || `<img src="/api/dataviz/charts/${encodeURIComponent(result.id)}.svg" alt="chart" />`}</div>
            <div class="caption" style="margin-top:10px"><b>Figure Caption</b><p>${escapeHtml(result.caption || "")}</p></div>
            ${scriptBlock(result.script || "", "matplotlib 复现脚本")}
          </div>`;
        bindCopy(host);
      } catch (err) {
        toast(err.message, "err");
      } finally {
        setBusy(event.currentTarget, false);
      }
    });
  };

  const upload = async (file) => {
    if (!file) return;
    try {
      const dataset = await api.uploadCsv(file);
      const latest = await api.datasets();
      renderDatasets(latest);
      selectDataset(await api.getDataset(dataset.id));
      toast("CSV 已解析", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  };

  const drop = $("#v-drop", host);
  $("#v-file", host).addEventListener("change", (event) => upload(event.target.files?.[0]));
  ["dragenter", "dragover"].forEach((name) =>
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("on");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.remove("on");
    })
  );
  drop.addEventListener("drop", (event) => upload(event.dataTransfer?.files?.[0]));

  renderDatasets(datasets);
  // 默认选中第一个数据集，便于直接配置图表（#dataviz?auto=1 时自动渲染一张折线图，用于演示与截图）
  if (datasets && datasets.length) {
    const first = await api.getDataset(datasets[0].id);
    selectDataset(first);
    if (location.hash.includes("auto=1")) {
      setTimeout(() => $("#v-build", host)?.click(), 80);
    }
  }
}
