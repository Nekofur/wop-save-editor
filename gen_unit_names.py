# -*- coding: utf-8 -*-
"""从游戏语言包生成载具中英文名字表 unit-names.json。

游戏把载具名放在 `client/lang.vromfs.bin` 的 `lang/units.csv` 里，键是
`<载具ID>_shop`（商店/机库用名）、`<载具ID>_0`（全称）、`<载具ID>_1`（简称）。
本脚本解包该文件，按优先级取中文名，生成 `unit-names.json` 供修改器显示。

因为解包依赖游戏自带的 vromfs 工具与 zstandard，必须用游戏自带 Python 运行：

    runtime\\python\\python.exe -X utf8 gen_unit_names.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wop_profile  # noqa: E402  复用同一套游戏目录发现逻辑

WORK = HERE / "work"
UNITS_CSV = WORK / "lang" / "units.csv"
OUTPUT = HERE / "unit-names.json"

KEY_FIELD = "<ID|readonly|noverify>"
ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff]")


def find_game_root() -> Path:
    root = wop_profile.find_game_root()
    if root is None:
        raise SystemExit("没有找到 War Thunder Offline 游戏目录，请先填写 config.json 的 gameRoot。")
    return root


GAME_ROOT = find_game_root()


def extract_units_csv() -> Path:
    """从当前客户端的语言包解出 lang/units.csv。"""
    for relative in ("work/python-libs", "work/vromfs-tool/src", "tools"):
        candidate = GAME_ROOT / relative
        if not candidate.is_dir():
            raise SystemExit(
                "当前游戏目录缺少解包工具，无法自动提取语言文件。\n"
                "本工具依赖离线项目自带的 work/vromfs-tool 与 work/python-libs，\n"
                "如果你的目录里已经有 work/units-localization.csv，直接删掉本脚本的 work 目录后重跑即可。")
        sys.path.insert(0, str(candidate))
    import extract_resources  # noqa: F401  适配 construct 的流接口
    from vromfs.bin import BinFile
    from vromfs.vromfs import VromfsFile

    archive = VromfsFile(BinFile(GAME_ROOT / "client" / "lang.vromfs.bin"))
    UNITS_CSV.parent.mkdir(parents=True, exist_ok=True)
    result = archive.unpack(Path("lang/units.csv"), WORK)
    if result.error or not UNITS_CSV.is_file():
        raise SystemExit(f"解包语言包失败：{result.error}")
    return UNITS_CSV


def resolve_units_csv() -> Path:
    """优先用本地缓存，其次用项目里已有的人工解包结果，最后现场解包。"""
    if UNITS_CSV.is_file():
        return UNITS_CSV
    legacy = GAME_ROOT / "work" / "units-localization.csv"
    if legacy.is_file():
        return legacy
    return extract_units_csv()


def clean(text: str) -> str:
    return ZERO_WIDTH.sub("", text or "").strip()


def build() -> None:
    path = resolve_units_csv()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = {row.get(KEY_FIELD, ""): row for row in csv.DictReader(stream, delimiter=";")}

    catalog = json.loads((GAME_ROOT / "data" / "unit-catalog.json").read_text(
        encoding="utf-8-sig"))["units"]

    names = {}
    missing = []
    for unit in catalog:
        picked = None
        for suffix in ("_shop", "_0", "_1"):
            row = rows.get(unit + suffix)
            if row and clean(row.get("<Chinese>") or ""):
                picked = (row, suffix)
                break
        if picked is None:
            missing.append(unit)
            continue
        row, suffix = picked
        names[unit] = {
            "cn": clean(row.get("<Chinese>") or ""),
            "en": clean(row.get("<English>") or ""),
            "source": suffix.lstrip("_"),
        }

    payload = {
        "generatedFrom": path.as_posix(),
        "langArchive": (GAME_ROOT / "client" / "lang.vromfs.bin").stat().st_size,
        "catalogSize": len(catalog),
        "named": len(names),
        "missing": len(missing),
        "units": names,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                      encoding="utf-8")
    print(f"写出 {OUTPUT}：{len(names)}/{len(catalog)} 辆有名字，缺失 {len(missing)} 辆")
    if missing:
        print("缺失示例：" + ", ".join(missing[:10]))


if __name__ == "__main__":
    build()
