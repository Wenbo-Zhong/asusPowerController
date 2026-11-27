import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from asus_power import i18n, igpu
from asus_power.i18n import CATALOG_DIR, Translator, placeholders, read_catalog

PROJECT = Path(__file__).resolve().parents[1]


class LocaleTests(unittest.TestCase):
    def test_chinese_regions_and_explicit_scripts(self):
        cases = {
            'zh': 'zh_Hans', 'zh_CN.UTF-8': 'zh_Hans', 'zh_SG': 'zh_Hans',
            'zh_TW.UTF-8': 'zh_Hant', 'zh_HK': 'zh_Hant', 'zh_MO': 'zh_Hant',
            'zh-Hans-TW': 'zh_Hans', 'zh-Hant-CN': 'zh_Hant',
            'zh_hant_hk.UTF-8@modifier': 'zh_Hant',
        }
        for locale, expected in cases.items():
            with self.subTest(locale=locale):
                self.assertEqual(Translator(locale).language, expected)

    def test_english_and_unknown_languages(self):
        for locale in ('en_US.UTF-8', 'en_GB', 'fr_FR', 'ja_JP', 'C', 'C.UTF-8',
                       'POSIX', '../../zh_Hant', ''):
            with self.subTest(locale=locale):
                self.assertEqual(Translator(locale)('app.name'), 'ASUS Power Controller')

    def test_locale_environment_precedence(self):
        env = {'LANG': 'zh_CN', 'LC_MESSAGES': 'zh_TW', 'LC_ALL': 'en_US'}
        self.assertEqual(Translator(environ=env).language, 'en')
        env.pop('LC_ALL')
        self.assertEqual(Translator(environ=env).language, 'zh_Hant')
        env.pop('LC_MESSAGES')
        self.assertEqual(Translator(environ=env).language, 'zh_Hans')

    def test_language_preferences_and_c_override(self):
        env = {'LANG': 'en_US.UTF-8', 'LANGUAGE': 'fr_FR:zh_TW:zh_CN'}
        self.assertEqual(Translator(environ=env).language, 'zh_Hant')
        for locale in ('C', 'C.UTF-8', 'POSIX'):
            self.assertEqual(Translator(environ=dict(env, LC_ALL=locale)).language, 'en')

    def test_new_catalogs_are_discovered_and_missing_messages_use_english(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(CATALOG_DIR / 'en.json', root / 'en.json')
            (root / 'de.json').write_text('{"app.name": "ASUS Energieverwaltung"}')
            (root / 'de_AT.json').write_text('{"app.name": "ASUS Energie"}')
            self.assertEqual(Translator('de_CH', directory=root)('app.name'), 'ASUS Energieverwaltung')
            self.assertEqual(Translator('de_AT', directory=root)('app.name'), 'ASUS Energie')
            self.assertEqual(Translator('de', directory=root)('editor.save'), 'Save all six configurations')

    def test_invalid_message_templates_fall_back_per_message(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(CATALOG_DIR / 'en.json', root / 'en.json')
            data = {'app.name': 'ASUS Energie', 'igpu.max_applied': 'Maximum {wrong}',
                    'app.ready': '', 'battery.eta_target': '{target.__class__}'}
            (root / 'de.json').write_text(json.dumps(data))
            translator = Translator('de', directory=root)
            self.assertEqual(translator('app.name'), 'ASUS Energie')
            self.assertEqual(translator('app.ready'), 'Ready')
            self.assertEqual(translator('igpu.max_applied', maximum=1400),
                             'iGPU maximum frequency set to 1400 MHz')
            self.assertEqual(translator('battery.eta_target', target=80), 'Estimated time to 80%')

    def test_invalid_catalog_falls_back_without_crashing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(CATALOG_DIR / 'en.json', root / 'en.json')
            for data in ('{bad', '{"app.name": 12}', '{"app.name":"one","app.name":"two"}'):
                (root / 'de.json').write_text(data)
                self.assertEqual(Translator('de', directory=root)('app.ready'), 'Ready')

    def test_all_bundled_catalogs_are_complete_and_format_compatibly(self):
        english = read_catalog(CATALOG_DIR / 'en.json')
        for language in ('en', 'zh_Hans', 'zh_Hant'):
            translator = Translator(language)
            catalog = read_catalog(CATALOG_DIR / f'{language}.json')
            self.assertEqual(catalog.keys(), english.keys())
            for key, original in english.items():
                with self.subTest(language=language, key=key):
                    self.assertTrue(catalog[key])
                    self.assertEqual(placeholders(catalog[key]), placeholders(original))
                    values = {field: 42 for field in placeholders(original)}
                    self.assertEqual(translator(key, **values), catalog[key].format(**values))
        self.assertEqual(Translator('zh_CN')('editor.save'), '保存六套配置')
        self.assertEqual(Translator('zh_TW')('editor.save'), '儲存六套配置')
        self.assertEqual(Translator('en')('editor.save'), 'Save all six configurations')

    def test_placeholder_order_and_literal_braces(self):
        self.assertEqual(placeholders('{unit}: {value:g} / {{literal}}'),
                         {'unit': ('', None), 'value': ('g', None)})
        for text in ('{}', '{value[0]}', '{value.attr}', '{value:{width}}'):
            with self.assertRaises(ValueError):
                placeholders(text)

    def test_translation_checker_accepts_bundled_catalogs_and_rejects_bad_fields(self):
        checked = subprocess.run([sys.executable, 'scripts/check_translations.py', '--strict'],
                                 cwd=PROJECT, capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'de.json'
            path.write_text('{"igpu.max_applied":"{wrong}"}')
            checked = subprocess.run([sys.executable, 'scripts/check_translations.py', str(path)],
                                     cwd=PROJECT, capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 1)
            self.assertIn('placeholder mismatch', checked.stderr)

    def test_new_language_template_preserves_existing_translation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'asus_power'
            (package / 'locales').mkdir(parents=True)
            (package / '__init__.py').write_text('')
            shutil.copyfile(PROJECT / 'asus_power/i18n.py', package / 'i18n.py')
            shutil.copyfile(CATALOG_DIR / 'en.json', package / 'locales/en.json')
            script = root / 'scripts/check_translations.py'
            script.parent.mkdir()
            shutil.copyfile(PROJECT / 'scripts/check_translations.py', script)
            command = [sys.executable, '-I', str(script), '--new', 'de']
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            created = package / 'locales/de.json'
            self.assertEqual(read_catalog(created), read_catalog(CATALOG_DIR / 'en.json'))
            created.write_text('{"app.name":"Übersetzung"}')
            repeated = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=10)
            self.assertEqual(repeated.returncode, 2)
            self.assertEqual(read_catalog(created), {'app.name': 'Übersetzung'})


class LanguageIntegrationTests(unittest.TestCase):
    def test_application_and_launcher_help_use_system_language(self):
        for locale, text in (('en_US.UTF-8', 'Start in the system tray'),
                             ('zh_CN.UTF-8', '启动到系统托盘'),
                             ('zh_TW.UTF-8', '啟動到系統托盤'),
                             ('ja_JP.UTF-8', 'Start in the system tray')):
            env = dict(os.environ, LC_ALL=locale)
            env.pop('LANGUAGE', None)
            with self.subTest(locale=locale):
                output = subprocess.run([sys.executable, '-m', 'asus_power', '--help'],
                                        cwd=PROJECT, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(output.returncode, 0, output.stderr)
                self.assertIn(text, output.stdout)
                launcher = subprocess.run([sys.executable, '-I', 'scripts/desktop_launcher.py', '--help'],
                                          cwd=PROJECT, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(launcher.returncode, 0, launcher.stderr)
                expected = Translator(locale)('launcher.hidden_help')
                self.assertIn(expected, launcher.stdout)

    def test_isolated_helpers_load_only_sibling_translations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / 'installed'
            installed.mkdir()
            for name in ('igpu.py', 'epp.py', 'i18n.py'):
                shutil.copyfile(PROJECT / 'asus_power' / name, installed / name)
            for path in CATALOG_DIR.glob('*.json'):
                shutil.copyfile(path, installed / path.name)
            # -I 必须忽略当前目录及 PYTHONPATH；真正的资源从安装目录加载。
            (root / 'i18n.py').write_text('raise RuntimeError("untrusted import")')
            env = dict(os.environ, LC_ALL='C', PYTHONPATH=str(root))
            for language in ('en', 'zh_Hans', 'zh_Hant'):
                result = subprocess.run(
                    [sys.executable, '-I', str(installed / 'igpu.py'),
                     '--language', language, '--device', 'missing', '--max', '1400'],
                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                # 非 root 环境首先要求授权，root 环境首先校验设备；两条路径都应本地化。
                key = 'igpu.admin_required' if os.geteuid() != 0 else 'igpu.device_missing'
                self.assertEqual(result.stderr.strip(), Translator(language)(key))
            result = subprocess.run([sys.executable, '-I', str(installed / 'epp.py'), '--help'],
                                    cwd=root, env=dict(env, LC_ALL='zh_TW.UTF-8'),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(Translator('zh_TW')('epp.description'), result.stdout)

    def test_desktop_and_autostart_names_share_catalogs(self):
        from asus_power.autostart import set_enabled
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'autostart.desktop'
            set_enabled(True, path)
            text = path.read_text()
            self.assertIn('Name=ASUS Power Controller\n', text)
            self.assertIn('Name[zh_CN]=电脑管家\n', text)
            self.assertIn('Name[zh_TW]=電腦管家\n', text)
            self.assertIn('Name[zh_HK]=電腦管家\n', text)
            self.assertNotIn('Name[en]', text)

    def test_helper_requires_trusted_support_files(self):
        original_lstat = Path.lstat
        def root_owned(path, *args, **kwargs):
            metadata = original_lstat(path, *args, **kwargs)
            return SimpleNamespace(st_uid=0, st_mode=metadata.st_mode)

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            helper = root / 'igpu.py'
            helper.write_text('')
            with patch.object(igpu, 'HELPER', helper), patch.object(Path, 'lstat', root_owned):
                self.assertFalse(igpu.trusted_helper())
                for name in ('i18n.py', 'en.json', 'zh_Hans.json', 'zh_Hant.json'):
                    (root / name).write_text('')
                self.assertTrue(igpu.trusted_helper())
                extra = root / 'de.json'
                extra.write_text('')
                extra.chmod(0o666)
                self.assertFalse(igpu.trusted_helper())
                extra.unlink()
                locale_directory = root / 'locales'
                locale_directory.symlink_to(root, target_is_directory=True)
                self.assertFalse(igpu.trusted_helper())
                locale_directory.unlink()
                support = root / 'i18n.py'
                support.unlink()
                support.symlink_to(helper)
                self.assertFalse(igpu.trusted_helper())
