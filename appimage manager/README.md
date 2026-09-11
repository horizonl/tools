# AppImage Manager

`appimage manager` 是一个面向 Linux 桌面环境的 AppImage 管理脚本，当前版本为 1.1.1。它可以将 AppImage 安装到当前用户的本地目录，并自动生成命令行启动器、桌面菜单项和应用图标。

## 主要功能

- **安装**：解包 AppImage 以读取应用名称、`.desktop` 文件和图标，再将应用安装到 `~/.local/opt`。
- **桌面集成**：在 `~/.local/share/applications` 中生成或修正 `.desktop` 文件，并安装多尺寸图标。
- **命令行启动**：在 `~/.local/bin` 中创建以应用 ID 命名的启动器。
- **更新**：原子替换 AppImage，同时更新启动器、桌面项、图标和运行时信息；新版未提供图标时保留旧图标。
- **卸载**：删除脚本管理的 AppImage、启动器、桌面项、图标和元数据。
- **查询**：列出已安装应用，或显示单个应用的路径、大小、时间、SHA-256 校验值和图标等信息。
- **输入法适配**：识别 Electron/Chromium 类 AppImage，针对 Wayland、X11/XWayland 和 Fcitx5 生成合适的启动参数与环境变量。
- **修复**：重新检测应用运行时类型，并重建启动器和桌面项。

## 安装管理器

建议将仓库中的脚本安装为 `appimage` 命令：

```bash
install -Dm755 "appimage manager" "$HOME/.local/bin/appimage"
```

确保 `~/.local/bin` 已加入 `PATH`，然后可以运行：

```bash
appimage version
appimage help
```

## 使用方法

```bash
# 安装 AppImage
appimage install ~/Downloads/ColaMD-1.9.0.AppImage

# 查看已安装应用及详细信息
appimage list
appimage info colamd

# 更新应用
appimage update colamd ~/Downloads/ColaMD-1.10.0.AppImage

# 设置输入法/显示后端模式
appimage ime colamd auto

# 重新检测并修复启动器
appimage repair colamd

# 卸载应用
appimage uninstall colamd
```

部分命令提供别名：

| 主命令 | 别名 |
| --- | --- |
| `uninstall` | `remove`、`rm` |
| `update` | `upgrade` |
| `list` | `ls` |
| `info` | `show` |

也可直接传入文件路径，效果等同于 `install`：

```bash
appimage ~/Downloads/example.AppImage
```

## 输入法模式

| 模式 | 说明 |
| --- | --- |
| `auto` | 根据当前会话自动选择 Wayland 或 X11，为默认模式 |
| `wayland` | 对 Electron/Chromium 强制使用原生 Wayland 和 `text-input-v3` |
| `x11` | 对 Electron/Chromium 强制使用 X11/XWayland 和 Fcitx5 IM module |
| `off` | 不注入输入法或显示后端参数 |

可以通过环境变量临时覆盖某个应用的默认模式：

```bash
APPIMAGE_IME_MODE=x11 colamd
```

## 数据存放位置

| 内容 | 默认位置 |
| --- | --- |
| AppImage 文件 | `~/.local/opt/<应用名>/` |
| 命令行启动器 | `~/.local/bin/<app-id>` |
| 桌面菜单项 | `~/.local/share/applications/<app-id>.desktop` |
| 图标 | `~/.local/share/icons/hicolor/` |
| 管理元数据 | `~/.local/share/appimage-manager/apps/<app-id>/` |

当设置了 `XDG_DATA_HOME` 时，脚本会使用该目录代替 `~/.local/share`。

## 运行要求与注意事项

- 需要 Bash，以及 `find`、`sed`、`grep`、`awk`、`realpath`/`readlink`、`sha256sum` 等常见 Linux 命令。
- `update-desktop-database` 和 `gtk-update-icon-cache` 是可选命令；存在时脚本会自动刷新桌面与图标缓存。
- 安装和更新时会执行 AppImage 的 `--appimage-extract` 模式以读取元数据，因此应只处理来自可信来源的 AppImage。
- 该工具仅管理当前用户的文件，不需要 root 权限。
