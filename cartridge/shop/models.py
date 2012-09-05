import random
from datetime import datetime
from decimal import Decimal, ROUND_UP
from operator import iand, ior

from django.db import models
from django.db.models import CharField, Q
from django.db.models.base import ModelBase
from django.utils.translation import ugettext_lazy as _
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from _mysql_exceptions import OperationalError
from django.utils import simplejson

from mezzanine.conf import settings
from mezzanine.core.managers import DisplayableManager, PublishedManager
from mezzanine.core.models import Displayable, RichText, Orderable, CONTENT_STATUS_DRAFT, CONTENT_STATUS_CHOICES
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


class Category(Page, RichText):
    """
    A category of products on the website.
    """

    options = models.ManyToManyField("ProductOption", blank=True,
                                     related_name="product_options")
    sale = models.ForeignKey("Sale", blank=True, null=True)
    price_min = fields.MoneyField(_("Minimum price"), blank=True, null=True)
    price_max = fields.MoneyField(_("Maximum price"), blank=True, null=True)
    combined = models.BooleanField(default=True, help_text="If checked, "
        "products must match all specified filters, otherwise products "
        "can match any specified filter.")
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
        # Build a list of Q objects to filter variations by.
        filters = []
        # Build a lookup dict of selected options for variations.
        options = self.options.as_fields()
        if options:
            lookup = dict([("%s__in" % k, v) for k, v in options.items()])
            filters.append(Q(**lookup))
        # Q objects used against variations to ensure sale date is
        # valid when filtering by sale, or sale price.
        now = datetime.now()
        valid_sale_from = Q(sale_from__isnull=True) | Q(sale_from__lte=now)
        valid_sale_to = Q(sale_to__isnull=True) | Q(sale_to__gte=now)
        valid_sale_date = valid_sale_from & valid_sale_to
        # Filter by variations with the selected sale if the sale date
        # is valid.
        if self.sale_id:
            filters.append(Q(sale_id=self.sale_id) & valid_sale_date)
        # If a price range is specified, use either the unit price or
        # a sale price if the sale date is valid.
        if self.price_min or self.price_max:
            prices = []
            if self.price_min:
                sale = Q(sale_price__gte=self.price_min) & valid_sale_date
                prices.append(Q(unit_price__gte=self.price_min) | sale)
            if self.price_max:
                sale = Q(sale_price__lte=self.price_max) & valid_sale_date
                prices.append(Q(unit_price__lte=self.price_max) | sale)
            filters.append(reduce(iand, prices))
        # Turn the variation filters into a product filter.
        operator = iand if self.combined else ior
        products = Q(id__in=self.products.only("id"))
        if filters:
            filters = reduce(operator, filters)
            variations = ProductVariation.objects.filter(filters)
            filters = [Q(variations__in=variations)]
            # If filters exist, checking that products have been
            # selected is neccessary as combining the variations
            # with an empty ID list lookup and ``AND`` will always
            # result in an empty result.
            if self.products.count() > 0:
                filters.append(products)
            return reduce(operator, filters)
        return products


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
    date_last_synced = models.DateTimeField(
        _("Images Last Synced"), null=True,
        help_text="When images for this product was last synced from RMS."
    )
    related_products = models.ManyToManyField("self", blank=True)
    upsell_products = models.ManyToManyField("self", blank=True)
    rating = RatingField(verbose_name=_("Rating"))
    featured = models.BooleanField(_("Featured Product"), default=False)
    in_stock = models.BooleanField(_("In Stock"), default=False)
    ranking = models.IntegerField(default=500)

    product_colours = CharField(_("Available colours"), blank=True, default="", max_length=500)
    product_sizes = CharField(_("Available colours"), blank=True, default="", max_length=255)

    tags = TaggableManager()
    objects = DisplayableManager()
    search_fields = ("master_item_code",)

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")
        ordering = ("ranking", "title")

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
                             (899, 'women')):
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


class ProductImage(Orderable):
    """
    An image for a product - a relationship is also defined with the
    product's variations so that each variation can potentially have
    it own image, while the relationship between the ``Product`` and
    ``ProductImage`` models ensures there is a single set of images
    for the product.
    """

    file = models.ImageField(_("Image"), upload_to="product")
    description = CharField(_("Description"), blank=True, max_length=100)
    product = models.ForeignKey("Product", related_name="images")

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
    image = models.ForeignKey("ProductImage", verbose_name=_("Image"),
                              null=True, blank=True)

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
        live = self.live_num_in_stock()
        return live is None or quantity == 0 or live >= quantity

    @property
    def total_in_stock(self):
        """
        Get the total stock levels.
        If stock_pool > STOCK_POOL_CUTOFF: stock - STOCK_THRHESHOLD
        If stock_pool <= 0: stock - STOCK_POOL_THRESHOLD
        """
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
    session_fields = ("shipping_type", "shipping_total", "discount_total")

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ("-id",)

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
        if self.discount_total is not None:
            self.total -= self.discount_total
        self.currency = session_currency(request)
        self.save()  # We need an ID before we can add related items.
        for item in request.cart:
            product_fields = [f.name for f in SelectedProduct._meta.fields]
            item = dict([(f, getattr(item, f)) for f in product_fields])
            self.items.create(**item)

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
        for item in request.cart:
            try:
                variation = ProductVariation.objects.get(sku=item.sku)
            except ProductVariation.DoesNotExist:
                pass
            else:
                variation.reduce_stock(item.quantity)
                variation.product.actions.purchased()

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

    def receipt_order_items(self):
        """
        Return a simplified data structure containing the essential
        order item details required for rending the order receipt
        email templates. The reason for doing this is that celery
        running asychronously does not play nicely with retreiving
        related model instaces off of the order (ie, order.items.all())
        - we end up just getting an empty list, which is obviously
        not what we want.
        """
        items = []
        for i in self.items.all():
            items.append({
                'quantity': i.quantity,
                'description': i.description,
                'unit_price': i.unit_price,
            })
        return items


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
            self._cached_items = self.items.all()
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
        return sum([item.total for item in self])

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
        # HACK: Get the cart back up and running. Comment this out because
        # sku no longer exists!
        return []
        cart = Product.objects.filter(variations__sku__in=self.skus())
        published_products = Product.objects.published()
        for_cart = published_products.filter(upsell_products__in=cart)
        with_cart_excluded = for_cart.exclude(variations__sku__in=self.skus())
        return list(with_cart_excluded.distinct())

    def calculate_discount(self, discount):
        """
        Calculates the discount based on the items in a cart, some
        might have the discount, others might not.
        """
        # Discount applies to cart total if not product specific.
        products = discount.all_products()
        if products.count() == 0:
            return discount.calculate(self.total_price())
        total = Decimal("0")
        # Create a list of skus in the cart that are applicable to
        # the discount, and total the discount for appllicable items.
        lookup = {"product__in": products, "sku__in": self.skus()}
        discount_variations = ProductVariation.objects.filter(**lookup)
        discount_skus = discount_variations.values_list("sku", flat=True)
        for item in self:
            if item.sku in discount_skus:
                total += discount.calculate(item.unit_price) * item.quantity
        return total

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
    quantity = models.IntegerField(_("Quantity"), default=0)
    unit_price = fields.MoneyField(_("Unit price"), default=Decimal("0"))
    total_price = fields.MoneyField(_("Total price"), default=Decimal("0"))

    class Meta:
        abstract = True

    def __unicode__(self):
        return ""

    def save(self, *args, **kwargs):
        self.total_price = self.unit_price * self.quantity
        super(SelectedProduct, self).save(*args, **kwargs)

class CartItem(SelectedProduct):

    cart = models.ForeignKey("Cart", related_name="items")
    url = CharField(max_length=200)
    image = CharField(max_length=200, null=True)

    @property
    def total(self):
        """ total_price including promotion discount if available """
        t = self.total_price
        promotion_discount = getattr(self, "promotion_discount", Decimal("0.0"))
        if promotion_discount>0:
            t -= promotion_discount
        return t

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

    class Meta:
        abstract = True

    def __unicode__(self):
        return self.title

    def all_products(self):
        """
        Return the selected products as well as the products in the
        selected categories.
        """
        filters = [category.filters() for category in self.categories.all()]
        filters = reduce(ior, filters + [Q(id__in=self.products.only("id"))])
        return Product.objects.filter(filters).distinct()


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

    def calculate(self, amount):
        """
        Calculates the discount for the given amount.
        """
        if self.discount_deduct is not None:
            # Don't apply to amounts that would be negative after
            # deduction.
            if self.discount_deduct < amount:
                return self.discount_deduct
        elif self.discount_percent is not None:
            return amount / Decimal("100") * self.discount_percent
        return 0

    def calculate_cart(self, cart):
        """
        Calculates the discount based on the items in the cart
        """
        #if not applied to individual products or categories, discount the entire cart (as per the original cartridge functionality)
        if self.products.count() == 0 and self.categories.count() == 0:
            return self.calculate(cart.total_price())
        #or, since there are products and categories, loop through cart and calc.
        discount = Decimal("0")
        #the products that can be discounted
        skus = [x.sku for x in cart]
        products = self.products.filter(variations__sku__in=skus) | Product.objects.filter(categories=self.categories.all(), variations__sku__in=skus)

        for item in cart:
            if products.filter(variations__sku=item.sku).count()>0: #apply a discount to this product
                discount += self.calculate(item.total_price)
        discount = discount.quantize(Decimal('0.01'), rounding=ROUND_UP)
        return discount

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

class CategoryPageImage(models.Model):
    panel = models.ForeignKey('CategoryTheme')

    image = models.ImageField(_("Image"), max_length=100, blank=True, upload_to="category_page")
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
