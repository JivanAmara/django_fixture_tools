""" @brief 
    @since Nov 17, 2011
    @author jivan
        dependencies.

    @note The word 'children' in this file refers to related objects that aren't
        required for database consistency, these are objects that have a foreign
        key to the parent, or have a many-to-many relationship with an object
        defined on the object's side of the relationship (making it the parent).
    Assumptions:
        Primary key for models is obj.id.
        Foreign keys don't form loops (an object reference an object which eventually
            references the first).
        Foreign keys that aren't NULL are assumed to be required for database
            consistency.
"""
from __future__ import print_function, unicode_literals, division

import logging
import re
import sys
import traceback

from django.contrib.contenttypes.generic import GenericRelation
from django.contrib.contenttypes.models import ContentType
import django.db
from django.db.models.fields.related import ManyToOneRel, OneToOneRel, \
    ManyToManyRel

from django_fixture_tools.shared import reset_db, dumpdata, fake_migrations, sync_all


logfilename = 'make_fixture.log'
logging.basicConfig(filename=logfilename,level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.info('Loaded db_sampler_script')

FIXTURE_DB = 'fixture_tools_db'

def identify_dependencies(django_model_instance, show_progress=False):
    """ @brief Lists the django model instances that \a django_model_instance
            depends on either directly or indirectly.

        @return A list of django model instances in reverse order of dependency.
            Ex: -> indicates dependency
                obj1 -> obj2
                obj1 -> obj3
                obj2 -> obj4
            In the returned list, obj4 will appear before obj1 or obj2,
                obj3 will appear before obj1, obj2 will appear before obj1.
            This allows you to safely loop through the objects in the list
                and save them in the order they appear.
    """
    required_relations = [ManyToOneRel, OneToOneRel]

    deps = list()

    for field in django_model_instance._meta._field_name_cache:
        if field.rel is not None and type(field.rel) in required_relations:
            # Its a dependency
            obj = getattr(django_model_instance, field.name)
            
            # Ignore it if it's empty.
            if obj is None:
                continue
            
            # Ignore it if it's a self-reference.
            if obj == django_model_instance:
                continue
            
            # Make sure its dependencies appear in the list before it does.
            deps.extend(identify_dependencies(obj))
            deps.append(obj)

    return deps

def identify_simple_children(django_model_instance, depth=1):
    """ @brief Returns a list of django model instances that have a
            ManyToOne or OneToOne relationship with \a django_model_instance.
        @param depth identifies how far from the original object to include
            children.  A depth of 1 means only include direct children of
            \a django_model_instance.
        Recursively includes children of the children
            of \a django_model_instance, to a depth of \a depth.  Also includes
            any dependencies the children have.
    """
    # End recursion
    if depth==0:
        children = []
    else:
        dmobj = django_model_instance
        children = list()
        # Get the attributes of this model instance with their names
        related_objs = dmobj._meta.get_all_related_objects()
        
        for related_object in related_objs:
            try:
                related_name = related_object.get_accessor_name()
                relationship = related_object.field.rel
                related = getattr(dmobj, related_name)
            except related_object.model.DoesNotExist:
                # If a model has a foreign key to the model of dmobj, but there isn't
                #    an instance of that model keying to dmobj, we can safely
                #    continue.
                continue
            
            # We're not interested in ManyToMany relationships here.
            relationship_type = type(relationship)
            if relationship_type == ManyToManyRel:
                continue
            elif relationship_type == OneToOneRel:
                new_children =  [related]
            elif relationship_type == ManyToOneRel:
                new_children = related.all()
            else:
                raise Exception('Unexpected relationship type: {} (not {}, {}, {}'\
                                    .format(relationship_type, ManyToManyRel, OneToOneRel, ManyToOneRel))
            
            for child in new_children:
                children.append(child)
                children.extend(identify_simple_children(child, depth=depth-1))

    return children

def identify_basic_m2m_children(django_model_instance):
    """ @brief Returns the basic many-to-many children of this model.
        Basic m2m children are those related to this object through a
        Django-managed m2m relationship (the default for a m2m field).
        @return A list of 2-tuples where the first item is the field name
            for a m2m relationship on \a django_model_instance, and the second
            item is a list of objects related through that field name.
            Ex: [ ('games', [<Game object>, <Game object>]), 
                  ('people', [<Person object>, <Person object>])
                ]
    """
    dmobj = django_model_instance
    related_m2m_objects = dict()

    for m2m_field in dmobj._meta.many_to_many:
        rel_type = type(m2m_field)
        if rel_type != GenericRelation and not m2m_field.rel.through._meta.auto_created:
            continue

        field_name = m2m_field.name
        field_children = list(getattr(dmobj, m2m_field.name).all())
        related_m2m_objects[field_name] = field_children

    return related_m2m_objects

def identify_through_m2m_children(django_model_instance):
    """ @brief Returns the many-to-many children of this model mapped with
            a custom through table and the objects from the 'through' model.
        @return Two lists, the first is the objects that \a django_model_instance
            has a m2m relationship with, the second is the objects from the
            'through' model connecting the objects in the first list to
            \a django_model_instance.
    """
    dmobj = django_model_instance
    related_m2m_objects = list()
    through_m2m_objects = list()

    for m2m_field in dmobj._meta.many_to_many:
        rel_type = type(m2m_field)
        if rel_type == GenericRelation or m2m_field.rel.through._meta.auto_created:
            continue

        related_objects = getattr(dmobj, m2m_field.name).all()
        related_m2m_objects.extend(related_objects)

        through_objects = m2m_field.rel.through.objects.all()
        through_m2m_objects.extend(through_objects)

    return (related_m2m_objects, through_m2m_objects)

def sample_object(obj, child_depth=1, dest_db_alias=FIXTURE_DB, show_progress=False):
    """
            # A lot of objects are getting saved multiple times.

    """
    if not hasattr(sample_object, 'already_saved'):
        sample_object.already_saved = set()

    # Object's dependencies, the objects that this object has a foreign key to
    #    either directly or indirectly.
    dependencies = identify_dependencies(obj, show_progress=show_progress)

#     # If we're getting the objects related children as well,
#     if child_depth > 0:
#         # Simple children are models with a foreign key to obj.
#         simple_children = identify_simple_children(obj)
#
#         # Basic m2m children are models linked with a m2m relationship that
#         #    uses a Django-managed linking table, rather than a custom table
#         #    using the 'through' parameter.
#         # For this type of m2m relationship, we need to save the children, then
#         #    add them to the relationship.
#         basic_m2m_children = identify_basic_m2m_children(obj)
#
#         # Through m2m children are models linked with a custom 'through' model.
#         #    For this type of m2m relationship, we need to manually save both the
#         #    children and
#         #    the link objects.
#         through_m2m_children, through_m2m_links = \
#             identify_through_m2m_children(obj)

    # Save dependencies.
    for dep in dependencies:
        try:
            if type(dep) == ContentType:
                pass
                try:
                    existing_ct = ContentType.objects.get(app_label=dep.app_label, model=dep.model)
                    if dep.id != existing_ct.id:
                        dep.id = existing_ct.id
                        msg = 'Existing ContentType with id {} matches ContentType with id {}\n'\
                              'application={}, model={}'\
                                  .format(existing_ct.id, dep.id, dep.app_label, dep.model)
                        logger.warn(msg)
                except ContentType.DoesNotExist:
                    pass

            # If this model instance isn't already saved to the fixture database,
            #    or it's saved to the fixture database, but has been retrieved 
            #    again from the original database through another object.
            if (dep.__class__.__name__, dep.pk) not in sample_object.already_saved \
                or dep._state.db != dest_db_alias:
                if show_progress:
                    sys.stdout.write('.')
                    sys.stdout.flush()
                dep.save(using=dest_db_alias)
                sample_object.already_saved.add((dep.__class__.__name__, dep.pk))
                msg = '{} (pk: {})'.format(dep.__class__.__name__, dep.pk)
                logger.info(msg)

        except BaseException as e:
            msg = 'An exception occurred while trying to save a dependency object.\n'\
                  "We'll attempt to continue without saving this object.\n"\
                  'The object we were attempting to save was a {}:\n{} (id:{})\n'\
                  "It's contents (dir(object)) are:\n{}\n"\
                  'The stack trace for the error is:\n{}'\
                       .format(dep.__class__.__name__, dep, dep.id, dir(object), traceback.format_exc())
            logger.error(msg)
            pass

    if (obj.__class__.__name__, obj.pk) not in sample_object.already_saved \
        or obj._state.db != dest_db_alias:
        if show_progress:
            sys.stdout.write('.')
            sys.stdout.flush()
        obj.save(using=dest_db_alias)
        sample_object.already_saved.add((obj.__class__.__name__, obj.pk))
        msg = '{} (pk: {})'.format(obj.__class__.__name__, obj.pk)
        logger.info(msg)

#     if child_depth > 0:
#         # Save object's simple children.
#         for sc in simple_children:
#             sample_object(sc, child_depth=child_depth-1, dest_db_alias=dest_db_alias)
#
#         # Save object's basic m2m children.
#         for field_name, children in basic_m2m_children.items():
#             for child in children:
#                 sample_object(child, child_depth=child_depth-1, dest_db_alias=dest_db_alias)
#
#             # Connect the children to the object
#             for child in children:
#                 try:
#                     getattr(obj, field_name).add(child)
#                 except ValueError as e:
#                     print("Can't add {}:{} to {}:{}.{} -- parent is on {}, child is on {}"\
#                             .format(child.__class__.__name__, child.id,
#                                     obj.__class__.__name__, obj.id, field_name,
#                                     obj._state.db, child._state.db))
#                 except UnicodeDecodeError as e:
#                     logger.error('Problem with {}.{}'.format(obj.__class__.__name__, field_name))
#                     raise
#
#         # Save object's custom-through m2m children.
#         for tm2mc in through_m2m_children:
#             sample_object(tm2mc, child_depth=child_depth-1, dest_db_alias=dest_db_alias)
#
#         # Save object's custom-through model entries.  (Children can be ignored
#         #    since the table is only performing a mapping function and isn't
#         #    accessed as a child of this object.)
#         for tm2ml in through_m2m_links:
#             sample_object(tm2ml, child_depth=0, dest_db_alias=dest_db_alias)

def db_sample(db_obj_iterable, skip_south_history=False, child_depth=1, dest_db_alias=FIXTURE_DB, show_progress=False,
              outfile=None):
    at_least_one_object = False
    if show_progress: print("Sampling {} objects from 'origin' to '{}".format(len(db_obj_iterable), dest_db_alias))

    # Make an empty database with schema reflecting current code.
    if show_progress: print("Emptying destination database '{}'".format(dest_db_alias))
    django.db.close_connection()
    reset_db(database=dest_db_alias)
    sync_all(database=dest_db_alias)
    if not skip_south_history:
        fake_migrations(database=dest_db_alias)
    if show_progress: print("Destination database emptied, sampling objects.")

    # Copy requested objects from default db to fixture db.
    if show_progress:
        output_description = """
            O = top-level object being sampled.
            . = top-level object dependency being saved.
        """
        print(output_description)

    for obj in db_obj_iterable:
        at_least_one_object = True
        if show_progress:
            sys.stdout.write('O')
            sys.stdout.flush()
        sample_object(obj, child_depth=child_depth, dest_db_alias=dest_db_alias, show_progress=show_progress)
    if not at_least_one_object:
        logger.warn('No objects were requested for sampling.  Did you want an empty fixture?')
    if show_progress: print()

    # Dump sampled data
    dumpdata(dest_db_alias, outfile)
