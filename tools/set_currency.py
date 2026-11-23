# -*- coding: utf-8 -*-
"""Set Golden Eagles / Silver Lions for one local War Thunder Offline save.

Uses the game's own backend ProfileStore so that SQLite revision handling,
profile.json publication and the edit_profile whitelist are all respected,
instead of hand-editing files.

Usage:
    python.exe -X utf8 set_currency.py <save-folder-name> <gold> <silver>
"""
import json
import pathlib
import secrets
import sys

PROJECT = pathlib.Path(r"D:\game\WarThunderOffline\WarThunderOffline")


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    folder, gold, silver = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    save = PROJECT / "Save" / folder
    if not (save / "profile.sqlite3").is_file():
        raise SystemExit(f"no save database in {save}")

    sys.path.insert(0, str(PROJECT / "backend"))
    import service  # noqa: E402

    catalog = json.loads(
        (PROJECT / "data" / "unit-catalog.json").read_text(encoding="utf-8-sig"))["units"]
    store = service.ProfileStore(save, catalog)
    try:
        snapshot = store.read()
        before = snapshot["profile"]
        print("before:", {"name": before.get("name"), "gold": before.get("gold"),
                          "silver": before.get("silver"), "revision": snapshot["revision"]})
        command = {
            "operationId": "set-currency-" + secrets.token_hex(6),
            "revision": snapshot["revision"],
            "action": "edit_profile",
            "payload": {"gold": gold, "silver": silver},
        }
        result = store.apply(command)
        after = result["profile"]
        print("after:", {"name": after.get("name"), "gold": after.get("gold"),
                         "silver": after.get("silver"), "revision": result["revision"]})
        published = json.loads((save / "profile.json").read_text(encoding="utf-8"))
        print("published profile.json:", {"gold": published["profile"].get("gold"),
                                          "silver": published["profile"].get("silver"),
                                          "revision": published["revision"]})
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
