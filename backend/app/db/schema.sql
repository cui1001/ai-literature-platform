-- AgentLit 元数据模式（SQLite）
-- 约定：所有表均为「可重建」；业务实体 ID 由内容哈希派生（见 core/hashing.py）。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- 文献
CREATE TABLE IF NOT EXISTS papers (
    id              TEXT PRIMARY KEY,
    sha256          TEXT NOT NULL UNIQUE,
    filename        TEXT,
    file_path       TEXT,
    size_bytes      INTEGER DEFAULT 0,
    title           TEXT,
    title_norm      TEXT,
    authors         TEXT,              -- JSON array
    affiliations    TEXT,              -- JSON array
    abstract        TEXT,
    keywords        TEXT,              -- JSON array
    year            INTEGER,
    venue           TEXT,
    doi             TEXT,
    arxiv_id        TEXT,
    arxiv_version   TEXT,
    pages           INTEGER DEFAULT 0,
    ocr_used        INTEGER DEFAULT 0,
    ocr_engine      TEXT,
    layout          TEXT,              -- single | double | mixed
    parse_status    TEXT DEFAULT 'pending',
    parse_quality   TEXT,              -- JSON：score / warnings / field_hits
    version_group   TEXT,
    version_label   TEXT,
    is_latest       INTEGER DEFAULT 1,
    doc_simhash     INTEGER DEFAULT 0,
    source          TEXT DEFAULT 'upload',
    lang            TEXT DEFAULT 'en',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_papers_title_norm ON papers(title_norm);
CREATE INDEX IF NOT EXISTS idx_papers_group ON papers(version_group);

-- ---------------------------------------------------------------- 结构
CREATE TABLE IF NOT EXISTS sections (
    paper_id    TEXT NOT NULL,
    sid         TEXT NOT NULL,
    level       INTEGER NOT NULL,
    number      TEXT,
    title       TEXT NOT NULL,
    page        INTEGER DEFAULT 1,
    end_page    INTEGER DEFAULT 1,
    path        TEXT,
    appendix    INTEGER DEFAULT 0,
    block_ids   TEXT,                  -- JSON array
    order_idx   INTEGER DEFAULT 0,
    PRIMARY KEY (paper_id, sid)
);
CREATE INDEX IF NOT EXISTS idx_sections_paper ON sections(paper_id, order_idx);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    paper_id    TEXT NOT NULL,
    sid         TEXT,
    page        INTEGER DEFAULT 1,
    x0          REAL DEFAULT 0,
    y0          REAL DEFAULT 0,
    x1          REAL DEFAULT 0,
    y1          REAL DEFAULT 0,
    text        TEXT NOT NULL,
    order_idx   INTEGER DEFAULT 0,
    token_len   INTEGER DEFAULT 0,
    kind        TEXT DEFAULT 'paragraph'
);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id, order_idx);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id    TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vec         BLOB NOT NULL,
    updated_at  TEXT
);

-- ---------------------------------------------------------------- 引用
CREATE TABLE IF NOT EXISTS refs (
    paper_id        TEXT NOT NULL,
    ref_idx         INTEGER NOT NULL,
    raw             TEXT NOT NULL,
    title           TEXT,
    authors         TEXT,              -- JSON array
    year            INTEGER,
    venue           TEXT,
    doi             TEXT,
    arxiv_id        TEXT,
    url             TEXT,
    matched_paper   TEXT,
    match_score     REAL DEFAULT 0,
    value_score     REAL DEFAULT 0,    -- 引用含金量
    value_label     TEXT,              -- core | useful | perfunctory
    value_reason    TEXT,
    PRIMARY KEY (paper_id, ref_idx)
);
CREATE INDEX IF NOT EXISTS idx_refs_title ON refs(title);

CREATE TABLE IF NOT EXISTS edges (
    src         TEXT NOT NULL,
    dst         TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'cites',
    weight      REAL DEFAULT 1,
    evidence    TEXT,
    PRIMARY KEY (src, dst, kind)
);

-- ---------------------------------------------------------------- 阅读 / 翻译
CREATE TABLE IF NOT EXISTS translations (
    id          TEXT PRIMARY KEY,
    paper_id    TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT 'zh',
    scope       TEXT NOT NULL DEFAULT 'block',
    anchor      TEXT,
    src_hash    TEXT NOT NULL,
    src_text    TEXT NOT NULL,
    text        TEXT NOT NULL,
    provider    TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_translations_paper ON translations(paper_id, lang);

-- ---------------------------------------------------------------- 问答
CREATE TABLE IF NOT EXISTS qa_log (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    user_id     TEXT,
    question    TEXT NOT NULL,
    paper_ids   TEXT,
    answer      TEXT,
    citations   TEXT,                  -- JSON
    verified    INTEGER DEFAULT 0,
    confidence  REAL DEFAULT 0,
    provider    TEXT,
    mode        TEXT DEFAULT 'single',
    trace       TEXT
);

-- ---------------------------------------------------------------- 发现 / 推荐
CREATE TABLE IF NOT EXISTS discovery (
    arxiv_id        TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    authors         TEXT,
    abstract        TEXT,
    comments        TEXT,
    categories      TEXT,
    published       TEXT,
    updated         TEXT,
    url             TEXT,
    github          TEXT,
    pdf_url         TEXT,
    imported_paper  TEXT,
    fetched_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovery_published ON discovery(published DESC);

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    interests   TEXT,                  -- JSON array
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    user_id     TEXT NOT NULL,
    target      TEXT NOT NULL,
    action      TEXT NOT NULL,         -- like | dislike | read | save
    weight      REAL DEFAULT 1,
    ts          TEXT NOT NULL,
    PRIMARY KEY (user_id, target, action)
);

CREATE TABLE IF NOT EXISTS track_topics (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    query       TEXT NOT NULL,
    enabled     INTEGER DEFAULT 1,
    last_run    TEXT,
    hits        INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS track_state (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT
);

-- ---------------------------------------------------------------- Agent 产物
CREATE TABLE IF NOT EXISTS graphs (
    id          TEXT PRIMARY KEY,
    paper_ids   TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS surveys (
    id          TEXT PRIMARY KEY,
    graph_id    TEXT,
    paper_ids   TEXT,
    title       TEXT,
    markdown    TEXT NOT NULL,
    sections    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outlines (
    id          TEXT PRIMARY KEY,
    idea        TEXT NOT NULL,
    paper_ids   TEXT,
    markdown    TEXT NOT NULL,
    sections    TEXT,
    provider    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    columns     TEXT,                  -- JSON array of {name,type}
    rows        TEXT,                  -- JSON array of arrays
    path        TEXT,
    size_bytes  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS charts (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    spec        TEXT,                  -- JSON
    svg         TEXT NOT NULL,
    caption     TEXT,
    script      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    payload     TEXT,
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    module      TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts DESC);
