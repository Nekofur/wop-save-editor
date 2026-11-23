# -*- coding: utf-8 -*-
"""WOP 存档桥接层：用游戏自己的 backend.ProfileStore 读写本地账号存档。

设计要点
--------
1. 不手改 JSON / SQLite：所有写入都经过游戏后端，保证 revision 自增、
   profile.json 快照发布、operations 幂等表、载具库存同步等行为一致。
2. 货币走官方 `edit_profile` 动作；载具走官方 `grant_unit` 动作。
   批量解锁时为了速度，使用后端内部方法完成同一次事务，并在最后重新
   打开存档让后端自己的初始化流程再同步一遍（弹药、改装、编制等）。
3. 所有写入前自动备份整份账号目录。
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_GAME_ROOT = Path(r"D:\game\WarThunderOffline\WarThunderOffline")


def app_dir() -> Path:
    """打包成 exe 后，配置、备份与日志放在 exe 同级目录而不是临时解包目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = app_dir()
BACKUP_ROOT = APP_DIR / "backups"
LOG_ROOT = APP_DIR / "logs"
NAMES_PATH = APP_DIR / "unit-names.json"
BUNDLED_NAMES = Path(__file__).resolve().parent / "unit-names.json"


def load_unit_names() -> dict:
    """载具中英文名表，由 gen_unit_names.py 从游戏语言包生成。"""
    for path in (NAMES_PATH, BUNDLED_NAMES):
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if document.get("units"):
            return document["units"]
    return {}

COUNTRY_LABELS = {
    "country_usa": "美国",
    "country_germany": "德国",
    "country_ussr": "苏联",
    "country_britain": "英国",
    "country_japan": "日本",
    "country_china": "中国",
    "country_italy": "意大利",
    "country_france": "法国",
    "country_sweden": "瑞典",
    "country_israel": "以色列",
}

GROUP_LABELS = {
    "army": "陆军",
    "aviation": "空军",
    "helicopters": "直升机",
    "ships": "舰船",
    "boats": "小艇",
    "": "其他",
}

CLASS_LABELS = {
    "exp_fighter": "战斗机",
    "exp_bomber": "轰炸机",
    "exp_assault": "攻击机",
    "exp_tank": "中型坦克",
    "exp_tank_destroyer": "坦克歼击车",
    "exp_heavy_tank": "重型坦克",
    "exp_SPAA": "自行防空炮",
    "exp_destroyer": "驱逐舰",
    "exp_cruiser": "巡洋舰",
    "exp_helicopter": "直升机",
    "exp_torpedo_boat": "鱼雷艇",
    "exp_gun_boat": "炮艇",
    "exp_torpedo_gun_boat": "鱼雷炮艇",
    "exp_submarine_chaser": "猎潜艇",
    "exp_human": "步兵/特殊",
}


def is_game_root(path: Path) -> bool:
    """判断目录是不是游戏根目录（含 backend/service.py 与 data/unit-catalog.json）。"""
    path = Path(path)
    return ((path / "backend" / "service.py").is_file()
            and (path / "data" / "unit-catalog.json").is_file())


SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", "__pycache__", "node_modules"}


def resolve_game_root(path, max_depth: int = 3, limit: int = 400) -> Path | None:
    """玩家选任意相关目录都行：自动向下找到真正的游戏根目录。

    D:\\game、D:\\game\\WarThunderOffline 这类“只差一层”的选择都能救回来，
    采用广度优先，返回层级最浅的那个匹配目录。
    """
    start = Path(path)
    if is_game_root(start):
        return start
    frontier = [start]
    visited = 0
    for _ in range(max_depth):
        following = []
        for folder in frontier:
            try:
                children = sorted(p for p in folder.iterdir() if p.is_dir())
            except OSError:
                continue
            for child in children:
                if child.name in SKIP_DIRS or child.name.startswith("$"):
                    continue
                visited += 1
                if visited > limit:
                    return None
                if is_game_root(child):
                    return child
                following.append(child)
        frontier = following
    return None


def find_game_root() -> Path | None:
    """配置文件优先，其次尝试常见路径。"""
    config = APP_DIR / "config.json"
    candidates = []
    if config.is_file():
        try:
            candidates.append(Path(json.loads(config.read_text(encoding="utf-8"))["gameRoot"]))
        except (ValueError, KeyError, OSError):
            pass
    candidates.append(DEFAULT_GAME_ROOT)
    candidates.append(Path(r"D:\WarThunderOffline"))
    for candidate in candidates:
        if is_game_root(candidate):
            return candidate
        if candidate.is_dir():
            resolved = resolve_game_root(candidate)
            if resolved is not None:
                return resolved
    return None


def list_accounts(game_root: Path) -> list[dict]:
    """列出 Save 下带 account.json 的本地账号。"""
    save_root = game_root / "Save"
    accounts = []
    if not save_root.is_dir():
        return accounts
    for folder in sorted(save_root.iterdir()):
        account = folder / "account.json"
        if not folder.is_dir() or not account.is_file():
            continue
        if not (folder / "profile.sqlite3").is_file():
            continue
        try:
            data = json.loads(account.read_text(encoding="utf-8-sig"))
            username = data.get("username") or folder.name
        except (ValueError, OSError):
            username = folder.name
        accounts.append({"folder": folder.name, "path": folder, "username": username})
    return accounts


def active_account_folder(game_root: Path) -> str:
    """游戏登录后写入 data/active-save.txt，用来确定默认账号。"""
    pointer = game_root / "data" / "active-save.txt"
    if pointer.is_file():
        try:
            return pointer.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return ""


def _decode_console(data: bytes) -> str:
    """tasklist 输出是系统 OEM 代码页，UTF-8 模式下不能按 UTF-8 解。"""
    for encoding in ("oem", "mbcs", "gbk"):
        try:
            return (data or b"").decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return (data or b"").decode("latin-1", "replace")


def game_running() -> str:
    """返回非空字符串表示游戏或本地服务正在占用存档。"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq aces.exe", "/NH"],
            capture_output=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if "aces.exe" in _decode_console(result.stdout).lower():
            return "检测到 aces.exe 正在运行"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with socket.create_connection(("127.0.0.1", 43173), timeout=0.4):
            return "检测到本地存档服务正在 43173 端口运行"
    except OSError:
        pass
    return ""


class LiveSession:
    """游戏运行时的写入通道：通过游戏自己的本地服务 HTTP 接口下发命令。

    游戏启动后会在存档目录写 runtime.json（含 baseUrl 与一次性 token）。
    走 HTTP 意味着写入由游戏服务进程自己完成，不会出现两个进程同时写数据库。
    """

    def __init__(self, base_url: str, token: str, save_dir: Path):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.save_dir = Path(save_dir)

    @classmethod
    def discover(cls, save_dir: Path) -> "LiveSession | None":
        """校验三件事：服务活着、就是写 runtime.json 的那个进程、且已打开本账号。"""
        save_dir = Path(save_dir)
        runtime = save_dir / "runtime.json"
        if not runtime.is_file():
            return None
        try:
            document = json.loads(runtime.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        base_url = document.get("baseUrl")
        token = document.get("token")
        pid = document.get("pid")
        if not isinstance(base_url, str) or not isinstance(token, str):
            return None
        session = cls(base_url, token, save_dir)
        try:
            health = session._request("GET", "/health", authorized=False)
        except (OSError, ValueError):
            return None
        if health.get("app") != "WarThunderOffline.local-profile":
            return None
        if pid is not None and health.get("pid") != pid:
            return None
        try:
            state = session.profile()
        except ProfileError:
            return None   # 409：服务还停在登录页，没有打开任何账号
        except (OSError, ValueError):
            return None
        try:
            expected = json.loads((save_dir / "account.json").read_text(
                encoding="utf-8-sig")).get("username")
        except (ValueError, OSError):
            expected = None
        if expected and state.get("profile", {}).get("name") != expected:
            return None
        return session

    def _request(self, method: str, path: str, body=None, authorized=True):
        import urllib.error
        import urllib.request

        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if authorized:
            request.add_header("X-WTOFFLINE-Token", self.token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            try:
                message = json.loads(detail).get("error") or detail
            except ValueError:
                message = detail
            raise ProfileError(f"游戏服务返回错误：{message}") from error
        return json.loads(payload)

    def profile(self) -> dict:
        return self._request("GET", "/v1/profile")

    def command(self, action: str, payload: dict, revision: int | None = None) -> dict:
        if revision is None:
            revision = self.profile()["revision"]
        return self._request("POST", "/v1/command", {
            "operationId": "editor-live-" + datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "revision": revision,
            "action": action,
            "payload": payload,
        })


class ProfileError(RuntimeError):
    pass


def read_snapshot_files(save_dir: Path) -> dict:
    """只读快照：复制数据库与 WAL 到临时目录再读，绝不碰运行中的存档。"""
    import sqlite3
    import tempfile

    save_dir = Path(save_dir)
    source = save_dir / "profile.sqlite3"
    if not source.is_file():
        raise ProfileError("存档缺少 profile.sqlite3")
    with tempfile.TemporaryDirectory(prefix="wop-editor-") as folder:
        target = Path(folder) / "profile.sqlite3"
        shutil.copy2(source, target)
        for suffix in ("-wal", "-shm"):
            extra = save_dir / ("profile.sqlite3" + suffix)
            if extra.is_file():
                shutil.copy2(extra, Path(folder) / ("profile.sqlite3" + suffix))
        connection = sqlite3.connect(target)
        try:
            row = connection.execute(
                "SELECT revision,value FROM profile WHERE id=1").fetchone()
        finally:
            connection.close()
    if row is None:
        raise ProfileError("存档数据库里没有进度记录")
    revision, value = row
    profile = json.loads(value)
    return {
        "revision": int(revision),
        "name": profile.get("name", ""),
        "gold": int(profile.get("gold", 0)),
        "silver": int(profile.get("silver", 0)),
        "gaijinCoins": int(profile.get("gaijinCoins", 0)),
        "owned": set(profile.get("ownedUnits", [])),
        "activeBattle": bool(profile.get("activeBattle")),
        "savedAt": datetime.fromtimestamp(
            source.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


class GameProfile:
    """一个本地账号存档的读写封装。"""

    def __init__(self, game_root: Path, save_dir: Path):
        self.game_root = Path(game_root)
        self.save_dir = Path(save_dir)
        self._service = None
        self._store = None
        self.catalog = json.loads(
            (self.game_root / "data" / "unit-catalog.json").read_text(encoding="utf-8-sig"))["units"]
        self.hidden_pool = json.loads(
            (self.game_root / "data" / "hidden-vehicle-pool.json").read_text(encoding="utf-8-sig"))
        self.names = load_unit_names()

    # ---------- 基础 ----------
    @property
    def account_name(self) -> str:
        try:
            return json.loads((self.save_dir / "account.json").read_text(
                encoding="utf-8-sig")).get("username", self.save_dir.name)
        except (ValueError, OSError):
            return self.save_dir.name

    def _backend(self):
        if self._store is None:
            backend = str(self.game_root / "backend")
            if backend not in sys.path:
                sys.path.insert(0, backend)
            try:
                import service  # 游戏自带后端
            except Exception as error:  # noqa: BLE001 - 明确告知失败原因
                raise ProfileError(f"无法加载游戏后端：{error}") from error
            self._service = service
            self._store = service.ProfileStore(self.save_dir, self.catalog)
        return self._store

    def close(self):
        if self._store is not None:
            self._store.close()
            self._store = None

    def reopen(self):
        self.close()
        self._backend()

    def snapshot(self) -> dict:
        # 读取永远走独立副本，不打开正在被游戏使用的数据库。
        return read_snapshot_files(self.save_dir)

    def live(self) -> LiveSession | None:
        """游戏正在运行时返回可用的实时通道，否则返回 None。"""
        if not game_running():
            return None
        return LiveSession.discover(self.save_dir)

    def ensure_closed(self):
        reason = game_running()
        if reason:
            raise ProfileError(
                f"{reason}。请先完全退出游戏（包括启动器）再修改存档，"
                "否则可能损坏存档或出现数值不一致。")

    # ---------- 备份 ----------
    def backup(self) -> Path:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = BACKUP_ROOT / f"{self.save_dir.name}-{stamp}"
        suffix = 1
        while target.exists():
            target = BACKUP_ROOT / f"{self.save_dir.name}-{stamp}-{suffix}"
            suffix += 1
        shutil.copytree(self.save_dir, target)
        return target

    # ---------- 货币 ----------
    def set_currency(self, gold=None, silver=None, coins=None) -> dict:
        payload = {}
        if gold is not None:
            payload["gold"] = int(gold)
            if payload["gold"] < 0:
                raise ProfileError("金鹰不能为负数")
        if silver is not None:
            payload["silver"] = int(silver)
            if payload["silver"] < 0:
                raise ProfileError("银狮不能为负数")
        if coins is not None:
            payload["gaijinCoins"] = int(coins)
            if payload["gaijinCoins"] < 0:
                raise ProfileError("Gaijin 币不能为负数")
        if not payload:
            raise ProfileError("没有需要修改的货币字段")
        session = self.live()
        if session is not None:
            result = session.command("edit_profile", payload)
            profile = result["profile"]
            return {
                "revision": result["revision"],
                "gold": int(profile.get("gold", 0)),
                "silver": int(profile.get("silver", 0)),
                "gaijinCoins": int(profile.get("gaijinCoins", 0)),
                "mode": "live",
            }
        self.ensure_closed()
        store = self._backend()
        operation = "editor-currency-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
        result = store.apply({
            "operationId": operation,
            "revision": store.read()["revision"],
            "action": "edit_profile",
            "payload": payload,
        })
        profile = result["profile"]
        return {
            "revision": result["revision"],
            "gold": int(profile.get("gold", 0)),
            "silver": int(profile.get("silver", 0)),
            "gaijinCoins": int(profile.get("gaijinCoins", 0)),
            "mode": "file",
        }

    # ---------- 载具 ----------
    def unit_row(self, unit: str) -> dict:
        """把目录条目整理成界面需要的字段。"""
        info = self.catalog.get(unit, {})
        names = self.names.get(unit, {})
        return {
            "id": unit,
            "name": names.get("cn", ""),
            "nameEn": names.get("en", ""),
            "displayName": names.get("cn") or unit,
            "country": info.get("country", ""),
            "countryLabel": COUNTRY_LABELS.get(info.get("country", ""), info.get("country", "")),
            "group": info.get("shopGroup", ""),
            "groupLabel": GROUP_LABELS.get(info.get("shopGroup", ""), info.get("shopGroup", "")),
            "class": info.get("class", ""),
            "classLabel": CLASS_LABELS.get(info.get("class", ""), info.get("class", "")),
            "rank": info.get("rank", 0),
            "battleRating": info.get("battleRating", 0),
            "gold": int(info.get("gold", 0) or 0),
            "silver": int(info.get("silver", 0) or 0),
            "event": bool(info.get("event")),
            "gift": bool(info.get("gift")),
            "inShop": bool(info.get("inShop")),
            "inHiddenPool": unit in set(self.hidden_pool.get("approvedUnits", [])),
        }

    def all_units(self) -> list[dict]:
        rows = [self.unit_row(unit) for unit in self.catalog]
        rows.sort(key=lambda row: (row["countryLabel"], row["groupLabel"], row["rank"], row["id"]))
        return rows

    def hidden_units(self) -> list[dict]:
        approved = self.hidden_pool.get("approvedUnits", [])
        rows = [self.unit_row(unit) for unit in approved if unit in self.catalog]
        rows.sort(key=lambda row: (row["countryLabel"], row["groupLabel"], row["rank"], row["id"]))
        return rows

    def grant_units(self, units: list[str]) -> dict:
        """解锁（直接给予）载具：离线一次事务写完，游戏运行时走本地服务。"""
        targets = [unit for unit in dict.fromkeys(units) if unit in self.catalog]
        session = self.live()
        if session is not None:
            return self._grant_live(session, targets)
        self.ensure_closed()
        if not targets:
            return {"added": 0, "skipped": [], "revision": self._backend().read()["revision"],
                    "mode": "file"}
        store = self._backend()
        owned = set(store.read()["profile"]["ownedUnits"])
        target_set = set(targets) | owned
        accepted, skipped = [], []
        for unit in targets:
            info = self.catalog[unit]
            master = info.get("masterUnit") or ""
            if master and master not in target_set:
                skipped.append((unit, "需要先拥有配套雷达车"))
                continue
            if unit in owned:
                continue
            accepted.append(unit)
        if not accepted:
            return {"added": 0, "skipped": skipped, "revision": store.read()["revision"],
                    "mode": "file"}
        added = self._grant_fast(store, accepted)
        if added is None:  # 后端内部结构变化时退回逐条官方命令
            added = 0
            for unit in accepted:
                store.apply({
                    "operationId": "editor-unit-" + datetime.now().strftime("%Y%m%d%H%M%S%f"),
                    "revision": store.read()["revision"],
                    "action": "grant_unit",
                    "payload": {"unit": unit},
                })
                added += 1
        self.reopen()  # 让后端初始化流程同步弹药、改装、编制等
        return {"added": added, "skipped": skipped,
                "revision": self._backend().read()["revision"], "mode": "file"}

    def _grant_live(self, session: "LiveSession", targets: list[str]) -> dict:
        """游戏运行中：逐条通过本地服务下发 grant_unit。"""
        state = session.profile()
        profile = state["profile"]
        revision = state["revision"]
        owned = set(profile.get("ownedUnits", []))
        target_set = set(targets) | owned
        accepted, skipped = [], []
        for unit in targets:
            info = self.catalog[unit]
            master = info.get("masterUnit") or ""
            if master and master not in target_set:
                skipped.append((unit, "需要先拥有配套雷达车"))
                continue
            if unit in owned:
                continue
            accepted.append(unit)
        added = 0
        for unit in accepted:
            result = session.command("grant_unit", {"unit": unit}, revision=revision)
            revision = result["revision"]
            added += 1
        return {"added": added, "skipped": skipped, "revision": revision, "mode": "live"}

    def _grant_fast(self, store, units: list[str]):
        """复刻 store.apply 在同一动作后的同步步骤，一次写入完成批量解锁。

        返回实际新增数量；后端接口不匹配时返回 None，由调用方退回逐条命令。
        """
        service = self._service
        modifications = getattr(service, "modifications", None)
        if modifications is None:
            return None
        try:
            lock = store.lock
            read_encoded = store._read_encoded
            publish = store._publish
            sync_linked = store._sync_linked_units
            sync_pack_skins = store._sync_pack_skins
            sync_crew = store._sync_crew_skill_snapshots
            sync_ownership = store._sync_ownership_unlocks
            seed_targets = store._seed_research_targets
            save_lineup = store._save_active_lineup
        except AttributeError:
            return None
        profile = None
        with lock:
            revision, encoded = read_encoded()
            profile = json.loads(encoded)
            owned_before = len(profile["ownedUnits"])
            owned = set(profile["ownedUnits"])
            for unit in units:
                if unit in owned:
                    continue
                profile["ownedUnits"].append(unit)
                if unit not in profile["unlockedUnits"]:
                    profile["unlockedUnits"].append(unit)
                profile.setdefault("unitExperience", {}).setdefault(unit, 0)
                owned.add(unit)
            sync_linked(profile)
            sync_pack_skins(profile)
            modifications.sync(profile)
            new_units = profile["ownedUnits"][owned_before:]
            if new_units:
                modifications.initialize_vehicle_stock(profile, new_units)
            service.profile_progression.sync(profile)
            sync_crew(profile)
            sync_ownership(profile)
            seed_targets(profile)
            save_lineup(profile)
            profile["countryCrews"][profile["country"]] = json.loads(
                json.dumps(profile["crewGroups"]))
            profile["countrySelections"].setdefault(
                profile["country"], {})[profile["group"]] = profile["selectedUnit"]
            result = {"schemaVersion": service.SCHEMA,
                      "revision": revision + 1, "profile": profile}
            payload = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
            operation = "editor-units-" + datetime.now().strftime("%Y%m%d%H%M%S%f")
            receipt = json.dumps({"schemaVersion": service.SCHEMA,
                                  "revision": result["revision"],
                                  "action": "grant_unit", "count": len(new_units)})
            with store.db:
                store.db.execute("UPDATE profile SET revision=?,value=? WHERE id=1",
                                 (result["revision"], payload))
                store.db.execute("INSERT OR REPLACE INTO operations VALUES(?,?)",
                                 (operation, receipt))
            publish(result, encoded_profile=payload)
        return len(profile["ownedUnits"]) - owned_before
