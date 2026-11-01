"""学术术语表：离线翻译 / 术语一致性校验 / 关键词中英对照。

术语来源为机器学习与 NLP 领域的通行译法；新增术语只需在 ``TERMS`` 中登记。
"""

from __future__ import annotations

import re
from typing import Iterable

TERMS: dict[str, str] = {
    # 通用研究
    "paper": "论文", "method": "方法", "approach": "方法", "framework": "框架", "model": "模型",
    "architecture": "架构", "algorithm": "算法", "baseline": "基线", "benchmark": "基准",
    "dataset": "数据集", "corpus": "语料库", "experiment": "实验", "evaluation": "评估",
    "performance": "性能", "accuracy": "准确率", "precision": "精确率", "recall": "召回率",
    "f1 score": "F1 值", "loss": "损失", "objective": "目标函数", "optimization": "优化",
    "training": "训练", "inference": "推理", "inference time": "推理时延", "parameter": "参数",
    "hyperparameter": "超参数", "gradient": "梯度", "convergence": "收敛", "ablation": "消融实验",
    "state of the art": "最先进水平", "generalization": "泛化能力", "robustness": "鲁棒性",
    "efficiency": "效率", "scalability": "可扩展性", "reproducibility": "可复现性",
    "contribution": "贡献", "limitation": "局限性", "future work": "未来工作",
    # 结构
    "abstract": "摘要", "introduction": "引言", "related work": "相关工作", "background": "背景",
    "methodology": "方法", "experiments": "实验", "results": "结果", "discussion": "讨论",
    "conclusion": "结论", "appendix": "附录", "reference": "参考文献", "section": "章节",
    "figure": "图", "table": "表", "equation": "公式", "proof": "证明", "theorem": "定理",
    # 网络结构
    "neural network": "神经网络", "transformer": "Transformer", "attention": "注意力",
    "self-attention": "自注意力", "multi-head attention": "多头注意力", "encoder": "编码器",
    "decoder": "解码器", "embedding": "嵌入", "layer normalization": "层归一化",
    "batch normalization": "批归一化", "residual connection": "残差连接", "feed-forward": "前馈",
    "convolution": "卷积", "recurrent": "循环", "pooling": "池化", "dropout": "随机失活",
    "activation function": "激活函数", "softmax": "softmax", "logits": "logits",
    "token": "词元", "tokenization": "分词", "vocabulary": "词表", "context window": "上下文窗口",
    "positional encoding": "位置编码", "sequence length": "序列长度", "batch size": "批大小",
    "learning rate": "学习率", "epoch": "轮次", "checkpoint": "检查点", "fine-tuning": "微调",
    "pre-training": "预训练", "zero-shot": "零样本", "few-shot": "少样本", "in-context learning": "上下文学习",
    "prompt": "提示", "prompting": "提示工程", "instruction tuning": "指令微调",
    "reinforcement learning": "强化学习", "supervised": "有监督", "unsupervised": "无监督",
    "semi-supervised": "半监督", "self-supervised": "自监督", "contrastive learning": "对比学习",
    "knowledge distillation": "知识蒸馏", "quantization": "量化", "pruning": "剪枝",
    # 任务
    "machine translation": "机器翻译", "text classification": "文本分类", "question answering": "问答",
    "named entity recognition": "命名实体识别", "summarization": "摘要生成", "retrieval": "检索",
    "generation": "生成", "reasoning": "推理", "alignment": "对齐", "hallucination": "幻觉",
    "retrieval-augmented generation": "检索增强生成", "information extraction": "信息抽取",
    "semantic parsing": "语义解析", "image classification": "图像分类", "object detection": "目标检测",
    "segmentation": "分割", "speech recognition": "语音识别", "recommendation": "推荐",
    # 统计与实验
    "statistical significance": "统计显著性", "confidence interval": "置信区间",
    "variance": "方差", "mean": "均值", "median": "中位数", "correlation": "相关性",
    "distribution": "分布", "sampling": "采样", "annotation": "标注", "inter-annotator agreement": "标注一致性",
    "human evaluation": "人工评估", "case study": "案例分析", "error analysis": "错误分析",
    "scaling law": "缩放定律", "throughput": "吞吐量", "latency": "时延", "memory footprint": "显存占用",
    "floating point operations": "浮点运算量", "gpu": "GPU", "tensor": "张量",
    # 学术写作
    "we propose": "我们提出", "we present": "我们提出", "we introduce": "我们引入",
    "we demonstrate": "我们验证", "we show that": "我们表明", "in this paper": "本文",
    "experimental results show": "实验结果表明", "compared with": "与……相比",
    "outperforms": "优于", "significantly": "显著地", "however": "然而", "therefore": "因此",
    "moreover": "此外", "furthermore": "进一步地", "in contrast": "相反", "as a result": "因此",
    "to the best of our knowledge": "据我们所知", "substantial": "显著的",
    "effective": "有效的", "promising": "有前景的", "challenging": "具有挑战性的",
}

#: 短语级替换（优先于单词级），按长度降序匹配
PHRASES: list[tuple[str, str]] = [
    ("in this paper, we propose", "本文提出"),
    ("in this paper", "本文"),
    ("to the best of our knowledge", "据我们所知"),
    ("experimental results show that", "实验结果表明"),
    ("experimental results demonstrate that", "实验结果表明"),
    ("compared with the baseline", "与基线相比"),
    ("state-of-the-art performance", "最先进性能"),
    ("large language models", "大语言模型"),
    ("large language model", "大语言模型"),
    ("retrieval-augmented generation", "检索增强生成"),
    ("multi-head self-attention", "多头自注意力"),
    ("layer normalization", "层归一化"),
    ("batch normalization", "批归一化"),
    ("residual connection", "残差连接"),
    ("context window", "上下文窗口"),
    ("self-attention", "自注意力"),
    ("machine translation", "机器翻译"),
    ("question answering", "问答"),
    ("knowledge graph", "知识图谱"),
    ("graph neural network", "图神经网络"),
    ("convolutional neural network", "卷积神经网络"),
    ("recurrent neural network", "循环神经网络"),
    ("natural language processing", "自然语言处理"),
    ("computer vision", "计算机视觉"),
    ("reinforcement learning", "强化学习"),
    ("fine-tuning", "微调"),
    ("pre-training", "预训练"),
    ("zero-shot", "零样本"),
    ("few-shot", "少样本"),
    ("case study", "案例分析"),
    ("ablation study", "消融实验"),
    ("error analysis", "错误分析"),
    ("future work", "未来工作"),
    ("related work", "相关工作"),
    ("benchmark dataset", "基准数据集"),
]

#: 中文 → 英文（取 TERMS 中出现的第一个英文写法）
ZH_TERMS: dict[str, str] = {}
for _en, _zh in TERMS.items():
    ZH_TERMS.setdefault(_zh, _en)

#: 需要保护、不参与翻译的片段（公式 / 引用 / 数字 / 代码）
PROTECT_RE = re.compile(
    r"(\$\$?[^$]{1,200}\$\$?)"
    r"|(\\\([^)]{1,200}\\\))"
    r"|(\[\s*\d{1,3}(?:\s*[,，\-–]\s*\d{1,3})*\s*\])"
    r"|(\b[A-Za-z]+_[A-Za-z0-9]+\b)"
    r"|(\b\d+(?:\.\d+)?(?:%|k|M|B)?\b)"
    r"|(https?://\S+)"
)


def protect(text: str) -> tuple[str, list[str]]:
    """把公式/引用/数字替换为占位符，返回 (占位文本, 原片段表)。"""
    pieces: list[str] = []

    def repl(match: re.Match) -> str:
        pieces.append(match.group(0))
        return f"\u0001{len(pieces) - 1}\u0001"

    return PROTECT_RE.sub(repl, text), pieces


def restore(text: str, pieces: list[str]) -> str:
    def repl(match: re.Match) -> str:
        idx = int(match.group(1))
        return pieces[idx] if 0 <= idx < len(pieces) else ""

    return re.sub(r"\u0001(\d+)\u0001", repl, text)


def detect_terms(text: str) -> list[str]:
    lowered = text.lower()
    found = [en for en in TERMS if re.search(rf"(?<![a-z]){re.escape(en)}(?![a-z])", lowered)]
    return sorted(set(found), key=len, reverse=True)


def translate_en_to_zh(text: str) -> tuple[str, float, list[str]]:
    """术语级英译中：返回 (译文, 术语覆盖率, 命中术语)。"""
    lowered = text.lower()
    used: list[str] = []
    out = text

    # 1) 短语优先（长短语先替换，避免被拆散）
    for en, zh in sorted(PHRASES, key=lambda kv: -len(kv[0])):
        pattern = re.compile(rf"(?<![a-z]){re.escape(en)}(?![a-z])", re.IGNORECASE)
        if pattern.search(out):
            out = pattern.sub(zh, out)
            used.append(en)

    # 2) 单词/术语替换
    for en, zh in sorted(TERMS.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(rf"(?<![a-z]){re.escape(en)}(?![a-z])", re.IGNORECASE)
        if pattern.search(out):
            out = pattern.sub(zh, out)
            used.append(en)

    total_words = max(1, len(re.findall(r"[A-Za-z]{2,}", lowered)))
    covered = sum(len(w.split()) for w in used if w in TERMS)
    coverage = min(1.0, covered / total_words)
    return out, round(coverage, 3), sorted(set(used))


def translate_zh_to_en(text: str) -> tuple[str, float, list[str]]:
    """术语级中译英：中文术语 → 英文，其余保留。"""
    out = text
    used: list[str] = []
    for zh, en in sorted(ZH_TERMS.items(), key=lambda kv: -len(kv[0])):
        if zh in out and len(zh) >= 2:
            out = out.replace(zh, f" {en} ")
            used.append(zh)
    out = re.sub(r"\s+", " ", out).strip()
    # 中文标点转英文标点，并清理替换产生的多余空格（离线模式至少输出规范英文排版）
    out = (
        out.replace("，", ", ")
        .replace("。", ". ")
        .replace("；", "; ")
        .replace("：", ": ")
        .replace("（", " (")
        .replace("）", ") ")
        .replace("、", ", ")
    )
    out = re.sub(r"\s+([,.;:!?)])", r"\1", out)
    out = re.sub(r"\(\s+", "(", out)
    out = re.sub(r"([,.;:!?])\1+", r"\1", out)
    out = re.sub(r"\.\s*\.", ".", out)
    out = re.sub(r"\s+", " ", out).strip()
    if out[:1].islower():
        out = out[0].upper() + out[1:]
    tokens_total = max(1, len(re.findall(r"[\u4e00-\u9fff]", text)))
    covered = sum(len(zh) for zh in used)
    return out, round(min(1.0, covered / tokens_total), 3), used


def term_pairs(texts: Iterable[str]) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for text in texts:
        for en in detect_terms(text):
            seen.setdefault(en, TERMS[en])
    return [{"en": en, "zh": zh} for en, zh in sorted(seen.items())]
