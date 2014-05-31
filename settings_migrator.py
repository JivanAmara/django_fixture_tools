"""
@since: 2014-04-15
@author: Jivan
@brief: Adds settings needed by fixture migrator to project settings.
"""
from dowant.settings import *

# Hide the default database so:
#  1. We work around syncdb issues with auth & contenttypes
#  2. No programming mistakes will damage the original database
DATABASES['default'] = {
    'ENGINE': 'django.contrib.gis.db.backends.postgis',
    'NAME': 'fixture_tools_db',
    'PASSWORD': 'fixture_tools',
    'HOST': 'localhost',
    'USER': 'fixture_tools',
}
DATABASES['fixture_tools_db'] = DATABASES['default']

INSTALLED_APPS += ('django_extensions','south')

# Allows an extra check to make sure you don't clobber your existing database.
FIXTURE_MIGRATOR_SETTINGS_FILE = True
