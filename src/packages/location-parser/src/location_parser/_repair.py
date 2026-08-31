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

    The bracket/string state needed to close each candidate length is computed ONCE,
    in a single forward pass over `candidate` (`_bracket_states`), and then looked up
    by index for every trim length tried -- not recomputed from scratch each time.
    An earlier version called a fresh string-aware bracket scan per trim attempt,
    which was O(_MAX_TRIM x len(candidate)) in the worst case (PR review flagged this
    on a dense reply cut off after many entries); benchmarked at over 5 seconds on a
    ~26KB pathological input (500 entries plus a long unparseable trailing run).
    This version's own scan is O(len(candidate)) once, and each of up to _MAX_TRIM
    attempts after that is a constant-size table lookup plus one `json.loads` call.

    Returns None if nothing in the trim window parses.
    """
    states = _bracket_states(candidate)
    max_trim = min(len(candidate), _MAX_TRIM)
    for trim in range(max_trim + 1):
        cutoff = len(candidate) - trim
        cleaned_length = _effective_length(candidate, cutoff)
        in_string, stack = states[cleaned_length]
        closer = ('"' if in_string else "") + "".join(
            "}" if c == "{" else "]" for c in reversed(stack)
        )
        try:
            return json.loads(candidate[:cleaned_length] + closer)
        except json.JSONDecodeError:
            continue
    return None


def _bracket_states(text: str) -> list[tuple[bool, tuple[str, ...]]]:
    """`states[i]` is `(in_string, stack)` -- whether position `i` sits inside a
    string literal, and the tuple of still-open `{`/`[` characters at that point --
    for every prefix length `i` from `0` to `len(text)` inclusive. String-aware
    (respects `\\"` escapes) and computed in a single forward pass, so any prefix
    length's closing-bracket state can be read back in O(1) instead of rescanning
    `text` from the start."""
    states: list[tuple[bool, tuple[str, ...]]] = [(False, ())]
    in_string = False
    stack: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                if index + 1 < length:
                    states.append((True, tuple(stack)))
                    states.append((True, tuple(stack)))
                    index += 2
                else:
                    states.append((True, tuple(stack)))
                    index += 1
                continue
            if char == '"':
                in_string = False
            states.append((in_string, tuple(stack)))
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if stack:
                stack.pop()
        states.append((in_string, tuple(stack)))
        index += 1

    return states


def _effective_length(text: str, cutoff: int) -> int:
    """The length remaining after conceptually `.rstrip()`-ing `text[:cutoff]` and
    then removing one trailing `,` or `:` (plus any whitespace before it) -- the same
    cleanup `repair_truncated` always applied before attempting to close brackets --
    computed by scanning backward from `cutoff`, without materializing the
    substring. Bounded by how much trailing whitespace/punctuation there is, not by
    `len(text)`, so this stays cheap however large `text` is."""
    index = cutoff
    while index > 0 and text[index - 1].isspace():
        index -= 1
    if index > 0 and text[index - 1] in ",:":
        index -= 1
        while index > 0 and text[index - 1].isspace():
            index -= 1
    return index