import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
APP_VERSION = '3.7.0'
LOCAL_MODE = os.environ.get('CRM_LOCAL_MODE') == '1'
DATA_DIR = Path(os.environ.get('CRM_DATA_DIR',str(BASE_DIR))).resolve()
BACKUP_DIR = DATA_DIR/'backups'
DATA_DIR.mkdir(parents=True,exist_ok=True)
DEBUG = os.environ.get('DEBUG', '0') == '1'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    raise RuntimeError('Set DJANGO_SECRET_KEY in the server environment')
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'crm']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','crm.context.ui_context']}}]
WSGI_APPLICATION = 'config.wsgi.application'
if os.environ.get('POSTGRES_HOST'):
    DATABASES = {'default': {'ENGINE':'django.db.backends.postgresql','HOST':os.environ['POSTGRES_HOST'],'NAME':os.environ.get('POSTGRES_DB','egrcrm'),'USER':os.environ.get('POSTGRES_USER','egrcrm'),'PASSWORD':os.environ['POSTGRES_PASSWORD'],'PORT':os.environ.get('POSTGRES_PORT','5432')}}
else:
    DATABASES = {'default':{'ENGINE':'django.db.backends.sqlite3','NAME':DATA_DIR/'dev.sqlite3','OPTIONS':{'timeout':20}}}
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'}]
LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Europe/Minsk'
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR/'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = not DEBUG

SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS','0' if DEBUG else '31536000'))
CSRF_TRUSTED_ORIGINS = [x for x in os.environ.get('CSRF_TRUSTED_ORIGINS','').split(',') if x]
if os.environ.get('TRUST_PROXY_HTTPS') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
DATA_UPLOAD_MAX_MEMORY_SIZE = 100*1024*1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024*1024

import sys
PROGRESS_CACHE_SECONDS = 0 if 'test' in sys.argv else 2

SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get('SECURE_HSTS_INCLUDE_SUBDOMAINS') == '1'
SECURE_HSTS_PRELOAD = os.environ.get('SECURE_HSTS_PRELOAD') == '1'

# Desktop HTTP is available only on the loopback interface. Public defaults remain HTTPS.
if LOCAL_MODE:
    ALLOWED_HOSTS = ['127.0.0.1','localhost','[::1]']
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
    SESSION_COOKIE_NAME = 'egrcrm_local_sessionid'
    CSRF_COOKIE_NAME = 'egrcrm_local_csrftoken'
    SECURE_PROXY_SSL_HEADER = None
