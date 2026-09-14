# KOReader 本地安装脚本

`install-koreader.sh` 用于在已安装 KPM（Kindle Package Manager）的 Kindle 上，从用户存储根目录本地安装 KOReader 的 `.kpkg` 软件包。

脚本会调用设备上的 KPM：

```text
/var/local/kmc/kindlehf/bin/kpm
```

安装过程的全部输出会写入：

```text
/mnt/us/kpm-local-install.log
```

## 使用前提

- Kindle 已完成越狱，并已正确安装、可正常使用 KPM。
- 使用与设备平台匹配、来源可信的 KOReader `.kpkg` 软件包。
- 能够将文件复制到 Kindle 用户存储根目录；设备端对应路径为 `/mnt/us/`。

## 下载 KOReader 安装包

从 KindleModding 软件仓库的 [KOReader artifacts 目录](https://github.com/KindleModding/repo/tree/main/packages/koreader/artifacts) 下载适合设备的最新 `.kpkg` 文件。

该目录可能同时提供不同设备平台的软件包，例如文件名以 `kindlehf` 或 `kindlepw2` 结尾的版本。请根据自己的 Kindle 型号和 KPM 环境选择，不能仅按版本号判断。

在 GitHub 中打开所需文件后，点击 **Download raw file** 下载。下载完成后，将文件重命名为：

```text
koreader.kpkg
```

> `.kpkg` 会以可执行安装包的形式交给 KPM 处理，请只使用可信来源的文件。

## 安装步骤

1. 通过 USB 将下载并重命名后的 `koreader.kpkg` 复制到 Kindle 用户存储根目录。
2. 将本仓库的 `install-koreader.sh` 复制到 Kindle 的 `documents` 文件夹。
3. 安全弹出 Kindle，然后在图书馆中找到名为 **install-koreader** 的“书”并点击。
4. 等待约 30 秒，即可完成安装。

复制完成后的设备路径应为：

```text
/mnt/us/koreader.kpkg
/mnt/us/documents/install-koreader.sh
```

脚本随后会设置 KPM 所需的动态库路径，并以非交互方式执行本地安装：

```sh
/var/local/kmc/kindlehf/bin/kpm -d -y install "file:///mnt/us/koreader.kpkg"
```

## 启动 KOReader

安装完成后，在 Kindle 搜索栏中输入以下命令，即可进入 KOReader：

```text
;kpm launch koreader
```

## 查看安装结果

安装完成后查看日志：

```sh
cat /mnt/us/kpm-local-install.log
```

日志中的 `KPM exit code` 表示 KPM 的执行结果：

- `0`：KPM 命令执行成功。
- 非 `0`：安装失败，应结合该行之前的日志定位原因。

每次运行脚本都会覆盖之前的日志。如需保留故障信息，请在再次运行前先备份 `kpm-local-install.log`。

常见问题包括：

- `/mnt/us/koreader.kpkg` 不存在或文件名不正确。
- 下载的软件包与 Kindle 平台不匹配。
- KPM 未安装，或其路径不是 `/var/local/kmc/kindlehf/bin/kpm`。
- 软件包下载不完整或已损坏。

## 开发检查

此脚本保持 POSIX `/bin/sh` 兼容。在电脑上只能进行语法和静态检查，不要直接执行安装脚本，因为它使用 Kindle 专用的绝对路径和 KPM 程序。

```sh
sh -n install-koreader.sh
shellcheck install-koreader.sh
```
