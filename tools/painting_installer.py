#!/usr/bin/env python3
"""
little ripple painting helper i use for Liminal Dream.

it grabs the painting position and rotation from a Fast64 marker:

    MODEL_NONE + bhvStaticObject + behavior param 0x504E54xx

xx is the painting index used by PAINTING_WARP_NODE.
the groups go in the ROM painting table.
add --apply when i actually want it to write stuff.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

MAGIC_BASE = 0x504E5400  # PNT marker index
MAGIC_MASK = 0xFFFFFF00

AUTO_FILE = "painting_auto.inc.c"
TEXTURE_FILE = "texture.inc.c"

NORMAL_COMMON_BEGIN = "/* === AUTO_PAINTING_COMMON_NORMAL_BEGIN === */"
NORMAL_COMMON_END = "/* === AUTO_PAINTING_COMMON_NORMAL_END === */"
ENV_COMMON_BEGIN = "/* === AUTO_PAINTING_COMMON_CONTINUOUS_BEGIN === */"
ENV_COMMON_END = "/* === AUTO_PAINTING_COMMON_CONTINUOUS_END === */"


class InstallerError(RuntimeError):
    pass


def die(msg: str) -> None:
    raise InstallerError(msg)


def sanitize_identifier(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not out:
        die("name ended up empty after cleaning it up.")
    if out[0].isdigit():
        out = "p_" + out
    return out


def find_repo_root(start: Path) -> Path:
    start = start.resolve()
    for p in [start, *start.parents]:
        if (p / "levels" / "level_defines.h").is_file() and (p / "src" / "game" / "paintings.c").is_file():
            return p
    die("could not find the repo root. run this inside the repo or use --repo.")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        die(f"missing file: {path}")


def write_if_changed(path: Path, old: str, new: str, apply: bool, changed: list[Path]) -> None:
    if old == new:
        return
    changed.append(path)
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")


def split_top_level_args(s: str) -> list[str]:
    args: list[str] = []
    start = 0
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            args.append(s[start:i].strip())
            start = i + 1
    args.append(s[start:].strip())
    return args


def macro_calls(text: str, names: Iterable[str]) -> list[tuple[str, str, int, int]]:
    """grab macro calls without getting messed up by nested parentheses."""
    wanted = set(names)
    rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(wanted, key=len, reverse=True)) + r")\s*\(")
    out: list[tuple[str, str, int, int]] = []
    pos = 0
    while True:
        m = rx.search(text, pos)
        if not m:
            break
        name = m.group(1)
        open_paren = text.find("(", m.start())
        depth = 1
        i = open_paren + 1
        in_string = False
        escape = False
        while i < len(text) and depth:
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
            i += 1
        if depth != 0:
            die(f"macro {name} has an unclosed parenthesis.")
        out.append((name, text[open_paren + 1:i - 1], m.start(), i))
        pos = i
    return out


def parse_int_literal(s: str) -> int | None:
    s = s.strip()
    # strip the outside parentheses
    while len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        s = s[1:-1].strip()
    s = re.sub(r"[uUlL]+$", "", s)
    try:
        return int(s, 0)
    except ValueError:
        return None


def parse_float_literal(s: str) -> float | None:
    s = s.strip()
    s = re.sub(r"[fF]$", "", s)
    try:
        return float(s)
    except ValueError:
        iv = parse_int_literal(s)
        return float(iv) if iv is not None else None


def get_area_block(script: str, area: int) -> tuple[str, int, int]:
    calls = macro_calls(script, ["AREA", "END_AREA"])
    for idx, (name, args_s, start, end) in enumerate(calls):
        if name != "AREA":
            continue
        args = split_top_level_args(args_s)
        if not args:
            continue
        n = parse_int_literal(args[0])
        if n != area:
            continue
        for name2, _args2, start2, end2 in calls[idx + 1:]:
            if name2 == "END_AREA":
                return script[start:end2], start, end2
            if name2 == "AREA":
                break
        die(f"could not find END_AREA() for AREA({area}, ...).")
    die(f"could not find AREA({area}, ...) in script.c.")


def find_marker(area_text: str, marker_index: int) -> tuple[float, float, float, float, float, float, int]:
    matches = []
    for name, args_s, _start, _end in macro_calls(area_text, ["OBJECT", "OBJECT_WITH_ACTS"]):
        args = split_top_level_args(args_s)
        if len(args) < 9:
            continue
        model = args[0].strip()
        behavior = args[8].strip()
        if model != "MODEL_NONE" or behavior != "bhvStaticObject":
            continue
        beh_param = parse_int_literal(args[7])
        if beh_param is None or (beh_param & MAGIC_MASK) != MAGIC_BASE:
            continue
        idx = beh_param & 0xFF
        if idx != marker_index:
            continue
        nums = [parse_float_literal(a) for a in args[1:7]]
        if any(v is None for v in nums):
            die(f"found marker {marker_index}, but i could not read its position/rotation: {args_s}")
        x, y, z, pitch, yaw, roll = [float(v) for v in nums]  # type: ignore[arg-type]
        matches.append((x, y, z, pitch, yaw, roll, beh_param))
    if not matches:
        die(
            f"could not find marker {marker_index} in this area. "
            f"use MODEL_NONE + bhvStaticObject + behavior param 0x{MAGIC_BASE | marker_index:08X}."
        )
    if len(matches) > 1:
        die(f"found {len(matches)} markers using index {marker_index} in the same area. there should only be one.")
    return matches[0]


def resolve_level_enum(root: Path, level_dir: str) -> str:
    text = read_text(root / "levels" / "level_defines.h")
    for name, args_s, _s, _e in macro_calls(text, ["DEFINE_LEVEL"]):
        args = split_top_level_args(args_s)
        if len(args) >= 4 and args[3].strip() == level_dir:
            return args[1].strip()
    die(f"could not figure out the level enum for levels/{level_dir}. use --level-enum if needed.")


def find_decl_block(text: str, symbol: str) -> str:
    # grab the whole array block
    m = re.search(r"(?m)^.*\b" + re.escape(symbol) + r"\s*\[.*?\]\s*=\s*\{", text)
    if not m:
        die(f"could not grab the template block '{symbol}'.")
    brace = text.find("{", m.start())
    depth = 1
    i = brace + 1
    in_string = False
    escape = False
    while i < len(text) and depth:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        i += 1
    if depth:
        die(f"block '{symbol}' is not closed.")
    semi = text.find(";", i)
    if semi == -1:
        die(f"block '{symbol}' is missing the final ';'.")
    return text[m.start():semi + 1].strip()


def replace_identifiers(text: str, mapping: dict[str, str]) -> str:
    # do the longer names first
    for old in sorted(mapping, key=len, reverse=True):
        text = re.sub(r"\b" + re.escape(old) + r"\b", mapping[old], text)
    return text


def ensure_common_normal(root: Path, auto_text: str, level_id: str) -> str:
    if NORMAL_COMMON_BEGIN in auto_text:
        return auto_text
    candidates = [
        root / "levels" / "plexal_castle_2" / "painting.inc.c",
        root / "levels" / "castle_inside" / "painting.inc.c",
    ]
    source_path = next((p for p in candidates if p.is_file()), None)
    if source_path is None:
        die("could not find a normal painting template in plexal_castle_2 or castle_inside.")
    source = read_text(source_path)
    symbols = [
        "static_painting_vertices",
        "painting_texture_map_bottom",
        "painting_texture_map_top",
        "painting_texture_maps",
        "ripple_dl",
        "non_ripple_dl",
    ]
    blocks = [find_decl_block(source, sym) for sym in symbols]
    mapping = {
        "static_painting_vertices": f"{level_id}_auto_static_vertices",
        "painting_texture_map_bottom": f"{level_id}_auto_texture_map_bottom",
        "painting_texture_map_top": f"{level_id}_auto_texture_map_top",
        "painting_texture_maps": f"{level_id}_auto_texture_maps",
        "ripple_dl": f"{level_id}_auto_ripple_dl",
        "non_ripple_dl": f"{level_id}_auto_non_ripple_dl",
    }
    common = replace_identifiers("\n\n".join(blocks), mapping)
    addition = f"\n{NORMAL_COMMON_BEGIN}\n{common}\n{NORMAL_COMMON_END}\n"
    return auto_text.rstrip() + "\n" + addition


def ensure_common_continuous(root: Path, auto_text: str, level_id: str) -> str:
    if ENV_COMMON_BEGIN in auto_text:
        return auto_text
    source_path = root / "levels" / "hmc" / "areas" / "1" / "painting.inc.c"
    source = read_text(source_path)
    symbols = [
        "hmc_seg7_painting_dl_070242D0",
        "hmc_seg7_pool_texture_map_070242F0",
        "hmc_seg7_painting_texture_maps_07024CD4",
    ]
    blocks = [find_decl_block(source, sym) for sym in symbols]
    mapping = {
        "hmc_seg7_painting_dl_070242D0": f"{level_id}_auto_env_ripple_dl",
        "hmc_seg7_pool_texture_map_070242F0": f"{level_id}_auto_env_texture_map",
        "hmc_seg7_painting_texture_maps_07024CD4": f"{level_id}_auto_env_texture_maps",
    }
    common = replace_identifiers("\n\n".join(blocks), mapping)
    addition = f"\n{ENV_COMMON_BEGIN}\n{common}\n{ENV_COMMON_END}\n"
    return auto_text.rstrip() + "\n" + addition


def marked_block(begin: str, end: str, body: str) -> str:
    return f"{begin}\n{body.rstrip()}\n{end}"


def upsert_marked_block(text: str, begin: str, end: str, body: str) -> str:
    block = marked_block(begin, end, body)
    if begin in text:
        start = text.index(begin)
        stop = text.find(end, start)
        if stop == -1:
            die(f"found {begin}, but i could not find {end}.")
        stop += len(end)
        return text[:start] + block + text[stop:]
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + block + "\n"


def ensure_include(text: str, include_line: str, before_hint: str | None = None) -> str:
    if include_line in text:
        return text
    line = include_line + "\n"
    if before_hint and before_hint in text:
        idx = text.index(before_hint)
        return text[:idx] + line + text[idx:]
    # just stick it after the last include
    matches = list(re.finditer(r"(?m)^#include[^\n]*\n", text))
    if matches:
        idx = matches[-1].end()
        return text[:idx] + line + text[idx:]
    return line + text


def ensure_leveldata_includes(text: str, level: str) -> str:
    tex = f'#include "levels/{level}/{TEXTURE_FILE}"'
    auto = f'#include "levels/{level}/{AUTO_FILE}"'
    # textures need to stay before the painting data
    if tex not in text:
        anchor = f'#include "levels/{level}/{AUTO_FILE}"'
        if anchor in text:
            text = text.replace(anchor, tex + "\n" + anchor, 1)
        else:
            text = ensure_include(text, tex, '#include "leveldata.inc.c"')
    if auto not in text:
        text = ensure_include(text, auto, '#include "leveldata.inc.c"')
    return text


def ensure_header_extern(text: str, struct_name: str) -> str:
    decl = f"extern struct Painting {struct_name};"
    if decl in text:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f"\n// AUTO PAINTING\n{decl}\n"


def ensure_level_header_include(text: str, level: str) -> str:
    inc = f'#include "levels/{level}/header.h"'
    if inc in text:
        return text
    level_includes = list(re.finditer(r'(?m)^#include "levels/[^\"]+/header\.h"\s*$', text))
    if level_includes:
        idx = level_includes[-1].end()
        return text[:idx] + "\n" + inc + text[idx:]
    return ensure_include(text, inc)


def initializer_body_bounds(text: str, decl_pattern: str) -> tuple[int, int]:
    m = re.search(decl_pattern, text)
    if not m:
        die(f"could not find this initializer: {decl_pattern}")
    brace = text.find("{", m.start())
    depth = 1
    i = brace + 1
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    if depth:
        die("initializer is not closed.")
    return brace + 1, i - 1


def get_level_values(root: Path) -> dict[str, int]:
    text = read_text(root / "levels" / "level_defines.h")
    values: dict[str, int] = {}
    value = 1  # LEVEL_NONE is 0
    for _name, args_s, _start, _end in macro_calls(text, ["STUB_LEVEL", "DEFINE_LEVEL"]):
        args = split_top_level_args(args_s)
        if len(args) < 2:
            continue
        level_enum = args[1].strip()
        if level_enum in values:
            die(f"duplicate level enum in level_defines.h: {level_enum}")
        values[level_enum] = value
        value += 1
    return values


def get_painting_group_max(root: Path) -> int:
    text = read_text(root / "src" / "tables" / "painting_array_table.h")
    m = re.search(r"(?m)^\s*#define\s+PAINTING_GROUP_MAX\s+(\d+)\b", text)
    if not m:
        die("could not find PAINTING_GROUP_MAX in painting_array_table.h.")
    return int(m.group(1))


def painting_table_bounds(text: str) -> tuple[int, int]:
    return initializer_body_bounds(
        text,
        r"(?s)const\s+struct\s+PaintingGroup\s+gPaintingGroups\s*\[\s*\][^=]*=\s*\{",
    )


def top_level_brace_blocks(text: str, start: int, end: int) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    depth = 0
    block_start: int | None = None
    in_string = False
    escape = False
    line_comment = False
    block_comment = False
    i = start

    while i < end:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < end else ""

        if line_comment:
            if ch == "\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == "/" and nxt == "/":
            line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "{":
            if depth == 0:
                block_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                die("gPaintingGroups has messed up braces.")
            if depth == 0 and block_start is not None:
                blocks.append((block_start, i + 1))
                block_start = None
        i += 1

    if depth != 0:
        die("gPaintingGroups has messed up braces.")
    return blocks


def parse_painting_table(text: str, level_values: dict[str, int]) -> tuple[int, int, list[dict[str, object]]]:
    body_start, body_end = painting_table_bounds(text)
    entries: list[dict[str, object]] = []

    for start, end in top_level_brace_blocks(text, body_start, body_end):
        block = text[start:end]
        m = re.match(
            r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(\d+)\s*,",
            block,
        )
        if not m:
            die("found a gPaintingGroups row without a level/area pair.")
        level_enum = m.group(1)
        area = int(m.group(2))
        if level_enum not in level_values:
            die(f"could not get the value for {level_enum}, so i cannot sort the table.")
        if not re.search(r"\bNULL\b", block):
            die(f"{level_enum} area {area} is missing its ending NULL.")
        names = re.findall(r"&\s*([A-Za-z_][A-Za-z0-9_]*)", block)
        key_value = (level_values[level_enum] << 8) | area
        entries.append({
            "level": level_enum,
            "area": area,
            "key": key_value,
            "names": names,
            "start": start,
            "end": end,
        })

    return body_start, body_end, entries


def validate_painting_table(entries: list[dict[str, object]], group_max: int) -> None:
    last_key = -1
    seen: set[int] = set()
    for entry in entries:
        key = int(entry["key"])
        level_enum = str(entry["level"])
        area = int(entry["area"])
        names = list(entry["names"])
        if key in seen:
            die(f"duplicate painting group: {level_enum} area {area}.")
        if key < last_key:
            die(f"gPaintingGroups is out of order before {level_enum} area {area}.")
        if len(names) > group_max:
            die(f"{level_enum} area {area} has {len(names)} paintings. max is {group_max}.")
        seen.add(key)
        last_key = key


def find_table_group(entries: list[dict[str, object]], level_enum: str, area: int) -> dict[str, object] | None:
    for entry in entries:
        if entry["level"] == level_enum and entry["area"] == area:
            return entry
    return None


def ensure_table_registration(
    text: str,
    level_enum: str,
    area: int,
    struct_name: str,
    marker_index: int,
    level_values: dict[str, int],
    group_max: int,
) -> tuple[str, list[str]]:
    body_start, body_end, entries = parse_painting_table(text, level_values)
    validate_painting_table(entries, group_max)
    entry = find_table_group(entries, level_enum, area)

    if entry is not None:
        names = list(entry["names"])
        if struct_name in names:
            actual = names.index(struct_name)
            if actual != marker_index:
                die(f"{struct_name} is already at index {actual}, but marker {marker_index} was requested.")
            return text, names
        if marker_index != len(names):
            die(
                f"{level_enum} area {area} already has {len(names)} painting(s): "
                f"{', '.join(names) or '(none)'}. the new one needs marker {len(names)}, not {marker_index}."
            )
        if len(names) >= group_max:
            die(f"{level_enum} area {area} already hit the {group_max} painting limit.")

        start = int(entry["start"])
        end = int(entry["end"])
        block = text[start:end]
        null_match = re.search(r"\bNULL\b", block)
        if null_match is None:
            die(f"could not find the NULL for {level_enum} area {area}.")
        block = block[:null_match.start()] + f"&{struct_name}, " + block[null_match.start():]
        return text[:start] + block + text[end:], names

    if marker_index != 0:
        die(f"{level_enum} area {area} has no group yet. the first painting has to use marker 0.")
    if level_enum not in level_values:
        die(f"could not get the value for {level_enum}, so i cannot sort the table.")
    if group_max < 1:
        die("PAINTING_GROUP_MAX does not allow any paintings.")

    target_key = (level_values[level_enum] << 8) | area
    row = (
        f"    {{ {level_enum}, {area},\n"
        f"        {{ &{struct_name}, NULL }} }},\n"
    )

    insert_at = body_end
    for other in entries:
        if int(other["key"]) > target_key:
            row_start = int(other["start"])
            line_start = text.rfind("\n", body_start, row_start) + 1
            insert_at = line_start
            break

    if insert_at == body_end and not text[:body_end].endswith("\n"):
        row = "\n" + row

    return text[:insert_at] + row + text[insert_at:], []

def strip_c_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//.*?$", "", s, flags=re.M)
    return s


def find_struct_id(level_dir: Path, struct_name: str) -> int | None:
    pat = re.compile(r"struct\s+Painting\s+" + re.escape(struct_name) + r"\s*=\s*\{(.*?)\};", re.S)
    for path in level_dir.rglob("*.c"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        m = pat.search(text)
        if not m:
            continue
        clean = strip_c_comments(m.group(1))
        n = re.search(r"\b(0x[0-9A-Fa-f]+|\d+)\b", clean)
        if n:
            return int(n.group(1), 0)
    return None


def choose_painting_id(level_dir: Path, existing_names: list[str], marker_index: int, requested: int | None) -> int:
    used = set()
    for n in existing_names:
        v = find_struct_id(level_dir, n)
        if v is not None:
            used.add(v)
    if requested is not None:
        if not (0 <= requested <= 0xFFFF):
            die("--painting-id has to be between 0 and 0xFFFF.")
        if requested in used:
            die(f"painting id 0x{requested:04X} is already used in this group.")
        return requested
    if marker_index not in used:
        return marker_index
    for i in range(0x100):
        if i not in used:
            return i
    die("could not find a free painting id from 0 to 255.")


def fmt_f(v: float) -> str:
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}.0f"
    return f"{v:.6f}".rstrip("0").rstrip(".") + "f"


def parse_alpha(s: str) -> int:
    v = parse_int_literal(s)
    if v is None or not (0 <= v <= 0xFF):
        die("--alpha has to be 0..255, like 0xFF or 0xB4.")
    return v


def make_texture_block(level: str, name_id: str, ptype: str, bottom: str | None, top: str | None, texture: str | None) -> str:
    if ptype == "normal":
        assert bottom and top
        return f'''ALIGNED8 static const Texture {level}_{name_id}_bottom_tex[] = {{
#include "levels/{level}/{bottom}"
}};

ALIGNED8 static const Texture {level}_{name_id}_top_tex[] = {{
#include "levels/{level}/{top}"
}};'''
    assert texture
    return f'''ALIGNED8 static const Texture {level}_{name_id}_env_tex[] = {{
#include "levels/{level}/{texture}"
}};'''


def make_painting_block(
    level_id: str,
    name_id: str,
    struct_name: str,
    ptype: str,
    painting_id: int,
    pos: tuple[float, float, float],
    rot: tuple[float, float, float],
    size: float,
    alpha: int,
) -> str:
    x, y, z = pos
    pitch, yaw, _roll = rot
    lut = f"{level_id}_{name_id}_lut"
    if ptype == "normal":
        texs = f'''ALIGNED8 static const Texture *const {lut}[] = {{
    {level_id}_{name_id}_bottom_tex,
    {level_id}_{name_id}_top_tex,
}};'''
        return texs + f'''

struct Painting {struct_name} = {{
    /* id */ 0x{painting_id:04X},
    /* image count */ 0x02,
    /* texture type */ PAINTING_IMAGE,
    /* floor stuff */ 0x00, 0x00, 0x00,
    /* ripple state */ 0x00,
    /* rotation */ {fmt_f(pitch)}, {fmt_f(yaw)},
    /* position */ {fmt_f(x)}, {fmt_f(y)}, {fmt_f(z)},
    /*                         current passive     entry */
    /* ripple mag */     0.0f,    20.0f,    80.0f,
    /* ripple decay */         1.0f,  0.9608f,  0.9524f,
    /* ripple rate */          0.0f,    0.24f,    0.14f,
    /* ripple spread */    0.0f,    40.0f,    30.0f,
    /* ripple timer */    0.0f,
    /* ripple x, y */     0.0f,     0.0f,
    /* normal dl */ {level_id}_auto_non_ripple_dl,
    /* texture maps */ {level_id}_auto_texture_maps,
    /* textures */     {lut},
    /* texture size */ 64, 32,
    /* ripple dl */ {level_id}_auto_ripple_dl,
    /* ripple trigger */ RIPPLE_TRIGGER_PROXIMITY,
    /* alpha */ 0x{alpha:02X},
    /* mario below */ 0x00, 0x00, 0x00,
    /* size */ {fmt_f(size)},
}};'''

    texs = f'''ALIGNED8 static const Texture *const {lut}[] = {{
    {level_id}_{name_id}_env_tex,
}};'''
    return texs + f'''

struct Painting {struct_name} = {{
    /* id */ 0x{painting_id:04X},
    /* image count */ 0x01,
    /* texture type */ PAINTING_ENV_MAP,
    /* floor stuff */ 0x00, 0x00, 0x00,
    /* ripple state */ PAINTING_RIPPLE,
    /* rotation */ {fmt_f(pitch)}, {fmt_f(yaw)},
    /* position */ {fmt_f(x)}, {fmt_f(y)}, {fmt_f(z)},
    /*                         current passive     entry */
    /* ripple mag */    10.0f,    10.0f,    30.0f,
    /* ripple decay */         1.0f,     1.0f,    0.98f,
    /* ripple rate */          0.05f,    0.05f,    0.05f,
    /* ripple spread */   15.0f,    15.0f,    15.0f,
    /* ripple timer */    0.0f,
    /* ripple x, y */     {fmt_f(size * 0.5)}, {fmt_f(size * 0.5)},
    /* normal dl */ NULL,
    /* texture maps */ {level_id}_auto_env_texture_maps,
    /* textures */     {lut},
    /* texture size */ 32, 32,
    /* ripple dl */ {level_id}_auto_env_ripple_dl,
    /* ripple trigger */ RIPPLE_TRIGGER_CONTINUOUS,
    /* alpha */ 0x{alpha:02X},
    /* mario below */ 0x00, 0x00, 0x00,
    /* size */ {fmt_f(size)},
}};'''


def png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as f:
            header = f.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def texture_include_name(source_name: str) -> str:
    if source_name.endswith(".png"):
        return source_name[:-4] + ".inc.c"
    return source_name


def resolve_texture(level_dir: Path, requested: str, expected_w: int, expected_h: int, warnings: list[str]) -> tuple[str, str]:
    """find the texture file and the include name i need."""
    candidates: list[str] = []
    if requested.endswith(".rgba16.inc.c"):
        candidates.append(requested)
        candidates.append(requested[:-6] + "png")  # try the source png too
    elif requested.endswith(".rgba16.png"):
        candidates.append(requested)
        candidates.append(requested[:-4] + ".inc.c")
    else:
        candidates.append(requested)
        candidates.append(requested + ".rgba16.png")
        candidates.append(requested + ".rgba16.inc.c")

    found: Path | None = None
    for name in candidates:
        p = level_dir / name
        if p.is_file():
            found = p
            break
    if found is None:
        die(
            f"texture does not exist: {level_dir / requested}\n"
            f"i was expecting a source PNG like {Path(requested).stem}.png inside {level_dir}."
        )

    source_name = found.name
    include_name = texture_include_name(source_name)

    if source_name.endswith(".png"):
        dims = png_dimensions(found)
        if dims is None:
            warnings.append(f"{source_name}: could not read the PNG size.")
        elif dims != (expected_w, expected_h):
            warnings.append(f"{source_name}: it is {dims[0]}x{dims[1]}, expected {expected_w}x{expected_h}.")
    else:
        try:
            text = found.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = ""
        vals = re.findall(r"0x[0-9A-Fa-f]{1,4}\b", text)
        expected_pixels = expected_w * expected_h
        if vals and len(vals) != expected_pixels:
            warnings.append(f"{source_name}: found {len(vals)} RGBA16 values, expected {expected_pixels}. check the size.")

    return source_name, include_name


def find_geo_file(level_dir: Path, level: str, area: int) -> Path | None:
    candidates = [
        level_dir / f"area_{area}" / "geo.inc.c",
        level_dir / "areas" / str(area) / "geo.inc.c",
    ]
    for p in candidates:
        if p.is_file():
            return p
    needle = f"{level}_area_{area}_geo"
    for p in level_dir.rglob("geo.inc.c"):
        try:
            if needle in p.read_text(encoding="utf-8"):
                return p
        except UnicodeDecodeError:
            pass
    return None


def audit_fast64(level_dir: Path, level: str, area: int, area_text: str, marker_index: int) -> list[str]:
    out: list[str] = []
    geo = find_geo_file(level_dir, level, area)
    if geo is None:
        out.append("[WARN] could not find this area\'s geo.inc.c to check GEO_ASM.")
    else:
        gt = read_text(geo)
        if "geo_painting_update" in gt:
            out.append(f"[OK] found geo_painting_update in {geo.relative_to(level_dir.parent.parent)}")
        else:
            out.append(f"[WARN] geo_painting_update is missing in {geo}")
        if "geo_painting_draw" in gt:
            out.append(f"[OK] found geo_painting_draw in {geo.relative_to(level_dir.parent.parent)}")
        else:
            out.append(f"[WARN] geo_painting_draw is missing in {geo}")

    found_warp = False
    for _name, args_s, _s, _e in macro_calls(area_text, ["PAINTING_WARP_NODE"]):
        args = split_top_level_args(args_s)
        if args and parse_int_literal(args[0]) == marker_index:
            found_warp = True
            break
    if found_warp:
        out.append(f"[OK] found PAINTING_WARP_NODE({marker_index}, ...) in this area.")
    else:
        out.append(f"[WARN] PAINTING_WARP_NODE({marker_index}, ...) is missing in Fast64/script.c.")
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="little Liminal Dream ripple painting installer using Fast64 markers.")
    p.add_argument("--repo", type=Path, help="repo root. if i leave this out it tries to find it from the current folder.")
    p.add_argument("--level", required=True, help="level folder, like plexal_castle_2")
    p.add_argument("--level-enum", help="manual LEVEL_* override. usually i just let it read level_defines.h")
    p.add_argument("--area", required=True, type=int)
    p.add_argument("--name", required=True, help="short unique name for the painting")
    p.add_argument("--marker", required=True, type=int, help="xx index from 0x504E54xx and PAINTING_WARP_NODE")
    p.add_argument("--type", choices=["normal", "continuous"], default="normal")
    p.add_argument("--size", required=True, type=float)
    p.add_argument("--alpha", default="0xFF")
    p.add_argument("--painting-id", type=lambda x: int(x, 0), help="optional internal id. if i leave it out it picks a free one")
    p.add_argument("--struct-name", help="optional C name for the Painting struct")

    p.add_argument("--bottom", help="normal: bottom .rgba16.png texture. default is <name>2.rgba16.png. .rgba16.inc.c also works")
    p.add_argument("--top", help="normal: top .rgba16.png texture. default is <name>1.rgba16.png. .rgba16.inc.c also works")
    p.add_argument("--texture", help="continuous: 32x32 .rgba16.png texture. default is <name>.rgba16.png. .rgba16.inc.c also works")

    p.add_argument("--apply", action="store_true", help="actually write the changes. without this it is just a dry run")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (0 <= args.marker <= 0xFF):
        die("--marker has to be between 0 and 255.")
    if args.area < 0:
        die("--area cannot be negative.")
    if args.size <= 0:
        die("--size has to be bigger than 0.")

    root = args.repo.resolve() if args.repo else find_repo_root(Path.cwd())
    level = args.level
    level_dir = root / "levels" / level
    if not level_dir.is_dir():
        die(f"levels/{level} does not exist.")

    level_enum = args.level_enum or resolve_level_enum(root, level)
    level_id = sanitize_identifier(level)
    name_id = sanitize_identifier(args.name)
    struct_name = args.struct_name or f"{level_id}_{name_id}_painting"
    struct_name = sanitize_identifier(struct_name)
    alpha = parse_alpha(args.alpha)

    script_path = level_dir / "script.c"
    script_text = read_text(script_path)
    area_text, _area_start, _area_end = get_area_block(script_text, args.area)
    x, y, z, pitch, yaw, roll, beh_param = find_marker(area_text, args.marker)

    warnings: list[str] = []
    if abs(roll) > 1e-6:
        warnings.append(f"marker has roll Z={roll}; Painting only uses pitch/yaw, so roll gets ignored.")

    if args.type == "normal":
        bottom_req = args.bottom or f"{args.name}2.rgba16.png"
        top_req = args.top or f"{args.name}1.rgba16.png"
        _bottom_src, bottom = resolve_texture(level_dir, bottom_req, 64, 32, warnings)
        _top_src, top = resolve_texture(level_dir, top_req, 64, 32, warnings)
        texture = None
    else:
        bottom = top = None
        texture_req = args.texture or f"{args.name}.rgba16.png"
        _texture_src, texture = resolve_texture(level_dir, texture_req, 32, 32, warnings)

    table_path = root / "src" / "tables" / "painting_array_table.c"
    table_old = read_text(table_path)
    level_values = get_level_values(root)
    group_max = get_painting_group_max(root)
    if level_enum not in level_values:
        die(f"could not resolve {level_enum} from levels/level_defines.h.")

    _tb, _te, table_entries = parse_painting_table(table_old, level_values)
    validate_painting_table(table_entries, group_max)
    table_group = find_table_group(table_entries, level_enum, args.area)
    existing_names = list(table_group["names"]) if table_group is not None else []

    if table_group is not None:
        if struct_name in existing_names:
            actual_idx = existing_names.index(struct_name)
            if actual_idx != args.marker:
                die(f"{struct_name} is already registered at index {actual_idx}; requested marker is {args.marker}.")
        elif args.marker != len(existing_names):
            die(f"next free index for {level_enum} area {args.area} is {len(existing_names)}, not {args.marker}.")
        elif len(existing_names) >= group_max:
            die(f"{level_enum} area {args.area} already hit the {group_max} painting limit.")
    elif args.marker != 0:
        die("this area has no painting group yet, so the first one has to use marker 0.")

    painting_id = choose_painting_id(
        level_dir, [n for n in existing_names if n != struct_name], args.marker, args.painting_id
    )

    changed: list[Path] = []

    # texture stuff
    texture_path = level_dir / TEXTURE_FILE
    texture_old = texture_path.read_text(encoding="utf-8") if texture_path.is_file() else ""
    tex_begin = f"/* === AUTO_PAINTING_TEXTURE:{struct_name}:BEGIN === */"
    tex_end = f"/* === AUTO_PAINTING_TEXTURE:{struct_name}:END === */"
    tex_body = make_texture_block(level_id, name_id, args.type, bottom, top, texture)
    texture_new = upsert_marked_block(texture_old, tex_begin, tex_end, tex_body)
    write_if_changed(texture_path, texture_old, texture_new, args.apply, changed)

    # painting stuff
    auto_path = level_dir / AUTO_FILE
    auto_old = auto_path.read_text(encoding="utf-8") if auto_path.is_file() else '#include "game/paintings.h"\n'
    if args.type == "normal":
        auto_mid = ensure_common_normal(root, auto_old, level_id)
    else:
        auto_mid = ensure_common_continuous(root, auto_old, level_id)
    paint_begin = f"/* === AUTO_PAINTING:{struct_name}:BEGIN === */"
    paint_end = f"/* === AUTO_PAINTING:{struct_name}:END === */"
    paint_body = make_painting_block(
        level_id, name_id, struct_name, args.type, painting_id,
        (x, y, z), (pitch, yaw, roll), args.size, alpha,
    )
    auto_new = upsert_marked_block(auto_mid, paint_begin, paint_end, paint_body)
    write_if_changed(auto_path, auto_old, auto_new, args.apply, changed)

    # header stuff
    header_path = level_dir / "header.h"
    header_old = read_text(header_path)
    header_new = ensure_header_extern(header_old, struct_name)
    write_if_changed(header_path, header_old, header_new, args.apply, changed)

    # leveldata stuff
    leveldata_path = level_dir / "leveldata.c"
    leveldata_old = read_text(leveldata_path)
    leveldata_new = ensure_leveldata_includes(leveldata_old, level)
    write_if_changed(leveldata_path, leveldata_old, leveldata_new, args.apply, changed)

    # rom painting table
    table_new = ensure_level_header_include(table_old, level)
    table_new, _old_names = ensure_table_registration(
        table_new, level_enum, args.area, struct_name, args.marker, level_values, group_max
    )
    write_if_changed(table_path, table_old, table_new, args.apply, changed)

    audits = audit_fast64(level_dir, level, args.area, area_text, args.marker)

    mode = "APPLIED" if args.apply else "DRY RUN"
    print("=" * 66)
    print(f" RIPPLE PAINTING INSTALLER - {mode}")
    print("=" * 66)
    print(f"Repo       : {root}")
    print(f"Level      : {level} ({level_enum})")
    print(f"Area       : {args.area}")
    print(f"Name       : {args.name}")
    print(f"Struct     : {struct_name}")
    print(f"Type       : {args.type.upper()}")
    print(f"Marker     : {args.marker} / 0x{beh_param:08X}")
    print(f"Position   : ({x:g}, {y:g}, {z:g})")
    print(f"Rotation   : pitch={pitch:g}, yaw={yaw:g}, roll={roll:g}")
    print(f"Size       : {args.size:g}")
    print(f"Painting ID: 0x{painting_id:04X}")
    print()

    if changed:
        print("Files " + ("changed:" if args.apply else "that would change:"))
        for p in changed:
            print(f"  [CHANGE] {p.relative_to(root)}")
    else:
        print("[OK] nothing to change in the files this script handles.")

    print("\nFast64 check:")
    for line in audits:
        print("  " + line)

    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print("  [WARN] " + w)

    if not args.apply:
        print("\nnothing was written. if it looks good, run it again with --apply.")
    else:
        print("\n[OK] done. check git diff and then run make -j$(nproc).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallerError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        raise SystemExit(1)
