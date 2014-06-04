"""
@since: 2014-05-07
@author: Jivan
@brief: Script to combine fixture data migration & dumping the data back to the fixture file.
    This is a workaround because django's model cache gets confused by the git checkouts and
    the dumpdata command needs to be executed in a fresh django process untouched by the evil
    hacks to the cache done by the migration process.
@todo: Make sure the current git workspace is clean before attempting to switch commits.
"""
import argparse
import logging
from subprocess import check_output
import sys

import django.db
from south.models import MigrationHistory

from django_fixture_tools.shared import query_yes_no, identify_and_check_out_last_modified_commit,\
    check_out_branch, create_compatible_db, load_fixture, fake_migrations,\
    get_latest_fixture_migrations, reset_db, sync_all, migrate_and_dump,\
    scan_filesystem_for_fixtures, clear_south_migration_caches, git_commit_all


logger = logging.getLogger(__name__)
sh = logging.StreamHandler()
logger.addHandler(sh)
logger.setLevel(logging.DEBUG)


def migrate_all_fixtures(scan_path, load_commit=None, exclude_dirs=[], skip_fixtures=[],
                            database='fixture_tools_db', debug=False):
    """ @brief: Peforms migrate_fixture() on all json fixtures beneath \a path.
        @author: Jivan
        @since: 2014-05-23
        @note: Fixtures are files ending in .json in a directory with name containing 'fixture'.
        @param exclude_dirs: Don't search for fixtures in these directories.  Directories
            should be expressed relative to \a path.
        @param skip_fixtures: Don't process these fixtures.  Fixtures should be expressed with
            the full path from \a path.
    """
    fs = scan_filesystem_for_fixtures(
             scan_path, exclude_dirs=exclude_dirs, exclude_fixtures=skip_fixtures)

    successful_fixtures = []
    failed_fixtures = []
    skipped_fixtures = []
    for f in fs:
        if f in skip_fixtures:
            skipped_fixtures.append(f)
            logger.info('{}: skipped'.format(f))
            continue
        logger.info('{}: migrating'.format(f))
        success = migrate_fixture(f, load_commit=load_commit, database=database, debug=debug)
        if success:
            successful_fixtures.append(f)
            msg = 'auto-migrated: {}'.format(f)
            git_commit_all(msg)
        else: failed_fixtures.append(f)

    return (successful_fixtures, failed_fixtures, skip_fixtures)


def migrate_fixture(fixture_path, database='fixture_tools_db', load_commit=None, debug=False):
    """ @brief: Migrates \a fixture_path from the commit it was last modified to the current
            state of South migrations.
        @author: Jivan
        @since: 2014-05-23
        @param load_commit: If not None, this commit will be used to load \a fixture_path
            instead of the commit in which it was most recently modified commit.
    """
    fms = get_latest_fixture_migrations(fixture_path)
    # If there is no migration history in the fixture, exit with warning
    if len(fms) == 0:
        logger.info('There is no South migration history in this fixture.  You need to '\
                    'initialize the fixture before attempting to migrate it.')
        ret = False
    else:
        logger.info('--- Finding commit when fixture was last modified.')
        original_branch, load_commit = \
            identify_and_check_out_last_modified_commit(
                fixture_path, debug=debug, commit=load_commit
            )
        logger.info('Using commit: {} to load fixture.'.format(load_commit[:8]))

        logger.info('--- Creating compatible database.')
        # Prevents 'another session is using the database' errors due to lingering connections.
        django.db.close_connection()
        clear_south_migration_caches()
        reset_db(database=database)
        if debug:
            if not query_yes_no('Database reset, continue?'):
                check_out_branch(original_branch, debug=debug)
                return False
        sync_all(database=database)
        if debug:
            if not query_yes_no('Empty database created, continue?'):
                check_out_branch(original_branch, debug=debug)
                return False

        logger.info('--- Loading fixture.')
        logger.info('Fixture name: {}'.format(fixture_path))
        load_fixture(fixture_path, database=database)
    
        logger.info('--- Checking out original commit.')
        check_out_branch(original_branch, debug=debug)

        # This is performed in a subprocess because the django model caching gets too confused
        #    by checking out a different commit to do it in this process.
        logger.info('--- Migrating to latest and dumping back to fixture file.')
        migrate_and_dump_cmd = ' '.join(['python','../django_fixture_tools/shared.py',
                                 'migrate_and_dump', fixture_path])
        migrate_and_dump_out = check_output(migrate_and_dump_cmd, shell=True)
        ret = True
    return ret


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
                description='Add current South migration history to fixture(s).')
    parser.add_argument('-s', '--scan_path', nargs=1,
        help='path to scan for fixtures when initializing multiple fixtures')
    parser.add_argument('-p', '--fixture_path', nargs=1,
        help='path to fixture to initialize')
    parser.add_argument('-d', '--debug', default=False, action='store_true',
        help='Turn on debugging features')
    parser.add_argument('-c', '--commit', nargs=1, default=None,
        help='Use this commit instead of last modified commit')
    args = parser.parse_args()

    debug = args.debug
    commit = args.commit

    if args.scan_path and args.fixture_path:
        msg = 'Please use only one of -s / -f'
        print(msg)
    elif args.scan_path:
        exclude_dirs = ['build', 'sandbox']
        skip_fixtures = []
        scan_path = args.scan_path[0]
        migrate_all_fixtures(scan_path, debug=debug, load_commit=commit,
                             skip_fixtures=skip_fixtures, exclude_dirs=exclude_dirs)
    elif args.fixture_path:
        fixture_path = args.fixture_path[0]
        migrate_fixture(fixture_path, debug=args.debug, load_commit=commit)
    