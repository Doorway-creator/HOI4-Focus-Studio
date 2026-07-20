"""Small loss-preserving parser for Paradox Clausewitz script."""
from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class Entry:
    key: str
    value: str | "Block"
    line: int
    raw: str = ""


@dataclass
class Block:
    entries: list[Entry] = field(default_factory=list)

    def values(self, key: str):
        return [entry.value for entry in self.entries if entry.key == key]

    def first(self, key: str, default=None):
        values = self.values(key)
        return values[0] if values else default


TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\{|\}|=|[^\s{}=#"]+|#[^\r\n]*|\r?\n')


def tokenize(text: str):
    line = 1
    for match in TOKEN.finditer(text):
        token = match.group(0)
        start_line = line
        line += token.count("\n")
        if token.startswith("#") or token in ("\n", "\r\n"):
            continue
        yield token, start_line


def parse(text: str) -> Block:
    tokens = list(tokenize(text))
    index = 0

    def scalar(token: str) -> str:
        if len(token) >= 2 and token[0] == token[-1] == '"':
            return token[1:-1].replace('\\"', '"')
        return token

    def block(stop_on_brace=False):
        nonlocal index
        result = Block()
        while index < len(tokens):
            token, line = tokens[index]
            if token == "}":
                if stop_on_brace:
                    index += 1
                    return result
                raise ValueError(f"Unexpected closing brace at line {line}")
            key = scalar(token); index += 1
            if index < len(tokens) and tokens[index][0] == "=":
                index += 1
            if index >= len(tokens):
                result.entries.append(Entry(key, "yes", line, key))
                break
            value_token, _ = tokens[index]
            if value_token == "{":
                index += 1
                value = block(True)
            elif value_token == "}":
                result.entries.append(Entry(key, "yes", line, key))
                continue
            else:
                index += 1
                value = scalar(value_token)
            result.entries.append(Entry(key, value, line))
        if stop_on_brace:
            raise ValueError("Unclosed Clausewitz block")
        return result

    return block()


def identifiers(value) -> set[str]:
    """Collect identifier-like scalar values from a parsed subtree."""
    found = set()
    if isinstance(value, Block):
        for entry in value.entries:
            if not entry.key.startswith("@"): found.add(entry.key)
            found.update(identifiers(entry.value))
    elif isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", value):
        found.add(value)
    return found


def serialize(value, indent: int = 0) -> str:
    if not isinstance(value, Block):
        text = str(value)
        return f'"{text}"' if any(char.isspace() for char in text) else text
    lines = []
    pad = "\t" * indent
    for entry in value.entries:
        if isinstance(entry.value, Block):
            lines.append(f"{pad}{entry.key} = {{")
            lines.append(serialize(entry.value, indent + 1))
            lines.append(f"{pad}}}")
        else:
            lines.append(f"{pad}{entry.key} = {serialize(entry.value)}")
    return "\n".join(lines)
