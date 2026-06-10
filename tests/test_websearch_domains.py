"""WebSearch CC parity: allowed_domains / blocked_domains filtering + min query length."""
import pytest
from pydantic import ValidationError

from tools.builtin.WebSearchTool.WebSearchTool import WebSearchTool, WebSearchParams

_RES = [
    {"title": "A", "href": "https://www.example.com/a", "body": "x"},
    {"title": "B", "href": "https://evil.com/b", "body": "y"},
    {"title": "C", "href": "https://docs.python.org/c", "body": "z"},
]


def test_allowed_domains_filters():
    out = WebSearchTool._filter_domains(_RES, ["example.com"], None)
    assert [r["title"] for r in out] == ["A"]


def test_blocked_domains_filters():
    out = WebSearchTool._filter_domains(_RES, None, ["evil.com"])
    assert [r["title"] for r in out] == ["A", "C"]


def test_no_filters_passthrough():
    out = WebSearchTool._filter_domains(_RES, None, None)
    assert len(out) == 3


def test_schema_has_domain_fields_and_min_length():
    p = WebSearchParams(query="hi", allowed_domains=["example.com"], blocked_domains=["evil.com"])
    assert p.allowed_domains == ["example.com"] and p.blocked_domains == ["evil.com"]
    with pytest.raises(ValidationError):
        WebSearchParams(query="h")  # below min_length 2
