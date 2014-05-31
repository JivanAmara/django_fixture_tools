"""
@author: Jivan
@since: 2014-05-28
@brief: An example script to generate a new fixture.
"""
logfilename = 'make_fixture.log'
import logging
logging.basicConfig(filename=logfilename,level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.info('Starting to make fixture')

from dowant.restaurant.models import Restaurant
from django_fixture_tools.fixture_maker.db_sampler_script import db_sample

objects = []

r = Restaurant.objects.get(id=233)
das = r.deliveryarea_set.all()

objects.append(r)
objects.extend(das)

db_sample(objects, show_progress=True, skip_south_history=False)
logger.info('Finished making fixture')
