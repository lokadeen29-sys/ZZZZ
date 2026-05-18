# Translations (Flask-Babel)

V45 introduces real i18n via Flask-Babel.

## Workflow

```bash
# 1. Extract messages from python + jinja templates
pybabel extract -F babel.cfg -o messages.pot .

# 2. First-time language init (already done for ar/en)
pybabel init -i messages.pot -d translations -l ar
pybabel init -i messages.pot -d translations -l en

# 3. After updating source strings:
pybabel update -i messages.pot -d translations

# 4. Compile .po -> .mo (required for runtime)
pybabel compile -d translations
```

## Templates

Use either:

```jinja
{{ _('Sign in') }}
{{ gettext('Sign in') }}
```

The legacy `{{ t('key') }}` filter is kept for backward compatibility and
now delegates to `gettext()` when a translation exists.
