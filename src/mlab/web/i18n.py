"""The page's languages. Code, mlab and the server write English; static/locales/zh.json maps English text to Chinese.

The page translates almost everything itself (static/i18n.js). The server translates only what it renders whole: the
report, and the fake agent's replies. Its translator works like the page's: exact text first, then templates such as
"{label} finished ({owner}).", whose values are translated in turn.
"""
import functools
import json
import re
from pathlib import Path

LOCALES = Path(__file__).parent / "static" / "locales"
LANGUAGES = {"en": "English", "zh": "Chinese (Simplified)"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def language(value):
    """The language a request asked for (header X-Mlab-Lang), or None."""
    return value if value in LANGUAGES else None


@functools.cache
def table(lang):
    if lang == "en" or lang not in LANGUAGES:
        return {}
    path = LOCALES / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@functools.cache
def patterns(lang):
    found = []
    for key in table(lang):
        names = PLACEHOLDER.findall(key)
        if not names or len(PLACEHOLDER.sub("", key)) < 4:  # Too little fixed text to match safely: exact lookup only.
            continue
        parts = re.split(r"(\{\w+\})", key)
        source = "".join("([\\s\\S]+?)" if PLACEHOLDER.fullmatch(part) else re.escape(part) for part in parts)
        found.append((len(PLACEHOLDER.sub("", key)), key, names, re.compile(f"^{source}$")))
    return [item[1:] for item in sorted(found, key=lambda item: -item[0])]


def fill(template, params):
    return PLACEHOLDER.sub(lambda m: str(params[m.group(1)]) if params.get(m.group(1)) is not None else m.group(0), template)


def translator(lang):
    """tr(english, **params): the text in lang, with params filled in."""
    words = table(lang)
    def tr(template, /, **params):
        return fill(words.get(template, template), params)
    return tr


def translate(lang, text):
    """A sentence formatted in English, in lang when it matches a known template; otherwise unchanged."""
    words = table(lang)
    if not words or not isinstance(text, str) or not text:
        return text
    if text in words:
        return words[text]
    for key, names, pattern in patterns(lang):
        found = pattern.match(text)
        if found:
            return fill(words[key], {name: translate(lang, found.group(i + 1)) for i, name in enumerate(names)})
    return text


@functools.cache
def reverse(lang):
    return {value: key for key, value in table(lang).items() if "{" not in key}


def english(text):
    """Text from the page in English when it is one of the page's own sentences (a quick reply, a question template)."""
    text = (text or "").strip()
    refs = re.match(r"^((?:@\S+\s+)*)(.*)$", text, re.S)
    head, rest = refs.group(1), refs.group(2).strip()
    for lang in LANGUAGES:
        if rest in reverse(lang):
            return head + reverse(lang)[rest]
    return text
