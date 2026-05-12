import re


def strip_cpp_comments(content, preserve_lines=False):
    if not content:
        return ""

    result = []
    index = 0
    in_string = ""
    escaped = False

    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""

        if in_string:
            result.append(char)

            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""

            index += 1
            continue

        if char in {'"', "'"}:
            in_string = char
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2

            while index < len(content) and content[index] not in "\r\n":
                if preserve_lines:
                    result.append(" ")
                index += 1

            continue

        if char == "/" and next_char == "*":
            index += 2

            while index < len(content):
                if content[index] == "*" and index + 1 < len(content) and content[index + 1] == "/":
                    index += 2
                    break

                if preserve_lines:
                    result.append(content[index] if content[index] in "\r\n" else " ")

                index += 1

            continue

        result.append(char)
        index += 1

    return "".join(result)


def find_matching_brace(content, open_index):
    depth = 0
    in_string = ""
    escaped = False

    for index in range(open_index, len(content)):
        char = content[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = ""
            continue

        if char in {'"', "'"}:
            in_string = char
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

    return -1


def find_class_body(content, class_name):
    pattern = re.compile(r"\bclass\s+" + re.escape(class_name) + r"\b[^;{]*\{", re.IGNORECASE)
    match = pattern.search(content)

    if not match:
        return ""

    open_index = content.find("{", match.start())
    close_index = find_matching_brace(content, open_index)

    if close_index < 0:
        return ""

    return content[open_index + 1:close_index]


def iter_class_blocks(content):
    pattern = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*\{", re.IGNORECASE)
    position = 0

    while True:
        match = pattern.search(content, position)

        if not match:
            break

        open_index = content.find("{", match.start())
        close_index = find_matching_brace(content, open_index)

        if close_index < 0:
            position = match.end()
            continue

        yield match.group(1), match.group(2) or "", content[open_index + 1:close_index], match.start(), close_index + 1
        position = close_index + 1


def iter_top_level_class_blocks(content):
    position = 0
    pattern = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_]*))?\s*\{", re.IGNORECASE)

    while True:
        match = pattern.search(content, position)

        if not match:
            break

        open_index = content.find("{", match.start())
        close_index = find_matching_brace(content, open_index)

        if close_index < 0:
            position = match.end()
            continue

        yield match.group(1), match.group(2) or "", content[open_index + 1:close_index]
        position = close_index + 1


def parse_array_values(content, array_name):
    pattern = re.compile(r"\b" + re.escape(array_name) + r"\s*\[\s*\]\s*\+?=\s*\{(.*?)\}\s*;", re.IGNORECASE | re.DOTALL)
    match = pattern.search(content)

    if not match:
        return None

    values = []

    for item in match.group(1).split(","):
        item = item.strip().strip('"').strip("'")

        if item:
            values.append(item)

    return values


def get_line_number_from_index(content, index):
    if content is None or index is None:
        return 0

    try:
        return content.count(chr(10), 0, max(0, index)) + 1
    except Exception:
        return 0
