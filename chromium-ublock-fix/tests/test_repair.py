from pathlib import Path
from types import SimpleNamespace
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('repair_test_module', Path(__file__).resolve().parents[1] / 'repair.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)

class RepairTests(unittest.TestCase):
    def test_overlapping_patch_rejected(self):
        sites = [SimpleNamespace(jpos=3, orig=b'aa', new=b'bb'), SimpleNamespace(jpos=4, orig=b'a', new=b'b')]
        with self.assertRaises(repair.RepairError): repair.validate_plan(sites, 20)

    def test_out_of_range_rejected(self):
        with self.assertRaises(repair.RepairError):
            repair.validate_plan([SimpleNamespace(jpos=9, orig=b'aa', new=b'bb')], 10)

    def test_variable_size_patch_rejected(self):
        with self.assertRaises(repair.RepairError):
            repair.validate_plan([SimpleNamespace(jpos=2, orig=b'aa', new=b'b')], 10)

    def test_unknown_layout_never_patched(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); (root/'source').mkdir()
            source = root/'source/chromium'; source.write_bytes(b'unknown executable bytes')
            engine = Mock()
            candidate, changes = repair.stage_runtime(engine, source, root/'runtime', None, 'fail')
            self.assertEqual(candidate.read_bytes(), source.read_bytes())
            self.assertEqual(changes, [])
            engine.write_patched_copy.assert_not_called()

    def test_activation_failure_restores_runtime_and_metadata(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); base=root/'base';base.mkdir()
            (base/'runtime').mkdir();(base/'runtime/chromium').write_bytes(b'original')
            old=json.dumps({'source_sha256':'old'}).encode();(base/'installation.json').write_bytes(old)
            stage=root/'staged';stage.mkdir();(stage/'chromium').write_bytes(b'candidate')
            with patch.object(repair, 'atomic_json', side_effect=OSError('simulated metadata write failure')):
                with self.assertRaises(OSError):repair.activate(base, stage, {'source_sha256':'new'})
            self.assertEqual((base/'runtime/chromium').read_bytes(), b'original')
            self.assertEqual((base/'installation.json').read_bytes(), old)
            self.assertEqual((stage/'chromium').read_bytes(), b'candidate')

    def test_lock_conflict_does_not_start_another_repair(self):
        import fcntl
        with tempfile.TemporaryDirectory() as name:
            lock=Path(name)/'repair.lock'
            with lock.open('a') as first,lock.open('a') as second:
                fcntl.flock(first,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):fcntl.flock(second,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_corrupted_runtime_is_not_current(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            (base / 'runtime').mkdir()
            (base / 'runtime/chromium').write_bytes(b'changed')
            repair.atomic_json(base / 'installation.json', {
                'bundle_root': str(repair.HERE),
                'source_path': str(repair.DEFAULT_SOURCE),
                'source_sha256': 'source',
                'patched_sha256': 'expected',
            })
            self.assertFalse(repair.current_installation(base, repair.DEFAULT_SOURCE, 'source'))

    def test_matching_installation_is_current(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            source = base / 'source'
            source.write_bytes(b'system executable')
            (base / 'runtime').mkdir()
            binary = base / 'runtime/chromium'
            binary.write_bytes(b'patched executable')
            extension = base / 'extension'
            repair.atomic_json(base / 'installation.json', {
                'bundle_root': str(repair.HERE),
                'source_path': str(source),
                'source_sha256': repair.digest(source),
                'patched_sha256': repair.digest(binary),
                'extension_path': str(extension),
            })
            self.assertTrue(repair.current_installation(base, source, repair.digest(source), extension))
            self.assertFalse(repair.current_installation(base, source, repair.digest(source), base / 'other-extension'))
            # Identical bytes from another project location must still rebuild.
            with patch.object(repair, 'HERE', base / 'moved-bundle'):
                self.assertFalse(repair.current_installation(base, source, repair.digest(source), extension))

    def test_moved_bundle_ignores_old_machine_resource_paths(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            source = base / 'destination-chromium'
            source.write_bytes(b'destination executable')
            extension = base / 'bundled-extension'
            extension.mkdir()
            repair.atomic_json(base / 'installation.json', {
                'bundle_root': '/old/machine/bundle',
                'source_path': '/old/machine/chromium',
                'extension_path': '/old/machine/uBlock',
            })
            with patch.object(repair, 'SOURCE_CANDIDATES', (source,)), \
                    patch.object(repair, 'DEFAULT_EXTENSION', extension):
                self.assertEqual(repair.configured_source(base), source)
                self.assertEqual(repair.configured_extension(base), extension)

    def test_custom_resource_paths_persist_and_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            source = base / 'custom-chromium'
            source.touch()
            extension = base / 'custom-extension'
            extension.mkdir()
            other = base / 'another-resource'
            other.touch()
            repair.atomic_json(base / 'installation.json', {
                'bundle_root': str(repair.HERE),
                'source_path': str(source),
                'extension_path': str(extension),
            })
            self.assertEqual(repair.configured_source(base), source)
            self.assertEqual(repair.configured_extension(base), extension)
            self.assertEqual(repair.configured_source(base, other), other)
            self.assertEqual(repair.configured_extension(base, other), other)

    def test_entrypoints_work_outside_project_without_old_home_installation(self):
        with tempfile.TemporaryDirectory() as name:
            env = dict(os.environ, HOME=str(Path(name) / 'uninstalled-home'))
            commands = (
                ['sh', str(repair.HERE / 'chromium-ublock.sh'), '--help'],
                [sys.executable, '-B', str(repair.HERE / 'chromium-ubo'), '--help'],
            )
            for command in commands:
                result = subprocess.run(command, cwd=name, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--source', result.stdout)

    def test_launcher_uses_own_runtime_and_preserves_browser_arguments(self):
        loader = SourceFileLoader('launcher_test_module', str(repair.HERE / 'chromium-ubo'))
        launcher_spec = importlib.util.spec_from_loader(loader.name, loader)
        launcher = importlib.util.module_from_spec(launcher_spec)
        loader.exec_module(launcher)
        with tempfile.TemporaryDirectory() as name:
            base = Path(name) / 'state with spaces'
            (base / 'runtime').mkdir(parents=True)
            source = base / 'system-chromium'
            source.write_bytes(b'system executable')
            binary = base / 'runtime/chromium'
            binary.write_bytes(b'patched executable')
            extension = repair.DEFAULT_EXTENSION
            repair.atomic_json(base / 'installation.json', {
                'bundle_root': str(repair.HERE),
                'source_path': str(source),
                'source_sha256': repair.digest(source),
                'patched_sha256': repair.digest(binary),
                'extension_path': str(extension),
            })
            config_home = Path(name) / 'config with spaces'
            arguments = ['chromium-ubo', '--base-dir', str(base),
                         '--incognito', 'https://example.com/?q=hello world']
            with patch.object(sys, 'argv', arguments), \
                    patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}), \
                    patch.object(os, 'geteuid', return_value=1000), \
                    patch.object(launcher.repair, 'validate_source'), \
                    patch.object(launcher.repair, 'browser_processes', return_value=[]), \
                    patch.object(os, 'execv', side_effect=RuntimeError('exec intercepted')) as execute:
                with self.assertRaisesRegex(RuntimeError, 'exec intercepted'):
                    launcher.main()
            executable, browser_arguments = execute.call_args.args
            self.assertEqual(executable, str(binary))
            self.assertIn('--user-data-dir=' + str(config_home / 'chromium'), browser_arguments)
            self.assertIn('--load-extension=' + str(extension), browser_arguments)
            self.assertEqual(browser_arguments[-2:], ['--incognito', 'https://example.com/?q=hello world'])

if __name__ == '__main__':unittest.main(verbosity=2)
