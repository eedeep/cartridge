import os
import random
import md5
import re
from datetime import datetime
from decimal import Decimal, ROUND_UP
import itertools
from operator import iand, ior
from exceptions import NotImplementedError

from django.db import models
from django.db.models import CharField, Q
from django.db.models.base import ModelBase
from django.utils.translation import ugettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.core.files.base import ContentFile
from _mysql_exceptions import OperationalError
from django.utils import simplejson
from django.db.utils import DatabaseError

from mezzanine.conf import settings
from mezzanine.core.managers import DisplayableManager, PublishedManager
from mezzanine.core.models import Displayable, RichText, Orderable
from mezzanine.core.models import CONTENT_STATUS_PUBLISHED, CONTENT_STATUS_DRAFT, CONTENT_STATUS_CHOICES
from mezzanine.generic.fields import RatingField
from mezzanine.pages.models import Page

from cartridge.taggit.managers import TaggableManager
from cartridge.taggit.models import Tag, TagFacet

from cartridge.shop import fields, managers
from cartridge.shop.regexinv import invert

from multicurrency.utils import session_currency

try: #if south is installed, add a rule to ignore the fake manager field
    from south.modelsinspector import add_ignored_fields
    add_ignored_fields(["^cartridge\.taggit\.managers"])
except ImportError:
    pass

import logging
splog = logging.getLogger('stockpool.log')
elog = logging.getLogger('cottonon.errors')


#session values
SESSION_SHIPPINGTYPE = "shipping_type"
SESSION_SHIPPINGTOTAL = "shipping_total"
SESSION_DISCOUNTCODE = "discount_code"
SESSION_DISCOUNTTOTAL = "discount_total"

def iter_bundle(bundle_items, bundle_size):
    """Take a list and pick of item in bundles
    of bundle_sizes. The last bundle will
    contains the remainders.
    """
    for idx, bundled in itertools.groupby(
            enumerate(bundle_items),
            lambda x: x[0] // bundle_size):
        yield zip(*bundled)[1]



class Category(Page, RichText):
    """
    A category of products on the website.
    """

    hide_sizes = models.BooleanField(_("Hide size filter"),
            help_text=_("If ticked the size filter will be hidden when the category is displayed."),
            default=False)

    class Meta:
        verbose_name = _("Product category")
        verbose_name_plural = _("Product categories")

    def filters(self):
        """
        Returns product filters as a Q object for the category.
        """
        return Q(id__in=self.products.only("id"))


class Priced(models.Model):
    """
    Abstract model with unit and sale price fields. Inherited by
    ``Product`` and ``ProductVariation`` models.
    """

    unit_price = fields.MoneyField(_("Unit price"))
    sale_id = models.IntegerField(null=True)
    sale_price = fields.MoneyField(_("Sale price"))
    sale_from = models.DateTimeField(_("Sale start"), blank=True, null=True)
    sale_to = models.DateTimeField(_("Sale end"), blank=True, null=True)
    bundle_discount_id = models.IntegerField(null=True)

    class Meta:
        abstract = True

    def on_sale(self):
        """
        Returns True if the sale price is applicable.
        """
        now = datetime.now()
        valid_from = self.sale_from is None or self.sale_from < now
        valid_to = self.sale_to is None or self.sale_to > now
        return self.sale_price is not None and valid_from and valid_to

    def has_price(self):
        """
        Returns True if there is a valid price.
        """
        return self.on_sale() or self.unit_price is not None

    def price(self):
        """
        Returns the actual price - sale price if applicable otherwise
        the unit price.
        """
        if self.on_sale():
            return self.sale_price
        elif self.has_price():
            return self.unit_price
        return Decimal("0")


class Product(Displayable, Priced, RichText):
    """
    Container model for a product that stores information common to
    all of its variations such as the product's title and description.
    """

    available = models.BooleanField(_("Available for purchase"),
                                    default=False)
    master_item_code = CharField(_("Master Item Code"), blank=False, max_length=64, unique=True)
    image = CharField(max_length=100, blank=True, null=True)
    categories = models.ManyToManyField("Category", blank=True,
                                        related_name="products")
    date_added = models.DateTimeField(_("Date added"), auto_now_add=True,
                                      null=True)
    first_published_date = models.DateTimeField(_("First Published"), blank=True, null=True)
    sync_images = models.BooleanField(_("Schedule Image Sync"), default=False)
    date_images_last_synced = models.DateTimeField(
        _("Images Last Synced"), null=True,
        help_text="When images for this product was last synced from RMS."
    )
    sync_stock = models.BooleanField(_("Schedule Stock Sync"), default=False)
    date_stock_last_synced = models.DateTimeField(
        _("Stock Last Synced"), null=True,
        help_text="When stock for this product was last synced from RMS."
    )
    related_products = models.ManyToManyField("self", blank=True, symmetrical=False)
    upsell_products = models.ManyToManyField("self", blank=True)
    rating = RatingField(verbose_name=_("Rating"))
    featured = models.BooleanField(_("Featured Product"), default=False)
    in_stock = models.BooleanField(_("In Stock"), default=False)
    ranking = models.IntegerField(default=250)

    product_colours = CharField(_("Available colours"), blank=True, default="", max_length=500)
    product_sizes = CharField(_("Available colours"), blank=True, default="", max_length=255)

    tags = TaggableManager(blank=True)
    objects = DisplayableManager()
    search_fields = ("master_item_code",)

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ("ranking", "title")

    def categories_str(self):
        return ', '.join(self.categories.all().values_list('title', flat=True))
    def tags_str(self):
        return ', '.join(self.tags.all().values_list('name', flat=True))

    def __unicode__(self):
        return '%s :: %s' % (self.title, self.master_item_code)

    @models.permalink
    def get_absolute_url(self):
        return ("shop_product", (), {"slug": self.slug})

    @property
    def available_sizes(self): #TODO: potentially denormalise this onto the model
        return self.product_sizes.split(",")

    @property
    def available_colours(self):
        return self.product_colours.split(",")

    @property
    def available_brands(self): #TODO: potentially denormalise
        results = self.tags.filter(tagfacet__name=settings.FACET_BRAND).values_list("name", flat=True)
        return results

    @property
    def available_styles(self): #TODO: potentially denormalise
        results = self.tags.filter(tagfacet__name=settings.FACET_STYLE).values_list("name", flat=True)
        return results

    #XXX replace these two methods with tastypie calls
    def colours_json(self):
        colours = self.variations.all().values_list("option%s"%settings.OPTION_STYLE, flat=True)
        json = []
        for c in colours: json.append({"colour":c})
        return simplejson.dumps(json)

    def sizes_json(self):
        cs = self.variations.all().values_list("option%s"%settings.OPTION_SIZE, flat=True)
        json = []
        for c in cs: json.append({"size":c})
        return simplejson.dumps(json)

    def brands_json(self):
        json = self.tags.filter(tagfacet__name="brand").values_list("id", "display_name", flat=True)
        #json = []
        return simplejson.dumps(json)

    def styles_json(self):
        #json = ""
        json = self.tags.filter(tagfacet__name="style").values_list("id", "display_name", flat=True)
        return simplejson.dumps(json)

    @property
    def default_variation(self):
        url = "http://admin-asia.aws.cottonon.com/admin/shop/product/{0}".format(self.id)
        try:
            v = self.variations.get(default=True)
        except ProductVariation.DoesNotExist: #fail gracefully by falling back to other variation
            elog.error('No default variation for {0} ({1})'.format(self.title, url))
            vs = self.variations.all()
            v = self if vs.count() == 0 else vs[0] #if no variations at all, return Product else first variation
        except MultipleObjectsReturned:
            elog.error('Multiple default variations for {0} ({1})'.format(self.title, url))
            v = self.variations.filter(default=True)[0]
        return v

    @property
    def size_chart(self):
        """
        Return a slug that suggests which size chart to use for this product.
        Use the product categories to select the html size guide subsection.
        """
        categories = self.categories.all().values_list('id', flat=True)
        for cat_id, name in ((822, None),
                             (872, 'mens-shoes'),
                             (857, 'kidsfootwear'),
                             (1747, 'kidsfootwear'),
                             (924, 'body'),
                             (923, 'rubi'),
                             (847, 'kids'),
                             (860, 'men'),
                             (899, 'women'),
                             (1757, 'girls'),
                             (1771, 'guys')):
            if cat_id in categories:
                sizes = [x.upper() for x in self.available_sizes]
                if (len(sizes)==1 and
                    any(x in ["SOLID", "OSFA"] for x in sizes)):
                    return None
                return name
        return None

    def copy_default_variation(self):
        """
        Copies the price and image fields from the default variation.
        """
        default = self.variations.get(default=True)
        for field in Priced._meta.fields:
            if not isinstance(field, models.AutoField):
                setattr(self, field.name, getattr(default, field.name))
        if default.image:
            self.image = default.image.file.name
        self.save()

    def save(self, *args, **kwargs):
        # Update in stock flag.
        # XXX: stockpool update needed
        self.in_stock = False
        for variation in self.variations.all():
            if variation.has_stock():
                self.in_stock = True
                break

        if not self.first_published_date and self.status == CONTENT_STATUS_PUBLISHED:
            self.first_published_date = datetime.now()

        #store available variation colours on the product
        style_field = "option%i" % settings.OPTION_STYLE
        self.product_colours = ",".join(set(self.variations.values_list(style_field,flat=True)))
        self.product_sizes = ",".join(set(self.variations.values_list("option%i"%settings.OPTION_SIZE, flat=True)))
        super(Product, self).save(*args, **kwargs)

    def admin_thumb(self):
        if self.image is None:
            return ""
        from mezzanine.core.templatetags.mezzanine_tags import thumbnail
        thumb_url = thumbnail(self.image, 24, 24)
        return "<img src='%s%s' />" % (settings.MEDIA_URL, thumb_url)
    admin_thumb.allow_tags = True
    admin_thumb.short_description = ""


def product_image_path(instance, filename):
    product_code = instance.product.master_item_code.split('-')[0]
    return "static/media/product/images/{0}/{1}".format(product_code, filename)


class CloudFrontImage(models.Model):

    class Meta:
        abstract = True

    def stored_image_path(self):
        raise NotImplementedError

    def get_absolute_path(self):
        """
        In order to make use of the boto storage backend to upload
        files to the CDN we need to set the path to the aboslute
        path, which includes the static/media prefix. This method
        deals with the fact that during the transition to
        using the boto storage backend to manage file uploads
        we may have some images with paths that are like:
            static/media/product/images/202718/v_040700cdca4a031ed80e975056f23f9c_202718-70-2.JPG
        and some that are still like:
            product/images/202718/202718-70-2.JPG
        So in templates, use this method and don't prefix with {{ MEDIA_URL }}
        """
        return os.path.join(
            settings.MEDIA_URL,
            self.stored_image_path().replace('static/media/', '', 1)
        )


class ProductImage(Orderable, CloudFrontImage):
    """
    An image for a product - a relationship is also defined with the
    product's variations so that each variation can potentially have
    it own image, while the relationship between the ``Product`` and
    ``ProductImage`` models ensures there is a single set of images
    for the product.
    """
    file = models.ImageField(_("Image"), upload_to=product_image_path)
    description = CharField(_("Description"), blank=True, max_length=100)
    product = models.ForeignKey(
        "Product",
        related_name="images"
    )

    class Meta:
        verbose_name = _("Image")
        verbose_name_plural = _("Images")
        order_with_respect_to = "product"

    def __unicode__(self):
        value = self.description
        if not value:
            value = self.file.name
        if not value:
            value = ""
        return value

    @staticmethod
    def extract_base_filename(filename):
        result = re.match("^v_(.*)_(.*)$", os.path.basename(filename))
        if result:
            return result.group(2)
        else:
            return os.path.basename(filename)

    @staticmethod
    def autoversioned_image_filename(filename, image_content):
        """
        Return a file name which includes an md5
        prefix based on the file content, for the purposes
        of autoversioning. ie, every time the image content
        changes, we get a unique file name. Useful for
        avoiding problems with far future expires headers
        and browser caching.
        """
        format_mask = "v_{0}_{1}"
        base_filename = ProductImage.extract_base_filename(filename)
        image_md5 = md5.new()
        image_md5.update(image_content)
        return format_mask.format(image_md5.hexdigest(), base_filename)

    def stored_image_path(self):
        return self.file.name

    def image_content(self):
        image_content = self.file.file.file.getvalue()
        self.file.file.seek(0)
        return image_content

    def save(self, *args, **kwargs):
        try:
            autoversioned_name = ProductImage.autoversioned_image_filename(
                self.file.file.name,
                self.image_content()
            )
            self.file.save(
                autoversioned_name,
                ContentFile(self.image_content()),
                save=False
            )
        except IOError:
            pass

        super(ProductImage, self).save(*args, **kwargs)


class ProductOption(models.Model):
    """
    A selectable option for a product such as size or colour.
    """
    type = models.IntegerField(_("Type"),
                               choices=settings.SHOP_OPTION_TYPE_CHOICES)
    display_name = fields.CharField(blank=True, max_length=100) #eg "red"
    name = fields.OptionField(_("Name")) #eg an RMS colour code like 04

    ranking = models.IntegerField(default=100)

    objects = managers.ProductOptionManager()

    def __unicode__(self):
        return "%s: %s" % (self.get_type_display(), self.name)

    @staticmethod
    def colourName(code):
        try:
            return ProductOption.objects.filter(name=code)[0].display_name
        except:
            return code

    class Meta:
        verbose_name = _("Product option")
        verbose_name_plural = _("Product options")


class ProductVariationMetaclass(ModelBase):
    """
    Metaclass for the ``ProductVariation`` model that dynamcally
    assigns an ``fields.OptionField`` for each option in the
    ``SHOP_PRODUCT_OPTIONS`` setting.
    """
    def __new__(cls, name, bases, attrs):
        if not ("Meta" in attrs and getattr(attrs["Meta"], "proxy", False)): #skip proxy models
            for option in settings.SHOP_OPTION_TYPE_CHOICES:
                attrs["option%s" % option[0]] = fields.OptionField(option[1])
        args = (cls, name, bases, attrs)
        return super(ProductVariationMetaclass, cls).__new__(*args)

class ProductVariationAbstract(models.Model):
    """
    Product Variation abstract used to extend the
    cartridge base functionality
    """

    # Stock pool, Reserved stock for online store
    num_in_stock_pool = models.PositiveIntegerField(_("Number in Stock Pool"),
                                            blank=True,
                                            default=0)
    class Meta:
        abstract = True


class ProductVariation(Priced, ProductVariationAbstract):
    """
    A combination of selected options from
    ``SHOP_OPTION_TYPE_CHOICES`` for a ``Product`` instance.
    """

    product = models.ForeignKey("Product", related_name="variations")
    sku = fields.SKUField(unique=True)
    num_in_stock = models.IntegerField(_("Number in stock"), blank=True,
                                       null=True)
    default = models.BooleanField(_("Default"))
    image = models.ForeignKey(
        "ProductImage",
        verbose_name=_("Image"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    objects = managers.ProductVariationManager()

    __metaclass__ = ProductVariationMetaclass

    class Meta:
        ordering = ("-default",)

    def __unicode__(self):
        """
        Display the option names and values for the variation.
        """
        options = []
        for field in self.option_fields():
            if getattr(self, field.name) is not None:
                if field.name == 'option1':
                    value = ProductOption.colourName(getattr(self, field.name))
                else:
                    value = getattr(self, field.name)
                options.append("%s: %s" % (unicode(field.verbose_name),
                                           value))
        return ("%s %s" % (unicode(self.product), ", ".join(options))).strip()

    def get_absolute_url(self):
        return self.product.get_absolute_url()

    @classmethod
    def option_fields(cls):
        """
        Returns each of the model fields that are dynamically created
        from ``SHOP_OPTION_TYPE_CHOICES`` in
        ``ProductVariationMetaclass``.
        """
        all_fields = cls._meta.fields
        return [f for f in all_fields if isinstance(f, fields.OptionField)]

    def options(self):
        """
        Returns the field values of each of the model fields that are
        dynamically created from ``SHOP_OPTION_TYPE_CHOICES`` in
        ``ProductVariationMetaclass``.
        """
        return [getattr(self, field.name) for field in self.option_fields()]

    def live_num_in_stock(self):
        """
        Returns the live number in stock, which is
        ``self.num_in_stock - num in carts``. Also caches the value
        for subsequent lookups.
        """
        if self.num_in_stock is None:
            return None
        if not hasattr(self, "_cached_num_in_stock"):
            num_in_stock = self.total_in_stock
            items = CartItem.objects.filter(sku=self.sku)
            aggregate = items.aggregate(quantity_sum=models.Sum("quantity"))
            num_in_carts = aggregate["quantity_sum"]
            if num_in_carts is not None:
                num_in_stock = num_in_stock - num_in_carts
            self._cached_num_in_stock = num_in_stock
        return self._cached_num_in_stock

    def has_stock(self, quantity=1):
        """
        Returns ``True`` if the given quantity is in stock, by checking
        against ``live_num_in_stock``. ``True`` is returned when
        ``num_in_stock`` is ``None`` which is how stock control is
        disabled.
        """
        if settings.SHOP_CART_STOCK_LEVEL:
            live = self.live_num_in_stock()
            return live is None or quantity == 0 or live >= quantity
        else:
            return self.total_in_stock >= quantity

    @property
    def total_in_stock(self):
        """
        Get the total stock levels.
        If stock_pool > STOCK_POOL_CUTOFF: stock - STOCK_THRHESHOLD
        If stock_pool <= 0: stock - STOCK_POOL_THRESHOLD
        """
        if not self.num_in_stock:
            stock = 0
        else:
            stock = self.num_in_stock

        stock_pool = self.num_in_stock_pool
        if stock_pool > settings.STOCK_POOL_CUTOFF:
            threshold = settings.STOCK_POOL_THRESHOLD
        else:
            threshold = settings.STOCK_THRESHOLD
        if stock - threshold > 0:
            return stock - threshold
        return 0

    def reduce_stock(self, amount):
        """
        Reduces stock levels
        """
        splog.info('Reducing stock for sku: %s, by amount: %s, from total stock: %s' % (self.sku, amount, self.total_in_stock))
        splog.info('SOH: %s, SP:%s' % (self.num_in_stock, self.num_in_stock_pool))
        if self.total_in_stock - amount >= 0:
            self.num_in_stock -= amount
            self.num_in_stock_pool -= amount
            if self.num_in_stock_pool < 0:
                self.num_in_stock_pool = 0
            self.save()
            splog.info('SOH: %s, SP: %s' % (self.num_in_stock, self.num_in_stock_pool))
            return True
        return False

    @property
    def alternate_sku(self):
        """
        SKU is derived from the ``product.master_item_code`` and selected options
        """
        return "%s-%s-%s" % (self.product.master_item_code, self.options()[0], self.options()[1])

    @property
    def master_item_code(self):
        """
        Convenience property for getting the parent ``Product`` master_item_code
        """
        return self.product.master_item_code

    @property
    def actual_item_code(self):
        """
        Convenience property for getting the legacy actual_item_code
        This code is simply made up of the master_item_code and the style option
        """
        return "%s-%s" % (self.master_item_code, self.options()[0])

    @property
    def colour_name(self):
        """Returns the colour string from ProductOptions
        for use in templates"""
        return ProductOption.colourName(self.option1)

class Order(models.Model):

    billing_detail_first_name = CharField(_("First name"), max_length=32)
    billing_detail_last_name = CharField(_("Last name"), max_length=32)
    billing_detail_street = CharField(_("Street 1"), max_length=32)
    billing_detail_street2 = CharField(_("Street 2"), max_length=32, default="", blank=True)
    billing_detail_country = CharField(_("Country"), max_length=32)
    billing_detail_postcode = CharField(_("Zip/Postcode"), max_length=10)
    billing_detail_city = CharField(_("City/Suburb"), max_length=32)
    billing_detail_state = CharField(_("State/Region"), max_length=12)
    billing_detail_phone = CharField(_("Phone"), max_length=16)
    billing_detail_email = models.EmailField(_("Email"))
    shipping_detail_first_name = CharField(_("First name"), max_length=32)
    shipping_detail_last_name = CharField(_("Last name"), max_length=32)
    shipping_detail_street = CharField(_("Street 1"), max_length=32)
    shipping_detail_street2 = CharField(_("Street 2"), max_length=32, default="", blank=True)
    shipping_detail_country = CharField(_("Country"), max_length=32)
    shipping_detail_postcode = CharField(_("Zip/Postcode"), max_length=12)
    shipping_detail_city = CharField(_("City/Suburb"), max_length=32)
    shipping_detail_state = CharField(_("State/Region"), max_length=12)
    shipping_detail_phone = CharField(_("Phone"), max_length=16)
    additional_instructions = models.TextField(_("Additional instructions"),
                                               blank=True)
    time = models.DateTimeField(_("Time"), auto_now_add=True, null=True)
    key = CharField(max_length=40)
    user_id = models.IntegerField(blank=True, null=True)
    shipping_type = CharField(_("Shipping type"), max_length=50, blank=True)
    shipping_total = fields.MoneyField(_("Shipping total"))
    item_total = fields.MoneyField(_("Item total"))
    discount_code = fields.DiscountCodeField(_("Discount code"), blank=True)
    discount_total = fields.MoneyField(_("Discount total"))
    total = fields.MoneyField(_("Order total"))
    transaction_id = CharField(_("Transaction ID"), max_length=255, null=True,
                               blank=True)

    status = models.IntegerField(_("Status"),
                            choices=settings.SHOP_ORDER_STATUS_CHOICES,
                            default=settings.SHOP_ORDER_STATUS_CHOICES[0][0])

    #custom CO fields
    rms_order_id = CharField(_("RMS Order ID"), max_length=60, blank=True)
    has_rms_order_id = models.BooleanField(default=False)

    rms_customer_id = CharField(_("RMS Customer ID"), max_length=60, blank=True)
    rms_message = CharField(_("RMS Message"), max_length=200, blank=True,
            default="")
    rms_last_submitted = models.DateTimeField(_("RMS Last submitted"), blank=True, null=True)

    payment_gateway_transaction_id = CharField(_("TNS Transaction ID"),
            max_length=80, blank=True, help_text="Currently this comes from TNS")
    payment_gateway_transaction_type = CharField(_("TNS Transaction Type"),
            max_length=80, blank=True, help_text="Currently this comes from TNS")

    objects = managers.OrderManager()

    # These are fields that are stored in the session. They're copied to
    # the order in setup() and removed from the session in complete().
    session_fields = ("shipping_type", "shipping_total", "discount_total", "tax_total")

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ("-id",)

    def __init__(self, *args, **kwargs):
        super(Order, self).__init__(*args, **kwargs)
        if self.total:
            self.refresh_tax_total()


    def save(self, *args, **kwargs):
        #custom CO save
        self.has_rms_order_id = len(self.rms_order_id)>0
        super(Order, self).save(*args, **kwargs)

    def __unicode__(self):
        return "#%s %s %s" % (self.id, self.billing_name(), self.time)

    def billing_name(self):
        return "%s %s" % (self.billing_detail_first_name,
                          self.billing_detail_last_name)

    def setup(self, request):
        """
        Set order fields that are stored in the session, item_total
        and total based on the given cart, and copy the cart items
        to the order. Called in the final step of the checkout process
        prior to the payment handler being called.
        """
        self.key = request.session.session_key
        self.user_id = request.user.id
        for field in self.session_fields:
            if field in request.session:
                setattr(self, field, request.session[field])
        self.total = self.item_total = request.cart.total_price()
        if self.shipping_total is not None:
            self.shipping_total = Decimal(str(self.shipping_total))
            self.total += self.shipping_total
        # Note that tax_total is not a persistent model field at this stage
        if hasattr(self, 'tax_total') and self.tax_total is not None:
            self.total += self.tax_total
        if self.discount_total is not None:
            self.total -= self.discount_total
        self.currency = session_currency(request)
        self.save()  # We need an ID before we can add related items.
        for item in request.cart:
            product_fields = [f.name for f in SelectedProduct._meta.fields]
            #CO custom code to copy promotion details from cartitem to orderitems if available
            product_fields.extend([f.name for f in item._meta.fields if "promotion" in f.name])
            item = dict([(f, getattr(item, f)) for f in product_fields])
            self.items.create(**item)
        for item in request.cart:
            try:
                variation = ProductVariation.objects.get(sku=item.sku)
            except ProductVariation.DoesNotExist:
                pass
            else:
                variation.reduce_stock(item.quantity)
                variation.product.actions.purchased()

    def complete(self, request):
        """
        Remove order fields that are stored in the session, reduce
        the stock level for the items in the order, and then delete
        the cart.

        Also increment the number of times used attribute for the
        unique discount code that was used, if one was used at all.
        """
        self.save()  # Save the transaction ID.
        for field in self.session_fields:
            if field in request.session:
                del request.session[field]
        del request.session["order"]

        # If a discount code was used and it was a unique discount code
        # (ie, no_of_allowed_uses > 0) then we increment the number of times
        # used for the particular discount code.
        try:
            discount_code = DiscountCodeUnique.objects.get(code=self.discount_code, allowed_no_of_uses__gt=0)
        except DiscountCodeUnique.DoesNotExist:
            pass
        else:
            discount_code.use_code()
            discount_code.save()

        request.cart.delete()

    def details_as_dict(self):
        """
        Returns the billing_detail_* and shipping_detail_* fields
        as two name/value pairs of fields in a dict for each type.
        Used in template contexts for rendering each type as groups
        of names/values.
        """
        context = {}
        for fieldset in ("billing_detail", "shipping_detail"):
            fields = [(f.verbose_name, getattr(self, f.name)) for f in
                self._meta.fields if f.name.startswith(fieldset)]
            context["order_%s_fields" % fieldset] = fields
        return context

    def invoice(self):
        """
        Returns the HTML for a link to the PDF invoice for use in the
        order listing view of the admin.
        """
        url = reverse("shop_invoice", args=(self.id,))
        text = ugettext("Download PDF invoice")
        return "<a href='%s?format=pdf'>%s</a>" % (url, text)
    invoice.allow_tags = True
    invoice.short_description = ""

    def refresh_tax_total(self):
        """Attempts to recalculate the tax_total, a non-presistent
        attribute on the order class from the other information
        available.
        """
        tax_total = self.total
        tax_total -= self.item_total

        if self.discount_total:
            tax_total += self.discount_total

        if self.shipping_total:
            tax_total -= self.shipping_total

        if tax_total:
            self.tax_total = tax_total


class Cart(models.Model):

    last_updated = models.DateTimeField(_("Last updated"), auto_now=True,
                                        null=True)

    objects = managers.CartManager()

    def __iter__(self):
        """
        Allow the cart to be iterated giving access to the cart's items,
        ensuring the items are only retrieved once and cached.
        """
        if not hasattr(self, "_cached_items"):
            self._cached_items = list(self.items.all())
        return iter(self._cached_items)

    def add_item(self, variation, quantity):
        """
        Increase quantity of existing item if SKU matches, otherwise create
        new.
        """
        kwargs = {"sku": variation.sku, "unit_price": variation.price()}
        item, created = self.items.get_or_create(**kwargs)
        if created:
            item.description = unicode(variation)
            item.unit_price = variation.price()
            item.url = variation.product.get_absolute_url()
            image = variation.image
            if image is not None:
                item.image = unicode(image.file)
            variation.product.actions.added_to_cart()
        item.quantity += quantity
        item.save()
        self._clear_item_cache()

    def remove_item(self, item_id):
        """Remove the item from the cart with the specified id
        and return True is successful.
        """
        result = False
        try:
            item = CartItem.objects.get(id=item_id, cart=self)
        except (ObjectDoesNotExist, ValueError):
            elog.warning("Unable to find item {} in user's cart {} (cart last update {})".format(
                item_id,
                self.id,
                self.last_updated.strftime("%c"),
                )
            )
        else:
            item.delete()
            result = True
            self._clear_item_cache()

        return result

    def _clear_item_cache(self):
        if hasattr(self, "_cached_items"):
            del self._cached_items

    def has_items(self):
        """
        Template helper function - does the cart have items?
        """
        return len(list(self)) > 0

    def total_quantity(self):
        """
        Template helper function - sum of all item quantities.
        """
        return sum([item.quantity for item in self])

    def total_price(self):
        """
        Template helper function - sum of all costs of item quantities.
        """
        return sum([item.total_price for item in self])

    def skus(self):
        """
        Returns a list of skus for items in the cart. Used by
        ``upsell_products`` and ``calculate_discount``.
        """
        return [item.sku for item in self]

    def upsell_products(self):
        """
        Returns the upsell products for each of the items in the cart.
        """
        cart = Product.objects.filter(variations__sku__in=self.skus()) #products linked to items in cart
        published_products = Product.objects.published()

        #all publish products that are linked via upsell_products on cart item products
        for_cart = published_products.filter(upsell_products__in=cart)
        with_cart_excluded = for_cart.exclude(variations__sku__in=self.skus())
        return list(with_cart_excluded.distinct())

    def calculate_discount(self, discount, currency):
        # Discount applies to cart total if not product specific.
        deductable_items = False
        discount_deduct = False
        discount_exact = False
        min_purchase = False
        specific_products = True
        discount_skus = []
        if discount:
            specific_products = discount.products.count() or \
              discount.categories.count()
            discount_variations = discount.all_variations().filter(
                sku__in=self.skus(),
            )
            discount_skus = discount_variations.values_list("sku", flat=True)
            discount_deduct = getattr(discount, "_discount_deduct_{}".format(
                    currency.lower()))
            discount_exact = getattr(discount, "_discount_exact_{}".format(
                    currency.lower()))
            min_purchase = getattr(discount, "_min_purchase_{}".format(
                    currency.lower()))

        from multicurrency.models import MultiCurrencyProductVariation
        mc_variations = MultiCurrencyProductVariation.objects.filter(
            sku__in=self.skus(),
        )

        bundle_ids = set(mc_variation.bundle_discount_id
                for mc_variation in mc_variations
                if mc_variation.bundle_discount_id
        )

        active_bundles = BundleDiscount.objects.active(currency).filter(
            id__in=bundle_ids
        ).values_list(
            'id',
            '_title_{}'.format(currency.lower()),
            'quantity',
            '_bundled_unit_price_{}'.format(currency.lower()),
        ).distinct()

        # Collect all the sku which we could bundle and so we can aggregate
        # their quantities. Keep track of the discount code and bundle
        # discount which could be applied so we can try and maximise (or
        # minimise) the total discount.
        # Create a list of skus in the cart that are applicable to
        # the discount, and total the discount for appllicable items.
        bundle_collection = {
            id_: (title, quantity, bundle_unit_price, [])
            for id_, title, quantity, bundle_unit_price in active_bundles
        }
        fall_back_collection = ('', 0, 0, [])
        bundle_collection[None] = fall_back_collection
        for item in self:
            sku = item.sku
            mc_variation = max(mc_variations, key=lambda x: sku == x.sku)
            item.unit_price = mc_variation.price(currency)
            item.bundle_unit_price = item.unit_price
            item.discount_unit_price = item.unit_price
            item.bundle_quantity = 0
            item.bundle_title = None
            should_discount = discount_exact or all([
                not mc_variation.on_sale(currency),
                not mc_variation.is_marked_down(currency),
                not specific_products or sku in discount_skus
            ])
            if should_discount:
                if discount_deduct or discount_exact:
                    deductable_items = True
                else:
                    item.discount_unit_price -= discount.calculate(
                        item.unit_price,
                        currency
                    )

            bundle_title, bundle_quantity, bundle_unit_price, bundlable = \
              bundle_collection.get(
                  mc_variation.bundle_discount_id,
                  fall_back_collection
            )
            should_bundle = all([
                bundle_quantity,
                not mc_variation.on_sale(currency),
                not mc_variation.is_marked_down(currency),
            ])
            if should_bundle:
                item.bundle_title = bundle_title
                item.bundle_unit_price = bundle_unit_price / bundle_quantity
                bundlable.extend([item] * item.quantity)


        # Bundle things up as much as we can. Note: Just
        # Because we could bundle something doesn't mean
        # we should. It possible that it's cheaper for the
        # customer not bundle or use a discount code instead.
        for title, quantity, bundle_price, bundlable in \
          bundle_collection.values():

            if not all([quantity, title, bundle_price]):
                # Filter out out fall_back_collection and
                # any other bundles that have invalid values.
                continue

            bundlable.sort(key=lambda x: x.discount_unit_price, reverse=True)
            potential_bundles = iter_bundle(
                bundlable,
                quantity,
            )
            # Try to bundle keep bundling items for as long
            # as it is reducing the over price.
            keep_bundling = True
            for potential_bundle in potential_bundles:
                discounted_price = sum(
                    item.discount_unit_price for item in potential_bundle
                )
                keep_bundling &= discounted_price > bundle_price
                keep_bundling &= len(potential_bundle) == quantity
                if keep_bundling:
                    for item in potential_bundle:
                        item.bundle_quantity += 1

        for item in self:
            item.save()

        discount_total = Decimal("0.00")
        if deductable_items and (not min_purchase or
                                 self.total_price() >= min_purchase):
            if discount_deduct:
                discount_total = discount_deduct
            elif discount_exact:
                discount_total = discount_exact

        return bundle_collection, discount_total

    def has_no_stock(self):
        "Return the products of the cart with no stock"
        no_stock = []
        for item in self:
            if item.quantity <= item.variation().total_in_stock:
                continue
            no_stock += [item]
        return no_stock


class SelectedProduct(models.Model):
    """
    Abstract model representing a "selected" product in a cart or order.
    """

    sku = fields.SKUField()
    description = CharField(_("Description"), max_length=200)
    quantity = models.IntegerField(
        _("Total quantity"),
        default=0,
        help_text="The total quantity including bundled and non-bundled items. Any items that aren't bundled will incur the discount unit price."
    )
    bundle_quantity = models.IntegerField(
        _("Bundle quantity"),
        default=0,
        help_text="The subset of the total quanity which will incur 'bundle unit price'.",
    )

    unit_price = fields.MoneyField(
        _("Unit price"),
        default=Decimal("0"),
        help_text="The unit price without bundle or discount code application.",
    )

    discount_unit_price = fields.MoneyField(
        _("Discount unit price"),
        default=Decimal("0"),
        help_text="The per item price that applies to non-bundled items.",
    )

    bundle_unit_price = fields.MoneyField(
        _("Bundle unit price"),
        default=Decimal("0"),
        help_text="The per item price that applies to bundled items.",
    )

    total_price = fields.MoneyField(
        _("Total price"),
        default=Decimal("0"),
        help_text="The total price of the items including bundling and discount codes.",
    )
    bundle_title = CharField(max_length=100, blank=True, null=True)

    class Meta:
        abstract = True

    def __unicode__(self):
        return ""

    def save(self, *args, **kwargs):
        self.total_price = sum([
                self.non_discounted_price,
                self.bundle_discount,
                self.discount_code_discount,
        ])
        super(SelectedProduct, self).save(*args, **kwargs)

    @property
    def non_discounted_price(self):
        """The price of the items without taking into account discounting
        and bundling."""
        return self.quantity * self.unit_price

    @property
    def bundle_discount(self):
        """The discount from bundling that can be attributed
        to this SelectedProduct."""
        return self.bundle_quantity * \
          (self.bundle_unit_price - self.unit_price)

    @property
    def discount_code_discount(self):
        """The discount from the discount code that can be
        attributed to this SelectedProduct."""
        return  (self.quantity - self.bundle_quantity) * \
           (self.discount_unit_price - self.unit_price)

class CartItem(SelectedProduct):

    cart = models.ForeignKey("Cart", related_name="items", on_delete=models.CASCADE)
    url = CharField(max_length=200)
    image = CharField(max_length=200, null=True)

    @property
    def title(self):
        """ Try and extract a cleaner title from the description """
        try:
            title = self.description.split("::")[0].strip()
        except:
            title = self.description
        return title


    def get_absolute_url(self):
        return self.url

    def variation(self):
        try:
            return ProductVariation.objects.get(sku=self.sku)
        except:
            return None

class OrderItem(SelectedProduct):
    """
    A selected product in a completed order.
    """
    order = models.ForeignKey("Order", related_name="items")


class ProductAction(models.Model):
    """
    Records an incremental value for an action against a product such
    as adding to cart or purchasing, for sales reporting and
    calculating popularity. Not yet used but will be used for product
    popularity and sales reporting.
    """

    product = models.ForeignKey("Product", related_name="actions")
    timestamp = models.IntegerField()
    total_cart = models.IntegerField(default=0)
    total_purchase = models.IntegerField(default=0)

    objects = managers.ProductActionManager()

    class Meta:
        unique_together = ("product", "timestamp")


class Discount(models.Model):
    """
    Abstract model representing one of several types of monetary
    reductions, as well as a date range they're applicable for, and
    the products and products in categories that the reduction is
    applicable for.
    """

    title = CharField(max_length=100)
    active = models.BooleanField(_("Active"))
    products = models.ManyToManyField("Product", blank=True)
    categories = models.ManyToManyField("Category", blank=True,
                                        related_name="%(class)s_related")
    discount_deduct = fields.MoneyField(_("Reduce by amount"))
    discount_percent = models.DecimalField(_("Reduce by percent"),
                                           max_digits=4, decimal_places=2,
                                           blank=True, null=True)
    discount_exact = fields.MoneyField(_("Reduce to amount"))
    valid_from = models.DateTimeField(_("Valid from"), blank=True, null=True)
    valid_to = models.DateTimeField(_("Valid to"), blank=True, null=True)

    objects = managers.DiscountManager()

    class Meta:
        abstract = True

    def __unicode__(self):
        return self.title

    def all_products(self):
        """Return a queryset containing all Products that this discount applies
        to.
        """
        return Product.objects.filter(
            Q(categories__in=self.categories.values_list('id', flat=True)) |
            Q(id__in=self.products.values_list('id', flat=True))
        ).distinct()

    def all_variations(self):
        """Return a queryset containing all ProductVariations that this discount
        applies to.
        """
        return ProductVariation.objects.filter(
            Q(product__categories__in=self.categories.values_list('id', flat=True)) |
            Q(product__in=self.products.values_list('id', flat=True))
        ).distinct()


class Sale(Discount):
    """
    Stores sales field values for price and date range which when saved
    are then applied across products and variations according to the
    selected categories and products for the sale.
    """

    class Meta:
        verbose_name = _("Sale")
        verbose_name_plural = _("Sales")

    def save(self, *args, **kwargs):
        """
        Apply sales field value to products and variations according
        to the selected categories and products for the sale.
        """
        super(Sale, self).save(*args, **kwargs)
        self._clear()
        if self.active:
            extra_filter = {}
            if self.discount_deduct is not None:
                # Don't apply to prices that would be negative
                # after deduction.
                extra_filter["unit_price__gt"] = self.discount_deduct
                sale_price = models.F("unit_price") - self.discount_deduct
            elif self.discount_percent is not None:
                sale_price = models.F("unit_price") - (
                    models.F("unit_price") / "100.0" * self.discount_percent)
            elif self.discount_exact is not None:
                # Don't apply to prices that are cheaper than the sale
                # amount.
                extra_filter["unit_price__gt"] = self.discount_exact
                sale_price = self.discount_exact
            else:
                return
            products = self.all_products()
            variations = ProductVariation.objects.filter(product__in=products)
            for priced_objects in (products, variations):
                # MySQL will raise a 'Data truncated' warning here in
                # some scenarios, presumably when doing a calculation
                # that exceeds the precision of the price column. In
                # this case it's safe to ignore it and the calculation
                # will still be applied.
                try:
                    update = {"sale_id": self.id,
                              "sale_price": sale_price,
                              "sale_to": self.valid_to,
                              "sale_from": self.valid_from}
                    priced_objects.filter(**extra_filter).update(**update)
                except OperationalError:
                    # Work around for MySQL which does not allow update
                    # to operate on subquery where the FROM clause would
                    # have it operate on the same table.
                    #
                    # http://dev.mysql.com/
                    # doc/refman/5.0/en/subquery-errors.html
                    try:
                        for priced in priced_objects.filter(**extra_filter):
                            for field, value in update.items():
                                setattr(priced, field, value)
                            priced.save()
                    except Warning:
                        pass
                except Warning:
                    pass

    def delete(self, *args, **kwargs):
        """
        Clear this sale from products when deleting the sale.
        """
        self._clear()
        super(Sale, self).delete(*args, **kwargs)

    def _clear(self):
        """
        Clears previously applied sale field values from products prior
        to updating the sale, when deactivating it or deleting it.
        """
        update = {"sale_id": None, "sale_price": None,
                  "sale_from": None, "sale_to": None}
        for priced_model in (Product, ProductVariation):
            priced_model.objects.filter(sale_id=self.id).update(**update)

class DiscountCodeUniqueAbstract(models.Model):
    """
    Table for unique discount codes
    """
    allowed_no_of_uses = models.IntegerField(_("Allowed number of usages"), default=0)
    no_of_times_used = models.IntegerField(_("Number of times used"), default=0)

    class Meta:
        abstract = True


class BundleDiscount(models.Model):
    title = CharField(max_length=100)
    active = models.BooleanField(_("Active"))
    upsell_message = CharField(_("Upsell message"), max_length=256, blank=True, null=True)
    applied_message = CharField(_("Bundle applied message"), max_length=256, blank=True, null=True)
    applied_upsell_message = CharField(_("Bundle applied but with remainders message"), max_length=256, blank=True, null=True)
    valid_from = models.DateTimeField(_("Valid from"), blank=True, null=True)
    valid_to = models.DateTimeField(_("Valid to"), blank=True, null=True)
    quantity = models.IntegerField(_("Bundle quantity"), default=2)
    bundled_unit_price = fields.MoneyField(_("Bundled unit price"))
    products = models.ManyToManyField("Product", blank=True)
    categories = models.ManyToManyField("Category", blank=True)

    objects = managers.BundleDiscountManager()

    class Meta:
        verbose_name = _("BundleDiscount")
        verbose_name_plural = _("BundleDiscounts")

    def __unicode__(self):
        return self.title

    def apply(self):
        """
        Apply BundleDiscount field value to products and variations according
        to the selected categories and products for the sale.
        """
        self._clear()
        if self.active:
            products = self.all_products()
            variations = ProductVariation.objects.filter(product__in=products)
            for priced_objects in (products, variations):
                try:
                    priced_objects.update(bundle_discount_id=self.id)
                except (OperationalError, DatabaseError):
                    for priced_object in priced_objects:
                        priced_object.bundle_discount_id = self.id
                        priced_object.save()

    def delete(self, *args, **kwargs):
        """
        Clear this BundleDiscount from products when deleting it.
        """
        self._clear()
        super(BundleDiscount, self).delete(*args, **kwargs)

    def _clear(self):
        """
        Clears previously applied sale field values from products prior
        to updating the BundleDiscount, when deactivating it or deleting it.
        """
        for priced_model in (Product, ProductVariation):
            filtered = priced_model.objects.filter(bundle_discount_id=self.id)
            filtered.update(bundle_discount_id=None)

    def all_products(self):
        """
        Return the selected products as well as the products in the
        selected categories.
        """
        filters = [category.filters() for category in self.categories.all()]
        filters = reduce(ior, filters + [Q(id__in=self.products.only("id"))])
        return Product.objects.filter(filters).distinct()


from countries.models import Country

#custom CO class, depends on cartridge MoneyField
class ShippingOption(models.Model):
    title = models.CharField(_("Title"), max_length=200)
    price = fields.MoneyField(_("Price"))
    countries = models.ManyToManyField(Country, blank=True, related_name="availablecountries")

    valid_from = models.DateTimeField(_("Valid from"), blank=True, null=True)
    valid_to = models.DateTimeField(_("Valid to"), blank=True, null=True)
    active = models.BooleanField(_("Active"), default=True)

    class Meta:
        ordering = ['price',]

    def __unicode__(self):
        return "%s: $%s"%(self.title, self.price)


#TODO: This is a stock cartridge class that has some CO customisations
#      One day shift out of stock cartridge.
class DiscountCode(Discount, DiscountCodeUniqueAbstract):
    """
    A code that can be entered at the checkout process to have a
    discount applied to the total purchase amount.
    """

    code = fields.DiscountCodeField(_("Code"), unique=True)
    min_purchase = fields.MoneyField(_("Minimum total purchase"))
    free_shipping = models.BooleanField(_("Free shipping"))
    shipping_restriction = models.ManyToManyField(ShippingOption, blank=True,
                        null=True)

    objects = managers.DiscountCodeManager()

    def calculate(self, amount, currency):
        """
        Calculates the discount for the given amount.
        """
        discount_deduct = getattr(self, "_discount_deduct_{}".format(
            currency.lower()
        ))
        if discount_deduct is not None:
            # Don't apply to amounts that would be negative after
            # deduction.
            if discount_deduct < amount:
                return discount_deduct
        elif self.discount_percent is not None:
            discount =  amount / Decimal("100") * self.discount_percent
            return discount.quantize(Decimal('0.01'), rounding=ROUND_UP)
        return 0

    class Meta:
        verbose_name = _("Discount code")
        verbose_name_plural = _("Discount codes")

class DiscountCodeUnique(DiscountCode):
    """
    NOT CARTRIDGE NATIVE
    Proxy class for unique promo code.
    """
    def is_valid(self):
        if self.no_of_times_used < self.allowed_no_of_uses:
            return True
        else:
            return False

    def use_code(self):
        if self.is_valid():
            self.no_of_times_used += 1
            self.save()

    def gen_suffix(self):
        """
        Generate a random suffix
        """
        pools = []
        for pattern in settings.UNIQUE_DISCOUNT_CODE_SUFFIX_PATTERN:
            pools.append([val for val in invert(pattern)])
        return ''.join([random.sample(pool, 1)[0] for pool in pools])

    class Meta:
        proxy = True
        app_label = 'Shop'
        verbose_name = 'Unique Discount Code'
        verbose_name_plural = 'Unique Discount Codes'

class CategoryTheme(models.Model):
    """ Cottonon Brand Pages ... a "skin" for the category page """
    category = models.ForeignKey(Category, unique=True)
    name = models.CharField(max_length=60, help_text="eg July Refresh 2012")
    created = models.DateTimeField(auto_now_add=True)
    status = models.IntegerField(_("Status"),
            choices=CONTENT_STATUS_CHOICES,
            default=CONTENT_STATUS_DRAFT)
    publish_date = models.DateTimeField(_("Published from"),
            help_text=_("With published checked, won't be shown until this time"),
            blank=True, null=True)
    expiry_date = models.DateTimeField(_("Expires on"),
            help_text=_("With published checked, won't be shown after this time"),
            blank=True, null=True)

    class Meta:
        db_table = "shop_categorypage"

    just_arrived = models.ForeignKey(Category, related_name='catergorypage_just_arrived_set', help_text=_("Select a category and its featured products will display on the brand page"))

    objects = PublishedManager()

    def __unicode__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.publish_date is None:
            self.publish_date = datetime.now()
        super(CategoryTheme, self).save(*args, **kwargs)

    def primary_images(self):
        return PrimaryCategoryPageImage.objects.active().filter(panel=self)

    def secondary_image(self):
        images = SecondaryCategoryPageImage.objects.active().filter(panel=self)[:1]
        if images:
            return images[0]
        return None

    def footer(self):
        images = FooterCategoryPageImage.objects.active().filter(panel=self)[:1]
        if images:
            return images[0]
        return None

############
# CO ADDED
############

class CategoryPageImage(CloudFrontImage):
    panel = models.ForeignKey('CategoryTheme')

    image = models.ImageField(
        _("Image"),
        max_length=100,
        blank=True,
        upload_to="static/media/category_page"
    )
    alt_text = models.CharField(max_length=140,blank=True)
    link = models.URLField(max_length=160, verify_exists=False, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    publish_date = models.DateTimeField(_("Published from"),
            help_text=_("With published checked, won't be shown until this time"),
            blank=True, null=True)
    expiry_date = models.DateTimeField(_("Expires on"),
            help_text=_("With published checked, won't be shown after this time"),
            blank=True, null=True)

    objects = managers.CategoryPageImageManager()

    def __unicode__(self):
        return self.alt_text

    def stored_image_path(self):
        return self.image.name

    def save(self, *args, **kwargs):
        if self.publish_date is None:
            self.publish_date = datetime.now()
        super(CategoryPageImage, self).save(*args, **kwargs)

class PrimaryCategoryPageImage(CategoryPageImage):
    objects = managers.CategoryPageImageManager()

class SecondaryCategoryPageImage(CategoryPageImage):
    objects = managers.CategoryPageImageManager()

class FooterCategoryPageImage(CategoryPageImage):
    objects = managers.CategoryPageImageManager()

class ProductSyncRequest(models.Model):
   product = models.OneToOneField(Product, primary_key=True)
   images = models.BooleanField(_("Sync Images"), default=False)
   stock = models.BooleanField(_("Sync Stock"), default=False)
