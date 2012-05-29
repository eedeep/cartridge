import csv
import os
import shutil
import datetime
from optparse import make_option

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils.translation import ugettext as _
from django.db.utils import IntegrityError
from mezzanine.conf import settings

from cartridge.shop.models import Product
from cartridge.shop.models import ProductOption
from cartridge.shop.models import ProductImage
from cartridge.shop.models import ProductVariation
from cartridge.shop.models import Category
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
# python < 2.7 doesn't have dictionary comprehensions ;(
TYPE_CHOICES = {choice:id for id, choice in settings.SHOP_OPTION_TYPE_CHOICES}

fieldnames = [TITLE, CONTENT, DESCRIPTION, CATEGORY, SUB_CATEGORY,
    SKU, IMAGE, NUM_IN_STOCK, UNIT_PRICE,
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
        if options['import']:
            import_products(csv_file)

def style_and_size_from_sku(sku):
    style = sku.split('-')[0]
    size = sku.split('-')[1]
    po, created = ProductOption.objects.get_or_create(type=1, name=style)
    if created:
        po.save()
        print("Created style: %s" % style)
    po, created = ProductOption.objects.get_or_create(type=2, name=size)
    if created:
        po.save()
        print("Created size: %s" % size)


def style_from_actual_item_code(code):
    sCode = code.split('-')[1]
    if len(sCode) > 2:
        print("WARNING! style code \"%s\" is longer than 2 characters" % sCode)
    po, created = ProductOption.objects.get_or_create(type=settings.SHOP_OPTION_TYPE_CHOICES[1][0], name=sCode)
    if created:
        po.save()
        print("Created style: %s" % sCode)

def _variation_from_row(row):
    """
    Get the variation from the csv row.
    """
    productvar, created = ProductVariation.objects.get_or_create(title=row[TITLE])
    return productvar


def _make_image(image_str, product):
    if image_str in EMPTY_IMAGE_ENTRIES:
        return None
    # try adding various image suffixes, if none given in original filename.
    root, suffix = os.path.splitext(image_str)
    if suffix not in IMAGE_SUFFIXES:
        raise CommandError("INCORRECT SUFFIX: %s" % image_str)
    image_path = os.path.join(LOCAL_IMAGE_DIR, image_str)
    if not os.path.exists(image_path):
        raise CommandError("NO FILE %s" % image_path)
    shutil.copy(image_path, PRODUCT_IMAGE_DIR)
    #shutil.copy(image_path, os.path.join(PRODUCT_IMAGE_DIR, "orig"))
    image, created = ProductImage.objects.get_or_create(
        file="%s" % (os.path.join(SITE_MEDIA_IMAGE_DIR, image_str)),
        description=image_str,  # TODO: handle column for this.
        product=product)
    return image


def _make_date(date_str, time_str):
    date_string = '%s %s' % (date_str, time_str)
    date = datetime.datetime.strptime(date_string, DATETIME_FORMAT)
    return date


def import_products(csv_file):
    print _("Importing ..")
    # More appropriate for testing.
    #Product.objects.all().delete()
    reader = csv.DictReader(open(csv_file), delimiter=',')
    for row in reader:
        print row
        product = _product_from_row(row)
        try:
            variation = ProductVariation.objects.create(
                # strip whitespace
                sku=row[SKU].replace(" ", ""),
                product=product,
            )
        except IntegrityError:
            raise CommandError("Product with SKU exists! sku: %s" % row[SKU])
        if row[NUM_IN_STOCK]:
            variation.num_in_stock = row[NUM_IN_STOCK]
        if row[UNIT_PRICE]:
            variation.unit_price = row[UNIT_PRICE]
        if row[SALE_PRICE]:
            variation.sale_price = row[SALE_PRICE]
        if row[SALE_START_DATE] and row[SALE_START_TIME]:
            variation.sale_from = _make_date(row[SALE_START_DATE],
                                                row[SALE_START_TIME])
        if row[SALE_END_DATE] and row[SALE_END_TIME]:
            variation.sale_to = _make_date(row[SALE_END_DATE],
                                                row[SALE_END_TIME])
        for option in TYPE_CHOICES:
            if row[option]:
                name = "option%s" % TYPE_CHOICES[option]
                setattr(variation, name, row[option])
                new_option, created = ProductOption.objects.get_or_create(
                    type=TYPE_CHOICES[option],  # TODO: set dynamically
                    name=row[option])
        variation.save()
        image = _make_image(row[IMAGE], product)
        if image:
            variation.image = image
        product.variations.manage_empty()
        product.copy_default_variation()
        product.save()

    print "Variations: %s" % ProductVariation.objects.all().count()
    print "Products: %s" % Product.objects.all().count()


def export_products(csv_file):
    print _("Exporting ..")
    filehandle = open(csv_file, 'w')
    writer = csv.DictWriter(filehandle, delimiter=',', fieldnames=fieldnames)
    headers = dict()
    for field in fieldnames:
        headers[field] = field
    writer.writerow(headers)
    for pv in ProductVariation.objects.all():
        row = dict()
        row[TITLE] = pv.product.title
        row[CONTENT] = pv.product.content
        row[DESCRIPTION] = pv.product.description
        row[SKU] = pv.sku
        row[IMAGE] = pv.image
        # TODO: handle multiple categories, and multiple levels of categories
        cat = pv.product.categories.all()[0]
        if cat.parent:
            row[SUB_CATEGORY] = cat.title
            row[CATEGORY] = cat.parent.title
        else:
            row[CATEGORY] = cat.title
            row[SUB_CATEGORY] = ""

        for option in TYPE_CHOICES:
            row[option] = getattr(pv, "option%s" % TYPE_CHOICES[option])

        row[NUM_IN_STOCK] = pv.num_in_stock
        row[UNIT_PRICE] = pv.unit_price
        row[SALE_PRICE] = pv.sale_price
        try:
            row[SALE_START_DATE] = pv.sale_from.strftime(DATE_FORMAT)
            row[SALE_START_TIME] = pv.sale_from.strftime(TIME_FORMAT)
        except AttributeError:
            pass
        try:
            row[SALE_END_DATE] = pv.sale_to.strftime(DATE_FORMAT)
            row[SALE_END_TIME] = pv.sale_to.strftime(TIME_FORMAT)
        except AttributeError:
            pass
        writer.writerow(row)
    filehandle.close()
