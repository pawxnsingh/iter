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


@dataclass(frozen=True)
class PermissionRuleValue:
    """Parsed CC rule string: `{toolName, ruleContent?}`.

    Claude Code: src/utils/permissions/permissionRuleParser.ts
        permissionRuleValueFromString('Bash')              → {toolName: 'Bash'}
        permissionRuleValueFromString('Bash(npm install)') → {toolName: 'Bash', ruleContent: 'npm install'}
        permissionRuleValueFromString('Read(./src/*)')     → {toolName: 'Read', ruleContent: './src/*'}
    """
    tool_name: str
    rule_content: str | None = None


def _find_first_unescaped(s: str, ch: str) -> int:
    """Index of first `ch` not preceded by `\\`. -1 if none."""
    i = 0
    while i < len(s):
        if s[i] == ch and (i == 0 or s[i - 1] != "\\"):
            return i
        i += 1
    return -1


def _find_last_unescaped(s: str, ch: str) -> int:
    """Index of last `ch` not preceded by `\\`. -1 if none."""
    i = len(s) - 1
    while i >= 0:
        if s[i] == ch and (i == 0 or s[i - 1] != "\\"):
            return i
        i -= 1
    return -1


def _unescape_rule_content(content: str) -> str:
    """Reverse of CC's escapeRuleContent. Order matters: parens first, then \\."""
    return content.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")


def parse_permission_rule_string(rule_string: str) -> PermissionRuleValue:
    """Parse a CC settings.json rule like `Bash(npm install)`.

    Mirrors CC's permissionRuleValueFromString() exactly:
      - No '(' present → just tool name
      - '(' present but ')' missing/misplaced → treat whole string as tool name
      - Empty content '()' or `(*)` → tool name only (tool-wide rule)
      - Otherwise → toolName + unescaped ruleContent
    """
    open_idx = _find_first_unescaped(rule_string, "(")
    if open_idx == -1:
        return PermissionRuleValue(tool_name=rule_string)

    close_idx = _find_last_unescaped(rule_string, ")")
    if close_idx == -1 or close_idx <= open_idx:
        return PermissionRuleValue(tool_name=rule_string)

    if close_idx != len(rule_string) - 1:
        return PermissionRuleValue(tool_name=rule_string)

    tool_name = rule_string[:open_idx]
    raw_content = rule_string[open_idx + 1 : close_idx]

    if not tool_name:
        return PermissionRuleValue(tool_name=rule_string)
    if raw_content == "" or raw_content == "*":
        return PermissionRuleValue(tool_name=tool_name)

    return PermissionRuleValue(
        tool_name=tool_name,
        rule_content=_unescape_rule_content(raw_content),
    )


def parse_rule_strings_to_dict(rules: list[str]) -> dict[str, list[str]]:
    """Convert a CC-style list of rule strings into our internal dict format.

    Used by config loading to bridge settings.json (CC-shape) ↔ runtime
    `ToolPermissionContext` (our shape).

    Examples:
      ["Bash(npm install)", "Bash(echo *)", "Read"]
        → {"Bash": ["npm install", "echo *"], "Read": [""]}
      ["Write(/tmp/*)"]
        → {"Write": ["/tmp/*"]}
      ["Bash"] (tool-wide)
        → {"Bash": [""]}    # empty pattern = match anything

    Empty/None entries are silently skipped.
    """
    out: dict[str, list[str]] = {}
    for raw in rules:
        if not raw or not isinstance(raw, str):
            continue
        parsed = parse_permission_rule_string(raw)
        bucket = out.setdefault(parsed.tool_name, [])
        # ruleContent=None means tool-wide rule → empty pattern in our format
        bucket.append(parsed.rule_content or "")
    return out


def match_rules_for_tool(
    tool_name: str,
    rules: dict[str, list[str]],
    permission_value: str | None = None,
) -> list[str]:
    """
    Find all rules that match a tool invocation.

    Claude Code parity: rules dict is keyed by tool name; each value is a
    list of sub-patterns (rule "content") that match against the tool's
    permission value (BashTool → command, file tools → file_path, etc.).

    Matching:
      - The rules dict key must equal the tool's name.
      - An empty pattern ("" / None) means "any call to this tool".
      - A non-empty pattern is wildcard-matched against `permission_value`.
        If `permission_value` is None (tool has no sub-pattern support),
        only empty patterns match.

    Examples (rules → tool, value → matches):
      {"Bash": [""]}          → Bash, anything    → [""]
      {"Bash": ["echo *"]}    → Bash, "echo hi"   → ["echo *"]
      {"Bash": ["echo *"]}    → Bash, "ls"        → []
      {"Read": ["./src/*"]}   → Read, "./src/x"   → ["./src/*"]
    """
    matched: list[str] = []
    patterns = rules.get(tool_name)
    if not patterns:
        return matched
    for pattern in patterns:
        # Empty pattern → "any call to this tool"
        if pattern == "" or pattern is None:
            matched.append(pattern or "")
            continue
        # Sub-pattern requires the tool to expose a permission value
        if permission_value is None:
            continue
        if match_rule(pattern, permission_value):
            matched.append(pattern)
    return matched
