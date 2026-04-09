"""
Permission rule matching — exact, prefix, and wildcard patterns.

Source: Claude Code src/utils/permissions/shellRuleMatching.ts

Three rule types (priority order):
  1. Exact match — literal string, no wildcards
  2. Prefix match — legacy `command:*` syntax
  3. Wildcard pattern — modern `command *` with glob-style `*`

Rule matching: deny > ask > allow (deny always wins)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class RuleType(str, Enum):
    EXACT = "exact"
    PREFIX = "prefix"
    WILDCARD = "wildcard"


@dataclass
class ParsedRule:
    type: RuleType
    pattern: str
    original: str


def parse_permission_rule(rule: str) -> ParsedRule:
    """
    Parse a permission rule into its type.
    Claude Code: parsePermissionRule() in shellRuleMatching.ts

    Examples:
      "npm install"    → exact match
      "npm:*"          → prefix (matches npm install, npm run, etc.)
      "git *"          → wildcard (matches git add, git commit, etc.)
    """
    # Legacy prefix syntax: "command:*"
    if rule.endswith(":*") and "\\" not in rule:
        return ParsedRule(
            type=RuleType.PREFIX,
            pattern=rule[:-2],  # strip :*
            original=rule,
        )

    # Check for unescaped * characters (wildcard)
    # \* is escaped (literal), * is wildcard
    unescaped_stars = re.findall(r'(?<!\\)\*', rule)
    if unescaped_stars:
        return ParsedRule(
            type=RuleType.WILDCARD,
            pattern=rule,
            original=rule,
        )

    # Plain exact match
    return ParsedRule(
        type=RuleType.EXACT,
        pattern=rule,
        original=rule,
    )


def match_rule(rule: str, value: str) -> bool:
    """
    Check if a value matches a permission rule.
    Claude Code: matchWildcardPattern() in shellRuleMatching.ts

    Supports:
      - Exact: literal equality
      - Prefix: value starts with prefix + space (or equals prefix)
      - Wildcard: * matches any sequence, \\* = literal *
    """
    parsed = parse_permission_rule(rule)

    if parsed.type == RuleType.EXACT:
        return value == parsed.pattern

    if parsed.type == RuleType.PREFIX:
        prefix = parsed.pattern
        return value == prefix or value.startswith(prefix + " ")

    if parsed.type == RuleType.WILDCARD:
        return _match_wildcard(parsed.pattern, value)

    return False


def _match_wildcard(pattern: str, value: str) -> bool:
    """
    Match a wildcard pattern against a value.
    Claude Code: matchWildcardPattern() in shellRuleMatching.ts

    * matches any sequence (including newlines)
    \\* matches literal *
    Trailing ` *` is made optional (so "git *" matches bare "git")
    """
    # Build regex char by char, handling \* escape
    regex_parts = []
    i = 0
    while i < len(pattern):
        if i < len(pattern) - 1 and pattern[i] == '\\' and pattern[i + 1] == '*':
            # Escaped star → literal *
            regex_parts.append(re.escape('*'))
            i += 2
        elif pattern[i] == '*':
            # Unescaped star → match anything
            regex_parts.append('.*')
            i += 1
        else:
            regex_parts.append(re.escape(pattern[i]))
            i += 1

    regex = ''.join(regex_parts)

    # Claude Code: trailing ` *` (space + wildcard) is made optional
    # so "git *" matches both "git add" AND bare "git"
    # Note: re.escape(' ') produces '\\ ' so we check for escaped space
    if regex.endswith('\\ .*'):
        regex = regex[:-4] + '( .*)?'
    elif regex.endswith(' .*'):
        regex = regex[:-3] + '( .*)?'

    try:
        return bool(re.fullmatch(regex, value, re.DOTALL))
    except re.error:
        return False


def match_rules_for_tool(
    tool_name: str,
    rules: dict[str, list[str]],
) -> list[str]:
    """
    Find all rules that match a tool name.
    Returns list of matched rule strings.
    """
    matched = []
    for source, patterns in rules.items():
        for pattern in patterns:
            if match_rule(pattern, tool_name):
                matched.append(pattern)
    return matched
