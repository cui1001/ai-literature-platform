"""文本工具：分词、句子切分、相似度、公式/引用识别等。

全部为纯 Python 实现，避免 jieba / sklearn 等不可安装依赖；
中文按「单字 + 二元组」混合切分，英文按词切分，兼顾召回与跨语言稳健性。
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from collections import Counter
from typing import Iterable, Sequence

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-']*|\d+(?:\.\d+)?%?|[\u4e00-\u9fff]")
SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])\s*|(?<=[.!?])\s+(?=[A-Z0-9\[\(\"“])")
CITATION_MARK_RE = re.compile(
    r"\[\s*(\d{1,3}(?:\s*[,，\-–]\s*\d{1,3})*)\s*\]"
    r"|\\cite[tp]?\{([^}]{1,200})\}"
)
SECTION_NUM_RE = re.compile(
    r"^(?P<num>(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?)|(?:[A-Z]\.(?:\d{1,2}\.?)?)|(?:[IVX]{1,5}\.?))\s+"
)
FORMULA_HINT_RE = re.compile(r"[=∑∏∫√≤≥≈≠∞∂∇α-ωΑ-Ω]|\^|_\{|\\frac|\\sum|\\alpha|=\s*[-0-9a-zA-Z(\[]")
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
URL_RE = re.compile(r"https?://[^\s)>\]]+")
GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/[\w.\-]+/[\w.\-]+", re.I)
YEAR_RE = re.compile(r"(?:19|20)\d{2}")

STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with", "by", "is", "are",
    "we", "this", "that", "as", "at", "from", "be", "can", "our", "it", "its", "these", "those",
    "which", "such", "than", "then", "their", "has", "have", "not", "but", "also", "more", "most",
    "的", "了", "和", "与", "在", "是", "对", "被", "将", "把", "而", "及", "等", "中", "为",
}

_CN_NUM = "零一二三四五六七八九十"


def normalize_ws(text: str) -> str:
    """折叠空白、统一全角标点、去掉软连字符。"""
    if not text:
        return ""
    text = text.replace("\u00ad", "").replace("\ufb00", "ff").replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def normalize_title(title: str) -> str:
    """标题规范化：用于去重与匹配。"""
    t = normalize_ws(title).lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _stem_en(token: str) -> str:
    """极简英文词干化：只处理复数，避免 GPUs/parameters 这类查询-原文不匹配。"""
    if len(token) <= 3 or not token.endswith("s") or not token.isascii() or not token.isalpha():
        return token
    if token.endswith(("ss", "us", "is", "as", "os")):
        return token
    return token[:-1]


def tokens(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """混合分词：英文词 / 数字 / 中文单字 + 中文二元组。"""
    lowered = text.lower()
    raw = WORD_RE.findall(lowered)
    out: list[str] = []
    for token in raw:
        token = _stem_en(token)
        if not keep_stopwords and token in STOPWORDS:
            continue
        out.append(token)
    # 中文二元组，提升短语召回
    cn_chars = [c for c in lowered if CJK_RE.match(c)]
    for i in range(len(cn_chars) - 1):
        bigram = cn_chars[i] + cn_chars[i + 1]
        if not keep_stopwords or bigram not in STOPWORDS:
            out.append(bigram)
    return out


def token_set(text: str) -> set[str]:
    return set(tokens(text))


def sentence_split(text: str) -> list[str]:
    text = normalize_ws(text)
    if not text:
        return []
    parts = [p.strip() for p in SENT_SPLIT_RE.split(text) if p and p.strip()]
    return parts


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cosine_counter(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def text_similarity(a: str, b: str) -> float:
    """词袋余弦 + 字符级 SequenceMatcher 的加权混合，0~1。"""
    if not a or not b:
        return 0.0
    bag = cosine_counter(Counter(tokens(a)), Counter(tokens(b)))
    seq = difflib.SequenceMatcher(None, a[:2000].lower(), b[:2000].lower()).ratio()
    return round(0.65 * bag + 0.35 * seq, 4)


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - len(suffix))].rstrip() + suffix


def dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text.strip().lower()).strip("-")
    return slug[:60] or fallback


# ---------------------------------------------------------------- 引用解析

def split_citation_numbers(marker: str) -> list[int]:
    """把 ``[11]`` / ``[3,5-7]`` / ``\\cite{a,b}`` 里的编号展开成整数列表。"""
    numbers: list[int] = []
    for part in re.split(r"[,，]", marker):
        part = part.strip()
        if not part:
            continue
        rng = re.split(r"[-–—~]", part)
        if len(rng) == 2 and all(x.strip().isdigit() for x in rng):
            lo, hi = int(rng[0]), int(rng[1])
            if 0 < hi - lo <= 60:
                numbers.extend(range(lo, hi + 1))
                continue
        if part.isdigit():
            numbers.append(int(part))
    return sorted(set(numbers))


def extract_citations(text: str) -> list[int]:
    out: list[int] = []
    for m in CITATION_MARK_RE.finditer(text or ""):
        marker = m.group(1) or ""
        if marker:
            out.extend(split_citation_numbers(marker))
    return sorted(set(n for n in out if 0 < n < 500))


def has_formula_hint(text: str) -> bool:
    compact = (text or "").strip()
    if len(compact) < 3:
        return False
    if compact.startswith("$$") and compact.endswith("$$"):
        return True
    symbols = FORMULA_HINT_RE.findall(compact)
    return len(symbols) >= 2 and len(compact) < 600


def looks_like_heading(text: str) -> tuple[bool, int, str]:
    """根据编号模式判断标题层级，返回 (is_heading, level, number)。"""
    compact = normalize_ws(text)
    if not compact or len(compact) > 160:
        return False, 0, ""
    m = SECTION_NUM_RE.match(compact)
    if m:
        num = m.group("num").strip()
        depth = num.count(".") + (0 if num.endswith(".") else 1)
        if re.match(r"^[IVX]+\.?$", num):
            depth = 1
        return True, max(1, min(depth, 3)), num.rstrip(".")
    if re.match(r"^(abstract|introduction|related work|method|methods|experiments?|results?|"
                r"discussion|conclusion|conclusions|references|acknowledg|appendix)\b", compact, re.I):
        return True, 1, ""
    if re.match(r"^(摘要|引言|相关工作|方法|实验|结果|讨论|结论|参考文献|附录)", compact):
        return True, 1, ""
    return False, 0, ""


# ---------------------------------------------------------------- 数值与统计

def extract_numbers(text: str) -> list[float]:
    out: list[float] = []
    for m in re.finditer(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(%|k|K|M|B)?", text):
        value = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit == "k":
            value *= 1_000
        elif unit == "m":
            value *= 1_000_000
        elif unit == "b":
            value *= 1_000_000_000
        out.append(value)
    return out


def pct_change(before: float, after: float) -> float:
    if not before:
        return 0.0
    return (after - before) / abs(before) * 100.0


def roman_to_int(value: str) -> int:
    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    prev = 0
    for ch in reversed(value.upper()):
        cur = table.get(ch, 0)
        total += -cur if cur < prev else cur
        prev = max(prev, cur)
    return total


def fmt_num(value: float) -> str:
    """确定性数字格式化：避免 1.0e-05 之类的意外表示。"""
    if value != value or value in (float("inf"), float("-inf")):
        return "0"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value))}"
    if abs(value) < 0.01:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")


def strip_math_delimiters(text: str) -> str:
    t = (text or "").strip()
    for left, right in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if t.startswith(left) and t.endswith(right) and len(t) > len(left) + len(right):
            return t[len(left) : len(t) - len(right)].strip()
    return t


def keyword_candidates(text: str, limit: int = 12) -> list[str]:
    """关键词候选：过滤停用词后按频次与长度打分。"""
    counter = Counter(t for t in tokens(text) if len(t) > 2 and t not in STOPWORDS)
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:limit]]


def centered(text: str, width: int = 100) -> str:
    return text.center(width)


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    def esc(cell) -> str:
        return str(cell).replace("|", "\\|").replace("\n", " ").strip()

    lines = ["| " + " | ".join(esc(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(esc(c) for c in row) + " |")
    return "\n".join(lines)
