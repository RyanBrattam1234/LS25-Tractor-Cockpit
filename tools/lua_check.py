#!/usr/bin/env python3
"""Strukturelle Pruefung der Mod-Lua-Datei - ohne Lua-Interpreter.

Auf diesem PC gibt es keinen Lua-Interpreter, ein Syntaxfehler wuerde also
erst beim Laden im Spiel auffallen. Dieses Skript findet die haeufigsten
Fehler vorher:

* Blockbilanz (function / if / for / while / do / repeat / end / until)
* Klammerbilanz (), [], {}
* nicht geschlossene Strings, Kurz- und Langkommentare
* erwartete API-Aufrufe (Absicherung gegen versehentliches Loeschen)

Ersetzt keinen echten Lua-Interpreter, ist aber eine schnelle Vorpruefung.

Aufruf:
    python tools/lua_check.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
LUA_FILE = WORKSPACE / "mod" / "FS25_TractorCockpit" / "scripts" / "TractorCockpit.lua"

# Diese Aufrufe muessen vorkommen - sonst wurde die Mod-Logik beschaedigt.
REQUIRED_SNIPPETS = (
    "TRACTORCOCKPIT",
    "addModEventListener",
    "getCurrentVehicle",
    "getLastSpeed",
    "getMotorRpmReal",
    "getConsumerFillUnitIndex",
    "getIsMotorStarted",
    "getOperatingTime",
    "getFillUnitFillLevel",
    # Phase 2: schneller Datei-Kanal
    "getUserProfileAppPath",
    "io.open",
    "FILE_NAME",
    # Phase 3: Anbaugeraete
    "getAttachedImplements",
    "typeName",
    "getIsLowered",
    "getIsTurnedOn",
    "getIsUnfolded",
    "getIsWorkAreaActive",
    "getFillUnits",
    "getFillTypeTitleByIndex",
    # Phase 4: Steuerkanal ueber die XML-API (io.open darf nur schreiben)
    "XMLFile.load",
    "XMLFile.create",
    "tractorcockpit#id",
    "detectCommandChannel",
    "commandMode",
    "getCommandCandidates",
    "COMMAND_ACTIONS",
    "handleLowerImplementEvent",
    "getAllowsLowering",
    "getToggledFoldDirection",
    "setFoldDirection",
    "setIsTurnedOn",
    "getIsFoldAllowed",
    # Ausgabekanal: Datei wird geschrieben und wieder geschlossen
    "COMMAND_FILE",
    "commandReadErrors",
    "file:close",
)

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def strip_strings_and_comments(source: str) -> tuple[str, list[str]]:
    """Ersetzt Strings/Kommentare durch Leerzeichen, damit sie nicht zaehlen."""
    problems: list[str] = []
    out: list[str] = []
    index = 0
    length = len(source)
    line = 1

    def long_bracket(start: int):
        """Erkennt [[...]], [=[...]=] ... und liefert (level, inhalt_start)."""
        match = re.match(r"\[(=*)\[", source[start:])
        if not match:
            return None
        return len(match.group(1)), start + match.end()

    while index < length:
        char = source[index]

        if char == "\n":
            line += 1
            out.append(char)
            index += 1
            continue

        # Kommentar
        if source.startswith("--", index):
            bracket = long_bracket(index + 2)
            if bracket:
                level, content_start = bracket
                closing = "]" + "=" * level + "]"
                end = source.find(closing, content_start)
                if end < 0:
                    problems.append(f"Zeile {line}: Langkommentar ohne Ende")
                    index = length
                    continue
                line += source.count("\n", index, end)
                out.append(" " * (end + len(closing) - index))
                index = end + len(closing)
            else:
                end = source.find("\n", index)
                end = length if end < 0 else end
                out.append(" " * (end - index))
                index = end
            continue

        # Langstring
        bracket = long_bracket(index)
        if bracket:
            level, content_start = bracket
            closing = "]" + "=" * level + "]"
            end = source.find(closing, content_start)
            if end < 0:
                problems.append(f"Zeile {line}: Langstring ohne Ende")
                index = length
                continue
            line += source.count("\n", index, end)
            out.append('"' + " " * max(0, end + len(closing) - index - 2) + '"')
            index = end + len(closing)
            continue

        # Kurzstring
        if char in ("'", '"'):
            quote = char
            cursor = index + 1
            while cursor < length:
                if source[cursor] == "\\":
                    cursor += 2
                    continue
                if source[cursor] == quote:
                    break
                if source[cursor] == "\n":
                    problems.append(f"Zeile {line}: String ohne Ende")
                    break
                cursor += 1
            else:
                problems.append(f"Zeile {line}: String ohne Ende")
                cursor = length
            out.append('"' + " " * max(0, cursor - index - 1) + '"')
            index = cursor + 1
            continue

        out.append(char)
        index += 1

    return "".join(out), problems


def check_blocks(cleaned: str) -> list[str]:
    problems: list[str] = []
    depth = 0
    expect_do = False
    max_depth = 0

    for match in WORD_RE.finditer(cleaned):
        word = match.group(0)
        if word == "function":
            depth += 1
        elif word == "if":
            depth += 1
        elif word in ("for", "while"):
            depth += 1
            expect_do = True
        elif word == "do":
            if expect_do:
                expect_do = False
            else:
                depth += 1
        elif word == "repeat":
            depth += 1
        elif word in ("end", "until"):
            depth -= 1
            if depth < 0:
                problems.append(f"Zu viele 'end'/'until' in der Naehe von '{word}'")
                depth = 0
        max_depth = max(max_depth, depth)

    if depth != 0:
        problems.append(f"Blockbilanz stimmt nicht: {depth} Block/Blöcke ohne 'end'")
    if max_depth == 0:
        problems.append("Keine Bloecke gefunden - Datei wirkt leer oder beschaedigt")
    return problems


def check_brackets(cleaned: str) -> list[str]:
    problems: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for char in cleaned:
        if char in "([{":
            stack.append(char)
        elif char in pairs:
            if not stack or stack[-1] != pairs[char]:
                problems.append(f"Klammerfehler bei '{char}'")
                return problems
            stack.pop()
    if stack:
        problems.append(f"{len(stack)} Klammer(n) nicht geschlossen: {''.join(stack)}")
    return problems


def main() -> int:
    if not LUA_FILE.is_file():
        print(f"[FEHL] Lua-Datei nicht gefunden: {LUA_FILE}")
        return 1

    source = LUA_FILE.read_text(encoding="utf-8")
    cleaned, problems = strip_strings_and_comments(source)
    problems += check_brackets(cleaned)
    problems += check_blocks(cleaned)

    for snippet in REQUIRED_SNIPPETS:
        if snippet not in source:
            problems.append(f"Erwarteter Aufruf fehlt: {snippet}")

    # Windows-1252/UTF-8-Mischmasch wuerde das Spiel stoeren.
    if "\ufffd" in source:
        problems.append("Datei enthaelt Ersatzzeichen - Encoding pruefen")

    print(f"Geprueft: {LUA_FILE}")
    print(f"Zeilen:   {source.count(chr(10)) + 1}")

    if problems:
        print()
        for problem in problems:
            print(f"[FEHL] {problem}")
        print(f"\n{len(problems)} Problem(e) gefunden.")
        return 1

    print("[OK  ] Blockbilanz, Klammern, Strings und API-Aufrufe sind plausibel.")
    print("Hinweis: ersetzt keinen echten Lua-Interpreter, sondern nur eine Vorpruefung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
