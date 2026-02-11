from .settings import *

# Use a lightweight SQLite DB for tests to avoid needing Postgres permissions
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_sqlite3.db',
    }
}

# Reduce logging noise during tests
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
}
