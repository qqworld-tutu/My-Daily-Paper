"""Summary generation with optional LLM and faithful fallback extraction."""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request

from src.models import NormalizedPaper


def summarize_papers(
    papers: list[NormalizedPaper],
    *,
    use_llm: bool = True,
    language: str = "zh-CN",
    summary_mode: str = "strict",
    enhanced_max_chars: int = 8000,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
) -> list[NormalizedPaper]:
    mode = _normalize_summary_mode(summary_mode)
    html_context_cache: dict[str, str] = {}

    for paper in papers:
        enhanced_context = ""
        if use_llm and mode == "enhanced":
            enhanced_context = _load_enhanced_context_for_paper(
                paper,
                cache=html_context_cache,
                max_chars=max(1000, int(enhanced_max_chars)),
            )

        paper.summary_zh = summarize_abstract(
            title=paper.title_en,
            abstract=paper.abstract_raw,
            use_llm=use_llm,
            language=language,
            summary_mode=mode,
            enhanced_context=enhanced_context,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
        )
    return papers


def summarize_abstract(
    *,
    title: str,
    abstract: str,
    use_llm: bool,
    language: str,
    summary_mode: str = "strict",
    enhanced_context: str = "",
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
) -> str:
    content = " ".join(abstract.split())
    if not content:
        return "原摘要为空，暂无可用摘要内容。"

    if use_llm:
        llm_summary = _llm_summary(
            title=title,
            abstract=content,
            language=language,
            summary_mode=_normalize_summary_mode(summary_mode),
            enhanced_context=enhanced_context,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
        )
        if llm_summary:
            return llm_summary

    return _fallback_extractive_summary(content)


def _llm_summary(
    *,
    title: str,
    abstract: str,
    language: str,
    summary_mode: str = "strict",
    enhanced_context: str = "",
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
) -> str | None:
    api_key = (llm_api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    if not api_key:
        return None

    base_url = (llm_base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
    model = (llm_model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()

    system_prompt = _build_system_prompt(language=language)
    user_prompt = _build_user_prompt(
        title=title,
        abstract=abstract,
        summary_mode=summary_mode,
        enhanced_context=enhanced_context,
    )

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _resolve_chat_completions_url(base_url),
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
        obj = json.loads(body)
        choices = obj.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return None
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        text = str(content).strip()
        if text:
            return _strip_code_fence(text)
        return None
    except Exception:
        return None


def _resolve_chat_completions_url(base_url: str) -> str:
    """Allow OPENAI_BASE_URL to be either API root or full chat endpoint."""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _fallback_extractive_summary(abstract: str) -> str:
    """Faithful fallback: extract directly from abstract, no fabricated claims."""
    clean = " ".join(abstract.split())
    sentence_split = re.split(r"(?<=[.!?。！？])\s+", clean)
    sentence_split = [s.strip() for s in sentence_split if s.strip()]

    if not sentence_split:
        sentence_split = [clean[:600].strip()]

    chosen = sentence_split[:2]
    excerpt = " ".join(chosen).strip()
    if len(excerpt) > 700:
        excerpt = excerpt[:700].rsplit(" ", 1)[0].strip()
    excerpt = excerpt.rstrip("。.!? ")

    return (
        "摘要（原文抽取，未调用LLM翻译）："
        f"{excerpt}"
        "。该内容未补充外部事实。建议查看原文获取完整实验与细节。"
    )


def _normalize_summary_mode(mode: str) -> str:
    normalized = str(mode).strip().lower()
    if normalized in {"strict", "enhanced"}:
        return normalized
    return "strict"


def _build_system_prompt(*, language: str) -> str:
    return (
        "你是一个学术论文摘要助手。"
        "只能基于用户给定的材料生成总结，不得补充外部事实。"
        "如果材料中没有对应信息，必须明确写“文中未明确报告”。"
        f"输出语言固定为：{language}。"
        "严格按照以下固定格式输出，且不要增删标题：\n"
        "【一句话结论】\n"
        "<1-2句>\n"
        "【研究问题】\n"
        "<1句>\n"
        "【方法核心】\n"
        "- <要点1>\n"
        "- <要点2>\n"
        "- <要点3，可选>\n"
        "【关键结果】\n"
        "- <结果1，优先数字；无数字则写文中未明确报告>\n"
        "- <结果2>\n"
        "【适用场景】\n"
        "<1-2句>\n"
        "【局限性】\n"
        "<1-2句，若缺失写文中未明确讨论>\n"
        "不要输出任何额外说明。"
    )


def _build_user_prompt(
    *,
    title: str,
    abstract: str,
    summary_mode: str,
    enhanced_context: str,
) -> str:
    prompt = (
        f"摘要模式：{summary_mode}\n"
        f"标题：{title}\n"
        f"原始摘要：{abstract}\n"
    )

    if summary_mode == "enhanced" and enhanced_context.strip():
        prompt += (
            "补充材料（来自 arXiv HTML 正文片段，可能被截断）：\n"
            f"{enhanced_context.strip()}\n"
        )

    prompt += (
        "请仅基于以上材料输出固定格式摘要。"
        "如果某字段信息不足，明确写“文中未明确报告”或“文中未明确讨论”。"
    )
    return prompt


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _load_enhanced_context_for_paper(
    paper: NormalizedPaper,
    *,
    cache: dict[str, str],
    max_chars: int,
) -> str:
    if paper.source_tag != "arXiv":
        return ""

    html_url = _arxiv_abs_to_html_url(paper.source_url)
    if not html_url:
        return ""

    if html_url in cache:
        return cache[html_url]

    text = _fetch_arxiv_html_context(html_url, max_chars=max_chars)
    cache[html_url] = text
    return text


def _arxiv_abs_to_html_url(source_url: str) -> str:
    if not source_url.strip():
        return ""

    parsed = urllib.parse.urlparse(source_url.strip())
    path = parsed.path or ""

    paper_id = ""
    if "/abs/" in path:
        paper_id = path.split("/abs/", 1)[1]
    elif "/pdf/" in path:
        paper_id = path.split("/pdf/", 1)[1]
        if paper_id.endswith(".pdf"):
            paper_id = paper_id[:-4]

    paper_id = paper_id.strip("/")
    if not paper_id:
        return ""
    return f"https://arxiv.org/html/{paper_id}"


def _fetch_arxiv_html_context(html_url: str, *, max_chars: int) -> str:
    request = urllib.request.Request(
        html_url,
        headers={
            "User-Agent": "daily-paper-push-mvp/0.1 (+https://github.com)",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

    paragraphs = re.findall(r"(?is)<p[^>]*>(.*?)</p>", raw)
    pieces: list[str] = []
    total = 0

    for block in paragraphs:
        cleaned = _clean_html_fragment(block)
        if len(cleaned) < 40:
            continue
        pieces.append(cleaned)
        total += len(cleaned) + 1
        if total >= max_chars:
            break

    if not pieces:
        body_match = re.search(r"(?is)<body[^>]*>(.*?)</body>", raw)
        body = body_match.group(1) if body_match else raw
        cleaned = _clean_html_fragment(body)
        return cleaned[:max_chars].strip()

    return " ".join(pieces)[:max_chars].strip()


def _clean_html_fragment(fragment: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", fragment)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
