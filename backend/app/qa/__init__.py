"""问答与跨文献对比层。"""

from .citations import Citation, build_citations, evidence_payload, link_citations, parse_markers, unique_sources
from .compare import DEFAULT_KEYS, DIMENSIONS, ComparisonResult, compare_papers, select_dimensions
from .service import QAResult, answer_question, history, run_task_with
from .verifier import VerificationResult, numbers_in, verify_answer

__all__ = [
    "DEFAULT_KEYS",
    "DIMENSIONS",
    "Citation",
    "ComparisonResult",
    "QAResult",
    "VerificationResult",
    "answer_question",
    "build_citations",
    "compare_papers",
    "evidence_payload",
    "history",
    "link_citations",
    "numbers_in",
    "parse_markers",
    "run_task_with",
    "select_dimensions",
    "unique_sources",
    "verify_answer",
]
