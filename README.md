django_fixture_tools
====================

Tools to create and maintain Django fixtures.

fixture_maker allows you to easily sample an existing database to make a new fixtures with the model instances you need.

fixture_migrator allows you to keep those fixtures up-to-date automatically with South migrations.

There are a couple of things that are a little clunky at this point, but should still lift a significant amount of the burden to maintain your fixtures:

 1. Add this package in the same directory as your Django project, so ../django_fixture_tools references it.

 2. To use any of the tools you will first need to create a new database 'fixture_tools_db'.  This database will be repeatedly emptied and used to construct/alter fixtures.  If you're using database extensions such as PostGIS, you'll need to make sure this database has the same extensions.  If you're using SQLite, you can skip this.

 3. Update the settings_migrator.py & settings_maker.py files with the engine/name/password/host/user of the new database.

Fixture Maker
=============
Before starting, make sure that all your South migrations have been applied to your database.
In a python script, collect the objects you want into a list and pass that list to db_sample().  Foreign key dependencies will automatically be included.
@see make_fixture_example.py for an example.

The script should be run:
 in the virtual environment of the project you want to create the fixture for.
 with DJANGO_SETTINGS_MODULE=django_fixture_tools.settings_maker
Either redirect output to your new fixture file or specify param 'outfile' in the call to db_sample().

Assumptions you probably don't need to worry about:
    Primary key for models is obj.id, if you've changed this for some models,
    	please let me know how it blows up.
    Foreign keys don't form loops (an object reference an object which eventually
        references the first).  This is very rarely needed, and many databases
        make it difficult, so if you're not sure don't worry about it.
        If you have some loops like these and genuinely need them, let me know &
        I'll update the code to deal with it.
    Foreign keys that aren't NULL are assumed to be required for database
        consistency.  At worst this will add some model instances to the resulting
        fixture that aren't really needed.  Unless someone contacts me about
        this causing a real problem, it will likely stay like this.


Fixture Migrator
================
initialize_fixtures:
Before starting, make sure that all your South migrations have been applied to your database.
For fixtures with no South migration history, you will need to run fixture_migrator/initialize_fixtures.py to add current South migration history to the fixtures.
For fixtures with South migration history included, you can either attempt to migrate them or use initialize_fixtures -f to overwrite their South migration history with current migration history.

migrate_fixtures (only for git users):
The script should be run:
 in the virtual environment of the project you want to create the fixture for.
 with DJANGO_SETTINGS_MODULE=django_fixture_tools.settings_migrator

Before starting, make sure that all your South migrations have been applied to your database.
Before starting, make sure that all code changes are committed.
The algorithm is: Check out commit when fixture was last modified.  Load the fixture.  Check out commit you started from.  Migrate.  Dump.

Run either with -h for details of use.
