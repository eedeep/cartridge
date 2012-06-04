import csv
import os
import datetime
from optparse import make_option

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils.translation import ugettext as _
from django.core.exceptions import ObjectDoesNotExist
from mezzanine.conf import settings

from cartridge.shop.models import Product
from cartridge.shop.models import ProductOption
from cartridge.shop.models import ProductVariation

import logging

logging.basicConfig(filename='import_variations.log', level=logging.INFO)

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
# python < 2.7 doesn't have dictionary comprehensions ;(
TYPE_CHOICES = {choice:id for id, choice in settings.SHOP_OPTION_TYPE_CHOICES}

fieldnames = [TITLE, CONTENT, DESCRIPTION, CATEGORY, SUB_CATEGORY,
    SKU, IMAGE, NUM_IN_STOCK, UNIT_PRICE,
    SALE_PRICE, SALE_START_DATE, SALE_START_TIME, SALE_END_DATE, SALE_END_TIME]
# TODO: Make sure no options conflict with other fieldnames.
fieldnames += TYPE_CHOICES.keys()

class InvalidProductCode(Exception):
    def __init__(self):
        self.error = ""
    def __str__(self):
        return repr("Invalid Item Code %s" % self.error)

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
        if options['import']:
            import_product_variations(csv_file)

def style_and_size_from_sku(sku):
    """
    Break the SKU into seperate fields for reassembly
    """
    sku_list = sku.split('-')
    if len(sku_list) < 2:
        logging.error("Invalid Code for sku: %s" % sku)
        raise InvalidProductCode

    master_item_code = sku_list[0]

    style = sku_list[1]
    size = sku_list[2]
    if style or size is None or "":
        raise InvalidProductCode

    style, created = ProductOption.objects.get_or_create(type=2, name=style)
    if created:
        style.save()
        logging.info("Created style: %s" % style)
    size, created = ProductOption.objects.get_or_create(type=1, name=size)
    if created:
        size.save()
        logging.info("Created size: %s" % size)
    return master_item_code, style, size

def _variation_from_row(row):
    """
    Get the variation from the csv row
    """
    try:
        master_item_code, style, size = style_and_size_from_sku(row[SKU])
    except InvalidProductCode:
        return None, False 
    try:
        product = Product.objects.get(master_item_code=master_item_code)
    except ObjectDoesNotExist:
        logging.error("Product: %s does not exist" % master_item_code)
    try:
        productvar, created = ProductVariation.objects.get_or_create(product=product,
                                                        option1=style,
                                                        option2=size)
    except:
        logging.error("Variation for Master_item_code: %s has failed to create" % master_item_code)

    return productvar, created

def _make_date(date_str, time_str):
    date_string = '%s %s' % (date_str, time_str)
    date = datetime.datetime.strptime(date_string, DATETIME_FORMAT)
    return date

def import_product_variations(csv_file):
    print _("Importing ..")
    # More appropriate for testing.
    reader = csv.DictReader(open(csv_file), delimiter=',')
    for row in reader:
        variation, created = _variation_from_row(row)
        if created:
            variation.save()
            logging.info("Created Variation: %s" % variation)

    print "Variations: %s" % ProductVariation.objects.all().count()
    print "Products: %s" % Product.objects.all().count()


