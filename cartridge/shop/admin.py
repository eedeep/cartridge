
from copy import deepcopy

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.widgets import ManyToManyRawIdWidget
from django.db.models import ImageField
from django.utils.encoding import smart_unicode
from django.utils.html import escape
from django.utils.translation import ugettext_lazy as _

from mezzanine.core.admin import DisplayableAdmin, TabularDynamicInlineAdmin
from mezzanine.pages.admin import PageAdmin

from cartridge.shop.fields import MoneyField
from cartridge.shop.forms import ProductAdminForm, ProductVariationAdminForm
from cartridge.shop.forms import ProductVariationAdminFormset
from cartridge.shop.forms import DiscountAdminForm, ImageWidget, MoneyWidget
from cartridge.shop.models import Category, Product, ProductImage
from cartridge.shop.models import ProductVariation, ProductOption, Order
from cartridge.shop.models import OrderItem, Sale, DiscountCode, BundleDiscount

from cartridge_extras.forms import CategoryAdminForm

# Lists of field names.
option_fields = [f.name for f in ProductVariation.option_fields()]
_flds = lambda s: [f.name for f in Order._meta.fields if f.name.startswith(s)]
billing_fields = _flds("billing_detail")
shipping_fields = _flds("shipping_detail")

category_fieldsets = deepcopy(PageAdmin.fieldsets)
category_fieldsets[0][1]["fields"][3:3] = ["content"]  # , "products"]
category_fieldsets += ((_("Product filters"), {
    "fields": ("products", "hide_sizes"),
    "classes": ("collapse-closed",)},),)


class CategoryAdmin(PageAdmin):
    fieldsets = category_fieldsets
    formfield_overrides = {ImageField: {"widget": ImageWidget}}
    form = CategoryAdminForm

    def save_model(self, request, obj, form, change):
        """
        Store the product object for creating variations in save_formset.
        """
        if obj.id:
            obj.products.clear()
            obj.products.add(*form.cleaned_data['products'])
        super(CategoryAdmin, self).save_model(request, obj, form, change)


class ProductVariationAdmin(admin.TabularInline):
    verbose_name_plural = _("Current variations")
    model = ProductVariation
    fields = ("default", "num_in_stock_pool", "num_in_stock", "unit_price", "sale_price",
        "sale_from", "sale_to", "image")
    if not settings.DEBUG:
        readonly_fields = ("num_in_stock", "num_in_stock_pool")
    extra = 0
    formfield_overrides = {MoneyField: {"widget": MoneyWidget}}
    form = ProductVariationAdminForm
    formset = ProductVariationAdminFormset


class ProductImageAdmin(TabularDynamicInlineAdmin):
    model = ProductImage
    formfield_overrides = {ImageField: {"widget": ImageWidget}}


product_fieldsets = deepcopy(DisplayableAdmin.fieldsets)
product_fieldsets[0][1]["fields"][1] = ("status")
product_fieldsets[0][1]["fields"].extend([("ranking", "available", "featured", ), "categories",
                                          "content", ("master_item_code", )])
product_fieldsets = list(product_fieldsets)
product_fieldsets.append((_("Other products"), {
    "fields": ("related_products", "upsell_products")}))
product_fieldsets.insert(1, (_("Create new variations"),
    {"classes": ("create-variations",), "fields": option_fields}))


class ProductAdmin(DisplayableAdmin):

    class Media:
        js = ("cartridge/js/admin/product_variations.js",)
        css = {"all": ("cartridge/css/admin/product.css",)}

    list_display = ("admin_thumb", "title", "status", "available",
                    "admin_link")
    list_display_links = ("admin_thumb", "title")
    list_editable = ("status", "available")
    list_filter = ("status", "available", "categories")
    filter_horizontal = ("categories", "related_products", "upsell_products")
    search_fields = ("title", "content", "categories__title")
    inlines = (ProductImageAdmin, ProductVariationAdmin)
    form = ProductAdminForm
#    fieldsets = product_fieldsets

    def __init__(self, model, admin_site):
        super(ProductAdmin, self).__init__(model, admin_site)
        # We do the following so we have access in the form
        # to the admin_site, which is required by RelatedFieldWidgetWrapper
        self.form.admin_site = admin_site

    def save_model(self, request, obj, form, change):
        """
        Store the product object for creating variations in save_formset.
        """
        super(ProductAdmin, self).save_model(request, obj, form, change)
        self._product = obj

    def save_formset(self, request, form, formset, change):
        """

        Here be dragons. We want to perform these steps sequentially:

        - Save variations formset
        - Run the required variation manager methods:
          (create_from_options, manage_empty, etc)
        - Save the images formset

        The variations formset needs to be saved first for the manager
        methods to have access to the correct variations. The images
        formset needs to be run last, because if images are deleted
        that are selected for variations, the variations formset will
        raise errors when saving due to invalid image selections. This
        gets addressed in the set_default_images method.

        An additional problem is the actual ordering of the inlines,
        which are in the reverse order for achieving the above. To
        address this, we store the images formset as an attribute, and
        then call save on it after the other required steps have
        occurred.

        """

        # Store the images formset for later saving, otherwise save the
        # formset.
        if formset.model == ProductImage:
            self._images_formset = formset
        else:
            super(ProductAdmin, self).save_formset(request, form, formset,
                                                   change)

        # Run each of the variation manager methods if we're saving
        # the variations formset.
        if formset.model == ProductVariation:

            # Build up selected options for new variations.
            options = dict([(f, request.POST.getlist(f)) for f in option_fields
                             if request.POST.getlist(f)])
            # Create a list of image IDs that have been marked to delete.
            deleted_images = [request.POST.get(f.replace("-DELETE", "-id"))
                              for f in request.POST if f.startswith("images-")
                              and f.endswith("-DELETE")]

            # Create new variations for selected options.
            self._product.variations.create_from_options(options)
            # Create a default variation if there are nonw.
            self._product.variations.manage_empty()
            # Copy duplicate fields (``Priced`` fields) from the default
            # variation to the prodyct.
            self._product.copy_default_variation()
            # Remove any images deleted just now from variations they're
            # assigned to, and set an image for any variations without one.
            self._product.variations.set_default_images(deleted_images)

            # Save the images formset stored previously.
            super(ProductAdmin, self).save_formset(request, form,
                                                 self._images_formset, change)

            # Run again to allow for no images existing previously, with
            # new images added which can be used as defaults for variations.
            self._product.variations.set_default_images(deleted_images)


class ProductOptionAdmin(admin.ModelAdmin):
    ordering = ("type", "name")
    list_display = ("type", "name")
    list_display_links = ("type",)
    list_editable = ("name",)
    list_filter = ("type",)
    search_fields = ("type", "name")
    radio_fields = {"type": admin.HORIZONTAL}


class OrderItemInline(admin.TabularInline):
    verbose_name_plural = _("Items")
    model = OrderItem
    extra = 0
    formfield_overrides = {MoneyField: {"widget": MoneyWidget}}


class OrderAdmin(admin.ModelAdmin):
    ordering = ("status", "-id")
    list_display = ("id", "billing_name", "total", "time", "status",
                    "transaction_id", "invoice")
    list_editable = ("status",)
    list_filter = ("status", "time")
    list_display_links = ("id", "billing_name",)
    search_fields = (["id", "status", "transaction_id"] +
                     billing_fields + shipping_fields)
    date_hierarchy = "time"
    radio_fields = {"status": admin.HORIZONTAL}
    inlines = (OrderItemInline,)
    formfield_overrides = {MoneyField: {"widget": MoneyWidget}}
    fieldsets = (
        (_("Billing details"), {"fields": (tuple(billing_fields),)}),
        (_("Shipping details"), {"fields": (tuple(shipping_fields),)}),
        (None, {"fields": ("additional_instructions", ("shipping_total",
            "shipping_type"), ("discount_total", "discount_code"),
            "item_total", ("total", "status"), "transaction_id")}),
    )


class SaleAdmin(admin.ModelAdmin):
    list_display = ("title", "active", "discount_deduct", "discount_percent",
        "discount_exact", "valid_from", "valid_to")
    list_editable = ("active", "discount_deduct", "discount_percent",
        "discount_exact", "valid_from", "valid_to")
    filter_horizontal = ("categories", "products")
    formfield_overrides = {MoneyField: {"widget": MoneyWidget}}
    form = DiscountAdminForm
    fieldsets = (
        (None, {"fields": ("title", "active")}),
        (_("Apply to product and/or products in categories"),
            {"fields": ("products", "categories")}),
        (_("Reduce unit price by"),
            {"fields": (("discount_deduct", "discount_percent",
            "discount_exact"),)}),
        (_("Sale period"), {"fields": (("valid_from", "valid_to"),)}),
    )


class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = ("title", "active", "code", "discount_deduct",
        "discount_percent", "min_purchase", "free_shipping", "valid_from",
        "valid_to")
    list_editable = ("active", "code", "discount_deduct", "discount_percent",
        "min_purchase", "free_shipping", "valid_from", "valid_to")
    filter_horizontal = ("categories", "products")
    formfield_overrides = {MoneyField: {"widget": MoneyWidget}}
    form = DiscountAdminForm
    fieldsets = (
        (None, {"fields": ("title", "active", "code")}),
        (_("Apply to product and/or products in categories"),
            {"fields": ("products", "categories")}),
        (_("Reduce unit price by"),
            {"fields": (("discount_deduct", "discount_percent"),)}),
        (None, {"fields": (("min_purchase", "free_shipping"),)}),
        (_("Valid for"), {"fields": (("valid_from", "valid_to"),)}),
    )

MULTI_CURRENCY_FIELDS = []
if hasattr(settings, 'STORE_CONFIGS'):
    for currency in settings.STORE_CONFIGS:
        MULTI_CURRENCY_FIELDS += [
            '_title_{}'.format(currency.lower()),
            '_bundled_unit_price_{}'.format(currency.lower()),
            '_upsell_message_{}'.format(currency.lower()),
            '_applied_message_{}'.format(currency.lower()),
            '_applied_upsell_message_{}'.format(currency.lower()),
        ]
else:
    MULTI_CURRENCY_FIELDS.extend([
        'bundled_unit_price',
        'upsell_message',
        'applied_message',
        'applied_upsell_message',
    ])


class VerboseManyToManyRawIdWidget(ManyToManyRawIdWidget):
    def label_for_value(self, value):
        values = value.split(',')
        str_values = []
        key = self.rel.get_related_field().name
        i = 1
        for product in Product.objects.filter(id__in=values).order_by('title'):
            str_values += ['<div style="display: inline-block; width:250px">%s '
                           '<a href="" data-id="%s">[X]</a></div>' %
                           (product, product.id)]
            if i % 3 == 0:
                str_values += ['<br>']
            i += 1
        return u'<div id="selected_products">%s</div>' % (''.join(str_values))

class BundleDiscountAdmin(admin.ModelAdmin):
    list_display = ["title", "active", "quantity", "upsell_product"] + MULTI_CURRENCY_FIELDS + \
        ["valid_from", "valid_to"]
    list_editable = ("active", "valid_from", "valid_to")
    model = BundleDiscount
    filter_horizontal = ("categories", "products")
    formfield_overrides = {MoneyField: {"widget": MoneyWidget}}
    fieldsets = (
        (None, {"fields": ["title", "active", "quantity", "upsell_product"] + MULTI_CURRENCY_FIELDS}),
        (_("Apply to product and/or products in categories"),
            {"fields": ("products", "categories")}),
        (_("Valid for"), {"fields": (("valid_from", "valid_to"),)}),
    )
    raw_id_fields = ('products', )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        x = super(BundleDiscountAdmin,
                  self).formfield_for_manytomany(db_field, request, **kwargs)
        if db_field.name in self.raw_id_fields:
            db = kwargs.get('using')
            x.widget = VerboseManyToManyRawIdWidget(
                db_field.rel, self.admin_site, using=db)
        return x

    def save_related(self, request, form, formsets, change):
        """
        By applying the bundle discount here, it means that the many to many
        fields for products and categories will have been properly saved, which
        is what we need.
        """
        super(BundleDiscountAdmin, self).save_related(request, form, formsets,
                                                      change)
        form.instance.apply()


admin.site.register(Category, CategoryAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductOption, ProductOptionAdmin)
admin.site.register(Order, OrderAdmin)
admin.site.register(Sale, SaleAdmin)
admin.site.register(DiscountCode, DiscountCodeAdmin)
admin.site.register(BundleDiscount, BundleDiscountAdmin)
