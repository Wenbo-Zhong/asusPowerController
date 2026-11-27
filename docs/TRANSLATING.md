# Contributing translations

Translations live in `asus_power/locales/`. You can contribute a language by adding one UTF-8 JSON file. No gettext compiler, Python dependency, or language registration change is required.

## Add a language

1. Create a template from the complete English catalog:

   ```bash
   python3 scripts/check_translations.py --new de
   ```

   Use canonical filenames such as `de.json`, `fr.json`, or `pt_BR.json`. A language-wide file such as `pt.json` also serves regions without their own catalog. Existing files are never overwritten.

2. Translate the **values**, keeping all message keys unchanged. Keys describe the feature and purpose: `editor.save`, `battery.charging`, and `power.value_range`, for example.

3. Validate the files:

   ```bash
   python3 scripts/check_translations.py
   python3 scripts/check_translations.py --strict
   python3 -m unittest discover -s tests -v
   ```

   The regular check permits missing messages; those messages use English. `--strict` requires all English keys. Both check duplicate/unknown keys, empty values, malformed JSON, placeholder compatibility, and source references. The command reports how many messages differ from English.

4. Preview the app with your locale and submit the JSON file in a pull request:

   ```bash
   LC_ALL=de_DE.UTF-8 python3 -m asus_power
   ```

   Test on Linux with GTK 4 and libadwaita, including a narrow window, confirmation dialogs, and the tray. Long translations should remain clear when controls wrap.

## Placeholders and technical identifiers

Messages use Python named placeholders, allowing translators to reorder words:

```json
{
  "igpu.max_applied": "iGPU maximum frequency set to {maximum} MHz",
  "power.value_range": "Value must be {minimum}–{maximum} {unit}, in steps of {step} {unit}"
}
```

Preserve placeholder names, format specifications, and conversions exactly. For example, keep `{target:g}` intact. The same placeholder may appear more than once. Do not use positional fields, attribute access, indexing, or nested format fields. Literal braces must be doubled: `{{` and `}}`.

Keep program names (`asusctl`, `asusd`, `fwupd`), hardware identifiers (`SPL`, `TGP`, `EPP`), and units (`MHz`, `W`, `°C`) recognizable. Raw driver values, configuration keys, D-Bus identifiers, and errors produced by external tools are not translated.

Translate the distinction between editing a draft, saving all six configurations, and applying the current draft carefully. EPP rules are saved independently to asusd and remain effective after the app exits. AC/battery combinations only run automatically while this app is running.

## Language selection and fallback

The app selects its language once at process startup:

- `LC_ALL`, then `LC_MESSAGES`, then `LANG` determines the system message locale. `LANGUAGE` supplies an ordered list of preferred message languages, following GNU behavior. `C` and `POSIX` always select English.
- Encodings and modifiers are removed before matching. `pt_BR.UTF-8` matches `pt_BR.json`, then `pt.json` if the regional catalog does not exist.
- `zh_CN`, `zh_SG`, and bare `zh` select `zh_Hans.json`; `zh_TW`, `zh_HK`, and `zh_MO` select `zh_Hant.json`. Explicit `Hans`/`Hant` script tags take priority over regions.
- Languages without a contributed catalog use English. Missing, empty, or invalid individual translations also use the corresponding English message. Invalid JSON makes the whole selected catalog fall back to English.

The window, tray, CLI help, app-generated errors, login startup entry, and packaged desktop entry share the same catalogs. Desktop entries are generated during packaging. Elevated desktop starts preserve locale variables; the isolated iGPU helper receives the selected language explicitly.

Rebuild and reinstall the package to distribute a new language. If the iGPU control component is already installed, click its install button again to copy the new catalogs into its administrator-owned directory.

## For developers adding messages

Add the complete English text to `en.json` under a stable semantic key, add the corresponding Chinese translations, and call `tr('feature.message', name=value)` in the source. Keep full sentences together when possible, and name placeholders after their meaning. Run the strict catalog check before submitting.

Never make hardware or policy decisions by searching translated text. Use machine identifiers or structured state, such as `pending` power values. The translation checker verifies source keys and arguments and flags unused English keys.
