# WOP Save Editor · War Thunder Offline 存档修改器

一个给 **War Thunder Offline（WOP 离线版）** 用的本地存档修改器：
改金鹰 / 银狮 / Gaijin 币，解锁任意载具，查看并解锁隐藏载具，**游戏运行中也能实时修改**。
界面为 Windows 图形界面，载具列显示游戏内中文名。

> 非官方工具，与 Gaijin Entertainment 无关，不包含任何游戏资源。仅用于你本机的离线存档。

![Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 功能

- **货币**：金鹰（Golden Eagles）、银狮（Silver Lions）、Gaijin 币（GJN）设为任意值，上限 1 万亿，附带快速填入按钮。
- **载具解锁**：列出全部 3471 辆载具，显示**游戏内中文名 + 目录 ID**；按国家、兵种、状态（已拥有/未拥有/隐藏池）筛选，中英文名与 ID 均可搜索；可解锁选中项或整个筛选结果。
- **隐藏载具**：单独列出 261 辆隐藏载具（被研发、购买资格、限时销售挡住的），可查看并一键解锁。
- **实时模式**：游戏开着时通过游戏自己的本地服务写入，不必退出游戏。
- **安全**：写入前自动整份备份存档；连不上本地服务时自动只读；有未结算对局时二次确认；全程日志（`logs/`）。

## 快速开始（Release 版，免安装）

1. 从 [Releases](../../releases) 下载 `WOP-Save-Editor-vX.Y.Z-win64.zip`，**解压整个文件夹**（不要只复制 exe）。
2. 把 `config.example.json` 复制为 `config.json`，把 `gameRoot` 改成你的游戏根目录：
   ```json
   { "gameRoot": "D:\\game\\WarThunderOffline\\WarThunderOffline" }
   ```
3. 双击 `WOP-Save-Editor\WOP-Save-Editor.exe`。
4. 顶部选择账号 → 改货币 / 解锁载具。

> 便携版不写系统临时目录、启动快；`_internal` 目录是运行库，请和 exe 放在一起。
> 部分杀毒软件会对未签名程序误报，如不放心可改用下面的源码方式运行。

## 从源码运行

需要带 tkinter 的 Python 3.9+（Windows 官方安装包自带）：

```powershell
# 1) 配置游戏目录
copy config.example.json config.json   # 然后编辑 gameRoot

# 2) 启动
python -X utf8 wop_editor.py
```

或者双击 `启动修改器.cmd` / `run-editor.cmd`（自动寻找本机 Python），
出错时用 `启动修改器(调试).cmd` 在控制台看报错。

## 实时模式（游戏不退出）

1. 正常启动游戏并**登录**到机库（登录后本地服务才会打开账号存档）。
2. 打开修改器，顶部会显示 `⚡ 游戏运行中：实时模式已连接`。
3. 修改货币或解锁载具，写入由游戏自己的服务进程完成。
4. 让客户端刷新一次存档快照，任选其一：
   - **打开一次游戏内商店再关掉**（客户端在商店窗口关闭时会主动 refresh）；
   - 触发任意会发送命令的操作（客户端遇到修订冲突会自动采用最新存档）；
   - 重启游戏（离线写入同样已经落盘）。

连不上时显示 `⚠ 游戏运行中且未连上本地服务`，表示游戏还停在登录页，登录后点“重新检测游戏状态”。

## 命令行工具

`tools/set_currency.py` 用于批量或脚本化设置货币（需要游戏关闭）：

```powershell
& 'D:\game\WarThunderOffline\WarThunderOffline\runtime\python\python.exe' -X utf8 `
  tools\set_currency.py Nekofu 100000000 100000000
```

## 载具中文名

`unit-names.json` 是从游戏语言包 `client/lang.vromfs.bin` 的 `lang/units.csv` 提取的名字表
（键 `<载具ID>_shop/_0/_1`，取 `<Chinese>` 列），覆盖 3422/3471 辆；缺口是步兵武器与少数未本地化条目，
这些显示目录 ID。游戏更新后可自行重生成：

```powershell
& 'D:\game\WarThunderOffline\WarThunderOffline\runtime\python\python.exe' -X utf8 gen_unit_names.py
```

（需要离线项目自带的 `work/vromfs-tool`、`work/python-libs`；若目录里已有 `work/units-localization.csv` 会直接复用。）

## 原理

```
wop_editor.py（tkinter 界面）
      │
      ├─ wop_profile.GameProfile ──► 游戏自带 backend/service.py（ProfileStore）
      │        离线：直接读写 Save\<账号>\profile.sqlite3 + profile.json
      │
      └─ wop_profile.LiveSession ──► http://127.0.0.1:43173（游戏正在跑的服务）
               实时：POST /v1/command，由游戏进程自己写存档
unit-names.json ◄── gen_unit_names.py ◄── client/lang.vromfs.bin
```

- 货币走官方动作 `edit_profile`（白名单含 `gold`、`silver`、`gaijinCoins`）。
- 载具走官方语义：加入 `ownedUnits` / `unlockedUnits`。单辆用官方 `grant_unit`；
  批量离线时在一次事务里复刻 `store.apply()` 之后的同步步骤（联动载具、改装、初始弹药、
  乘员快照、所有权、研发目标、编组），实测 3471 辆约 0.9 秒；后端接口变化时自动退回逐条命令。
- 读取永远不打开正在使用的数据库：把 `profile.sqlite3` 与 `-wal`、`-shm` 复制到临时目录再读。
- 实时模式用 `runtime.json` 的 pid + `/v1/profile` 账号名双重校验，确认连的是本账号的服务。

## 兼容与限制

- 目标版本：WOP 0.9.0 离线版；游戏若改动 `backend/service.py` 内部方法名，会自动退回兼容模式。
- 只支持 Windows。
- 不是“游戏画面内嵌面板”：那需要改客户端 GUI 归档并重建哈希与完整性校验，风险高，未实现。
- 解锁是直接获得载具，不消耗银狮、不需要研发；进游戏后需在对应国家的乘员组里编入才能使用。

## 常见问题

- **双击没反应**：单文件 exe 被杀软拦截，或 `config.json` 路径不对。源码运行请用 `启动修改器(调试).cmd`。
- **实时模式写入后游戏里没变**：按上文第 4 步刷新（开一次商店再关最方便），或重启游戏。
- **名字显示成 ID**：该载具没有中文条目，属正常；可重新生成名字表。
- **想恢复存档**：把 `backups/` 里对应时间戳的目录整份复制回 `Save\<账号名>`。

## 构建 exe

```powershell
# 需要 pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onedir --windowed `
  --name "WOP-Save-Editor" --add-data "unit-names.json;." wop_editor.py
```

产物在 `dist\WOP-Save-Editor\`，把整个目录连同 `config.example.json` 压缩即可分发。

## English summary

A save editor for the offline fan build **War Thunder Offline**. Edit Golden Eagles, Silver Lions
and Gaijin Coins, unlock any of the 3471 vehicles (Chinese names shown), browse 261 hidden vehicles,
and write changes while the game is running through the game's own local service.
Windows only, Python 3.9+ with tkinter, MIT licensed, unofficial and not affiliated with Gaijin.

## 免责声明

本项目为本地离线存档工具的爱好者作品，与 Gaijin Entertainment 及 War Thunder 官方无关；
不包含、不分发任何游戏资源或客户端代码。使用本工具修改存档的风险由使用者自行承担。
