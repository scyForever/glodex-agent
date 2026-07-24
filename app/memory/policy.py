"""记忆写入策略：过滤噪声、推断类型/标签、去重合并、检索打分。

所有策略都是纯函数，不依赖 IO 或 LLM，保证速度快且可测试。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.memory.schemas import MemoryItem, MemoryKind, MemoryWrite


# 噪声模式：这些是闲聊或一次性的无价值文本，不应写入长期记忆。
NOISE_PATTERNS = [
    "谢谢",
    "好的",
    "收到",
    "哈哈",
    "再见",
]

# 标签关键词映射：文本中命中任一关键词即自动打上对应标签。
# 标签用于后续检索时的语义匹配增强。
TAG_KEYWORDS: dict[str, list[str]] = {
    "material": ["塑料", "木", "金属", "棉", "皮", "食品接触"],
    "style": ["小众", "简约", "复古", "可爱", "高级", "网红"],
    "platform": ["京东", "淘宝", "天猫", "拼多多", "1688", "小红书", "抖音"],
    "shipping": ["包邮", "运费", "预售", "现货", "自营"],
    "budget": ["预算", "以内", "不超过", "元"],
}

# 记忆类型推断关键词：通过文本特征词判断 kind，无需 LLM。
CONSTRAINT_WORDS = ["不要", "不买", "不看", "必须", "只要", "拒绝", "避免", "不能"]
CORRECTION_WORDS = ["不是", "纠正", "我说的是", "别记错", "不是这个意思"]
PREFERENCE_WORDS = ["喜欢", "偏好", "倾向", "优先", "更想要", "不喜欢"]


def normalize_memory_text(text: str) -> str:
    """统一空白符，避免格式差异导致去重失效。

    例如 "不要塑料" 和 "不要  塑料" 在去重时应视为同一条。
    """
    return re.sub(r"\s+", " ", text.strip())


def infer_kind(text: str, default: MemoryKind = "preference") -> MemoryKind:
    """根据文本关键词推断记忆类型。

    规则优先级：correction > constraint > preference > default。
    例如"不是不喜欢塑料，只是不要食品接触类"会命中 correction 关键词。
    """
    normalized = normalize_memory_text(text)
    if any(word in normalized for word in CORRECTION_WORDS):
        return "correction"
    if any(word in normalized for word in CONSTRAINT_WORDS):
        return "constraint"
    if any(word in normalized for word in PREFERENCE_WORDS):
        return "preference"
    return default


def infer_tags(text: str) -> list[str]:
    """从文本中自动提取标签。

    遍历 TAG_KEYWORDS，命中任一类别的任一关键词即打上该类别标签。
    例如"包邮、自营" → ["shipping"]，"简约复古" → ["style"]。
    """
    tags: list[str] = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(keyword.lower() in text.lower() for keyword in keywords):
            tags.append(tag)
    return tags


def should_write(text: str) -> bool:
    """判断文本是否值得写入长期记忆。

    过滤条件：
    - 长度 < 4 字符（太短无信息量）。
    - 命中噪声模式（闲聊用语）。
    """
    normalized = normalize_memory_text(text)
    if len(normalized) < 4:
        return False
    return not any(pattern in normalized for pattern in NOISE_PATTERNS)


def build_memory_write(
    text: str,
    kind: MemoryKind | None = None,
    tags: list[str] | None = None,
    confidence: float = 0.8,
) -> MemoryWrite | None:
    """把原始文本规范化为待写入对象。

    流程：标准化空白 → 过滤噪声 → 推断 kind → 推断 tags → 构造 MemoryWrite。
    返回值可能为 None，表示该文本不值得写入。

    Args:
        text: 原始偏好文本，如"不喜欢拼多多"。
        kind: 显式指定的类型，为 None 则自动推断。
        tags: 显式指定的标签，为 None 则自动推断。
        confidence: 初始置信度，范围 [0, 1]。

    Returns:
        规范化后的 MemoryWrite，或 None（文本应丢弃）。
    """
    normalized = normalize_memory_text(text)
    if not should_write(normalized):
        return None
    # 合并显式标签和自动推断标签，去重排序
    inferred_tags = sorted(set((tags or []) + infer_tags(normalized)))
    return MemoryWrite(
        text=normalized,
        kind=kind or infer_kind(normalized),
        tags=inferred_tags,
        confidence=max(0.0, min(confidence, 1.0)),
    )


def memory_similarity(left: str, right: str) -> float:
    """轻量文本相似度，基于中文片段 + 英文 token 的 Jaccard 系数。

    不依赖向量库，适合第一版几百条以内的去重场景。
    """
    left_terms = set(_split_terms(left))
    right_terms = set(_split_terms(right))
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms & right_terms)
    return overlap / max(len(left_terms), len(right_terms))


def is_duplicate(candidate: MemoryWrite, existing: MemoryItem) -> bool:
    """判断候选写入是否与已有记忆高度重复。

    判定条件（任一满足即视为重复）：
    - candidate 和 existing 的 text 完全相同。
    - 两者 kind 相同且文本相似度 ≥ 75%。

    已删除的记忆不参与去重（允许重新写入）。
    """
    if existing.deleted:
        return False
    if candidate.kind != existing.kind:
        return False
    if normalize_memory_text(candidate.text) == normalize_memory_text(existing.text):
        return True
    return memory_similarity(candidate.text, existing.text) >= 0.75


def merge_memory(existing: MemoryItem, candidate: MemoryWrite) -> MemoryItem:
    """重复记忆不新增，改为合并标签并取更高置信度。

    例如已存"不喜欢塑料材质"（confidence=0.8），新写入"不要塑料"（confidence=0.7），
    合并后 confidence 保持 0.8，tags 合并去重。
    """
    now = datetime.now(timezone.utc)
    merged_tags = sorted(set(existing.tags + candidate.tags))
    return existing.model_copy(
        update={
            "tags": merged_tags,
            "confidence": max(existing.confidence, candidate.confidence),
            "updated_at": now,
        }
    )


def score_memory(item: MemoryItem, query: str) -> float:
    """检索相关性打分。

    打分维度（四项加权求和）：
    - kind 权重：constraint(2.0) > correction(1.8) > preference(1.2) > fact(1.0) > summary(0.6)
    - 文本词重叠：query 和记忆 text 的共有词数 × 0.6
    - 标签命中：query 命中的记忆标签数 × 0.8
    - 置信度：confidence × 0.5

    已删除的记忆返回 -1.0，确保排到最后。
    """
    if item.deleted:
        return -1.0

    query_terms = set(_split_terms(query))
    text_terms = set(_split_terms(item.text))
    tag_hits = sum(1 for tag in item.tags if tag.lower() in query.lower())
    overlap = len(query_terms & text_terms)

    # kind 权重：硬约束和纠错信息更重要，信息检索时要排在前面
    kind_weight = {
        "constraint": 2.0,
        "correction": 1.8,
        "preference": 1.2,
        "fact": 1.0,
        "summary": 0.6,
    }.get(item.kind, 1.0)

    return kind_weight + overlap * 0.6 + tag_hits * 0.8 + item.confidence * 0.5


def _split_terms(text: str) -> list[str]:
    """把中文文本切分为可匹配的词条。

    策略：
    - 连续中文字符作为词条（2 字以上）。
    - 英文/数字 token 作为词条。
    - TAG_KEYWORDS 中的关键词也作为额外词条，确保标签词一定可命中。
    """
    lowered = text.lower()
    terms = re.findall(r"[一-鿿]{2,}|[a-z0-9_+-]+", lowered)
    for keywords in TAG_KEYWORDS.values():
        for keyword in keywords:
            if keyword.lower() in lowered:
                terms.append(keyword.lower())
    return terms
