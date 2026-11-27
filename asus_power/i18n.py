"""独立的消息目录与系统语言选择；仅依赖标准库，供界面和提权助手共用。"""
import json
import locale
import os
from pathlib import Path
import re
from string import Formatter

# 常规包把 JSON 放在 locales/；独立安装的提权助手把可信资源放在同一目录。
_MODULE_DIR = Path(__file__).resolve().parent
CATALOG_DIR = _MODULE_DIR / 'locales' if (_MODULE_DIR / 'locales').is_dir() else _MODULE_DIR
LOCALE_ENV = ('LC_ALL', 'LC_MESSAGES', 'LANG', 'LANGUAGE')
_CODE = re.compile(r'[a-z]{2,3}(?:_[A-Z][a-z]{3})?(?:_(?:[A-Z]{2}|\d{3}))?')
_FORMATTER = Formatter()


def normalize_locale(value):
    """去掉编码/修饰符并规范地区和文字；文字标记优先于地区。"""
    value = value.split('.', 1)[0].split('@', 1)[0].replace('-', '_')
    if value.upper() in ('C', 'POSIX'):
        return 'en'
    parts = value.split('_')
    language = parts[0].lower()
    script = next((part.title() for part in parts[1:] if len(part) == 4), '')
    region = next((part.upper() for part in parts[1:] if len(part) in (2, 3)), '')
    if language == 'zh':
        return 'zh_Hant' if script == 'Hant' or (not script and region in ('TW', 'HK', 'MO')) else 'zh_Hans'
    code = '_'.join(part for part in (language, script, region) if part)
    return code if _CODE.fullmatch(code) else 'en'


def placeholders(text):
    """译文只能保留原有命名占位符，不能访问属性、下标或嵌套格式。"""
    fields = {}
    for _, field, spec, conversion in _FORMATTER.parse(text):
        if field is None:
            continue
        if not field.isidentifier() or '{' in spec or '}' in spec:
            raise ValueError(f'Invalid translation placeholder: {field}')
        signature = (spec, conversion)
        if field in fields and fields[field] != signature:
            raise ValueError(f'Conflicting translation placeholder: {field}')
        fields[field] = signature
    return fields


def read_catalog(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate translation key: {key}')
            result[key] = value
        return result

    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
    if not isinstance(data, dict) or any(not isinstance(value, str) for value in data.values()):
        raise ValueError(f'Invalid message catalog: {path.name}')
    return data


class Translator:
    def __init__(self, language=None, *, environ=None, directory=CATALOG_DIR):
        directory = Path(directory)
        available = {path.stem for path in directory.glob('*.json') if _CODE.fullmatch(path.stem)}
        self.english = read_catalog(directory / 'en.json')
        env = os.environ if environ is None else environ
        if language is None:
            system = next((env[key] for key in LOCALE_ENV[:3] if env.get(key)), None)
            if system is None:
                try:
                    system = locale.setlocale(locale.LC_MESSAGES, None)
                except (AttributeError, locale.Error):
                    system = 'C'
            # GNU LANGUAGE 是消息语言的有序候选；C/POSIX 明确要求英文，不能被覆盖。
            candidates = ([system] if normalize_locale(system) == 'en' and system.split('.')[0].upper() in ('C', 'POSIX')
                          else env.get('LANGUAGE', '').split(':') + [system])
        else:
            candidates = [language]
        self.language = 'en'
        for candidate in filter(None, candidates):
            code = normalize_locale(candidate)
            match = next((item for item in (code, code.split('_')[0]) if item in available), None)
            if match:
                self.language = match
                break
        self.messages = {}
        if self.language != 'en':
            try:
                self.messages = read_catalog(directory / f'{self.language}.json')
            except (OSError, ValueError):
                pass

    def __call__(self, key, **values):
        original = self.english[key]
        translated = self.messages.get(key) or original
        try:
            if placeholders(translated) != placeholders(original):
                translated = original
            return translated.format(**values)
        except (ValueError, KeyError, IndexError):
            return original.format(**values)


def desktop_entries(field='Name', key='app.name'):
    """桌面名称也从 JSON 生成；新增语言无需手工修改自启动或安装模板。"""
    lines = []
    for path in sorted(CATALOG_DIR.glob('*.json')):
        if not _CODE.fullmatch(path.stem):
            continue
        translator = Translator(path.stem)
        value = translator(key).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')
        codes = {'zh_Hans': ('zh', 'zh_CN', 'zh_SG'),
                 'zh_Hant': ('zh_TW', 'zh_HK', 'zh_MO')}.get(path.stem, (path.stem,))
        for code in codes:
            lines.append(f'{field}{"" if code == "en" else "[" + code + "]"}={value}\n')
    return ''.join(lines)


# 进程启动时确定语言；配置键、D-Bus 枚举和硬件属性始终使用原始标识。
tr = Translator()
