import re

_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def sanitize(text: str) -> tuple[str, bool]:
    """Repair the handful of non-JSON habits models fall into, without ever touching
    the inside of a quoted string:

    - a stray `;` where a `,` belongs (a frequent hallucination between two entries);
    - `//line` and `/* block */` comments (some models add these despite being told
      not to);
    - a trailing comma right before a closing `}` or `]`.

    Returns `(sanitized_text, changed)`; `changed` is True iff at least one of the
    above was actually found and fixed, which the caller uses to decide whether the
    reply counts as `complete`.
    """
    out: list[str] = []
    changed = False
    in_string = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < length and text[index + 1] == "/":
            changed = True
            while index < length and text[index] not in "\n\r":
                index += 1
            continue

        if char == "/" and index + 1 < length and text[index + 1] == "*":
            changed = True
            index += 2
            while index + 1 < length and not (
                text[index] == "*" and text[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue

        if char == ";":
            changed = True
            out.append(",")
            index += 1
            continue

        out.append(char)
        index += 1

    sanitized = "".join(out)
    without_trailing_commas = _TRAILING_COMMA_RE.sub(r"\1", sanitized)
    if without_trailing_commas != sanitized:
        changed = True

    return without_trailing_commas, changed
