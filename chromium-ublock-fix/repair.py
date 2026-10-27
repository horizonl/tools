#!/usr/bin/env python3
"""Rebuild and test the private Chromium MV2 copy after package upgrades.

No root privileges or network access are needed. The system Chromium executable
and the user's browser profile are never edited. Only recognized patch layouts
or an unmodified binary passing the complete functional test can be activated.
"""
from pathlib import Path
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
DEFAULT_BASE = HERE / '.state'
DEFAULT_SOURCE = Path('/usr/lib/chromium/chromium')
SOURCE_CANDIDATES = (
    DEFAULT_SOURCE,
    Path('/usr/lib/chromium-browser/chromium-browser'),
    Path('/usr/lib/chromium-browser/chromium'),
    Path('/usr/lib64/chromium/chromium'),
    Path('/usr/lib64/chromium-browser/chromium-browser'),
)
DEFAULT_EXTENSION = HERE / 'extensions/uBlock-Origin-1.75.0'
TOOLS = HERE / 'repair-tools'
ENGINE_HASHES = {
    'mv2ctl.py': '4dfce3aee670d16ea7de7d6a3ea3642df44f52c17212962c77227851f05c5652',
    'mv2profiles.py': '10b1d864767ba4ca8067fcf91c3bb55fa72c24acf3a12b5dd86b2e3861470794',
}
CACHY_LAYOUT = {'A': 4, 'B': 1, 'C': 1, 'S': 1, 'U': 1}


class RepairError(Exception):
    pass


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_meta(base):
    try:
        value = json.loads((base / 'installation.json').read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def configured_source(base, override=None):
    if override is not None:
        return override.expanduser().resolve(strict=True)
    meta = read_meta(base)
    if meta.get('bundle_root') == str(HERE) and meta.get('source_path'):
        return Path(meta['source_path']).resolve(strict=True)
    for candidate in SOURCE_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()
    raise RepairError('未找到系统 Chromium 主程序，请使用 --source 指定实际 ELF 文件，'
                      '例如 --source /usr/lib/chromium/chromium。')


def configured_extension(base, override=None):
    if override is not None:
        return override.expanduser().resolve(strict=True)
    meta = read_meta(base)
    if meta.get('bundle_root') == str(HERE) and meta.get('extension_path'):
        return Path(meta['extension_path']).resolve(strict=True)
    return DEFAULT_EXTENSION.resolve(strict=True)


def current_installation(base, source, source_hash, extension=None):
    meta = read_meta(base)
    binary = base / 'runtime/chromium'
    # Moving a bundle invalidates absolute resource symlinks and extension paths.
    # Rebuild on the destination rather than reusing another machine's runtime.
    if (meta.get('bundle_root') != str(HERE)
            or meta.get('source_sha256') != source_hash or not binary.is_file()):
        return False
    if extension is not None and meta.get('extension_path') != str(extension):
        return False
    recorded_source = Path(meta.get('source_path', str(DEFAULT_SOURCE)))
    return (recorded_source.resolve() == source.resolve()
            and digest(binary) == meta.get('patched_sha256'))


def browser_processes(base, source):
    targets = {str(source.resolve()), str((base / 'runtime/chromium').absolute())}
    result = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            exe = os.readlink(proc / 'exe').removesuffix(' (deleted)')
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
            if exe in targets and not any(arg.startswith(b'--type=') for arg in argv):
                result.append(int(proc.name))
        except (OSError, PermissionError):
            continue
    return result


def require_closed(base, source):
    running = browser_processes(base, source)
    if running:
        raise RepairError('请先完全退出 Chromium 再运行修复；脚本不会关闭你的窗口。PID: '
                          + ', '.join(map(str, running)))


def validate_source(source):
    with source.open('rb') as stream:
        header = stream.read(64)
    if (len(header) < 64 or header[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', header, 18)[0] != 62):
        raise RepairError('此修复器目前只支持 Linux x86_64 Chromium ELF 主程序。')
    if source.stat().st_size < 10_000_000 or not os.access(source, os.X_OK):
        raise RepairError('指定文件不是 Chromium 主程序：' + str(source))


def load_engine():
    vendor = TOOLS / 'vendor'
    for name, expected in ENGINE_HASHES.items():
        if digest(vendor / name) != expected:
            raise RepairError('补丁引擎文件校验失败：' + name)
    sys.path.insert(0, str(vendor))
    try:
        spec = importlib.util.spec_from_file_location('ubo_mv2_engine', vendor / 'mv2ctl.py')
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
    finally:
        sys.path.pop(0)
    engine.EXPECT_PROFILES = dict(engine.EXPECT_PROFILES)
    # All eight branches were checked against their existing exit paths, then
    # tested with uBO 1.75.0 on CachyOS 156. The metric-only helper is untouched.
    engine.EXPECT_PROFILES['CachyOS Chromium 156 validated ELF layout'] = CACHY_LAYOUT
    return engine


def analyze(engine, source):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        sites, state, _ = engine.analyze(str(source), verbose=True, with_discovery=False)
    return sites, state, output.getvalue()


def validate_plan(sites, size):
    occupied = set()
    for site in sorted(sites, key=lambda value: value.jpos):
        if len(site.orig) != len(site.new) or not site.orig:
            raise RepairError('补丁长度无效。')
        if site.jpos < 0 or site.jpos + len(site.orig) > size:
            raise RepairError('补丁位置越界。')
        span = set(range(site.jpos, site.jpos + len(site.orig)))
        if span & occupied:
            raise RepairError('补丁位置重叠，停止修复。')
        occupied.update(span)


def stage_runtime(engine, source, runtime, sites, state):
    runtime.mkdir()
    for item in source.parent.iterdir():
        if item.name != source.name:
            (runtime / item.name).symlink_to(item, target_is_directory=item.is_dir())
    candidate = runtime / 'chromium'
    pending = []
    if state == 'original':
        pending = engine.pending_patch_sites(sites)
        validate_plan(pending, source.stat().st_size)
        engine.write_patched_copy(str(source), str(candidate), pending)
        if engine.sha256_ignoring_sites(str(source), pending) != engine.sha256_ignoring_sites(str(candidate), pending):
            raise RepairError('补丁范围之外的字节发生了变化。')
    elif state in ('patched', 'fail'):
        # On an unknown layout only an unchanged copy may be tried; no byte
        # edits are inferred from partial pattern matches.
        shutil.copy2(source, candidate)
    else:
        raise RepairError('检测到混合补丁状态，无法自动修复。')
    return candidate, pending


def run_selftest(candidate, extension, work, report):
    node = shutil.which('node')
    if not node:
        raise RepairError('缺少 Node.js，无法验证广告拦截；请安装 Node.js 22+ 后重试。')
    version = subprocess.run([node, '--version'], capture_output=True, text=True,
                             timeout=10, check=True).stdout.strip()
    if not re.match(r'^v(\d+)\.', version) or int(version.split('.')[0][1:]) < 22:
        raise RepairError('功能验证需要 Node.js 22+；当前版本：' + version)
    work.mkdir()
    result = subprocess.run(
        [node, str(TOOLS / 'verify.mjs'), str(candidate), str(extension), str(work), str(report)],
        capture_output=True, text=True, timeout=120,
    )
    print(result.stdout, end='', flush=True)
    if result.returncode:
        details = report.read_text() if report.exists() else ''
        raise RepairError('兼容副本功能测试未通过；现有安装保持原样。\n'
                          + (result.stdout + result.stderr + details)[-18000:])
    value = json.loads(report.read_text())
    if not value.get('passed') or len(value.get('phases', [])) != 3:
        raise RepairError('功能验证报告不完整，停止替换。')
    return value


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def activate(base, staged_runtime, metadata):
    """Move a tested runtime and metadata into place, rolling back on errors."""
    target = base / 'runtime'
    meta_path = base / 'installation.json'
    backup = Path(tempfile.mkdtemp(prefix='previous-', dir=base))
    old_runtime = backup / 'runtime'
    old_meta = meta_path.read_bytes() if meta_path.exists() else None
    if old_meta is not None:
        (backup / 'installation.json').write_bytes(old_meta)
    moved_old = installed_new = False
    try:
        if target.exists():
            os.replace(target, old_runtime)
            moved_old = True
        os.replace(staged_runtime, target)
        installed_new = True
        atomic_json(meta_path, metadata)
    except BaseException:
        if installed_new:
            os.replace(target, staged_runtime)
        if moved_old:
            os.replace(old_runtime, target)
        if old_meta is None:
            meta_path.unlink(missing_ok=True)
        else:
            fd, temp = tempfile.mkstemp(prefix='.rollback-', dir=base)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(old_meta)
            os.replace(temp, meta_path)
        raise
    # Only a single prior version is retained. Never follow resource symlinks.
    for entry in base.glob('previous-*'):
        if entry != backup and entry.is_dir() and not entry.is_symlink():
            try:
                shutil.rmtree(entry)
            except OSError as error:
                print('旧备份清理未完成：' + str(error), file=sys.stderr)
    return backup


def repair(args):
    base = args.base_dir.expanduser().absolute()
    source = configured_source(base, args.source)
    validate_source(source)
    extension = configured_extension(base, args.extension)
    source_hash = digest(source)
    up_to_date = current_installation(base, source, source_hash, extension)
    if args.check:
        print('当前兼容副本校验通过。' if up_to_date else '需要修复或重新验证兼容副本。')
        return 0 if up_to_date else 3
    if up_to_date and not args.force and not args.dry_run:
        if not args.auto:
            print('当前兼容副本已是最新且校验正常，无需修复。')
        return 0
    engine = load_engine()
    sites, state, diagnostic = analyze(engine, source)
    if args.dry_run:
        print(diagnostic, end='')
        print('布局已识别；可制作兼容副本。' if state in ('original', 'patched')
              else '未知布局：只允许测试未修改的程序，不会套用部分补丁。')
        return 0 if state in ('original', 'patched') else 2
    base.mkdir(parents=True, exist_ok=True)
    with (base / 'repair.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RepairError('另一个修复进程正在运行，请稍后重试。')
        # Another process may have finished between the first check and lock.
        locked_hash = digest(source)
        if locked_hash != source_hash:
            raise RepairError('分析期间系统 Chromium 发生变化，请重新运行。')
        if current_installation(base, source, source_hash, extension) and not args.force:
            if not args.auto:
                print('当前兼容副本已是最新且校验正常，无需修复。')
            return 0
        require_closed(base, source)
        old_meta = read_meta(base)
        manifest = json.loads((extension / 'manifest.json').read_text())
        if manifest.get('manifest_version') != 2 or not manifest.get('key'):
            raise RepairError('没有找到带固定扩展 ID 的 Manifest V2 uBlock Origin。')
        version = subprocess.run([str(source), '--version'], capture_output=True,
                                 text=True, timeout=10, check=True).stdout.strip()
        if not version:
            raise RepairError('无法读取 Chromium 版本。')
        print('准备修复：' + version, flush=True)
        print('制作临时副本并进行离线拦截与重启测试，请稍候……', flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix='.repair-', dir=base) as temporary:
                stage = Path(temporary)
                candidate, pending = stage_runtime(engine, source, stage / 'runtime', sites, state)
                proof = run_selftest(candidate, extension, stage / 'test-profiles', stage / 'validation.json')
                if digest(source) != source_hash:
                    raise RepairError('修复期间系统 Chromium 又发生了变化，请重新运行。')
                require_closed(base, source)
                numeric_version = re.search(r'\b\d+\.\d+\.\d+\.\d+\b', version)
                metadata = dict(old_meta)
                metadata.update({
                    'bundle_root': str(HERE),
                    'version': numeric_version.group(0) if numeric_version else version,
                    'source_path': str(source),
                    'source_sha256': source_hash,
                    'patched_sha256': digest(candidate),
                    'extension_path': str(extension),
                    'extension_id': proof['extension_id'],
                    'extension_version': manifest['version'],
                    'changed_bytes': sum(sum(a != b for a, b in zip(s.orig, s.new)) for s in pending),
                    'patches': [[hex(s.jpos), s.orig.hex(), s.new.hex()] for s in pending],
                    'repair_mode': 'validated-patch' if pending else 'unmodified-copy',
                    'validated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    'validation': proof,
                })
                backup = activate(base, stage / 'runtime', metadata)
                atomic_json(base / 'repair-report.json', {'passed': True, 'version': version,
                            'source_sha256': source_hash, 'diagnostic': diagnostic,
                            'validation': proof, 'backup': str(backup)})
                print('修复成功：已启用通过测试的新兼容副本。', flush=True)
                return 0
        except BaseException as error:
            atomic_json(base / 'repair-report.json', {'passed': False, 'version': version,
                        'source_sha256': source_hash, 'diagnostic': diagnostic,
                        'error': str(error), 'at': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
            raise


def main():
    parser = argparse.ArgumentParser(description='Chromium 升级后重建 uBlock Origin MV2 兼容副本；无需 sudo。')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='只检查当前兼容副本，不修改文件')
    modes.add_argument('--dry-run', action='store_true', help='只读分析补丁位置，不启动浏览器或修改文件')
    parser.add_argument('--force', action='store_true', help='当前版本也重新构建测试；仍然执行全部兼容检查')
    parser.add_argument('--auto', action='store_true', help='供启动器自动调用')
    parser.add_argument('--base-dir', type=Path, default=DEFAULT_BASE, help='运行数据目录，默认：项目目录/.state')
    parser.add_argument('--source', type=Path, help='系统 Chromium ELF 主程序路径，默认自动查找并记住首次选择')
    parser.add_argument('--extension', type=Path, help='uBlock Origin 解包目录，默认使用项目附带的 1.75.0')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print('需要 Python 3.11 或更高版本。', file=sys.stderr)
        return 1
    if os.geteuid() == 0:
        print('请以普通用户运行，不要使用 sudo。', file=sys.stderr)
        return 1
    try:
        return repair(args)
    except (RepairError, OSError, ValueError, subprocess.SubprocessError, SystemExit) as error:
        print('修复未完成：' + str(error), file=sys.stderr)
        if (args.base_dir / 'repair-report.json').exists():
            print('诊断报告：' + str(args.base_dir / 'repair-report.json'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
