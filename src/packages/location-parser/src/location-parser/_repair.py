import json
import re
from typing import Any

# Cap on how much we're willing to trim from the end while hunting for something
# parseable. Generous enough to discard one partially-written box entry (a few hundred
# characters at most in this schema) without risking a slow, unbounded scan on a huge
# reply.
_MAX_TRIM = 2000

_DANGLING_RE = re.compile(r"[,:]\s*$")


def repair_truncated(candidate: str) -> Any | None:
    """Recover a JSON value from `candidate` when it has no matching closing bracket
    -- almost always a reply cut off by a token limit, not a genuinely malformed
    document.

    Trims characters off the end, closes whatever brackets are still open at each
    length, and returns the parsed result from the first attempt that succeeds.
    Trimming only ever *removes* characters that came from the model's own output, so
    the recovered JSON never contains anything the model didn't actually write; at
    worst it discards the last, incompletely-written entry in an array.

    Returns None if nothing in the trim window parses.
    """
    max_trim = min(len(candidate), _MAX_TRIM)
    for trim in range(max_trim + 1):
        attempt = candidate[: len(candidate) - trim] if trim else candidate
        attempt = _DANGLING_RE.sub("", attempt.rstrip())
        closer = _closing_brackets(attempt)
        if closer is None:
            continue
        try:
            return json.loads(attempt + closer)
        except json.JSONDecodeError:
            continue
    return None


def _closing_brackets(text: str) -> str | None:
    """The brackets needed to close every `{`/`[` still open at the end of `text`,
    respecting string literals. Returns None if `text` ends mid-string with no
    unescaped closing quote, since we can't know where the string was meant to end."""
    stack: list[str] = []
    in_string = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if stack:
                stack.pop()
        index += 1

    if in_string:
        return '"' + "".join("}" if c == "{" else "]" for c in reversed(stack))
    return "".join("}" if c == "{" else "]" for c in reversed(stack))
