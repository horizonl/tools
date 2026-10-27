# Chromium uBlock Fix

在 Linux x86_64 上为 Chromium 制作用户目录内的 MV2 兼容副本，让完整版 **uBlock Origin** 继续运行。系统 Chromium 升级后，配套启动器会重新构建并验证兼容副本。

本目录已包含修复器、补丁引擎、功能验证脚本和 **uBlock Origin 1.75.0** 解包文件。复制整个目录即可在另一台满足环境要求的设备上使用，不再依赖原电脑的 `~/.local/bin/chromium-ubo-repair` 或 `~/.local/share/chromium-ubo/`。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Linux x86_64；依赖 `/proc`、文件锁和符号链接 |
| Chromium | 目标设备本地安装的原生 Chromium，且其程序结构能被附带引擎识别，或未修改副本能通过功能测试 |
| Python | 3.11 或更高，命令为 `python3`，无需 `pip install` |
| Node.js | 22 或更高，命令为 `node`，无需 `npm install` |
| 文件系统 | 项目目录可读；运行数据目录可写，支持执行程序、创建符号链接与原子重命名；不要放在 `noexec` 挂载点 |
| 磁盘空间 | 为 Chromium 主程序的临时副本、当前副本、上一版备份及测试配置留出空间，建议至少 2 GB |

**Python、Node.js、Chromium 及 Chromium 的系统动态库由目标设备安装，未把这些系统运行环境复制进仓库。** 随项目提供的是应用层依赖和扩展文件。Snap、Flatpak、Windows、macOS、ARM64 不在此修复器的支持范围内。

本项目已在 CachyOS Chromium `156.0.8078.47` / uBlock Origin `1.75.0` 上重新构建，并通过启用、实际拦截和重启后三阶段测试，以及 12 项自动化测试。其他发行版和版本需要在目标设备重新检测和测试，不能据此保证所有 Chromium 版本兼容。

## 快速开始

先把整个文件夹放到一个固定位置，完全退出 Chromium，再在本目录运行：

```bash
python3 --version
node --version

# 首次构建兼容副本，并执行实际拦截及重启测试
sh ./chromium-ublock.sh

# 日常从此入口启动浏览器；以后升级会自动触发修复
./chromium-ubo
```

无需 `sudo`，也无需安装到 `~/.local/bin`。如果复制工具未保留执行权限，可使用 `python3 ./chromium-ubo` 启动，或先执行：

```bash
chmod +x chromium-ubo chromium-ublock.sh
```

启动器默认使用目标用户的 `${XDG_CONFIG_HOME:-~/.config}/chromium` 浏览器数据目录，因此正常浏览时会使用并更新该目录中的书签、登录状态和配置。修复时的功能测试只使用临时配置，不打开日常配置。

如需单独的浏览器配置：

```bash
./chromium-ubo --user-data-dir "$HOME/.config/chromium-ubo-test"
```

若普通 Chromium 仍在运行，请先完全退出。使用相同配置目录的浏览器应当通过同一个入口启动，避免窗口被交给另一个正在运行的 Chromium 实例。

## Chromium 路径与运行数据

默认依次查找以下主程序：

```text
/usr/lib/chromium/chromium
/usr/lib/chromium-browser/chromium-browser
/usr/lib/chromium-browser/chromium
/usr/lib64/chromium/chromium
/usr/lib64/chromium-browser/chromium-browser
```

无法自动查找时，指定 **真正的 ELF 主程序**。`/usr/bin/chromium` 在许多发行版上只是 shell 启动脚本，不能作为补丁输入。

```bash
sh ./chromium-ublock.sh --source /实际路径/chromium
./chromium-ubo
```

成功构建后会记住该路径，日常启动无需重复指定。要切换主程序，可以再次传入 `--source`；此时会重新构建和验证。

生成文件默认放在项目内的 `.state/`，已加入 `.gitignore`。目录较大或项目存放位置只读时，可指定其他运行数据目录；**修复与启动命令需要使用相同的 `--base-dir`**：

```bash
sh ./chromium-ublock.sh --base-dir "$HOME/.local/share/chromium-ubo-portable"
./chromium-ubo --base-dir "$HOME/.local/share/chromium-ubo-portable"
```

项目移动后，已有运行记录中的绝对路径会失效，工具会要求重新构建。迁移到其他设备时应当省略 `.state/`，使用新设备的 Chromium 重新生成副本。

## 常用命令

```bash
# 查看参数
sh ./chromium-ublock.sh --help
./chromium-ubo --help

# 只分析程序结构，不创建副本，也不启动浏览器
sh ./chromium-ublock.sh --dry-run

# 只检查当前副本的路径、来源和文件指纹，不执行功能测试
sh ./chromium-ublock.sh --check
./chromium-ubo --verify-installation

# 当前版本也重新构建并执行全部测试；请先完全退出 Chromium
sh ./chromium-ublock.sh --force

# 浏览器参数继续传给 Chromium
./chromium-ubo --new-window https://example.com
./chromium-ubo --incognito
```

修复命令退出码：`0` 为完成或检查通过；`1` 为错误；`--dry-run` 返回 `2` 表示结构未识别；`--check` 返回 `3` 表示需要重建。结构未识别时，正式修复仍可尝试测试未修改副本，但不会根据部分匹配修改二进制。

`--extension /路径` 可指定另一份带固定 `manifest.key` 的 uBlock Origin MV2 解包目录。首次成功后会记住该目录。功能验证依赖 uBlock 内部接口，因此这个参数目前不能作为任意 MV2 扩展的通用验证入口。

## 工作方式与边界

1. 读取目标 Chromium 主程序并核对补丁引擎的固定 SHA-256。
2. 只对已识别的完整程序结构应用补丁；未知结构只允许尝试未修改副本。
3. 复制主程序，链接目标设备 Chromium 安装目录下的其他资源，不改写系统主程序。
4. 在临时配置中启动无界面 Chromium，执行三阶段测试：无扩展时对照请求成功；加载 uBlock 后请求被拦截；重启后仍能拦截。
5. 全部通过后才启用新副本，并保留上一份运行程序及安装记录；替换失败时回滚。

验证用的广告地址通过浏览器域名映射指向本地 HTTP 服务，对照请求和拦截请求均在本机完成。修复过程不下载补丁或执行远端代码；浏览器日常联网及 uBlock 过滤列表更新则按各自设置进行。

兼容副本依赖目标设备的 Chromium 资源文件和系统库，不能作为独立浏览器直接复制到另一台电脑，也不应单独把旧兼容主程序与升级后的资源长期混用。启动器发现系统主程序变化后会重建；无法通过验证时会停止启动，并保留已有副本供诊断。

这个工具恢复的是仍存在于浏览器中的 MV2 能力。未来 Chromium 若删除扩展所需接口，仅修改检查分支无法恢复；浏览器结构变化也可能需要更新补丁引擎。底层补丁作用于通用 MV2 检查，但本项目只对 uBlock Origin 做功能验证，不保证所有 MV2 扩展正常工作，也不负责扩展商店的分发和更新。

## 文件结构

```text
chromium-ublock.sh              手动修复入口，从任意工作目录均可调用
chromium-ubo                    日常启动入口，自动检测升级并调用修复器
repair.py                       副本构建、校验、测试和原子替换
repair-tools/
  verify.mjs                    仅使用 Node.js 内置模块的功能测试
  vendor/
    mv2ctl.py                   MV2 补丁引擎
    mv2profiles.py              程序结构特征
    LICENSE.txt                 引擎许可证
    SOURCE.txt                  引擎来源记录
extensions/
  uBlock-Origin-1.75.0/          完整解包扩展、过滤列表和第三方许可证
  SOURCE.txt                    扩展快照来源与本地清单说明
tests/test_repair.py            补丁边界、回滚及迁移行为测试
SHA256SUMS                      分发文件指纹，供检查复制完整性
LICENSE.txt                    GPL v3 许可证文本
.state/                        本机生成的数据，不随项目迁移
```

已保留此前安装的 uBlock manifest 中的固定**公钥**，以保持扩展 ID 稳定；其中不含签名私钥。未复制旧设备的浏览器配置、Cookie、登录信息、个人扩展设置或已打补丁的 Chromium 主程序。附带版本为固定快照，工具不会自动升级扩展源码；过滤列表可按 uBlock 设置更新。

## 复制到其他设备

在项目目录执行以下命令生成仅含分发文件的压缩包：

```bash
tar -czf /tmp/chromium-ublock-fix.tar.gz \
  chromium-ublock.sh chromium-ubo repair.py repair-tools extensions \
  tests README.md LICENSE.txt SHA256SUMS .gitignore
```

在目标设备新建目录解压，安装环境要求中的系统软件后，先检查文件完整性，再按“快速开始”操作：

```bash
sha256sum --check --quiet SHA256SUMS
sh ./chromium-ublock.sh --dry-run
sh ./chromium-ublock.sh
./chromium-ubo
```

`SHA256SUMS` 用于发现文件损坏和漏拷贝，不构成独立的来源认证。更新分发文件后需要同步更新指纹。

## 诊断、测试与移除

默认运行目录中的主要文件：

- `.state/installation.json`：来源程序和兼容副本的指纹、路径、补丁记录与成功验证结果。
- `.state/repair-report.json`：最近一次进入构建测试流程后的报告；早期环境检查失败可能还没有此文件。
- `.state/last-auto-repair.log`：通过 `chromium-ubo` 触发自动构建时的输出。
- `.state/previous-*/`：保留的上一份运行程序与安装记录。所链接的系统资源不属于历史快照。

常见问题：

- **提示浏览器正在运行**：完全退出 Chromium 后重试；脚本不会替你关闭窗口。
- **找不到主程序 / 不是 ELF**：安装原生 Chromium，并用 `--source` 指向实际主程序。
- **功能测试失败**：检查 Python / Node.js 版本、Chromium 沙箱和系统库是否正常，查看报告；不会跳过测试强制启用。
- **补丁引擎校验失败**：检查传输完整性。维护者修改引擎后，需要重新审查分支与测试，并同步更新 `repair.py` 内的 `ENGINE_HASHES`。
- **移动目录后需要重新修复**：这是正常行为，新路径下会重新生成资源链接与扩展路径。

开发检查：

```bash
python3 -B -m unittest discover -s tests -v
node --check repair-tools/verify.mjs
sh -n chromium-ublock.sh
```

移除时先完全退出本项目启动的 Chromium，再删除项目目录及自定义的运行数据目录。浏览器数据默认存放在用户 Chromium 配置目录中，删除项目目录不会移除该配置；若自行指定了其他数据目录，请按需要另行管理。

## 第三方来源与许可证

- MV2 引擎：来源记录为 [meizfl/linux-chromium-mv2-patcher](https://github.com/meizfl/linux-chromium-mv2-patcher)，保留本地固定快照、`SOURCE.txt` 和 GPL v3 许可证。
- uBlock Origin：[gorhill/uBlock](https://github.com/gorhill/uBlock)，附带此前本机安装的 1.75.0 解包快照及其许可证；原始下载包和确切发布 URL 未保留，详见 `extensions/SOURCE.txt`。
- uBlock 内部字体、库、过滤列表等遵循其各自目录中的原有声明。

复制和再分发时应保留这些来源与许可证文件。
