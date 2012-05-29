"""
Import product from a csv file
** This relates to importing product from the previous version of the site.

*ONLY* product and variations are ported and mapped to there new fields
Creation of options for both sizes and styles are done via this script
"""

import csv
import os
import datetime
from optparse import make_option

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils.translation import ugettext as _
from mezzanine.conf import settings

from cartridge.shop.models import Product
from cartridge.shop.models import ProductVariation
from cartridge.shop.models import ProductOption
from mezzanine.core.models import CONTENT_STATUS_PUBLISHED


# images get copied from thie directory
LOCAL_IMAGE_DIR = "/tmp/orig"
# images get copied to this directory under STATIC_ROOT
IMAGE_SUFFIXES = [".jpg", ".JPG", ".jpeg", ".JPEG", ".tif", ".gif", ".GIF"]
EMPTY_IMAGE_ENTRIES = ["Please add", "N/A", ""]
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"

# Here we define what column headings are used in the csv.
TITLE = _("title")
CONTENT = _("content")
DESCRIPTION = _("description")
SKU = _("sku")
SLUG = _("slug")
MASTER_ITEM_CODE = _("master_item_code")
ACTUAL_ITEM_CODE = _("actual_item_code")
IMAGE = _("image")
CATEGORY = _("category")
SUB_CATEGORY = _("sub-category")
SIZE = _("size")
NUM_IN_STOCK = _("number in stock")
UNIT_PRICE = _("unit price")
SALE_PRICE = _("sale price")
SALE_START_DATE = _("sale start date")
SALE_START_TIME = _("sale start time")
SALE_END_DATE = _("sale end date")
SALE_END_TIME = _("sale end time")

DATETIME_FORMAT = "%s %s" % (DATE_FORMAT, TIME_FORMAT)
SITE_MEDIA_IMAGE_DIR = _("product")
PRODUCT_IMAGE_DIR = os.path.join(settings.STATIC_ROOT, SITE_MEDIA_IMAGE_DIR)
TYPE_CHOICES = {choice:id for id, choice in settings.SHOP_OPTION_TYPE_CHOICES}

fieldnames = [TITLE, CONTENT, DESCRIPTION, CATEGORY, SUB_CATEGORY,
    SKU, IMAGE, NUM_IN_STOCK, UNIT_PRICE,
    SLUG, MASTER_ITEM_CODE,
    SALE_PRICE, SALE_START_DATE, SALE_START_TIME, SALE_END_DATE, SALE_END_TIME]
# TODO: Make sure no options conflict with other fieldnames.
fieldnames += TYPE_CHOICES.keys()

class Command(BaseCommand):
    args = '--import <csv_file>'
    help = _('Import products from a csv file.')

    option_list = BaseCommand.option_list + (
        make_option('--import',
            action='store_true',
            dest='import',
            default=False,
            help=_('Import products from csv file.')),
    )

    def handle(self, *args, **options):
        try:
            csv_file = args[0]
        except IndexError:
            raise CommandError(_("Please provide csv file to import"))
        if not options["import"]:
            raise CommandError(_("need to import"))
        if options['import']:
            import_products(csv_file)

def style_from_actual_item_code(code):
    sCode = code.split('-')[1]
    if len(sCode) > 2:
        print("WARNING! style code is longer than 2 characters")
    po, created = ProductOption.objects.get_or_create(type=settings.TYPE_CHOICES[1], name=sCode)
    if created:
        po.save()
        print("Created style: %s" % sCode)

def _product_from_row(row):
    product, created = Product.objects.get_or_create(master_item_code=row[MASTER_ITEM_CODE])
    product.title=row[TITLE]
    product.content = row[CONTENT]
    product.description = row[DESCRIPTION]

    # TODO: set the 2 below from spreadsheet.
    product.status = CONTENT_STATUS_PUBLISHED
    product.available = True
    return product

def _make_date(date_str, time_str):
    date_string = '%s %s' % (date_str, time_str)
    date = datetime.datetime.strptime(date_string, DATETIME_FORMAT)
    return date

def import_products(csv_file):
    print _("Importing ..")
    # More appropriate for testing.
    reader = csv.DictReader(open(csv_file), delimiter=',')
    for row in reader:
        #print row
        product = _product_from_row(row)
        product.variations.manage_empty()
        product.copy_default_variation()
        product.save()

    print "Variations: %s" % ProductVariation.objects.all().count()
    print "Products: %s" % Product.objects.all().count()
