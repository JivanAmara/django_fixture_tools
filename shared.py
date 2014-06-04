"""
@since: 2014-04-07
@author: Jivan
@brief: Recursively searches current Django project paths for fixtures and migrates each it finds.

Requirements:
    Existing database 'fixture_tools_db'.
    django_extensions installed.
    Update settings_migrator to replace 'default' db with 'fixture_tools_db'.
"""
from _collections import defaultdict
import logging
import os
from subprocess import check_output, CalledProcessError
import subprocess
import sys
import threading

import django
from django.conf import settings, LazySettings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.commands.dumpdata import Command as DumpDataCommand
from django.core.management.commands.loaddata import Command as LoadDataCommand
from django.db.models.loading import AppCache
from django.utils.datastructures import SortedDict
from django.utils.functional import empty
from south.exceptions import NoMigrations
from south.management.commands import SyncCommand
from south.management.commands.migrate import Command as MigrateCommand
import south.migration
from south.migration.base import Migrations
from south.migration.utils import app_label_to_app_module
from south.models import MigrationHistory

import simplejson as json


logger = logging.getLogger(__name__)
sh = logging.StreamHandler()
logger.addHandler(sh)
logger.setLevel(logging.DEBUG)

DEFAULTDB = 'default'

original_branch = None


def get_last_modified_commit(file_path, ignore_commits=None):
    """ @brief: Returns the git commit \a file_path was last modified in.
        @author: Jivan
        @since: 2014-04-15
    """
    # --- Get the commit that \a fixture_path was last modified.
#     cmd = ['git', '--no-pager', 'log', '-1', '--pretty=oneline', '--follow {}'.format(file_path)]
    if not ignore_commits:
        cmd = ['git', '--no-pager', 'log', '-1', '--pretty=oneline', '{}'.format(file_path)]
    else:
        cmd = ['git', '--no-pager', 'log', '--pretty=oneline', '{}'.format(file_path)]

    cmd = ' '.join(cmd)
    o = subprocess.check_output(cmd, shell=True)
    o = o.strip()

    if not ignore_commits:
        outs = o.split()
        last_modified_commit = outs[0]
    else:
        outlines = o.split('\n')
        commits = [l.split()[0] for l in outlines]
        commits = [c for c in commits if c not in ignore_commits]
        last_modified_commit = commits[0]
    
    return last_modified_commit


def query_yes_no(question, default="yes"):
    """Ask a yes/no question via raw_input() and return their answer.

    "question" is a string that is presented to the user.
    "default" is the presumed answer if the user just hits <Enter>.
        It must be "yes" (the default), "no" or None (meaning
        an answer is required of the user).

    The "answer" return value is one of "yes" or "no".
    """
    valid = {"yes":True,   "y":True,  "ye":True,
             "no":False,     "n":False}
    if default == None:
        prompt = " [y/n] "
    elif default == "yes":
        prompt = " [Y/n] "
    elif default == "no":
        prompt = " [y/N] "
    else:
        raise ValueError("invalid default answer: '%s'" % default)

    while True:
        sys.stdout.write(question + prompt)
        choice = raw_input().lower()
        if default is not None and choice == '':
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            sys.stdout.write("Please respond with 'yes' or 'no' "\
                             "(or 'y' or 'n').\n")


def get_commits_when_file_modified(file_path, max_results=None):
    """ @brief: Returns the git commits when \a file_path was modified, newest to oldest.
        @author: Jivan
        @since: 2014-04-16
    """
    # --- Make external call to git to get the commits that \a fixture_path was last modified.
#     cmd = ['git', '--no-pager', 'log', '-{}'.format(max_results), '--pretty=oneline', '--follow {}'.format(file_path)]
    if max_results:
        cmd = ['git', '--no-pager', 'log', '-{}'.format(max_results), '--pretty=oneline', '{}'.format(file_path)]
    else:
        cmd = ['git', '--no-pager', 'log', '--pretty=oneline', '{}'.format(file_path)]

    cmd = ' '.join(cmd)
 
    o = subprocess.check_output(cmd, shell=True)
    
    # --- Parse the output from the git call.
    o = o.strip()
    lines = o.split('\n')
    commits = []
    for line in lines:
        line_items = line.split()
        commits.append(line_items[0])

    return commits


def git_commit_compare(commita, commitb):
    #git merge-base --is-ancestor <commit a> <commit b>
    # 0 if <commit a> is ancestor of <commit b>
    # 1 if <commit a> is not an ancestor of <commit b>
    # Errors signaled by numbers not in [0, 1]
    cmd_args = ['git', 'merge-base', commita, commitb]
    cmd = ' '.join(cmd_args)
    o = subprocess.check_output(cmd, shell=True)
    o = o.strip()
    if o == commita:
        resp = 1
    elif o == commitb:
        resp = -1
    elif commita == commitb:
        resp = 0
    else:
        print('merge base: {}'.format(o))
        raise Exception('Commit {} not an ancestor of {} and vice-versa'.format(commita, commitb))

    return resp


def get_latest_fixture_migrations(fixture_path):
    """ @brief: Returns the latest migration for each app found in \a fixture_path.
        @author: Jivan
        @since: 2014-04-15
        @return: {<app_name>: <latest migration>, ...}
    """
    with open(fixture_path, 'r') as ff:
        fixture_contents = json.load(ff)
        fixture_migrations = {
            i['fields']['app_name']: i['fields']['migration']
                for i in fixture_contents if i['model'] == 'south.migrationhistory'
        }

    fixture_latest_migrations = defaultdict(unicode)
    for app, migration in fixture_migrations.items():
        latest_migration = fixture_latest_migrations[app]
        if latest_migration == '' or migration > latest_migration:
            fixture_latest_migrations[app] = migration

    return fixture_latest_migrations


def get_migration_paths_by_label(migration_labels):
    """ @brief: Returns a dictionary keying \a migration_labels to paths for files for each
            migration.
        @author: Jivan
        @since: 2014-04-21
    """
    migration_filenames = [ '{}.py'.format(m) for m in migration_labels ]
    migration_paths_by_label = {}
    for root, dirs, files in os.walk("/home/jivan/.projects/DeliveryHeroChina/"):      # *** Working directory should be setable explicitly.
        # Skip hidden directories
        dirs = [d for d in dirs if not d.startswith('.')]
        # Limit files to migrations requested in \a migrations.
        files = [f for f in files if f in migration_filenames ]
        for file in files:
            path = os.path.join(root, file)
            migration_paths_by_label.update({file[:-3]: path})

    return(migration_paths_by_label)


def git_get_current_branch():
    """ @brief: Get the current git branch.
        @author: Jivan
        @since: 2014-04-16
    """
    # Call git status to get current branch
    cmd = ['git', '--no-pager', 'status', '-b', '-s']
    cmd = ' '.join(cmd)
    o = subprocess.check_output(cmd, shell=True)
 
    # Parse output to get current branch
    output_line_1 = o.split('\n')[0]
    output_items = output_line_1.split()
    branch_item = output_items[1]
    branch = branch_item.split('.')[0]
    
    return branch


def git_checkout_commit(commit):
    # --- Clean working directory (checkout won't work if previous checkout left untracked files)
    cmd = ['git', 'clean', '-f', '-q']
    cmd = ' '.join(cmd)
    o = subprocess.check_output(cmd, shell=True)
    logger.debug(o)
    
    # --- Check out commit.
    cmd = ['git', 'checkout', '--quiet', commit]
    cmd = ' '.join(cmd)
    o = subprocess.check_output(cmd, shell=True)
    logger.debug(o)


def git_commit_all(msg='Autocommit: No message specified'):
    """ @brief: Commits all modified files with message \a msg.
        @author: Jivan
        @since: 2014-06-03
    """
    cmd = ['git', 'commit', '-a', '-m', '"{}"'.format(msg)]
    cmd = ' '.join(cmd)
    o = subprocess.check_output(cmd, shell=True)
    logger.debug(o)


def pg_reset_db(dbname):
    check_output('/usr/bin/sudo -u postgres /usr/bin/dropdb {}'.format(dbname), shell=True)
    check_output('/usr/bin/sudo -u postgres /usr/bin/createdb --owner=fixture_maker '\
                 '--template=template_postgis --encoding=UTF8 --lc-collate=en_US.UTF-8 '\
                 '--lc-ctype=en_US.UTF-8 {}'.format(dbname), shell=True)


def reset_db(database=None, debug=False):
    """ @brief: Performs django_extension's reset on \a database
        @author: Jivan
        @since: 2014-05-23
    """
    if database is None:
        raise('database is a required parameter')
    dbname = settings.DATABASES[database]['NAME']
    verbosity = 1 if debug else 0
    
    # call_command() failed to correctly use 'noinput' argument, used command directly instead.
    from django_extensions.management.commands.reset_db import Command as ResetDBCommand
    ResetDBCommand().execute(dbname=dbname, router=dbname, noinput=True, verbosity=verbosity)

def sync_all(database=None, debug=False):
    """ @brief: Performs a django 'sync --all'  command on \a database.
        @note: *** Sync doesn't work properly on secondary databases, making mistakes with auth
            and contenttypes.  This function currently ignores \a database and uses the
            default database.  This limitation is worked around by creating the migrator settings
            file with the default database the same as the fixture_tools_db database.
    """
    if database is None:
        raise Exception('database is a required parameter')
    SyncCommand().execute(migrate_all=True, migrate=False, verbosity=0, database=database)


def clear_south_migration_caches():
    """ @brief: Makes South look again at state of migrations on disk.
        @author: Jivan
        @since: 2014-05-23
        This is important because we switch commits resulting in migrations changing.
    """
    # This South module caches the file names of migrations read from disk, so must be reloaded
    #    since migrations change when we check out different commits.
    reload(south.migration.base)
    # This South module is somehow caching migration data resulting in the failure to
    #    recognize all the migrations that need to be applied.
    reload(south.migration)


def recreate_database(debug=False, postgres=False, database=None):
    """ @brief: Removes all tables from database \a db and sync to current models.
        @author: Jivan
        @since: 2014-04-16
        @param database: This is the string identifying the database to django.  It is a
            key to the DATABASES dictionary from django's settings file.
    """
    if database is None:
        raise('database is a required parameter')

    # This is necessary to prevent 'another session is using the database' errors due to lingering
    #    connections.
    django.db.close_connection()
    clear_south_migration_caches()

    # --- Remove all tables from database.
    reset_db(database=database)

    if debug:
        stop = not query_yes_no("Reset DB, continue processing?")
        if stop: exit()

    # --- Sync to current models
    sync_all()

    fake_migrations()


def fake_migrations(database=None):
    if database is None:
        database = 'origin'

    # This South module caches the file names of migrations read from disk, so must be reloaded
    #    since migrations change when we check out different commits.
    reload(south.migration.base)
    # This South module is somehow caching migration data resulting in the failure to
    #    recognize all the migrations that need to be applied.
    reload(south.migration)

    # --- Update South migration history to match current state of database.
    logger.debug('Executing migrate --fake')
    call_command('migrate', database=database, fake=True, verbosity=0)


def load_fixture(fixture_path, database=None):
    if database is None:
        raise Exception('database is a required argument')
    ldc = LoadDataCommand()
    from cStringIO import StringIO
    original_stderr = sys.stderr
    sys.stderr = my_stderr = StringIO()
    ldc.execute(fixture_path, database=database, verbosity=1)
    sys.stderr = original_stderr
    ret = my_stderr.getvalue()
    if 'Problem installing fixture' in ret:
        raise Exception(ret)


def south_migrate(dblabel=None, app=None, target=None, fake=False, verbosity=0):
    """ @brief: Migrate \a app to \a target if specified, or migrate all to
            latest migrations in codebase if not.
        @author: Jivan
        @since: 2014-04-30
    """
    if dblabel is None: raise Exception('Missing dblabel paramater')
    if bool(app) != bool(target): raise Exception('app & target must be used together')
    
    try:
        django.db.close_connection()

        # This South module caches the file names of migrations read from disk, so must be reloaded
        #    since migrations change when we check out different commits.
        reload(south.migration.base)
        # This South module is somehow caching migration data resulting in the failure to
        #    recognize all the migrations that need to be applied.
        reload(south.migration)

        # Shared migrate params.
        migrate_params = ['migrate']
        migrate_kw_params = {
            'fake': fake,
            'verbosity': verbosity,
            'interactive': False,
        }
        
        if app:
            # Additional migrate params for only doing an app migration.
            migrate_params.append(app)
            migrate_params.append(target)
            call_command(*migrate_params)
        else:
            # Additional migrate params for migrating all applications.
#             migrate_params.update({'all_apps': True})
            call_command(*migrate_params, **migrate_kw_params)
#         mc.execute(**migrate_params)
    except Exception as ex:
        msg = 'Error attempting to migrate database forward to latest migrations.  '\
              'Please try this migration manually and correct any problems.\n'\
              'Original Error:\n{}'.format(ex.message)
        logger.error(msg)
        raise Exception(msg)


def dumpdata(database=None, fixture_path=None):
    """ @brief: Dumps data from \a database to \a fixture_path.
        @since: 2014-05-29
        @author: Jivan
        If fixture_path isn't provided, dumps to stdout.
    """
    if database is None: raise Exception('dblabel is a required argument')

    ddc = DumpDataCommand()

    # If fixture_path has been specified, steal output from stdout.
    if fixture_path:
        from cStringIO import StringIO
        old_stdout = sys.stdout
        sys.stdout = mystdout = StringIO()

    exclude = ['auth.permission', 'contenttypes']
    ddc.execute(format='json', natural=True, exclude=exclude, indent=4, database=database)

    # If fixture_path has been specified, dump the stolen output into it.
    if fixture_path:
        sys.stdout = old_stdout
        with open(fixture_path, 'w') as f:
            f.write(mystdout.getvalue())
            f.write('\n')
            mystdout.close()


def reload_models():
    import os
    from django.db.models.loading import AppCache
    cache = AppCache()
    
    curdir = os.getcwd()
    
    for app in cache.get_apps():
        f = app.__file__
        if f.startswith(curdir) and f.endswith('.pyc'):
            os.remove(f)
        __import__(app.__name__)
        reload(app)
    
    from django.utils.datastructures import SortedDict
    cache.app_store = SortedDict()
    cache.app_models = SortedDict()
    cache.app_errors = {}
    cache.handled = {}
    cache.loaded = False


def clear_django_model_cache():
    """ @brief: Sets the django model cache to reload model definitions & collects the current
            settings.INSTALLED_APPS content.
        @author: Jivan
        @since: 2014-05-06
    """
    cache = django.db.models.loading.cache
    cache.app_store = SortedDict()
    cache.app_labels = {}
#     cache.app_models = SortedDict()
    cache.app_errors = {}
    cache.loaded = False
    cache.handled = {}
    cache.postponed = []
    cache.nesting_level = 0
#     cache.write_lock = threading.RLock()
    cache._get_models_cache = {}

#     django.db.models.loading.cache.loaded = False
#     django.db.models.loading.cache.handled = {}
#     # *** Do we also need to clear cache.app_store?
#     django.db.models.loading.cache.app_store = SortedDict()
    from django.conf import settings
    settings._wrapped = empty


def clear_south_migration_cache():
    """ @brief: Sets the south migration cache to re-check the filesystem for migrations.
        @author: Jivan
        @since: 2014-05-06
    """
    # This South module caches the file names of migrations read from disk, so must be reloaded
    #    since migrations change when we check out different commits.
    reload(south.migration.base)
    # This South module is somehow caching migration data resulting in the failure to
    #    recognize all the migrations that need to be applied.
    reload(south.migration)

def identify_and_check_out_last_modified_commit(fixture_path, debug=False, commit=None):
    """ @brief: identify comit \a fixture_path was last modified and check it out.
        @author: Jivan
        @since: 2014-05-07
    """
    load_commit = commit if commit else get_last_modified_commit(fixture_path)

    global original_branch
    original_branch = git_get_current_branch()

    git_checkout_commit(load_commit)
    clear_django_model_cache()
    clear_south_migration_cache()

    if debug:
        stop = not query_yes_no("Checked out commit '{}', continue processing?".format(load_commit))
        if stop: exit()
    return (original_branch, load_commit)


def create_compatible_db(fixture_path=None, database=None, debug=False, postgres=False,
                         ignore_fixture_history=False):
    if database is None or fixture_path is None:
        raise Exception('database and fixture_path are required parameters')
    recreate_database(database=database, debug=debug, postgres=postgres)

    # Fixture migrations (latest migrations for each app with migration history in fixture)
    fms = get_latest_fixture_migrations(fixture_path)
    # If there is migration history in the fixture, and we're not ignoring it use it.
    if len(fms) > 0 and not ignore_fixture_history:
        logging.info('Found South migration history in fixture.')
        # Scrap the default migration history for the current commit
        MigrationHistory.objects.all().delete()
        if debug:
            stop = not query_yes_no("Dropped default migration history, continue processing?")
            if stop: exit()
 

def check_out_branch(branch, debug=False):
    """ @brief: Checkout out \a branch.
        @author: Jivan
        @since: 2014-05-07
    """
    git_checkout_commit(branch)


def migrate_and_dump(fixture_path, database='default', fake=False, debug=False):
    """ @brief: Migrate database to latest migrations and dump to \a fixture_path.
        @author: Jivan
        @since: 2014-05-07
    """
    call_command('migrate', database=database, fake=fake, verbosity=0)
 
    if debug:
        stop = not query_yes_no("Migrated data to latest schema in original branch, continue processing?".format(original_branch))
        if stop: exit()

#     logger.info("Sorry, you're going to have to dump the data back to fixture file yourself.")
    logger.info('Dumping migrated data back to fixture file.')
    dumpdata(DEFAULTDB, fixture_path)
    

def migrate_fixture(fixture_path, dblabel=DEFAULTDB, commit_override=None, debug=False):
    """ @brief: Use South migrations in the current project to update the contents of the
            fixture at \a fixture_path.
        @author: Jivan
        @since: 2014-04-16
        @param fixture_path: Path to the fixture file to migrate.
        @param commit_override: Use the state of models in this commit to load
            the data in \a fixture_path.  By default an appropriate commit will be guessed.
        @param try_n_guesses: Try this number of commits to attempt loading the fixture before
            giving up.
    """
    # Commit to use when loading the fixture.
    if commit_override:
        logger.info('Using commit {}.'.format(commit_override))
        load_commit = commit_override
    else:
        c = get_last_modified_commit(fixture_path)
        logger.info('Using commit when fixture was last modified: {}.'.format(c[:8]))
        load_commit = c
    
    identify_and_check_out_last_modified_commit(fixture_path)
    
    create_compatible_db_and_load_fixture(fixture_path)

    # Restore the original branch
    git_checkout_commit(original_branch)
    clear_django_model_cache()
    clear_south_migration_cache()

    if debug:
        stop = not query_yes_no("Returned to original branch '{}', continue processing?".format(original_branch))
        if stop: exit()
 
    migrate_and_dump(fixture_path)

def scan_filesystem_for_fixtures(top_dir, exclude_dirs=[], exclude_fixtures=[]):
    """ @brief: Recursively scans directory \a top_dir and returns the paths of fixtures found.
        @author: Jivan
        @since: 2014-04-21
        @note: Fixtures will be identified as a file ending in '.json' existing beneath
            a directory with 'fixture' in its name.
        @note: Skips hidden directories (those starting with '.')
        @note: Skips files contained in \a exclude_dirs
        @note: Skips files with names in \a exclude_fixtures.
    """
    fixture_files = []
    for root, dirs, files in os.walk(top_dir):
        # Skip hidden & excluded directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in exclude_dirs]
        if 'fixture' in root:
            new_fixture_files = [
                os.path.join(root, f)
                    for f in files if f.endswith('.json') and f not in exclude_fixtures
            ]
            fixture_files.extend(new_fixture_files)
    return fixture_files


if __name__ == '__main__':
    debug = False
    dblabel = DEFAULTDB
    exclude_dirs = ['build', 'sandbox']
    exclude_fixtures = [
#         'ajax_views_ajax_test.json',
#         'single_restaurant_with_polygon_delivery_area.json',
#         'distance_calculation_tests.json',
#         'test_restaurant_confirmed.json',
#         'test_order_assignment_fixture.json',
    ]
    if len(sys.argv) == 2 and sys.argv[1] == 'findfixtures':
        fs = scan_filesystem_for_fixtures('.', exclude_dirs=exclude_dirs, exclude_fixtures=exclude_fixtures)
        logger.debug('Found these fixtures in ".":')
        fixtures_with_migration_history = []
        fixtures_without_migration_history = []
        for f in fs:
            ms = get_latest_fixture_migrations(f)
            if ms:
                fixtures_with_migration_history.append(f)
            else:
                fixtures_without_migration_history.append(f)

        logger.debug('Fixtures with migration history')
        for f in fixtures_with_migration_history:
            logger.debug(f)
        logger.debug('Fixtures without migration history')
        for f in fixtures_without_migration_history:
            logger.debug(f)
    elif len(sys.argv) == 3 and sys.argv[1] == 'check_out_compatible_commit':
        fixture_path = sys.argv[2]
        original_branch = identify_and_check_out_last_modified_commit(fixture_path, debug=debug)
        print(original_branch)
    elif len(sys.argv) == 3 and sys.argv[1] == 'load_fixture':
        fixture_path = sys.argv[2]
        create_compatible_db_and_load_fixture(fixture_path, debug=debug)
    elif len(sys.argv) == 3 and sys.argv[1] == 'check_out_branch':
        branch = sys.argv[2]
        check_out_branch(branch, debug=debug)
    elif len(sys.argv) == 3 and sys.argv[1] == 'migrate_and_dump':
        fixture_path = sys.argv[2]
        migrate_and_dump(fixture_path, debug=debug)
    elif len(sys.argv) == 2:
        fixture_path = sys.argv[1]
        print('Migrating fixture: {}'.format(fixture_path))
        migrate_fixture(fixture_path, dblabel=dblabel, debug=debug)
    else:
        fs = scan_filesystem_for_fixtures('.', exclude_dirs=exclude_dirs, exclude_fixtures=exclude_fixtures)

        # *** During development, just attempt to migrate the first fixture found.
        for f in fs[:1]:
            print('Migrating fixture: {}'.format(f))
            migrate_fixture(f, dblabel=dblabel, debug=debug)
