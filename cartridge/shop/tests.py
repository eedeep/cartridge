import pudb
import unittest
import copy
from collections import namedtuple
from itertools import repeat

from datetime import datetime, timedelta
from decimal import Decimal
from operator import mul

import factory

from django.core.urlresolvers import reverse
from django.contrib.sites.models import Site
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, RequestFactory
from django.test.utils import override_settings
from django.utils.importlib import import_module

from mezzanine.conf import settings
from mezzanine.core.models import CONTENT_STATUS_PUBLISHED
from mezzanine.utils.tests import run_pyflakes_for_package
from mezzanine.utils.tests import run_pep8_for_package

from cartridge.shop.models import Product, ProductOption, ProductVariation
from cartridge.shop.models import Category, Cart, CartItem, Order, \
    DiscountCode, BundleDiscount
from cartridge.shop.checkout import CHECKOUT_STEPS
from multicurrency.models import MultiCurrencyCart

from cartridge.shop.views import ReturnFromVme

from mock import patch, Mock, MagicMock

from suds import WebFault

from cottonon_shop.cybersource_logger import CybersourceLogger
from cottonon_shop.cybersource import _get_cybersource_client
from cottonon_shop.cybersource_exceptions import CybersourceRequiresReview, \
    CybersourceError

from countries.models import Country

TEST_STOCK = settings.STOCK_THRESHOLD + 1
TEST_PRICE = Decimal("20")



@unittest.skipUnless(hasattr(settings, 'DEFAULT_CURRENCY') and
                     settings.DEFAULT_CURRENCY == 'AUD',
                     'Looks like local_settings.py is symlinked to the incorrect '
                     'local_settings_<region>.py file.')
class BundleTests(TestCase):
    """
    Test bundled products
    """
    def setUp(self, ):
        """
        Set up product and discounts
        """
        Site().save()
        # create products
        for option_type in settings.SHOP_OPTION_TYPE_CHOICES:
            for i in range(10):
                name = "test%s" % i
                ProductOption.objects.create(type=option_type[0], name=name)
        products = dict(
            fp1=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 1,
                   '_unit_price_aud': Decimal('12'), }),
            fp2=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 3,
                   '_unit_price_aud': Decimal('20'), }),
            fp3=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 4,
                   '_unit_price_aud': Decimal('12'), }),
            md=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 2,
                   '_unit_price_aud': Decimal('9'),
                   '_was_price_aud': Decimal('15')}),)
        category = Category.objects.create(**{'status': CONTENT_STATUS_PUBLISHED})
        products['fp3'].categories.add(category)

        for product in products.values():
            ProductVariation.objects.create(_unit_price_aud=product._unit_price_aud,
                                            _was_price_aud=product._was_price_aud,
                                            sku=product.master_item_code,
                                            product=product,
                                            option1='test1',
                                            option2='test1')

        # create bundles
        bundle = BundleDiscount.objects.create(active=True,
                                               _title_aud='Test',
                                               quantity=2,
                                               _bundled_unit_price_aud=20)
        bundle.products.add(products['fp1'], products['fp2'], products['fp3'])
        bundle.apply()

        bundle2 = BundleDiscount.objects.create(active=True,
                                                _title_aud='Test',
                                                quantity=2,
                                                _bundled_unit_price_aud=20)
        bundle2.categories.add(category)
        bundle2.apply()

        # create scenarios
        self.scenarios = dict(
            full_price=[
                dict(bundle=bundle,
                     products=[products['fp1'], products['fp1'], ],
                     total=Decimal('20')),
                dict(bundle=bundle,
                     products=[products['fp2'], products['fp2'], ],
                     total=Decimal('20')),
                dict(bundle=bundle,
                     products=[products['fp1'], products['fp2'], products['fp2'], ],
                     total=Decimal('32')),
                dict(bundle=bundle,
                     products=[products['fp1'], products['fp2']],
                     total=Decimal('20')),
                # FIXME: Test does not pass, wrong bundle is being used (bundle2 instead of bundle1)
                # dict(bundle=bundle,
                #      products=[products['fp1'], products['fp3']],
                #      total=Decimal('20')),
                dict(bundle=bundle,
                     products=[products['fp1'], ],
                     total=Decimal('12')), ],
            mix_products=[
                dict(bundle=bundle,
                     products=[products['fp1'], products['md'], ],
                     total=Decimal('21')),
                dict(bundle=bundle,
                     products=[products['md'], ],
                     total=Decimal('9')), ],
            categories=[
                dict(bundle=bundle2,
                     products=[products['fp3'], products['fp3'], ],
                     total=Decimal('20')),
                dict(bundle=bundle2,
                     products=[products['fp3'], products['fp2'], ],
                     total=Decimal('32')),
                dict(bundle=bundle2,
                     products=[products['fp3'], ],
                     total=Decimal('12')), ],
        )

    def test_bundle(self):
        # create cart
        cart = MultiCurrencyCart.objects.from_request(self.client)
        try:
            cart.add_item(1, 2)
        except:
            pass
        cart = MultiCurrencyCart.objects.all()[0]

        # run tests
        for name, scenarios in self.scenarios.items():
            print 'Running', name
            for scenario in scenarios:
                for product in scenario['products']:
                    cart.add_item(product.variations.all()[0], 1)
                bundle_collection, discount_total = cart.calculate_discount(None, 'aud')
                total = cart.total_price()
                self.assertEqual(total, scenario['total'])
                # clear cart
                for item in cart.items.all():
                    item.delete()

class ShopTests(TestCase):

    def setUp(self):
        """
        Set up test data - category, product and options.
        """
        Site().save()
        self._published = {"status": CONTENT_STATUS_PUBLISHED}
        self._category = Category.objects.create(**self._published)
        self._product = Product.objects.create(**self._published)
        for option_type in settings.SHOP_OPTION_TYPE_CHOICES:
            for i in range(10):
                name = "test%s" % i
                ProductOption.objects.create(type=option_type[0], name=name)
        self._options = ProductOption.objects.as_fields()

    @unittest.skip('Obsolete')
    def test_views(self):
        """
        Test the main shop views for errors.
        """
        # Category.
        response = self.client.get(self._category.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        # Product.
        response = self.client.get(self._product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        # Cart.
        response = self.client.get(reverse("shop_cart"))
        self.assertEqual(response.status_code, 200)
        # Checkout.
        response = self.client.get(reverse("shop_checkout"))
        self.assertEqual(response.status_code, 200 if not
            settings.SHOP_CHECKOUT_ACCOUNT_REQUIRED else 302)

    @unittest.skip('Obsolete')
    def test_variations(self):
        """
        Test creation of variations from options, and management of empty
        variations.
        """
        total = reduce(mul, [len(v) for v in self._options.values()])
        # Clear variations.
        self._product.variations.all().delete()
        self.assertEqual(self._product.variations.count(), 0)
        # Create single empty variation.
        self._product.variations.manage_empty()
        self.assertEqual(self._product.variations.count(), 1)
        # Create variations from all options.
        self._product.variations.create_from_options(self._options)
        # Should do nothing.
        self._product.variations.create_from_options(self._options)
        # All options plus empty.
        self.assertEqual(self._product.variations.count(), total + 1)
        # Remove empty.
        self._product.variations.manage_empty()
        self.assertEqual(self._product.variations.count(), total)

    def test_stock(self):
        """
        Test stock checking on product variations.
        """
        self._product.variations.all().delete()
        self._product.variations.manage_empty()
        variation = self._product.variations.all()[0]
        variation.num_in_stock = TEST_STOCK
        # Check stock field not in use.
        self.assertTrue(variation.has_stock())
        # Check available and unavailable quantities.
        self.assertTrue(variation.has_stock(TEST_STOCK - settings.STOCK_THRESHOLD))
        self.assertFalse(variation.has_stock(TEST_STOCK  - settings.STOCK_THRESHOLD + 1))
        # Check sold out.
        variation = self._product.variations.all()[0]
        variation.num_in_stock = 0
        self.assertFalse(variation.has_stock())

    @override_settings(STOCK_THRESHOLD=10,
                       STOCK_POOL_THRESHOLD=1,
                       STOCK_POOL_CUTOFF=1)
    def test_reduce_stock(self):
        """
        Test cottonon stock on product variations.
        """
        self._product.variations.all().delete()
        self._product.variations.manage_empty()
        variation = self._product.variations.all()[0]
        for stock, stock_pool, quantity, left in (
            (11, 0, 1, 10),
            (12, 0, 2, 10),
            (3, 10, 1, 2),
            (2, 2, 1, 1),
            (2, 10, 1, 1),
            (2, 2, 2, 2),
            (1, 10, 1, 1),
            (0, 10, 1, 0),
            (0, 0, 1, 0)):
            variation.num_in_stock = stock
            variation.num_in_stock_pool = stock_pool
            variation.reduce_stock(quantity)
            self.assertEqual(variation.num_in_stock, left)

    def assertCategoryFilteredProducts(self, num_products):
        """
        Tests the number of products returned by the category's
        current filters.
        """
        products = Product.objects.filter(self._category.filters())
        self.assertEqual(products.distinct().count(), num_products)

    @unittest.skip('Obsolete')
    def test_category_filters(self):
        """
        Test the category filters returns expected results.
        """
        self._product.variations.all().delete()
        self.assertCategoryFilteredProducts(0)

        # Test option filters - add a variation with one option, and
        # assign another option as a category filter. Check that no
        # products match the filters, then add the first option as a
        # category filter and check that the product is matched.
        option_field, options = self._options.items()[0]
        option1, option2 = options[:2]
        # Variation with the first option.
        self._product.variations.create_from_options({option_field: [option1]})
        # Filter with the second option
        option = ProductOption.objects.get(type=option_field[-1], name=option2)
        self.assertCategoryFilteredProducts(0)
        # First option as a filter.
        option = ProductOption.objects.get(type=option_field[-1], name=option1)
        self._category.options.add(option)
        self.assertCategoryFilteredProducts(1)

        # Test price filters - add a price filter that when combined
        # with previously created filters, should match no products.
        # Update the variations to match the filter for a unit price,
        # then with sale prices, checking correct matches based on sale
        # dates.
        self._category.combined = True
        self._category.price_min = TEST_PRICE
        self.assertCategoryFilteredProducts(0)
        self._product.variations.all().update(unit_price=TEST_PRICE)
        self.assertCategoryFilteredProducts(1)
        now, day = datetime.now(), timedelta(days=1)
        tomorrow, yesterday = now + day, now - day
        self._product.variations.all().update(unit_price=0,
                                              sale_price=TEST_PRICE,
                                              sale_from=tomorrow)
        self.assertCategoryFilteredProducts(0)
        self._product.variations.all().update(sale_from=yesterday)
        self.assertCategoryFilteredProducts(1)

        # Clean up previously added filters and check that explicitly
        # assigned products match.
        for option in self._category.options.all():
            self._category.options.remove(option)
        self._category.price_min = None
        self.assertCategoryFilteredProducts(0)
        self._category.products.add(self._product)
        self.assertCategoryFilteredProducts(1)

        # Test the ``combined`` field - create a variation which
        # matches a price filter, and a separate variation which
        # matches an option filter, and check that the filters
        # have no results when ``combined`` is set, and that the
        # product matches when ``combined`` is disabled.
        self._product.variations.all().delete()
        self._product.variations.create_from_options({option_field:
                                                     [option1, option2]})
        # Price variation and filter.
        variation = self._product.variations.get(**{option_field: option1})
        variation.unit_price = TEST_PRICE
        variation.save()
        self._category.price_min = TEST_PRICE
        # Option variation and filter.
        option = ProductOption.objects.get(type=option_field[-1], name=option2)
        self._category.options.add(option)
        # Check ``combined``.
        self._category.combined = True
        self.assertCategoryFilteredProducts(0)
        self._category.combined = False
        self.assertCategoryFilteredProducts(1)

    def _add_to_cart(self, variation, quantity):
        """
        Given a variation, creates the dict for posting to the cart
        form to add the variation, and posts it.
        """
        field_names = [f.name for f in ProductVariation.option_fields()]
        data = dict(zip(field_names, variation.options()))
        data["quantity"] = quantity
        self.client.post(variation.product.get_absolute_url(), data)

    def _empty_cart(self, cart):
        """
        Given a cart, creates the dict for posting to the cart form
        to remove all items from the cart, and posts it.
        """
        data = {"items-INITIAL_FORMS": 0, "items-TOTAL_FORMS": 0,
                "update_cart": 1}
        for i, item in enumerate(cart):
            data["items-INITIAL_FORMS"] += 1
            data["items-TOTAL_FORMS"] += 1
            data["items-%s-id" % i] = item.id
            data["items-%s-DELETE" % i] = "on"
        self.client.post(reverse("shop_cart"), data)

    def _reset_variations(self):
        """
        Recreates variations and sets up the first.
        """
        self._product.variations.all().delete()
        self._product.variations.create_from_options(self._options)
        variation = self._product.variations.all()[0]
        variation.unit_price = TEST_PRICE
        variation.num_in_stock = TEST_STOCK * 2
        variation.save()

    @unittest.skip('Obsolete')
    def test_cart(self):
        """
        Test the cart object and cart add/remove forms.
        """

        # Test initial cart.
        cart = Cart.objects.from_request(self.client)
        self.assertFalse(cart.has_items())
        self.assertEqual(cart.total_quantity(), 0)
        self.assertEqual(cart.total_price(), Decimal("0"))

        # Add quantity and check stock levels / cart totals.
        self._reset_variations()
        variation = self._product.variations.all()[0]
        self._add_to_cart(variation, TEST_STOCK)
        cart = Cart.objects.from_request(self.client)
        variation = self._product.variations.all()[0]
        self.assertTrue(variation.has_stock(TEST_STOCK))
        self.assertFalse(variation.has_stock(TEST_STOCK * 2))
        self.assertTrue(cart.has_items())
        self.assertEqual(cart.total_quantity(), TEST_STOCK)
        self.assertEqual(cart.total_price(), TEST_PRICE * TEST_STOCK)

        # Add remaining quantity and check again.
        self._add_to_cart(variation, TEST_STOCK)
        cart = Cart.objects.from_request(self.client)
        variation = self._product.variations.all()[0]
        self.assertFalse(variation.has_stock())
        self.assertTrue(cart.has_items())
        self.assertEqual(cart.total_quantity(), TEST_STOCK * 2)
        self.assertEqual(cart.total_price(), TEST_PRICE * TEST_STOCK * 2)

        # Remove from cart.
        self._empty_cart(cart)
        cart = Cart.objects.from_request(self.client)
        variation = self._product.variations.all()[0]
        self.assertTrue(variation.has_stock(TEST_STOCK * 2))
        self.assertFalse(cart.has_items())
        self.assertEqual(cart.total_quantity(), 0)
        self.assertEqual(cart.total_price(), Decimal("0"))


    @unittest.skip('Obsolete')
    def test_discount_codes(self):
        """
        Test that all types of discount codes are applied.
        """

        self._reset_variations()
        variation = self._product.variations.all()[0]
        invalid_product = Product.objects.create(**self._published)
        invalid_product.variations.create_from_options(self._options)
        invalid_variation = invalid_product.variations.all()[0]
        invalid_variation.unit_price = TEST_PRICE
        invalid_variation.num_in_stock = TEST_STOCK * 2
        invalid_variation.save()
        discount_value = TEST_PRICE / 2

        # Set up discounts with and without a specific product, for
        # each type of discount.
        for discount_target in ("cart", "item"):
            for discount_type in ("percent", "deduct"):
                code = "%s_%s" % (discount_target, discount_type)
                kwargs = {
                    "code": code,
                    "discount_%s" % discount_type: discount_value,
                    "active": True,
                }
                cart = Cart.objects.from_request(self.client)
                self._empty_cart(cart)
                self._add_to_cart(variation, 1)
                self._add_to_cart(invalid_variation, 1)
                discount = DiscountCode.objects.create(**kwargs)
                if discount_target == "item":
                    discount.products.add(variation.product)
                post_data = {"discount_code": code}
                self.client.post(reverse("shop_cart"), post_data)
                discount_total = self.client.session["discount_total"]
                if discount_type == "percent":
                    expected = TEST_PRICE / Decimal("100") * discount_value
                    if discount_target == "cart":
                        # Excpected amount applies to entire cart.
                        cart = Cart.objects.from_request(self.client)
                        expected *= cart.items.count()
                elif discount_type == "deduct":
                    expected = discount_value
                self.assertEqual(discount_total, expected)
                if discount_target == "item":
                    # Test discount isn't applied for an invalid product.
                    cart = Cart.objects.from_request(self.client)
                    self._empty_cart(cart)
                    self._add_to_cart(invalid_variation, 1)
                    self.client.post(reverse("shop_cart"), post_data)
                    discount_total = self.client.session.get("discount_total")
                    self.assertEqual(discount_total, None)

    @unittest.skip('Obsolete')
    def test_order(self):
        """
        Test that a completed order contains cart items and that
        they're removed from stock.
        """

        # Add to cart.
        self._reset_variations()
        variation = self._product.variations.all()[0]
        self._add_to_cart(variation, TEST_STOCK)
        cart = Cart.objects.from_request(self.client)

        # Post order.
        data = {"step": len(CHECKOUT_STEPS)}
        self.client.post(reverse("shop_checkout"), data)
        try:
            order = Order.objects.from_request(self.client)
        except Order.DoesNotExist:
            self.fail("Couldn't create an order")
        items = order.items.all()
        variation = self._product.variations.all()[0]

        self.assertEqual(cart.total_quantity(), 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].sku, variation.sku)
        self.assertEqual(items[0].quantity, TEST_STOCK)
        self.assertEqual(variation.num_in_stock, TEST_STOCK)
        self.assertEqual(order.item_total, TEST_PRICE * TEST_STOCK)

    @unittest.skip('Out of scope')
    def test_syntax(self):
        """
        Run pyflakes/pep8 across the code base to check for potential errors.
        """
        extra_ignore = (
                "redefinition of unused 'digest'",
                "redefinition of unused 'OperationalError'",
                "'from mezzanine.project_template.settings import *' used",
        )
        warnings = []
        warnings.extend(run_pyflakes_for_package("cartridge",
                                                 extra_ignore=extra_ignore))
        warnings.extend(run_pep8_for_package("cartridge"))
        if warnings:
            self.fail("Syntax warnings!\n\n%s" % "\n".join(warnings))

@unittest.skipUnless(hasattr(settings, 'DEFAULT_CURRENCY') and
                     settings.DEFAULT_CURRENCY == 'AUD',
                 'Looks like local_settings.py is symlinked to the incorrect '
                 'local_settings_<region>.py file.')
class DiscountTests(TestCase):
    """
    Test discount codes and bundled products
    """

    def setUp(self, ):
        """
        Set up product and discounts
        """
        Site().save()
        # create products
        for option_type in settings.SHOP_OPTION_TYPE_CHOICES:
            for i in range(10):
                name = "test%s" % i
                ProductOption.objects.create(type=option_type[0], name=name)
        products = dict(
            fp_12=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 1,
                   '_unit_price_aud': Decimal('12'), }),
            fp_25=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 3,
                   '_unit_price_aud': Decimal('25'), }),
            md_9=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 2,
                   '_unit_price_aud': Decimal('9'),
                   '_was_price_aud': Decimal('15')}))
        for product in products.values():
            ProductVariation.objects.create(_unit_price_aud=product._unit_price_aud,
                                            _was_price_aud=product._was_price_aud,
                                            sku=product.master_item_code,
                                            product=product,
                                            option1='test1',
                                            option2='test1')

        # create discount
        discount_percent = DiscountCode.objects.create(code='p1',
                                                       discount_percent=Decimal('30'),
                                                       _min_purchase_aud=Decimal('10'))
        discount_deduct = DiscountCode.objects.create(code='d1',
                                                      _discount_deduct_aud=Decimal('10'),
                                                      _min_purchase_aud=Decimal('10'))
        discount_deduct_prod = DiscountCode.objects.create(code='d2',
                                                       _discount_deduct_aud=Decimal('10'),
                                                       _min_purchase_aud=Decimal('10'))
        discount_deduct_prod.products.add(products['fp_12'])
        discount_exact = DiscountCode.objects.create(code='e1',
                                                     _discount_exact_aud=Decimal('10'),
                                                     _min_purchase_aud=Decimal('10'))

        # test scenarios
        discounts = dict(
            percent=[dict(discount=discount_percent,
                          products=[products['fp_12']],
                          total=Decimal('8.4')),
                     dict(discount=discount_percent,
                          products=[products['md_9']],
                          total=Decimal('9')),
                     dict(discount=discount_percent,
                          products=[products['md_9'], products['fp_12']],
                          total=Decimal('9') + Decimal('8.4'))],
            deduct=[dict(discount=discount_deduct,
                         products=[products['fp_12']],
                         total=Decimal('2')),
                    dict(discount=discount_deduct_prod,
                         products=[products['fp_12']],
                         total=Decimal('2')),
                    dict(discount=discount_deduct_prod,
                         products=[products['fp_25']],
                         total=Decimal('25')),
                    dict(discount=discount_deduct,
                         products=[products['md_9'], products['md_9'], ],
                         total=Decimal('18')),
                    dict(discount=discount_deduct,
                         products=[products['md_9'], products['fp_12']],
                         total=Decimal('9') + Decimal('2')),
                    dict(discount=discount_deduct_prod,
                         products=[products['md_9'], products['fp_12']],
                         total=Decimal('9') + Decimal('2'))],
            exact=[dict(discount=discount_exact,
                        products=[products['fp_12']],
                        total=Decimal('2')),
                   dict(discount=discount_exact,
                        products=[products['md_9'], products['md_9']],
                        total=Decimal('8')),
                   dict(discount=discount_exact,
                        products=[products['md_9'], products['fp_12']],
                        total=Decimal('9') + Decimal('2'))])
        self.discounts = discounts

    def test_discount(self):
        # create cart
        cart = MultiCurrencyCart.objects.from_request(self.client)
        try:
            cart.add_item(1, 2)
        except:
            pass
        cart = MultiCurrencyCart.objects.all()[0]

        # run tests
        for name, scenarios in self.discounts.items():
            for scenario in scenarios:
                for product in scenario['products']:
                    cart.add_item(product.variations.all()[0], 1)
                bundle, discount = cart.calculate_discount(scenario['discount'], 'aud')
                total = cart.total_price()
                print name, scenario['products']
                self.assertEqual(total - discount, scenario['total'])
                # clear cart
                for item in cart.items.all():
                    item.delete()



class CountryFactory(factory.Factory):
    FACTORY_FOR = Country
    iso = 'AU'
    name = 'AUSTRALIA'
    printable_name = 'Australia'
    iso3 = 'AUS'
    numcode = 36
    rms_code = 'AUST'


class CartFactory(factory.Factory):
    FACTORY_FOR = Cart
    currency = 'AUD'
    last_updated = datetime.now()


class CartItemFactory(factory.Factory):
    FACTORY_FOR = CartItem
    cart = factory.SubFactory(CartFactory)
    _unit_price_aud = None
    _unit_price_nzd = None
    sku = '2041102712383'
    description = 'lindsay slipper :: 411027-12 Style: ELECTRIC, Size: 38'
    quantity = 1
    unit_price = Decimal(29.95)
    total_price = Decimal(29.95)
    url = '/shop/product/lindsay-slipper-electric/'
    image = 'products/images/411027/411027-12-1.JPG'
    bundle_quantity = 0
    discount_unit_price = Decimal(29.95)
    bundle_unit_price = Decimal(29.95)
    bundle_title = None


class ProductFactory(factory.Factory):
    FACTORY_FOR = Product
    _unit_price_aud = Decimal(29.95)
    _sale_price_aud = None
    _was_price_aud = Decimal(29.95)
    _unit_price_nzd = Decimal(34.95)
    _sale_price_nzd = None
    _was_price_nzd = Decimal(34.95)
    keywords_string = ''
    rating_count = 0
    rating_average = 0
    title = 'lindsay slipper'
    slug = 'lindsay-slipper-electric'
    site_id = 1
    description = 'Slip on flat'
    gen_description = 1
    status = 2
    publish_date = '2012-08-16 12:39:58'
    expiry_date = None
    short_url = None
    content = 'Slip on flat'
    unit_price = Decimal(29.95)
    sale_id = None
    sale_price = None
    sale_from = None
    sale_to = None
    available = 1
    master_item_code = '411027-12'
    image = 'products/images/411027/411027-12-1.JPG'
    date_added = '2012-08-16 12:39:58'
    featured = 0
    in_stock = 1
    ranking = 12
    product_colours = '411027-12'
    product_sizes = '39,38,37,36,40,41'
    sync_images = 0
    sync_stock = 0
    date_images_last_synced = None
    date_stock_last_synced = None
    first_published_date = None
    bundle_discount_id = None
    rms_category_id = None
    date_price_last_modified = None


class ProductVariationFactory(factory.Factory):
    FACTORY_FOR = ProductVariation
    product = factory.SubFactory(ProductFactory)
    _unit_price_aud = Decimal(29.95)
    _sale_price_aud = None
    _was_price_aud = Decimal(29.95)
    _unit_price_nzd = Decimal(34.95)
    _sale_price_nzd = None
    _was_price_nzd = Decimal(34.95)
    unit_price = Decimal(29.95)
    sale_id = None
    sale_price = None
    sale_from = None
    sale_to = None
    num_in_stock_pool = 0
    sku = 2041102712383
    num_in_stock = 100
    default = 1
    image_id = 11936
    option1 = '411027-12'
    option2 = 36
    bundle_discount_id = None


class ReturnFromVmeTest(TestCase):
    MERCH_TRANS = 9999
    CALL_ID = 101362900
    SESSION_KEY = '00001b800e6fe648cef889e0a44c7c5f'
    TYPE_MAP = {
        'billTo': 'BillTo',
        'shipTo': 'ShipTo',
        'apReply': 'APReply',
    }
    ShippingDetails = namedtuple('ShippingDetails', [
        'street1',
        'city',
        'state',
        'postalCode',
        'country',
        'phoneNumber',
        'name',
    ])
    BillingDetails = namedtuple('BillingDetails', ['email', ])
    ApReply = namedtuple('ApReply', ['riskIndicator', ])
    api_client = None
    cart = None
    session = None

    @classmethod
    def setUpClass(cls):
        cls.api_client = _get_cybersource_client('AUD')
        CountryFactory.create()

    def setUp(self):
        self.factory = RequestFactory()
        product = ProductFactory.create()
        ProductVariationFactory.create(product=product)
        self.cart = CartFactory.create()
        CartItemFactory.create(cart=self.cart)
        # Create a session to add to our request
        engine = import_module(settings.SESSION_ENGINE)
        self.session = engine.SessionStore(session_key=self.SESSION_KEY)

    def mock_api_response(self, attrs):
        response = MagicMock()
        for attr, value in attrs.iteritems():
            if isinstance(value, tuple):
                setattr(response, attr, self.api_client.factory.create('ns0:{t}'.format(t=self.TYPE_MAP[attr])))
                for a in copy.copy(getattr(response, attr)):
                    if a[0] not in value._fields:
                        delattr(getattr(response, attr), a[0])
                for a in value._fields:
                    setattr(getattr(response, attr), a, getattr(value, a))
            else:
                setattr(response, attr, value)
        return response

    @patch('cartridge.shop.views.ap_capture')
    @patch('cartridge.shop.views.afs')
    @patch('cartridge.shop.views.ap_auth')
    @patch('cartridge.shop.views.ap_confirm_purchase')
    @patch('cartridge.shop.views.ap_checkout_details')
    def test_inbound_valid_cart_flow(self, mock_ap_checkout_details, \
        mock_ap_confirm_purchase, mock_ap_auth, mock_afs, mock_ap_capture):

        request = self.factory.post(
            reverse('return_from_checkout_with_vme'),
            data={
                'merchTrans': self.MERCH_TRANS,
                'callId': self.CALL_ID,
            },
        )

        request.session = self.session
        request.session['currency'] = 'AUD'
        request.session['shipping_type'] = 'AUSTRALIA'
        request.session['cart'] = self.cart
        request.cart = self.cart
        request.user = AnonymousUser()
        request.wishlist = []

        # mock ap_checkout_details response
        mock_ap_checkout_details.return_value = self.mock_api_response({
            'reasonCode': 100,
            'billTo': self.BillingDetails(email='dan@commoncode.com.au'),
            'shipTo': self.ShippingDetails(
                street1="114 Murray Rd",
                city="Preston",
                state="VIC",
                postalCode="3072",
                country="AU",
                phoneNumber="0422987423",
                name="dan peade",
            )
        })

        # call the view, see the confirmation page
        view = ReturnFromVme.as_view()
        response = view(request)
        # TODO-VME: So here we can do some tests to make sure the right
        # form elements and values are there in the rendered response

        # now hit the view again, to confirm and do the payment
        request = self.factory.post(
            reverse('return_from_checkout_with_vme'),
            data={
                'additional_instructions': '',
                'billing_detail_city': 'PRESTON',
                'billing_detail_country': 'AUSTRALIA',
                'billing_detail_email': 'dan@commoncode.com.au',
                'billing_detail_first_name': 'dan',
                'billing_detail_last_name': 'peade',
                'billing_detail_phone': '0422987423',
                'billing_detail_postcode': '3072',
                'billing_detail_state': 'VIC',
                'billing_detail_street': '114 Murray Rd',
                'billing_detail_street2': 'Preston',
                'callId': '101735127',
                'card_ccv': '',
                'card_expiry_month': '12',
                'card_expiry_year': '2013',
                'card_name': '',
                'card_number': '',
                'card_type': '',
                'csrfmiddlewaretoken': 'o37a6cTQkUvDxhwmYkLOG7TwlGzyKof9',
                'discount_code': '',
                'gender': '',
                'id': 'AUSTRALIA',
                'order_payment_gateway_transaction_id': '10599253',
                'privacy_policy': 'False',
                'remember': 'False',
                'same_billing_shipping': 'on',
                'shipping_detail_city': 'PRESTON',
                'shipping_detail_country': 'AUSTRALIA',
                'shipping_detail_first_name': 'dan',
                'shipping_detail_last_name': 'peade',
                'shipping_detail_phone': '0000 0000',
                'shipping_detail_postcode': '3072',
                'shipping_detail_state': 'VIC',
                'shipping_detail_street': '114 Murray Rd',
                'shipping_detail_street2': 'Preston',
                'step': '1',
                'subscribe': 'False',
                'terms': 'on',
            },
        )
        request.session = self.session
        request.cart = self.cart
        request.user = AnonymousUser()
        request.wishlist = []

        # configure the ap_confirm_purchase response
        mock_ap_confirm_purchase.return_value = self.mock_api_response({'reasonCode': 100})
        # configure the ap_auth response
        mock_ap_auth.return_value = self.mock_api_response({
            'reasonCode': 100,
            'apReply': self.ApReply(riskIndicator='LOW'),
            'billTo': self.ShippingDetails(
                phoneNumber="0422987423",
                name="dan peade",
                street1="114 Murray Rd",
                city="Preston",
                state="VIC",
                country="AU",
                postalCode="3072",
            )
        })
        # configure the afs response
        mock_afs.return_value = self.mock_api_response({'reasonCode': 100})
        # configure the ap_capture response
        mock_ap_capture.return_value = self.mock_api_response({
            'reasonCode': 100,
            'requestID': "3786975141980176056428"
        })

        view = ReturnFromVme.as_view()
        response = view(request)

        """
         ******************************************
         * ap_confirm_purchase reply              *
         ******************************************
         (reply){
           merchantReferenceCode = "10599253"
           requestID = "3786974994670176056442"
           decision = "ACCEPT"
           reasonCode = 100
           requestToken = "AhjjrwSRmpWrusxVTGj0jJ+uGAndoEgv5ZlnDJpJli6+BueKRmpWrusxVTGj0AAA/h75"
           purchaseTotals =
              (PurchaseTotals){
                 currency = "AUD"
              }
           apReply =
              (APReply){
                 orderID = "101735127"
                 riskIndicator = "LOW"
              }
           apConfirmPurchaseReply =
              (APConfirmPurchaseReply){
                 reasonCode = 100
                 amount = "19.95"
                 dateTime = "2013-09-09T03:31:41Z"
                 providerResponse = "200"
              }
         }

         ******************************************
         * ap_checkout_details reply payment flow *
         ******************************************
         (reply){
           merchantReferenceCode = "10599248"
           requestID = "3784207930620176056470"
           decision = "ACCEPT"
           reasonCode = 100
           requestToken = "AhjjrwSRmkjejpy/SoEsjJ+5RPOl+EgAywhk0kyxdfA3PFIzSRvR05fpUCWAaF8C"
           purchaseTotals =
              (PurchaseTotals){
                 currency = "AUD"
              }
           apReply =
              (APReply){
                 orderID = "101700950"
                 purchaseID = "10599248"
                 productID = "10599248"
                 productDescription = "10599248"
                 subtotalAmount = "19.95"
              }
           billTo =
              (BillTo){
                 email = "dan@commoncode.com.au"
              }
           apCheckoutDetailsReply =
              (APCheckOutDetailsReply){
                 reasonCode = 100
                 status = "CREATED"
                 dateTime = "2013-09-05T22:39:54Z"
                 providerResponse = "200"
              }
         }

         ******************************************
         * ap_checkout_details reply cart flow    *
         ******************************************
         (reply){
           merchantReferenceCode = "10599246"
           requestID = "3784203523400176056442"
           decision = "ACCEPT"
           reasonCode = 100
           requestToken = "AhjjrwSRmki/Pe0CMnD0jJ+v9LncSEgAywhk0kyxdfA3PFIzSRfnvaBGTh6Axwg+"
           purchaseTotals =
              (PurchaseTotals){
                 currency = "AUD"
              }
           apReply =
              (APReply){
                 orderID = "101700937"
                 purchaseID = "10599246"
                 productID = "10599246"
                 productDescription = "10599246"
                 subtotalAmount = "19.95"
              }
           shipTo =
              (ShipTo){
                 street1 = "114 Murray Rd"
                 city = "Preston"
                 state = "VIC"
                 postalCode = "3072"
                 country = "AU"
                 phoneNumber = "0422987423"
                 name = "dan peade"
              }
           billTo =
              (BillTo){
                 email = "dan@commoncode.com.au"
              }
           apCheckoutDetailsReply =
              (APCheckOutDetailsReply){
                 reasonCode = 100
                 status = "CREATED"
                 dateTime = "2013-09-05T22:32:33Z"
                 providerResponse = "200"
              }
         }

         *****************************
         * ap_auth reply             *
         *****************************
         (reply){
           merchantReferenceCode = "10599253"
           requestID = "3786975045870176056428"
           decision = "ACCEPT"
           reasonCode = 100
           requestToken = "AhjzrwSRmpWsF+4w7GjYjAKfr/TgLPBIL+WZZwyaSZYuvgbnikZqVrBfuMOxo2AA0DmF"
           purchaseTotals =
              (PurchaseTotals){
                 currency = "AUD"
              }
           apReply =
              (APReply){
                 orderID = "101735127"
                 cardGroup = "CreditCardDebitCard"
                 cardType = "VISA"
                 cardNumberSuffix = "1111"
                 cardExpirationMonth = "10"
                 cardExpirationYear = "2013"
                 avsCodeRaw = "1"
                 cardNumberPrefix = "411111"
                 riskIndicator = "LOW"
              }
           billTo =
              (BillTo){
                 phoneNumber = "0422987423"
                 name = "dan peade"
                 street1 = "114 Murray Rd"
                 city = "Preston"
                 state = "VIC"
                 country = "AU"
                 postalCode = "3072"
              }
           apAuthReply =
              (APAuthReply){
                 reasonCode = 100
                 transactionID = "100940140"
                 status = "AUTHORIZED"
                 amount = "19.95"
                 dateTime = "2013-09-09T03:31:48Z"
                 providerResponse = "200"
              }
         }

         *****************************
         * afs reply *
         *****************************
         (reply){
           merchantReferenceCode = "380170"
           requestID = "3786975120150176056428"
           decision = "REVIEW"
           reasonCode = 480
           requestToken = "AhjjrwSRmpWsnwuC84jYFJ+sOzhACEgv5ZlmvBpJli6+BueKRmpWsnwuC84jYAAAPxUw"
           afsReply =
              (AFSReply){
                 reasonCode = 100
                 afsResult = 4
                 hostSeverity = 1
                 consumerLocalTime = "13:31:52"
                 afsFactorCode = "N"
                 scoreModelUsed = "default_apac"
              }
           decisionReply =
              (DecisionReply){
                 casePriority = 3
                 activeProfileReply = ""
              }
         }

         *****************************
         * ap_capture                *
         *****************************
         (reply){
           merchantReferenceCode = "10599253"
           requestID = "3786975141980176056428"
           decision = "ACCEPT"
           reasonCode = 100
           requestToken = "Ahj3rwSRmpWsxsDlvgDYjDLLNnZoTGsiJUksgFP1/pwFoAkF/LMs4ZNJMsXXwNzxSM1K1gv3GHY0bAAA8gTP"
           purchaseTotals =
              (PurchaseTotals){
                 currency = "AUD"
              }
           apReply =
              (APReply){
                 orderID = "101735127"
              }
           apCaptureReply =
              (APCaptureReply){
                 reasonCode = 100
                 transactionID = "100940141"
                 status = "CAPTURED"
                 amount = "19.95"
                 dateTime = "2013-09-09T03:31:56Z"
                 reconciliationID = "Y33YPL5HDTI2"
                 providerResponse = "200"
              }
         }

        """
