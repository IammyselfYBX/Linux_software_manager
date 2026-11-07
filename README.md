# Linux Software Manager

一个无第三方 Python 依赖的 Linux 终端软件清单与残留清理工具。

它会在运行时检测当前发行版实际存在的包管理器和安装方式，不会把 Manjaro 的页面硬编码到 Ubuntu、CentOS 或其他发行版上。

## 支持范围

### 系统包管理器

| 安装方式 | 典型发行版 | 清单来源 |
|---|---|---|
| `apt` / `dpkg` | Ubuntu、Debian、Linux Mint、Kali | dpkg 数据库、`apt-mark showmanual` |
| `dnf` / `rpm` | Fedora、RHEL、CentOS Stream、Rocky、AlmaLinux | RPM 数据库、dnf 用户安装标记 |
| `yum` / `rpm` | CentOS 7、旧版 RHEL | RPM 数据库 |
| `zypper` / `rpm` | openSUSE、SLES | RPM 数据库 |
| `pacman` | Arch、Manjaro、EndeavourOS | pacman 数据库、显式/依赖、AUR |
| `apk` | Alpine Linux | APK 数据库 |
| `emerge` | Gentoo、Funtoo | `/var/db/pkg`、world 集合 |
| `XBPS` | Void Linux | `xbps-query` |
| `Nix` | NixOS、安装了 Nix 的系统 | Nix 用户 profile |

第一页会优先显示发行版原生包管理器，例如：

- Ubuntu：`系统包管理器 · apt/dpkg`
- Fedora / RHEL：`系统包管理器 · dnf/rpm`
- CentOS 7：`系统包管理器 · yum/rpm`
- openSUSE：`系统包管理器 · zypper/rpm`
- Arch / Manjaro：`系统包管理器 · pacman`

如果同一台机器还有其他包管理器，也会继续列出。

### 应用商店与便携软件

- Flatpak
- Snap
- AppImage
- Homebrew / Linuxbrew

### 语言级包管理器

- Python：`pip --user`、`pipx`
- Node.js：`npm -g`、`yarn global`、`pnpm -g`
- Rust：`cargo install`
- Ruby：`gem`
- Go：`$GOPATH/bin`
- Conda：当前环境
- PHP：Composer global
- Lua：LuaRocks

### 其他

- 源码 / 手动安装
- `~/.local/bin`、用户 Desktop 入口、`/opt`、`/usr/local`
- 已卸载软件的残留用户数据和缓存

## 页面结构

1. **安装方式页**：只显示当前系统检测到的安装方式。
2. **软件列表页**：列出该方式下的全部软件、依赖和第三方包。
3. **详情页**：
   - 软件本体及安装位置
   - 包管理器登记的文件
   - 依赖、可选依赖、反向依赖
   - 用户数据
   - 缓存和包缓存
   - 卸载命令
   - 当前软件的完整导出

## 快捷键

| 页面 | 按键 | 功能 |
|---|---|---|
| 安装方式 | ↑/↓、1-9、Enter | 选择并进入 |
| 软件列表 | ↑/↓、PgUp/PgDn、Home/End | 移动 |
| 软件列表 | `/` | 搜索 |
| 软件列表 | `f` | 全部 / 显式安装 / 依赖包 / 第三方 |
| 软件列表 | `e` | 导出整份清单 |
| 详情 | ↑/↓、PgUp/PgDn、Home/End | 滚动 |
| 详情 | `e` | 导出当前软件完整详情 |
| 详情 | `d` | 清理用户数据/缓存或删除便携/源码程序 |
| 任意页面 | `r` | 重新扫描 |
| 返回/退出 | `Esc` / `q` | 返回；详情页 `q` 退出 |

## 颜色

- 绿色：已安装，并发现包记录或启动入口
- 黄色：发现程序文件，但没有登记启动入口
- 红色：疑似软件本体已卸载，只剩用户数据/缓存

## 安装与启动

```bash
cd ~/.application/software_manager
./swman
```

安装快捷命令：

```bash
./install.sh
swman
```

如果 `~/.local/bin` 不在 `PATH`：

```bash
~/.local/bin/swman
```

## CLI

```bash
swman --summary       # 分类统计
swman --json          # 完整 JSON 清单
swman --self-test     # 扫描一致性检查
swman --export DIR    # 导出 JSON + Markdown
```

## 安全说明

- 对系统包管理器、Flatpak、Snap、Homebrew 和语言级包，`d` 默认只清理用户数据和缓存。
- 卸载本体使用详情页显示的包管理器命令，由用户确认后执行。
- AppImage 和源码/手动程序可通过 `d` 删除本体、入口和用户数据。
- 残留识别是启发式结果，删除前必须核对具体路径。
- 删除日志：`~/.local/state/software_manager/delete.log`
- 导出目录：`~/.local/state/software_manager/`

## 已知边界

- Nix 当前列出用户 profile，而不是把整个 NixOS 系统 closure 当成普通软件列表。
- 语言包管理器的依赖信息取决于该工具是否提供本地元数据。
- AppImage 通常自带依赖，系统加载器依赖可能因打包方式不同而无法完整枚举。
- “疑似残留”表示无法关联到已安装包、Desktop 文件、AppImage、命令链接或源码目录，不代表可以无条件删除。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 仓库

```text
git@github.com:IammyselfYBX/Linux_software_manager.git
```
