# session-finder

[English](README.md)

把散在 **Claude Code**、**ChatGPT Codex**、**WorkBuddy**、**CodeBuddy CLI** 里的历史会话找回来、
读出来、接着干——可选跨多台电脑。

四个客户端都把对话存成 jsonl，但位置、字段、标题来源各不相同，单个文件能到 20 MB。
session-finder 是一个 agent skill 加一个 Python 脚本：建索引，按标题、对话内容或 id
定位，只把需要的那几段读出来。这样你的 agent 能回答「上周那个会话最后定了什么」
「把笔记本上那个会话接着做」，又不会把上下文撑爆。

## 安装

需要 Python 3.9+ 和 git。

**Windows（PowerShell）**

```powershell
git clone https://github.com/WaziXuan/session-finder
cd session-finder
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**macOS / Linux**

```bash
git clone https://github.com/WaziXuan/session-finder
cd session-finder
bash install.sh
```

安装脚本把 skill 拷到 `~/.workbuddy/skills/session-finder/`（脚本 + 装了 `zstandard` 的 venv，
读 Codex 的压缩会话要用），再把 `SKILL.md` 放进你装了的客户端：`~/.claude/skills/`、
`~/.codex/skills/`、`~/.codebuddy/skills/`。不会有后台进程，也不会往外传任何东西。
装完**新开一个会话**，skill 才会加载。

想让 agent 替你装，把这段贴给它：

> 安装 https://github.com/WaziXuan/session-finder：克隆下来，先读 install.ps1（或 install.sh），
> 不带任何同步参数运行；然后用它建的 venv python 跑 `find_session.py list --limit 5`，
> 把输出给我看。遇到权限拦截就停下来告诉我，不要绕过。

## 用法

直接用大白话问 agent：「找一下配 CI 的那个会话」「我之前问 Codex 的 ffmpeg 那个问题」
「接着昨天那个重构会话干」。也可以自己跑：

```bash
S=~/.workbuddy/skills/session-finder/scripts/find_session.py
PY=~/.workbuddy/skills/session-finder/.venv/bin/python          # Windows: .venv/Scripts/python.exe

$PY $S list                        # 四个来源按时间倒序，标签如 cx|01a16eae、cl|11c4d28c
$PY $S list --source cl            # all | wb | cx | cl | cb
$PY $S find 发票 解析               # 搜标题 + 首问
$PY $S grep docker compose         # 全文；所有词须在同一会话里出现
$PY $S grep PIPESTATUS --deep      # 连工具命令和输出一起搜
$PY $S show "cl|11c4d28c"          # 你在里面说过的话（默认截断）
$PY $S show "cl|11c4d28c" --role all --tail 15 --max-chars 1500   # 最后做到哪
$PY $S show cx|01a07b55 --role all --grep ffmpeg --head 5
$PY $S files "cl|e04d7a2d"         # 这个会话碰过哪些文件
```

输出永远截断：这个工具存在的意义之一就是别把 20 MB 的记录灌进上下文。

### 接着某个会话干

不做格式转换，也不用各家的原生 resume：由你正在对话的 agent 按固定顺序读旧会话——
先看你提过的所有要求，再看最后几条完整往来，再看碰过的文件（并检查本机有没有）——
用两三句话跟你确认现状，然后接着做。四个客户端之间任意互通，换电脑也行。
具体步骤见 [SKILL.md](session-finder/SKILL.md#continue--inherit-a-session)。

## 可选：多台电脑同步

想在一台电脑上搜到其他电脑的会话：每台机器把自己的会话压缩后写进一个由同步盘共享的
文件夹（OneDrive、Dropbox、Syncthing、rclone 镜像都行），并从那里读其他机器的索引。

```powershell
.\install.ps1 -Sync                                    # 用 OneDrive\SessionSync
.\install.ps1 -Sync -SyncRoot D:\Dropbox\SessionSync   # 或任意共享文件夹
```

```bash
bash install.sh --sync ~/Dropbox/SessionSync           # Linux：加一个 systemd --user 服务
# 没有同步客户端的 Linux：rclone 单向镜像用来读，rclone copy 用来写
SESSION_SYNC_RCLONE='onedrive:SessionSync' bash install.sh --sync ~/OneDrive/SessionSync
```

Windows 上会注册计划任务 `SessionFinderSync`，常驻运行 `find_session.py watch`：会话停写
60 秒后推送（一直在写的最多 5 分钟一推），另外每 30 分钟全量过一遍。上传是增量的——jsonl
只会往后追加，所以只传新增的尾部，存成 `.zst` 分块。其他电脑的结果标签形如
`cl@LAPTOP|xxxx`，`--host local` / `--host LAPTOP` 过滤。搜索只读小索引和纯对话文本
（每台约 1.6 MB），`show` 时才下载那一个会话的原始记录。

macOS 没有自带服务：需要自己用 launchd 在登录时跑 `find_session.py watch`。

**隐私：** 同步出去的记录包含你和 agent 说过的一切，包括命令输出。只放在你信得过的存储里。

关闭：`find_session.py sync-root --off`，再删掉计划任务或服务。

**多台电脑之间自动更新：** 在一台电脑上 `git pull`（自己改了代码的话先调大
`find_session.py` 里的 `VERSION`），运行 `publish.ps1`；其他电脑 10 分钟内会从
`<SessionSync>/_install` 自动更新。

## 说明

- 会话文件只读。脚本只写自己的缓存/日志/状态目录（`%LOCALAPPDATA%\session-finder\` 或
  `~/.cache/session-finder/`），开了同步时再加 `<SessionSync>/<本机名>/`。
- Codex 自己的审批复查代理会写大量「Guardian review」会话，已跳过，否则会淹没所有搜索结果。
  注入的提示词（WorkBuddy 的 `<user_info>`、Codex 的 `AGENTS.md instructions`、Claude Code
  的工具结果）不算作「你说的话」。
- Codex 的 `session_index.jsonl` 只给约一半会话起了名字，其余用首问当标题。

## 卸载

```powershell
Unregister-ScheduledTask SessionFinderSync -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:USERPROFILE\.workbuddy\skills\session-finder", "$env:LOCALAPPDATA\session-finder"
Remove-Item -Recurse -Force "$env:USERPROFILE\.claude\skills\session-finder", "$env:USERPROFILE\.codex\skills\session-finder", "$env:USERPROFILE\.codebuddy\skills\session-finder" -ErrorAction SilentlyContinue
```

```bash
systemctl --user disable --now session-finder-sync 2>/dev/null
rm -rf ~/.workbuddy/skills/session-finder ~/.cache/session-finder ~/.{claude,codex,codebuddy}/skills/session-finder
```

## 协议

MIT
