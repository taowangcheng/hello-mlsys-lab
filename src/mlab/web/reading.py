"""Bounded evidence previews with immutable sources and explicit continuation calls."""
import hashlib
import json
import re

from ..core.io import atomic, require


MAX_OUTPUT = 60000
PAGE_SIZE = 12000


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def snapshot(root, text, suffix="json"):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    relative = f".mlab/evidence/{digest}.{suffix}"
    path = root / relative
    if path.exists():
        require(path.read_bytes() == text.encode("utf-8"), "E_FROZEN_CHANGED", "Evidence snapshot changed")
    else:
        atomic(path, text, text=True)
    return relative


def read_file(root, path):
    content = path.read_bytes().decode("utf-8")
    relative = path.relative_to(root.resolve()).as_posix()
    match = re.fullmatch(r"\.mlab/evidence/([a-f0-9]{64})\.(?:json|txt)", relative)
    if match:
        require(hashlib.sha256(content.encode("utf-8")).hexdigest() == match[1],
                "E_FROZEN_CHANGED", "Evidence snapshot changed")
    return content


def pointer_part(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def preview(root, value, max_output=MAX_OUTPUT):
    """Keep a usable record shape and give every omitted field an exact Read call."""
    source = snapshot(root, encoded(value))
    omitted = []

    def compact(item, path=""):
        if isinstance(item, str) and len(item) > 3000:
            omitted.append({"path": path, "kind": "text", "total_chars": len(item), "shown_chars": 3000,
                            "next": {"file_path": source, "json_pointer": path, "offset": 3000, "limit": PAGE_SIZE}})
            return item[:3000]
        if isinstance(item, list):
            if len(item) > 30:
                omitted.append({"path": path, "kind": "list", "total_items": len(item), "shown_items": 30,
                                "next": {"file_path": source, "json_pointer": path, "offset": 0, "limit": PAGE_SIZE}})
            return [compact(child, f"{path}/{index}") for index, child in enumerate(item[:30])]
        if isinstance(item, dict):
            return {key: compact(child, f"{path}/{pointer_part(key)}") for key, child in item.items()}
        return item

    result = compact(value)
    reading = {"complete": not omitted, "source": {"file_path": source}, "omitted": omitted}
    result = {**result, "_reading": reading} if isinstance(result, dict) else {"value": result, "_reading": reading}
    if len(encoded(result)) > max_output or any(len(item["path"]) > 4096 for item in omitted):
        return json.loads(read_text(encoded(value), {"file_path": source, "offset": 0, "limit": PAGE_SIZE}, max_output))
    return result


def select(value, pointer):
    require(isinstance(pointer, str) and len(pointer) <= 4096 and (pointer == "" or pointer.startswith("/")),
            "E_SCHEMA", "json_pointer must be empty or start with / and contain at most 4096 characters", 2)
    if pointer == "":
        return value
    for part in pointer[1:].split("/"):
        require(not re.search(r"~(?![01])", part), "E_SCHEMA", "Invalid JSON pointer escape", 2)
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            require(re.fullmatch(r"0|[1-9]\d*", key) and int(key) < len(value),
                    "E_SCHEMA", "JSON pointer index is outside the record", 2)
            value = value[int(key)]
        else:
            require(isinstance(value, dict) and key in value, "E_SCHEMA", "JSON pointer field does not exist", 2)
            value = value[key]
    return value


def read_text(content, args, max_output=MAX_OUTPUT):
    """Offsets count Unicode characters in the selected text or serialized JSON value."""
    version = hashlib.sha256(content.encode("utf-8")).hexdigest()
    require(args.get("version", version) == version, "E_FROZEN_CHANGED",
            "This file changed between pages. Read from offset 0 again.")
    value = select(json.loads(content), args["json_pointer"]) if "json_pointer" in args else content
    text = value if isinstance(value, str) else encoded(value)
    offset, limit = args.get("offset", 0), args.get("limit", PAGE_SIZE)
    require(type(offset) is int and 0 <= offset <= len(text), "E_SCHEMA", "offset is outside the selected text", 2)
    require(type(limit) is int and 1 <= limit <= 20000, "E_SCHEMA", "limit must be between 1 and 20000 characters", 2)
    if not any(key in args for key in ("offset", "limit", "json_pointer", "version")) and len(content) <= PAGE_SIZE:
        return content
    end = min(offset + limit, len(text))
    while True:
        next_call = {"file_path": args["file_path"], "offset": end, "limit": limit, "version": version}
        if "json_pointer" in args:
            next_call["json_pointer"] = args["json_pointer"]
        result = {"text": text[offset:end], "format": "text" if isinstance(value, str) else "json",
                  "offset": offset, "end": end, "total_chars": len(text), "complete": end == len(text),
                  "version": version, "next": next_call if end < len(text) else None}
        output = encoded(result)
        if len(output) <= max_output:
            return output
        require(end - offset > 1, "E_SCHEMA", "Read metadata exceeds the output limit", 2)
        end = offset + max(1, (end - offset) // 2)


def text_output(root, text):
    if len(text) <= MAX_OUTPUT:
        return text
    source = snapshot(root, text, "txt")
    return read_text(text, {"file_path": source, "offset": 0, "limit": PAGE_SIZE})
