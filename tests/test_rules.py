"""Tests for permission rule matching."""

from permissions.rules import RuleType, match_rule, parse_permission_rule


def test_parse_exact():
    r = parse_permission_rule("npm install")
    assert r.type == RuleType.EXACT
    assert r.pattern == "npm install"


def test_parse_prefix():
    r = parse_permission_rule("npm:*")
    assert r.type == RuleType.PREFIX
    assert r.pattern == "npm"


def test_parse_wildcard():
    r = parse_permission_rule("git *")
    assert r.type == RuleType.WILDCARD


def test_match_exact():
    assert match_rule("npm install", "npm install")
    assert not match_rule("npm install", "npm run")
    assert not match_rule("npm install", "npm install --save")


def test_match_prefix():
    assert match_rule("npm:*", "npm install")
    assert match_rule("npm:*", "npm run build")
    assert match_rule("npm:*", "npm")
    assert not match_rule("npm:*", "npx something")


def test_match_wildcard():
    assert match_rule("git *", "git add")
    assert match_rule("git *", "git commit -m 'msg'")
    assert match_rule("git *", "git")
    assert not match_rule("git *", "gitk")


def test_match_wildcard_middle():
    assert match_rule("python *.py", "python script.py")
    assert match_rule("python *.py", "python test.py")
    assert not match_rule("python *.py", "python script.js")
