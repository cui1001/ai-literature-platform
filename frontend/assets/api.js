/* 科研助手 Agent —— 前端 API 客户端（无构建、无依赖） */

const BASE = "";

async function request(path, options = {}) {
  const res = await fetch(BASE + path, options);
  const type = res.headers.get("content-type") || "";
  let data = null;
  if (type.includes("application/json")) {
    data = await res.json();
  } else {
    data = await res.text();
  }
  if (!res.ok) {
    const message = (data && data.error && data.error.message) || (data && data.detail) || res.statusText;
    const error = new Error(message);
    error.status = res.status;
    error.payload = data;
    throw error;
  }
  return data;
}

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request("/api/health"),
  status: () => request("/api/system/status"),

  listPapers(params = {}) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.set(k, v);
    });
    return request(`/api/papers?${qs.toString()}`);
  },
  getPaper(id) {
    return request(`/api/papers/${encodeURIComponent(id)}`);
  },
  deletePaper(id) {
    return request(`/api/papers/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  upload(file, force = false) {
    return request(`/api/papers/upload?filename=${encodeURIComponent(file.name)}&force=${force}`, {
      method: "POST",
      headers: { "Content-Type": "application/pdf" },
      body: file,
    });
  },
  async uploadMany(files) {
    const payload = { files: [] };
    for (const file of files.slice(0, 30)) {
      payload.files.push({ filename: file.name, content: await toBase64(file) });
    }
    return request("/api/papers/batch", json(payload));
  },
  versions(id) {
    return request(`/api/papers/${encodeURIComponent(id)}/versions`);
  },
  compare(a, b) {
    return request(`/api/papers/${encodeURIComponent(a)}/compare?other=${encodeURIComponent(b)}`);
  },
  document(id, lang = "en", translations = true) {
    return request(`/api/papers/${encodeURIComponent(id)}/document?lang=${lang}&translations=${translations}`);
  },
  translate(id, lang = "zh", blockIds = null) {
    const payload = { lang };
    if (blockIds && blockIds.length) payload.block_ids = blockIds;
    return request(`/api/papers/${encodeURIComponent(id)}/translate`, json(payload));
  },
  exportUrl(id, fmt, lang = "zh", translate = true) {
    return `/api/papers/${encodeURIComponent(id)}/export/${fmt}?lang=${lang}&translate=${translate ? "true" : "false"}`;
  },
  async exportFile(id, fmt, lang = "zh", translate = true) {
    const res = await fetch(BASE + this.exportUrl(id, fmt, lang, translate));
    if (!res.ok) {
      let message = `导出失败（HTTP ${res.status}）`;
      try {
        const data = await res.json();
        message = data.message || data.detail || message;
      } catch (_) {
        /* 非 JSON 错误体，保留默认文案 */
      }
      throw new Error(message);
    }
    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
    const filename = match ? decodeURIComponent(match[1]) : `paper-${id}.${fmt}`;
    const translated = Number(res.headers.get("x-translated-blocks") || 0);
    const translatable = Number(res.headers.get("x-translatable-blocks") || 0);
    const partial = res.headers.get("x-translation-partial") === "1";
    return { blob, filename, translated, translatable, partial };
  },
  pageUrl(id, page) {
    return `/api/papers/${encodeURIComponent(id)}/pages/${page}.png`;
  },

  ask(payload) {
    return request("/api/qa/ask", json(payload));
  },
  compareQa(payload) {
    return request("/api/qa/compare", json(payload));
  },
  history() {
    return request("/api/qa/history");
  },

  discoverSearch(payload) {
    return request("/api/discovery/search", json(payload));
  },
  discoverImport(payload) {
    return request("/api/discovery/import", json(payload));
  },
  discoverRecommend() {
    return request("/api/discovery/recommend");
  },
  discoverTrack(payload) {
    return request("/api/discovery/track", json(payload));
  },
  discoverFeedback(payload) {
    return request("/api/discovery/feedback", json(payload));
  },
  discoverRefs(id) {
    return request(`/api/discovery/references/${encodeURIComponent(id)}`, { method: "POST" });
  },

  graph(paperIds) {
    return request("/api/graph/build", json({ paper_ids: paperIds }));
  },
  survey(paperIds) {
    return request("/api/graph/survey", json({ paper_ids: paperIds }));
  },
  listGraphs() {
    return request("/api/graph");
  },
  getGraph(id) {
    return request(`/api/graph/${encodeURIComponent(id)}`);
  },
  exportGraph(id, fmt = "mermaid") {
    return request(`/api/graph/${encodeURIComponent(id)}/export?fmt=${encodeURIComponent(fmt)}`);
  },
  listSurveys() {
    return request("/api/graph/surveys");
  },
  getSurvey(id) {
    return request(`/api/graph/surveys/${encodeURIComponent(id)}`);
  },

  draftOutline(payload) {
    return request("/api/writing/outline", json(payload));
  },
  exportOutline(id, fmt = "md") {
    return request(`/api/writing/outlines/${encodeURIComponent(id)}/export?fmt=${encodeURIComponent(fmt)}`, {
      method: "POST",
    });
  },
  diagram(payload) {
    return request("/api/writing/diagram", json(payload));
  },
  translateAcademic(payload) {
    return request("/api/writing/translate", json(payload));
  },

  datasets() {
    return request("/api/dataviz/datasets");
  },
  getDataset(id) {
    return request(`/api/dataviz/datasets/${encodeURIComponent(id)}`);
  },
  uploadCsv(file) {
    return request(`/api/dataviz/datasets?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": "text/csv" },
      body: file,
    });
  },
  buildChart(payload) {
    return request("/api/dataviz/charts", json(payload));
  },
  seed() {
    return request("/api/demo/seed", { method: "POST" });
  },
};

function toBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export { toBase64 };
