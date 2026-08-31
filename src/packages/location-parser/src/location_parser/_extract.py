import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_candidate(text: str) -> str:
    """Pull the JSON payload out of a raw model reply.

    Prefers a fenced code block (` ```json ... ``` ` or plain ` ``` ... ``` `) if one is
    present. Otherwise finds the first ``{`` or ``[`` in the text and returns the
    balanced span it opens -- so leading commentary ("Here is the JSON:") and trailing
    commentary ("Let me know if you need anything else!") are both discarded, and only
    the JSON value itself is kept.

    If the opening bracket never finds a matching close before the text ends, the
    reply was almost certainly cut off by a token limit: everything from that opening
    bracket to the end of the text is returned as-is, unclosed, for `repair.py` to
    recover.
    """
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    text = text.strip()

    start = None
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            break
    if start is None:
        return text

    end = _matching_end(text, start)
    if end is not None:
        return text[start : end + 1]
    return text[start:]


def _matching_end(text: str, start: int) -> int | None:
    """Index of the character that closes the bracket opened at `start`, respecting
    string literals (so a `}` or `]` inside a quoted value is never mistaken for a
    structural one). Returns None if the text ends before the bracket closes."""
    stack = [text[start]]
    in_string = False
    index = start + 1
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
            if not stack:
                return None
            stack.pop()
            if not stack:
                return index
        index += 1
    return None
