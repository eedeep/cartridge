
from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Manager, Q, F
from django.utils.datastructures import SortedDict
from django.core.exceptions import ObjectDoesNotExist

from mezzanine.conf import settings

import logging
logger = logging.getLogger("cottonon")

class CartManager(Manager):

    def from_request(self, request):
        """
        Return a cart by ID stored in the session, creating it if not
        found as well as removing old carts prior to creating a new
        cart.
        """
        expiry_minutes = timedelta(minutes=settings.SHOP_CART_EXPIRY_MINUTES)
        expiry_time = datetime.now() - expiry_minutes
        create_cart = False
        try:
            cart_id = request.session.get("cart", None)
            cart = self.get(last_updated__gte=expiry_time, id=cart_id)
        except self.model.DoesNotExist:
            try:
                old_cart = self.get(id=cart_id)
            except self.model.DoesNotExist: #completely new cart
                pass
            else: #found an old expired cart
                try:
                    logging.warning(
                        "can not use request cart {}, it expired on {}. Expiry cut off is currently {}"\
                        .format(
                            cart_id,
                            old_cart.last_updated.strftime("%c"),
                            expiry_time.strftime("%c"),
                        )
                    )
                except ValueError:
                    pass
            self.filter(last_updated__lt=expiry_time).delete()
            cart = self.create()
            request.session["cart"] = cart.id
            create_cart = True
        else:
            if (create_cart or
                not (request.path == '/' or
                     request.path.startswith('/shop/women/') or
                     request.path.startswith('/shop/rubi/') or
                     request.path.startswith('/shop/typo/'))):
                cart.timestamp_save_only = True #provided so promotions only apply when cart changes
                cart.save()  # Update timestamp.
                cart.timestamp_save_only = False
        return cart


class OrderManager(Manager):

    def from_request(self, request):
        """
        Returns the last order made by session key. Used for
        Google Anayltics order tracking in the order complete view,
        and in tests.
        """
        orders = self.filter(key=request.session.session_key).order_by("-id")
        if orders:
            return orders[0]
        raise self.model.DoesNotExist


class ProductOptionManager(Manager):

    def as_fields(self):
        """
        Return a dict of product options as their field names and
        choices.
        """
        options = defaultdict(list)
        for option in self.all():
            options["option%s" % option.type].append(option.name)
        return options


class ProductVariationManager(Manager):

    use_for_related_fields = True

    def _empty_options_lookup(self, exclude=None):
        """
        Create a lookup dict of field__isnull for options fields.
        """
        if not exclude:
            exclude = {}
        return dict([("%s__isnull" % f.name, True)
            for f in self.model.option_fields() if f.name not in exclude])

    def create_from_options(self, options):
        """
        Create all unique variations from the selected options.
        """
        if options:
            options = SortedDict(options)
            # Build all combinations of options.
            variations = [[]]
            for values_list in options.values():
                variations = [x + [y] for x in variations for y in values_list]
            for variation in variations:
                # Lookup unspecified options as null to ensure a
                # unique filter.
                variation = dict(zip(options.keys(), variation))

                # Explicitly specify SKU, if any options available
                # Since the SKU is the primary key, that overwrites it
                option = None
                # XXX: Refactor for handling sku creation with colour option.
                for option in ('option1', 'option2'):
                    if option in variation:
                        from cartridge.shop.models import Product
                        product = Product.objects.get(id=self.core_filters['product__id'])
                        if product.master_item_code:
                            variation['sku'] = '%s-%s' % (product.master_item_code, variation[option])
                            break

                lookup = dict(variation)
                lookup.update(self._empty_options_lookup(exclude=variation))
                try:
                    self.get(**lookup)
                except self.model.DoesNotExist:
                    self.create(**variation)

    def manage_empty(self):
        """
        Create an empty variation (no options) if none exist,
        otherwise if multiple variations exist ensure there is no
        redundant empty variation. Also ensure there is at least one
        default variation.
        """
        total_variations = self.count()
        if total_variations == 0:
            self.create()
        elif total_variations > 1:
            self.filter(**self._empty_options_lookup()).delete()
        try:
            self.get(default=True)
        except self.model.DoesNotExist:
            first_variation = self.all()[0]
            first_variation.default = True
            first_variation.save()

    def set_default_images(self, deleted_image_ids):
        """
        Assign the first image for the product to each variation that
        doesn't have an image. Also remove any images that have been
        deleted via the admin to avoid invalid image selections.
        """
        variations = self.all()
        if not variations:
            return
        image = variations[0].product.images.exclude(id__in=deleted_image_ids)
        if image:
            image = image[0]
        for variation in variations:
            save = False
            if unicode(variation.image_id) in deleted_image_ids:
                variation.image = None
                save = True
            if image and not variation.image:
                variation.image = image
                save = True
            if save:
                variation.save()

    def get_by_sku(self, sku):
        """
        Given the sku is compiled from the ``master_item_code`` and the options
        This manager will get a Variation by ``master_item_code``-``style``-``size``
        as a string. Mostly a response to replacing the sku with a property and allowing
        a drop in replacement for get by ``sku``
        """
        mCode, style, size = sku.split("-")
        if not mCode or not style or not size:
            raise ObjectDoesNotExist
        return self.get(product__master_item_code=mCode, option1=style, option2=size)


class ProductActionManager(Manager):

    use_for_related_fields = True

    def _action_for_field(self, field):
        """
        Increases the given field by datetime.today().toordinal()
        which provides a time scaling value we can order by to
        determine popularity over time.
        """
        timestamp = datetime.today().toordinal()
        action, created = self.get_or_create(timestamp=timestamp)
        setattr(action, field, getattr(action, field) + 1)
        action.save()

    def added_to_cart(self):
        """
        Increase total_cart when product is added to cart.
        """
        self._action_for_field("total_cart")

    def purchased(self):
        """
        Increase total_purchased when product is purchased.
        """
        self._action_for_field("total_purchase")


class DiscountCodeManager(Manager):

    def active(self, *args, **kwargs):
        """
        Items flagged as active and in valid date range if date(s) are
        specified.
        """
        now = datetime.now()
        valid_from = Q(valid_from__isnull=True) | Q(valid_from__lte=now)
        valid_to = Q(valid_to__isnull=True) | Q(valid_to__gte=now)
        return self.filter(valid_from, valid_to, active=True)

    def get_valid(self, code, cart, currency):
        """
        Items flagged as active and within date range as well checking
        that the given cart contains items that the code is valid for.

        MB - Now supports categories & products
        DP - Also supports validation of number of times used
        """
        total_price_valid = (
            Q(**{'_min_purchase_{}__isnull'.format(currency.lower()): True}) |
            Q(**{'_min_purchase_{}__lte'.format(currency.lower()): cart.total_price()})
        )
        usages_remaining_valid = (
            Q(allowed_no_of_uses=0) |
            Q(no_of_times_used__lt=F('allowed_no_of_uses'))
        )

        discount = self.active().get(total_price_valid, usages_remaining_valid, code=code)

        # If no products or categories are set them assume the discount is
        # store wide.
        if discount.products.all().count() == 0 and \
                discount.categories.all().count() == 0:
            return discount

        discount_categories = discount.categories.all()
        skus = [item.sku for item in cart]
        # XXX: Required import as managers
        from models import Product
        cart_products = Product.objects.filter(variations__sku__in=skus)
        valid_categories = cart_products.filter(categories__in=discount_categories)

        # This does a SQL INTERSECT operation on the products on the discount code and
        # the products in their cart. If any results, then it's a valid discount code.
        valid_products = Product.objects.filter(variations__sku__in=skus) & discount.products.all()

        # So basically here we're confirming that the punter has at least 1 product
        # in their cart which is valid for the discount code's product or category restrictions
        # If so then it's a valid code that will only be applied to those products
        # (see DiscountCode.calculate_cart() for how that happens)
        if valid_products.count() == 0 and valid_categories.count() == 0:
            raise self.model.DoesNotExist
        return discount

class CategoryPageImageManager(Manager):
    """
    Provides filter for restricting items returned by status and
    publish date when the given user is not a staff member.
    """

    def active(self):
        return self.filter(
            Q(publish_date__lte=datetime.now()) | Q(publish_date__isnull=True),
            Q(expiry_date__gte=datetime.now()) | Q(expiry_date__isnull=True),
            Q(active=True)
        )
