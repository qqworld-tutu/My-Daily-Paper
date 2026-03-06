from src.summarization.summarizer import (
    _arxiv_abs_to_html_url,
    _build_user_prompt,
    _normalize_summary_mode,
)


def test_arxiv_abs_to_html_url() -> None:
    assert (
        _arxiv_abs_to_html_url("http://arxiv.org/abs/2501.01234v2")
        == "https://arxiv.org/html/2501.01234v2"
    )
    assert (
        _arxiv_abs_to_html_url("https://arxiv.org/pdf/2501.01234v2.pdf")
        == "https://arxiv.org/html/2501.01234v2"
    )
    assert _arxiv_abs_to_html_url("https://example.com/paper/123") == ""


def test_normalize_summary_mode() -> None:
    assert _normalize_summary_mode("enhanced") == "enhanced"
    assert _normalize_summary_mode("STRICT") == "strict"
    assert _normalize_summary_mode("other") == "strict"


def test_build_user_prompt_includes_enhanced_context() -> None:
    prompt = _build_user_prompt(
        title="T",
        abstract="A",
        summary_mode="enhanced",
        enhanced_context="extra context",
    )
    assert "摘要模式：enhanced" in prompt
    assert "补充材料（来自 arXiv HTML 正文片段" in prompt
    assert "extra context" in prompt
