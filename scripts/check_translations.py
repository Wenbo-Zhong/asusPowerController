"""检查消息键、命名占位符与源码引用；也可为贡献者生成新语言模板。"""
import argparse
import ast
import json
from pathlib import Path
import re
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from asus_power.i18n import CATALOG_DIR, normalize_locale, placeholders, read_catalog


def validate_catalog(path, english, strict=False):
    errors = []
    catalog = read_catalog(path)
    unknown = catalog.keys() - english.keys()
    missing = english.keys() - catalog.keys()
    if unknown:
        errors.append(f'{path.name}: unknown keys: {", ".join(sorted(unknown))}')
    if strict and missing:
        errors.append(f'{path.name}: missing keys: {", ".join(sorted(missing))}')
    for key in catalog.keys() & english.keys():
        try:
            if not catalog[key] or placeholders(catalog[key]) != placeholders(english[key]):
                errors.append(f'{path.name}: {key}: empty value or placeholder mismatch')
        except ValueError as error:
            errors.append(f'{path.name}: {key}: {error}')
    return catalog, errors


def source_errors(english):
    errors = []
    used = {'desktop.comment', 'app.name'}
    for path in [*PROJECT.joinpath('asus_power').rglob('*.py'), PROJECT / 'scripts/desktop_launcher.py']:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'tr':
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                errors.append(f'{path}:{node.lineno}: message key must be a literal')
                continue
            key = node.args[0].value
            used.add(key)
            if key not in english:
                errors.append(f'{path}:{node.lineno}: unknown key: {key}')
            elif {item.arg for item in node.keywords} != set(placeholders(english[key])):
                errors.append(f'{path}:{node.lineno}: {key}: argument mismatch')
    for key in sorted(english.keys() - used):
        errors.append(f'en.json: unused key: {key}')
    return errors


def main():
    parser = argparse.ArgumentParser(description='Validate ASUS Power Controller translations')
    parser.add_argument('catalogs', nargs='*', help='JSON file paths; defaults to all catalogs')
    parser.add_argument('--strict', action='store_true', help='Require every English message key')
    parser.add_argument('--new', metavar='LANGUAGE', help='Create a new catalog from en.json, e.g. de or pt_BR')
    args = parser.parse_args()
    english = read_catalog(CATALOG_DIR / 'en.json')
    if args.new:
        code = normalize_locale(args.new)
        if code == 'en' or not re.fullmatch(r'[a-z]{2,3}(?:_[A-Z][a-z]{3})?(?:_(?:[A-Z]{2}|\d{3}))?', args.new):
            parser.error('Use a canonical language code, e.g. de, pt_BR or sr_Latn')
        path = CATALOG_DIR / f'{code}.json'
        try:
            with path.open('x', encoding='utf-8') as stream:
                json.dump(english, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
        except FileExistsError:
            parser.error(f'{path.name} already exists')
        print(f'Created {path}; translate the values, keeping keys and placeholders unchanged.')
        return 0
    errors = source_errors(english)
    paths = [Path(path) for path in args.catalogs] or sorted(CATALOG_DIR.glob('*.json'))
    for path in paths:
        if not re.fullmatch(r'[a-z]{2,3}(?:_[A-Z][a-z]{3})?(?:_(?:[A-Z]{2}|\d{3}))?', path.stem):
            errors.append(f'{path.name}: invalid locale filename')
            continue
        try:
            catalog, issues = validate_catalog(path, english, args.strict)
            errors.extend(issues)
            translated = sum(value != english.get(key) for key, value in catalog.items())
            print(f'{path.name}: {len(catalog)}/{len(english)} messages, {translated} differ from English')
        except (OSError, ValueError) as error:
            errors.append(f'{path.name}: {error}')
    for error in errors:
        print(error, file=sys.stderr)
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
