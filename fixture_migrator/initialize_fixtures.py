"""
@since: 2014-05-23
@author: Jivan
@brief: Command-line utility to add South migration history to fixtures.
@requires:
    South in pythonpath
    DJANGO_SETTINGS_MODULE in environment (settings for this package, not your project!)
    
"""
import argparse
import logging
import os

import django.db
from south.models import MigrationHistory

from django_fixture_tools.shared import scan_filesystem_for_fixtures, \
    get_latest_fixture_migrations, reset_db, sync_all, load_fixture, migrate_and_dump


logger = logging.getLogger(__name__)
sh = logging.StreamHandler()
logger.addHandler(sh)
logger.setLevel(logging.DEBUG)


def initialize_all_fixtures(path, exclude_dirs=[], skip_fixtures=[],
                            database='fixture_tools_db', debug=False, force=False):
    """ @brief: Peforms initialize_fixture() on all json fixtures beneath \a path.
        @author: Jivan
        @since: 2014-05-23
        @note: Fixtures are files ending in .json in a directory with name containing 'fixture'.
    """
    fs = scan_filesystem_for_fixtures(
             path, exclude_dirs=exclude_dirs, exclude_fixtures=skip_fixtures)

    successful_fixtures = []
    failed_fixtures = []
    skipped_fixtures = []
    for f in fs:
        if f in skip_fixtures:
            skipped_fixtures.append(f)
            logger.info('{}: skipped'.format(f))
            continue
        logger.info('{}: initializing'.format(f))
        success = initialize_fixture(f, force=force)
        if success: successful_fixtures.append(f)
        else: failed_fixtures.append(f)

    return (successful_fixtures, failed_fixtures, skip_fixtures)


def initialize_fixture(fixture_path, database='fixture_tools_db', debug=False, force=False):
    """ @brief: Adds up-to-date South migration history to the fixture at \a fixture_path.
        @author: Jivan
        @since: 2014-05-23
        @param force: If True, existing South migration history in the fixture will be ignored.
            If False, fixtures with South migration history will result in a warning and
            remain unchanged.
    """
    fms = get_latest_fixture_migrations(fixture_path)
    # If there is migration history in the fixture, and we're not forcing an overwrite.
    if len(fms) > 0 and not force:
        msg = 'Found South migration history in fixture:\n{}\n'\
              "If you want to overwrite the fixture's South migration history please use --force."\
              .format(fixture_path)
        logger.warning(msg)
        ret = False
    else:
        django.db.close_connection()
        reset_db(database=database, debug=debug)
        sync_all(database=database, debug=debug)
        load_fixture(fixture_path, database=database)

        # If there is migration history history & we're forcing an overwrite
        if len(fms) > 0 and force:
            # Scrap the migration history from the fixture
            MigrationHistory.objects.all().delete()
    
        migrate_and_dump(fixture_path, database=database, fake=True)
        ret = True

    return ret


if __name__ == '__main__':
    filename = os.path.basename(__file__)
    parser = argparse.ArgumentParser(description='Add current South migration history to fixture(s).')
    parser.add_argument('-s', '--scan_path', nargs=1,
        help='path to scan for fixtures when initializing multiple fixtures')
    parser.add_argument('-p', '--fixture_path', nargs=1, help='path to fixture to initialize')
    parser.add_argument('-d', '--debug', type=bool, default=False, help='Turn on debugging features')
    parser.add_argument('-f', '--force', action='store_true',
        help='Ignore South migraton history in fixture(s).')
    args = parser.parse_args()
    
    if args.scan_path and args.fixture_path:
        parser.print_usage()
        print('Please use only one of -s / -f')
    elif args.fixture_path:
        fixture_path = args.fixture_path[0]
        force = args.force
        initialize_fixture(fixture_path, force=force, debug=args.debug)
    elif args.scan_path:
        scan_path = args.scan_path[0]
        force = args.force
        skip_fixtures = []
        exclude_dirs = ['build', 'sandbox']
        success, fail, skip = initialize_all_fixtures(scan_path, force=force, debug=args.debug,
                                                      exclude_dirs=exclude_dirs,
                                                      skip_fixtures=skip_fixtures)
        print('Successful: \n{}\n'\
              'Skipped: \n{}\n'\
              'Failed: \n{}'.format('\n'.join(success), '\n'.join(skip), '\n'.join(fail))
        )
