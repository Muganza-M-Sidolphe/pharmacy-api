#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    settings_module = 'config.settings'
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        has_explicit_settings = any(arg == '--settings' or arg.startswith('--settings=') for arg in sys.argv[2:])
        if not has_explicit_settings and 'DJANGO_SETTINGS_MODULE' not in os.environ:
            settings_module = 'config.test_settings'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', settings_module)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
