SECRET_KEY = "drf-toy-secret-key-0000000000000000000000000000000000000000"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_toy.blog",  # before the users app on purpose: its tables reference the users table
    "drf_toy.accounts",
]
AUTH_USER_MODEL = "accounts.User"
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "drf_toy.urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticatedOrReadOnly",),
}
