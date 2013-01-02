import unittest
from itertools import repeat

from datetime import datetime, timedelta
from decimal import Decimal
from operator import mul

from django.core.urlresolvers import reverse
from django.contrib.sites.models import Site
from django.test import TestCase
from django.test.utils import override_settings
from mezzanine.conf import settings
from mezzanine.core.models import CONTENT_STATUS_PUBLISHED
from mezzanine.utils.tests import run_pyflakes_for_package
from mezzanine.utils.tests import run_pep8_for_package

from cartridge.shop.models import Product, ProductOption, ProductVariation
from cartridge.shop.models import Category, Cart, Order, DiscountCode, BundleDiscount
from cartridge.shop.checkout import CHECKOUT_STEPS
from multicurrency.models import MultiCurrencyCart

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
        # create products
        for option_type in settings.SHOP_OPTION_TYPE_CHOICES:
            for i in range(10):
                name = "test%s" % i
                ProductOption.objects.create(type=option_type[0], name=name)
        products = dict(
            fp1=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 1,
                   '_unit_price_aud': Decimal('12'),}),
            fp2=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 3,
                   '_unit_price_aud': Decimal('20'),}),
            fp3=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 4,
                   '_unit_price_aud': Decimal('12'),}),
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
                dict(bundle=bundle,
                     products=[products['fp1'], products['fp3']],
                     total=Decimal('20')),
                dict(bundle=bundle,
                     products=[products['fp1'], ],
                     total=Decimal('12')),],
            mix_products=[
                dict(bundle=bundle,
                     products=[products['fp1'], products['md'], ],
                     total=Decimal('21')),
                dict(bundle=bundle,
                     products=[products['md'], ],
                     total=Decimal('9')),],
            categories=[
                dict(bundle=bundle2,
                     products=[products['fp3'], products['fp3'], ],
                     total=Decimal('20')),
                dict(bundle=bundle2,
                     products=[products['fp3'], products['fp2'], ],
                     total=Decimal('32')),
                dict(bundle=bundle2,
                     products=[products['fp3'], ],
                     total=Decimal('12')),],
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
            fp=Product.objects.create(
                **{"status": CONTENT_STATUS_PUBLISHED,
                   'master_item_code': 1,
                   '_unit_price_aud': Decimal('12'),}),
            md=Product.objects.create(
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
        discount_exact = DiscountCode.objects.create(code='e1',
                                                     _discount_exact_aud=Decimal('10'),
                                                     _min_purchase_aud=Decimal('10'))

        # test scenarios
        discounts = dict(
            percent=[dict(discount=discount_percent,
                          products=[products['fp']],
                          total=Decimal('8.4')),
                     dict(discount=discount_percent,
                          products=[products['md']],
                          total=Decimal('9')),
                     dict(discount=discount_percent,
                          products=[products['md'], products['fp']],
                          total=Decimal('9') + Decimal('8.4'))],
            deduct=[dict(discount=discount_deduct,
                         products=[products['fp']],
                         total=Decimal('2')),
                    dict(discount=discount_deduct,
                         products=[products['md'], products['md'], ],
                         total=Decimal('18')),
                    dict(discount=discount_deduct,
                         products=[products['md'], products['fp']],
                         total=Decimal('9') + Decimal('2'))],
            exact=[dict(discount=discount_exact,
                         products=[products['fp']],
                         total=Decimal('2')),
                   dict(discount=discount_exact,
                        products=[products['md'], products['md']],
                        total=Decimal('8')),
                   dict(discount=discount_exact,
                        products=[products['md'], products['fp']],
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
        for scenarios in self.discounts.values():
            for scenario in scenarios:
                for product in scenario['products']:
                    cart.add_item(product.variations.all()[0], 1)
                discount = cart.calculate_discount(scenario['discount'], 'aud')
                total = cart.total_price()
                self.assertEqual(total - discount, scenario['total'])
                # clear cart
                for item in cart.items.all():
                    item.delete()
