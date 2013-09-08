import base64
import hashlib
import hmac
import itertools
import logging
import time
import re
from urllib2 import urlopen, URLError
from decimal import Decimal
from datetime import datetime

logger = logging.getLogger("cottonon")
logger_payments = logging.getLogger("payments")

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import info
from django.contrib.sessions.backends.cached_db import SessionStore
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.core.urlresolvers import get_callable, reverse, resolve, Resolver404
from django.db.models import Sum, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render_to_response
from django.template import RequestContext
from django.template.defaultfilters import slugify
from django.template.loader import get_template
from django.utils import simplejson
from django.utils.http import urlencode
from django.utils.translation import ugettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.views.generic import FormView
from django.forms.models import model_to_dict

from mezzanine.conf import settings
from mezzanine.utils.importing import import_dotted_path
from mezzanine.utils.views import render, set_cookie

from cartridge.shop import checkout
from cartridge.shop.forms import AddProductForm, DiscountForm, CartItemFormSet, ShippingForm
from cartridge.shop.models import Product, ProductVariation, Order, Cart, Category
from cartridge.shop.models import DiscountCode, BundleDiscount
from cartridge.shop.utils import recalculate_discount, sign, \
    shipping_form_for_cart, discount_form_for_cart, add_header_sameorigin
from cartridge.shop.defaults import (ORDER_UNPROCESSED,
                                     ORDER_PROCESSED,
                                     ORDER_REVIEW,
                                     ORDER_REJECTED)
from multicurrency.templatetags.multicurrency_tags import _order_totals

from countries.models import Country

# TODO remove multicurrency imports from cartridge
from multicurrency.models import MultiCurrencyProduct, MultiCurrencyProductVariation
from multicurrency.utils import \
    session_currency, default_local_freight_type, is_local_shipping_option,\
    is_valid_shipping_choice, get_freight_type_for_id
from multicurrency.templatetags.multicurrency_tags import \
    local_currency, formatted_price, _order_totals

from cottonon_shop.forms import WishlistEmailForm
from cottonon_shop.paypal_handler import \
    set_express_checkout, log_no_order_for_token, PayPalFlowError, \
    get_express_checkout_details, do_express_checkout_payment, \
    build_order_form_data, paypal_confirmation_response, get_order_from_token, \
    log_cancelled_order, PaypalApiCallException, log_no_stock_order_aborted
from cottonon_shop.cybersource import _cybersetting, CybersourceResponseException, \
    CybersourceRequiresReview, CybersourceError
from cottonon_shop.cybersource_exceptions import CybersourceResponseException, \
    CybersourceRequiresReview, CybersourceError
from cottonon_shop.vme import ap_initiate, ap_checkout_details, \
    ap_confirm_purchase, ap_auth, ap_capture, afs
from cottonon_shop.vme_handler import get_order_from_merch_trans_number, \
    vme_confirmation_response, VMeFlowError, vme_update_order_billing_details
from cottonon_shop.vme_handler import build_order_form_data as vme_build_order_form_data


# TODO remove cartwatcher imports from cartridge
try:
    from cartwatcher.promotions.models import Promotion
except ImportError:  # keep running if cartwatcher not installed
    Promotion = None

# TODO remove cottonon_shop imports from cartridge
from cottonon_shop.cybersource import Requires3DSecureVerification
from cottonon_shop.models import ThreeDSecureTransaction, \
    create_subscriber_from_dict, GPPPoint

# Set up checkout handlers.
handler = lambda s: import_dotted_path(s) if s else lambda *args: None
billship_handler = handler(settings.SHOP_HANDLER_BILLING_SHIPPING)
payment_handler = handler(settings.SHOP_HANDLER_PAYMENT)
order_handler = handler(settings.SHOP_HANDLER_ORDER)
tax_handler = handler(settings.SHOP_HANDLER_TAX)


def product(request, slug, template="shop/product.html", extends_template="base.html"):
    """
    Display a product - convert the product variations to JSON as well as
    handling adding the product to either the cart or the wishlist.
    """
    published_products = MultiCurrencyProduct.objects.published(for_user=request.user)
    product = get_object_or_404(published_products, slug=slug)
    to_cart = (request.method == "POST" and
               request.POST.get("add_wishlist") is None)
    add_product_form = AddProductForm(request.POST or None, product=product,
                                      initial={"quantity": 1}, to_cart=to_cart)
    if request.method == "POST":
        if add_product_form.is_valid():
            if to_cart:
                quantity = add_product_form.cleaned_data["quantity"]
                request.cart.add_item(add_product_form.variation, quantity)
                recalculate_discount(request)
                info(request, _("Item added to cart"))
                return redirect("shop_cart")
            else:
                skus = request.wishlist
                sku = add_product_form.variation.sku
                if sku not in skus:
                    skus.append(sku)
                info(request, _("Item added to wishlist"))
                response = redirect("shop_wishlist")
                set_cookie(response, "wishlist", ",".join(skus))
                return response
    fields = [f.name for f in ProductVariation.option_fields()]
    fields += ["sku", "image_id", "total_in_stock", 'default']

    # weed out any variations whose colour variation is totally out of stock
    in_stock_colour_codes = set(zip(*add_product_form.fields['option1'].choices)[0])
    variations = MultiCurrencyProductVariation.objects.filter(
        id__in=product.variations.all(),
        option1__in=in_stock_colour_codes,
    )

    variations_json = simplejson.dumps([dict([(f, getattr(v, f))
                                        for f in fields])
                                        for v in variations])
    currency = session_currency(request)
    item_code_parts = product.master_item_code.split('-')
    other_products = Product.objects.published().filter(
        image__isnull=False,
        in_stock=True,
        available=True,
        master_item_code__startswith=item_code_parts[0]
    ).exclude(master_item_code=product.master_item_code).values('title', 'slug', 'master_item_code')
    context = {
        'other_products': other_products,
        "product": product,
        "extends_template": extends_template,
        "variations": variations,
        "variations_json": variations_json,
        "add_product_form": add_product_form
    }

    if product.bundle_discount_id:
        try:
            bundle_discount = BundleDiscount.objects.active(currency).get(
                id=product.bundle_discount_id
            )
        except BundleDiscount.DoesNotExist:
            pass
        else:
            if not product.on_sale(currency) and \
              not product.is_marked_down(currency):
                context["bundle_title"] = getattr(
                    bundle_discount,
                    "_title_{}".format(currency.lower())
                )

    cache_key = generate_cache_key(request)
    cached_data = cache.get(cache_key, None)
    if cached_data and 'invalidate-cache' not in request.GET:
        context.update(cached_data)
    else:
        categories = product.categories.all()
        if len(categories) > 0:
            breadcrumbs = list(categories[0].get_ancestors()) + [categories[0]]
        else:
            breadcrumbs = []
        cached_context = dict(
            breadcrumbs=breadcrumbs,
            root_category=breadcrumbs[0].slug if len(breadcrumbs) > 0 else None,
            images=product.reduced_image_set(variations),
            has_available_variations=any(v.has_price(currency) for v in variations),
            keywords=','.join([unicode(x) for x in product.keywords.all()]),
            related=product.related_products.published(for_user=request.user),
            size_chart=product.size_chart)
        if len(variations) > 0:
            variation = variations[0]
            cached_context['has_price'] = variation.has_price(currency)
            cached_context['on_sale'] = variation.on_sale(currency)
            cached_context['is_marked_down'] = variation.is_marked_down(currency)
        cache.set(cache_key, cached_context, settings.CACHE_TIMEOUT['product_details'])
        context.update(cached_context)

    # Get the first promotion for this object
    if Promotion:
        # luxury TODO: show the deal's "post applied" message if cart has met requirements.
        upsell_promotions = Promotion.active.promotions_for_products(request.cart, [product])
        if upsell_promotions.count() > 0:
            context["upsell_promotion"] = upsell_promotions[0].description
    return render(request, template, context)

def generate_cache_key(request):
    ctx = hashlib.md5()
    ctx.update(
        request.get_full_path().replace('?invalidate-cache', '')
    )
    ctx.update(session_currency(request))
    return ctx.hexdigest()

def wishlist(request, template="shop/wishlist.html"):
    """
    Display the wishlist and handle removing items from the wishlist and
    adding them to the cart.
    """
    skus = request.wishlist
    error = None
    if request.method == "POST":
        to_cart = request.POST.get("add_cart")
        add_product_form = AddProductForm(request.POST or None,
                                          to_cart=to_cart)
        if to_cart:
            if add_product_form.is_valid():
                request.cart.add_item(add_product_form.variation, 1)
                recalculate_discount(request)
                message = _("Item added to cart")
                url = "shop_cart"
            else:
                error = add_product_form.errors.values()[0]
        else:
            message = _("Item removed from wishlist")
            url = "shop_wishlist"
        sku = request.POST.get("sku")
        if sku in skus:
            skus.remove(sku)
        if not error:
            info(request, message)
            response = redirect(url)
            set_cookie(response, "wishlist", ",".join(skus))
            return response

    # Remove skus from the cookie that no longer exist.

    wishlist = []
    try:
        wishlist = ProductVariation.objects.select_related(depth=1).filter(
                                                    sku__in=skus)
    except Exception:
        # Variation does not exist
        pass

    context = {"wishlist_items": wishlist, "error": error, "emailForm": WishlistEmailForm}
    response = render(request, template, context)
    if len(wishlist) < len(skus):
        skus = [variation.sku for variation in wishlist]
        set_cookie(response, "wishlist", ",".join(skus))
    return response


def _discount_data(request, discount_form):
    """
    Call _order_totals from the multicurrency template tag library to
    basically simulate what happens during normal page rendering - ie,
    the order total, discount total and shipping total all get set
    in the context and then referenced in the template code in order to be
    displayed to the user.
    """
    updated_context = _order_totals({'request': request})

    # If the user goes through to the pay & confirm page and tax
    # gets added and then they come back to the cart page and enter
    # another discount code, we need to strip the tax out of the
    # session (if it exists) and subtract that amount from the order total
    # in the context, otherwise the tax amount will show up in the total
    updated_context['order_total'] = Decimal(updated_context['order_total']) - \
        request.session.pop('tax_total', 0)

    for key, val in updated_context.items():
        if '_total' in key:
            updated_context[key] = formatted_price(request, val)

    data = {
       'error_message': ' '.join(list(itertools.chain.from_iterable(discount_form.errors.values()))),
        'discount_total': '-' + updated_context['discount_total'],
        'total_price':  updated_context['order_total'],
       'shipping_total': updated_context['shipping_total'],
    }
    return simplejson.dumps(data, cls=DjangoJSONEncoder)


@add_header_sameorigin
def cart(request, template="shop/cart.html", extends_template="base.html"):
    """
    Display cart and handle removing items from the cart.
    """
    currency = session_currency(request)
    cart_formset = CartItemFormSet(instance=request.cart)
    shipping_form = shipping_form_for_cart(request, currency)
    discount_form = discount_form_for_cart(request)
    valid = False
    if request.method == "POST":
        if request.POST.get("update_cart"):
            valid = request.cart.has_items()
            if not valid:
                info(request, _("Your cart has expired"))
            else:
                cart_formset = CartItemFormSet(request.POST,
                                               instance=request.cart)
                valid = cart_formset.is_valid()
                if valid:
                    cart_formset.save()
                    recalculate_discount(request)
                    info(request, _("Cart updated"))
        else:
            discount_valid = discount_form.is_valid()
            discount_form.set_discount()
            shipping_valid = shipping_form.is_valid()
            if shipping_valid:
                shipping_form.set_shipping()
            valid = True if discount_valid and shipping_valid else False
            if not request.is_ajax() and valid:
                if request.POST.has_key('checkout-with-paypal'):
                    return redirect(get_checkout_with_paypal_redirect_url(request))
                return redirect('shop_checkout')
        if valid:
            if request.is_ajax():
                return HttpResponse(_discount_data(request, discount_form), "application/javascript")
            else:
                if request.POST.has_key('checkout-with-paypal'):
                    return redirect(get_checkout_with_paypal_redirect_url(request))
                return redirect("shop_checkout")

    context = {"cart_formset": cart_formset}
    settings.use_editable()
    if (settings.SHOP_DISCOUNT_FIELD_IN_CART and
        len(DiscountCode.objects.active()[:1]) > 0):
        context["discount_form"] = discount_form
    context["shipping_form"] = shipping_form
    context["extends_template"] = extends_template
    context['CURRENT_REGION'] = getattr(settings, 'CURRENT_REGION', '')
    context.update(get_vme_context(request))
    context.update(dict(cart_page=True))

    if request.is_ajax():
        return HttpResponse(_discount_data(request, discount_form), "application/javascript")
    else:
        return render(request, template, context)


def finalise_order(transaction_id, request, order,
                   card_and_billing_data):
    # Finalize order - ``order.complete()`` performs
    # final cleanup of session and cart.
    # ``order_handler()`` can be defined by the
    # developer to implement custom order processing.
    # Then send the order email to the customer.
    order.transaction_id = transaction_id
    order.complete(request)
    # Set the cookie for remembering address details
    # if the "remember" checkbox was checked.
    if request.get_full_path().startswith('/cobycottonon/'):
        response = redirect("coexclusives_shop_complete")
    else:
        response = redirect("shop_complete")

    if card_and_billing_data.get('remember'):
        remembered = "%s:%s" % (sign(order.key), order.key)
        set_cookie(response, "remember", remembered,
                   secure=request.is_secure())
    else:
        response.delete_cookie("remember")

    subscribe = card_and_billing_data.get('subscribe')
    if subscribe:
        create_subscriber_from_dict({
            'First Name': card_and_billing_data['billing_detail_first_name'],
            'Last Name': card_and_billing_data['billing_detail_last_name'],
            'Email': card_and_billing_data['billing_detail_email'],
            'Gender': card_and_billing_data['gender'],
            'Country': card_and_billing_data['billing_detail_country'],
            'Postcode/Zip': card_and_billing_data['billing_detail_postcode'],
            'Please subscribe me to': ','.join(card_and_billing_data.get('subscription_options', '')),
            'I have read and accept the Privacy Policy': card_and_billing_data['privacy_policy'],
        })



    return response


def get_checkout_with_paypal_redirect_url(request):
    """Get the URL which the user will be redirected to when they click
    the "Checkout With Paypal" button.
    """
    order = Order()
    order.discount_code = request.session.get('discount_code', '')
    order.setup(request)
    redirect_to_url = set_express_checkout(order)
    return redirect_to_url


def cancel_checkout_with_paypal(request):
    """When the user clicks the "cancel" button in PayPal,
    they get redirected here, where we delete the order for
    their token, log the cancellation to the payments log and
    redirect them back to the cart page.
    """
    token = request.GET.get('token')
    cancelled_order = get_order_from_token(token)
    log_cancelled_order(cancelled_order)
    cancelled_order.delete()
    return redirect('shop_cart')


@never_cache
def return_from_checkout_with_paypal(request):
    """
    Find the order for the paypal express checkout token and
    then call get_express_checkout_details to complete the next
    step in the 'checkout with paypal' flow.
    """
    token = request.GET.get('token')
    payer_id = request.GET.get('PayerID')
    order = get_order_from_token(token)
    everything = None
    everything_except_billing_shipping = lambda f: not (f.startswith('shipping_') or f.startswith('billing_'))
    if request.POST:
        shipping_type_id = request.POST.get('id')
        shipping_detail_country = request.POST.get('shipping_detail_country')
        discount_code = request.POST.get("discount_code", None)
        order_form_data = {k: v for k, v in request.POST.iteritems()
            if not k in ['paypal_email']}
        paypal_email = request.POST.get('paypal_email')
        what_to_hide = everything_except_billing_shipping
    else:
        express_checkout_details = get_express_checkout_details(order)
        paypal_email = express_checkout_details['EMAIL']

        try:
            if express_checkout_details['SHIPPINGCALCULATIONMODE'] == 'FlatRate':
                shipping_type_id = express_checkout_details['SHIPPINGOPTIONNAME']
            else:
                shipping_type_id = express_checkout_details['SHIPPINGOPTIONNAME'].split('|')[0].strip()
        except KeyError:
            # COT-748; sometimes the SHIPPINGCALCULATIONMODE is missing from the paypal
            # response. Cause currently unknown.
            shipping_type_id = order.shipping_type
            logger_payments.warn('PayPal: SHIPPINGCALCULATIONMODE is missing from the response.\n \
                Using {0}, from the order object.\n \
                Full response: {1}'.format(order.shipping_type, express_checkout_details))

        shipping_detail_country = express_checkout_details['PAYMENTREQUEST_0_SHIPTOCOUNTRYNAME'].upper()
        discount_code = order.discount_code
        order_form_data = build_order_form_data(express_checkout_details, order)
        what_to_hide = everything

    shipping_type = get_freight_type_for_id(order.currency, shipping_type_id)

    # Need to stash the shipping_type in the session here cos sadly that's
    # where the discount form grabs it from in order to validate whether the
    # discount code is valid for the shipping type
    request.session['shipping_type'] = shipping_type.id
    shipping_form = ShippingForm(request, order.currency, initial={'id': shipping_type.id})
    if not is_valid_shipping_choice(order.currency, shipping_type.id, shipping_detail_country):
        shipping_form.errors['id'] = 'The chosen shipping option is invalid for the destination country.'

    # We need the discount form too because discount code validity depends on
    # shipping type chosen, which depends on shipping address
    discount_form = DiscountForm(request, {'discount_code': discount_code})
    discount_form.is_valid()

    form_class = get_callable(settings.SHOP_CHECKOUT_FORM_CLASS)
    form_args = dict(
        request=request,
        step=1,
        initial=order_form_data,
        data=order_form_data,
        hidden=what_to_hide
    )
    order_form = form_class(**form_args)

    if shipping_form.errors or not order_form.is_valid() or not discount_form.is_valid():
        # Here we display the confirmation page, editable, so they can fix the errors
        form_args['hidden'] = everything_except_billing_shipping
        order_form = form_class(**form_args)
        order_form.is_valid()
        return paypal_confirmation_response(request, order, order_form, shipping_form, shipping_type.charge, discount_form, paypal_email)

    # If an item is out of stock, then for now we just abort the transaction
    no_stock = []
    cart = request.cart
    for cart_item in cart.has_no_stock():
        no_stock += [unicode(cart_item.variation())]
        cart_item.delete()
    if len(no_stock) != 0:
        delattr(cart, '_cached_items')
        log_no_stock_order_aborted(order, no_stock)
        order.delete()
        return render(request, 'shop/paypal_aborted.html')

    request.session["order"] = dict(order_form.cleaned_data)
    request.session['shipping_total'] = Decimal(shipping_type.charge)
    tax_handler(request, order_form)
    order_form.set_discount()

    if request.POST:
        # So now we try to actually do the payment
        for field_name, value in order_form.cleaned_data.iteritems():
            if hasattr(order, field_name) and value:
                setattr(order, field_name, value)
        order.shipping_total = shipping_type.charge
        order.payment_gateway_transaction_type = 'PAYPAL'
        try:
            express_payment_details = do_express_checkout_payment(order, payer_id)
            response = finalise_order(
                express_payment_details['PAYMENTINFO_0_TRANSACTIONID'],
                request,
                order,
                order_form.cleaned_data
            )
            # We need to get rid of these magic numbers but this means "PROCESSED"
            order.status = ORDER_PROCESSED
            order.save()
            return response
        except (PaypalApiCallException, checkout.CheckoutError) as e:
            # Revert product stock changes and delete order
            for item in request.cart:
                try:
                    variation = ProductVariation.objects.get(sku=item.sku)
                except ProductVariation.DoesNotExist:
                    pass
                else:
                    amount = item.quantity
                    variation.num_in_stock += amount
                    variation.num_in_stock_pool += amount
            order.delete()
            return render(request, 'shop/paypal_aborted.html')
    else:
        # Here we display the confirmation page, uneditable
        return paypal_confirmation_response(request, order, order_form, shipping_form, shipping_type.charge, discount_form, paypal_email)


def get_cybersource_device_fingerprint_context():
    context = {}
    if "cybersource" in settings.SHOP_HANDLER_PAYMENT.lower():
        if _cybersetting("do_device_fingerprinting"):
            org_id = _cybersetting('device_fingerprinting_org_id')
            context["cybersource_device_fingerprinting_pixel_url"] = \
                reverse("cybersource_device_fingerprinting_pixel", kwargs={"org_id": org_id})
            context["cybersource_device_fingerprinting_css_url"] = \
                reverse("cybersource_device_fingerprinting_css", kwargs={"org_id": org_id})
            context["cybersource_device_fingerprinting_js_url"] = \
                reverse("cybersource_device_fingerprinting_js", kwargs={"org_id": org_id})
            context["cybersource_device_fingerprinting_flash_url"] = \
                reverse("cybersource_device_fingerprinting_flash", kwargs={"org_id": org_id})
    return context


class ReturnFromVme(FormView):
    EVERYTHING = None
    EVERYTHING_EXCEPT_SHIPPING = lambda f: not (f.startswith('shipping_'))
    form_classes = {
        'order': get_callable(settings.SHOP_CHECKOUT_FORM_CLASS),
        'shipping': ShippingForm,
        'discount': DiscountForm,
    }
    http_method_names = [u'post', ]

    what_to_hide = EVERYTHING
    tid = None
    call_id = None
    confirming_and_paying = False
    shipping_type = None
    shipping_detail_country = None
    discount_code = None

    def __init__(self, *args, **kwargs):
        self.what_to_hide = self.EVERYTHING
        self.call_id = None
        self.tid = None
        self.confirming_and_paying = False
        self.shipping_type = None
        self.shipping_detail_country = None
        self.discount_code = None
        super(ReturnFromVme, self).__init__(*args, **kwargs)

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super(FormView, self).dispatch(*args, **kwargs)

    def _abort(self, order):
        # Revert product stock changes and delete order
        for item in self.request.cart:
            try:
                variation = ProductVariation.objects.get(sku=item.sku)
            except ProductVariation.DoesNotExist:
                pass
            else:
                amount = item.quantity
                variation.num_in_stock += amount
                variation.num_in_stock_pool += amount
        order.delete()
        return render(self.request, 'shop/vme_aborted.html')

    def _set_tid(self, request):
        self.tid = request.POST.get('order_payment_gateway_transaction_id', None)
        if self.tid:
            self.confirming_and_paying = True
        else:
            self.tid = request.POST.get('merchTrans')
            if not self.tid:
                raise VMeFlowError("No order.payment_gateway_transaction_id '\
                'or merchTrans to find or create order with!")
        self.call_id = request.POST.get('callId')

    def _abort_if_no_stock(self, order):
        no_stock = []
        cart = self.request.cart
        for cart_item in cart.has_no_stock():
            no_stock += [unicode(cart_item.variation())]
            # TODO-VME: Need to check this
            cart_item.delete()
        if len(no_stock) != 0:
            delattr(cart, '_cached_items')
#            log_no_stock_order_aborted(order, no_stock)
#            order.delete()
            # TODO-VME: need to tweek up this template to how they want it
            return self._abort(order)

    def _get_checkout_details(self, request):
        try:
            result = ap_checkout_details(
                self.call_id,
                session_currency(request),
                self.tid
            )
        except CybersourceResponseException as e:
            raise e
        return result

    def _build_order_form_data(self, vme_checkout_details, checkout_form_data):
        """
        Return a dictionary to initialise the order form with,
        which maps parameter values returned from v.me's ap_checkout_details
        to the appropriate form fields. Some mandatory form fields like phone number
        and some card_ fields, we don't have values for from v.me, so we
        provide dummy valudes in order to fake them so that the form still works.
        """
        order_form_data = {}
        # Bill to V.me fields: email
        bill_to = self._convert_vme_address(getattr(vme_checkout_details, 'billTo', None))
        # Ship to V.me fields (available for cart flow):
        # street[1-3], city, state, postalCode, country, phoneNumber, name
        ship_to = self._convert_vme_address(getattr(vme_checkout_details, 'shipTo', None))
        cart_flow = bool(ship_to)
        field_mapping = [
            ('billing_detail_city', None, ' '),
            ('billing_detail_country', None, 'AUSTRALIA'),
            ('billing_detail_email', bill_to.email, None),
            ('billing_detail_first_name', None, ' '),
            ('billing_detail_last_name', None, ' '),
            ('billing_detail_postcode', None, ' '),
            ('billing_detail_state', None, ' '),
            ('billing_detail_street', None, ' '),
            ('billing_detail_street2', None, None),
            ('billing_detail_phone', None, ' '),
            ('shipping_detail_city', ship_to.city, None),
            ('shipping_detail_country', ship_to.country, None),
            ('shipping_detail_first_name', ship_to.first_name, None),
            ('shipping_detail_last_name', ship_to.last_name, None),
            ('shipping_detail_postcode', ship_to.postcode, None),
            ('shipping_detail_state', ship_to.state, None),
            ('shipping_detail_street', ship_to.street, None),
            ('shipping_detail_street2', ship_to.street2, None),
            ('shipping_detail_phone', ship_to.phone, '0000 0000'),
            ('card_payment_type', None, 'V.ME'),
            ('card_expiry_month', None, '12'),
            ('card_expiry_year', None, datetime.now().year),
            ('same_billing_shipping', None, 'on'),
            ('terms', None, 'on'),
        ]
        for order_field_name, vme_value, default_value in field_mapping:
            if vme_value and cart_flow:
                value = vme_value
            else:
                value = checkout_form_data.get(order_field_name, default_value)
            order_form_data[order_field_name] = value
        return order_form_data

    def _convert_vme_address(self, vme_address):
        class Address(object):
            pass
        result = Address()
        full_name = getattr(vme_address, 'name', None)
        result.first_name = full_name.split()[0] if full_name else None
        result.last_name = ' '.join(full_name.split()[1:]) if full_name else None
        country_code = getattr(vme_address, 'country', 'AU')
        try:
            result.country = Country.objects.get(iso=country_code.upper).name
        except Country.DoesNotExist as e:
            raise VMeFlowError(e)
        result.email = getattr(vme_address, 'email', None)
        result.city = getattr(vme_address, 'city', None)
        result.postcode = getattr(vme_address, 'postalCode', None)
        result.state = getattr(vme_address, 'state', None)
        result.street = getattr(vme_address, 'street1', None)
        result.street2 = getattr(vme_address, 'street2', '') + getattr(vme_address, 'street3', '')
        result.phone = getattr(vme_address, 'phoneNumber', '0000 0000')
        return result

    def _update_order_billing_details(self, auth_result, order):
        vme_bill_to = getattr(auth_result, 'billTo', None)
        if not vme_bill_to:
            return order
        bill_to = self._convert_vme_address(vme_bill_to)
        order.billing_detail_first_name = bill_to.first_name
        order.billing_detail_last_name = bill_to.last_name
        order.billing_detail_street = bill_to.street
        order.billing_detail_street2 = bill_to.street2
        order.billing_detail_country = bill_to.country
        order.billing_detail_postcode = bill_to.postcode
        order.billing_detail_city = bill_to.city
        order.billing_detail_state = bill_to.state
        order.billing_detail_phone = bill_to.phone
        return order

    def _process_payment(self, order):
        # TODO-VME: so here we need to be looking at reponse codes and potentially
        # throwing checkout errors....what do we want to do if their payment
        # fails or we deem them too risky? Just show them the error message
        # and leave them at this page, so they can try again (which will probably be
        # fruitless) or abort them? I prefer abort them.
        ap_confirm_purchase(order, self.call_id)
        auth_result = ap_auth(order, self.call_id)

        # Log the transaction to Decision Manager
        risk_indicator = getattr(
            getattr(auth_result, 'apReply', None),
            'riskIndicator', None
        )
        order = self._update_order_billing_details(auth_result, order)
        if risk_indicator:
            # Update order with billing details from ap_auth
            try:
                afs(order, self.call_id, risk_indicator)
            except CybersourceRequiresReview:
                order.status = ORDER_REVIEW
            else:
                order.status = ORDER_PROCESSED
        order.save()
        # Now capture their money
        return ap_capture(order, auth_result.requestID)

    def _confirmation_response(self, order, order_form, shipping_form, shipping_total, discount_form, call_id, no_stock=[], checkout_errors=[]):
        """Return the rendered the confirmation page, to display when the user
        returns to our site from the PayPal site."""
        step_vars = checkout.CHECKOUT_STEPS[2]
        order_form.label_suffix = ''
        context = {
            'order': order,
            'form': order_form,
            'CHECKOUT_STEP_FIRST': False,
            'extends_template': 'base.html',
            'step_title': step_vars['title'],
            'step_url': step_vars['url'],
            'steps': checkout.CHECKOUT_STEPS,
            'step': 2,
            'no_stock': no_stock,
            'checkout_errors': checkout_errors,
            'shipping_form': shipping_form,
            'shipping_total': shipping_total,
            'discount_form': discount_form,
            'call_id': call_id
        }
        return render(
            self.request,
            "shop/checkout_with_vme_confirmation.html",
            context
        )

    def form_valid(self, form, shipping_form, discount_form):
        order = form.save(commit=False)
        order.setup(self.request)
        # Make sure we set payment_gateway_transaction_id, for audit trail purposes
        order.payment_gateway_transaction_id = self.tid

        if self.confirming_and_paying:
            order.save()
            self._abort_if_no_stock(order)
            try:
                capture_result = self._process_payment(order)
                for field_name, value in form.cleaned_data.iteritems():
                    if hasattr(order, field_name) and value:
                        setattr(order, field_name, value)
                order.shipping_total = self.shipping_type.charge
                order.payment_gateway_transaction_type = settings.VME
                response = finalise_order(
                    # TODO-VME: maybe this should be something different from the ap_auth.requestID
                    # this should probably be I think auth_result.apReply.orderID which is
                    # actually the call_id from V.Me
                    capture_result.requestID,
                    self.request,
                    order,
                    form.cleaned_data
                )
                order.save()
                return response
            except (VMeFlowError, checkout.CheckoutError):
                return self._abort(order)
        else:
            return self._confirmation_response(
                order, form, shipping_form,
                self.shipping_type.charge, discount_form, self.call_id
            )

    def form_invalid(self, form, shipping_form, discount_form):
        form_class = self.form_classes['order']
        form_args = self.get_form_kwargs()
        form_args['hidden'] = self.EVERYTHING_EXCEPT_SHIPPING
        order_form = form_class(self.request, checkout.CHECKOUT_STEP_FIRST, **form_args)
        order_form.is_valid()
        # TODO-VME: We have a problem here because the order doesn't exist yet
        # but the template requires it...different in paypal cos the order was
        # created at an earlier point
        return self._confirmation_response(
            order, order_form, shipping_form,
            self.shipping_type.charge, discount_form, self.call_id
        )

    def get_form(self, form_class):
        kwargs = self.get_form_kwargs()
        return form_class(self.request, checkout.CHECKOUT_STEP_FIRST, **kwargs)

    def get_form_class(self):
        return self.form_classes['order']

    def get_form_kwargs(self):
        initial = self.get_initial()
        return {
            'initial': initial,
            'data': initial
        }

    def get_initial(self):
        self._set_tid(self.request)

        if self.confirming_and_paying:
            self.shipping_detail_country = self.request.POST.get('shipping_detail_country')
            self.discount_code = self.request.POST.get("discount_code", None)
            shipping_type_id = self.request.POST.get('id')

            self.what_to_hide = self.EVERYTHING_EXCEPT_SHIPPING
            data = {k: v for k, v in self.request.POST.iteritems()
                if not k in ['order_payment_gateway_transaction_id', 'callId']}
        else:
            checkout_details = self._get_checkout_details(self.request)
            data = self._build_order_form_data(
                checkout_details,
                self.request.session.get('order', dict()),
            )
            # TODO-VME: These may need to be changed
            self.shipping_detail_country = data['shipping_detail_country']
            self.discount_code = self.request.session.get('discount_code')
            shipping_type_id = self.request.session.get('shipping_type')

        self.shipping_type = get_freight_type_for_id(session_currency(self.request), shipping_type_id)
        self.request.session['shipping_type'] = self.shipping_type.id
        return data

    def get_discount_form(self):
        return DiscountForm(self.request, {'discount_code': self.discount_code})

    def get_shipping_form(self):
        form = ShippingForm(
            self.request,
            session_currency(self.request),
            initial={'id': self.shipping_type.id}
        )
        if not is_valid_shipping_choice(session_currency(self.request), self.shipping_type.id, self.shipping_detail_country):
            form.errors['id'] = 'The chosen shipping option is invalid for the destination country.'
        return form

    def post(self, request, *args, **kwargs):
        form_class = self.get_form_class()
        form = self.get_form(form_class)

        discount_form = self.get_discount_form()
        shipping_form = self.get_shipping_form()

        if form.is_valid() and discount_form.is_valid() and not shipping_form.errors:
            return self.form_valid(form, shipping_form, discount_form)
        else:
            return self.form_invalid(form, shipping_form, discount_form)


@csrf_exempt
def return_from_checkout_with_vme(request):
    # merchTrans contains the cart.id and so will order_payment_gateway_transaction_id on the
    # second and subsequent form submissions, so try to get that first and if not
    # then use merchTrans. This might be the best way to distinguish between the
    # post back from v.me and subsequent form submissions due to validation issues
    # on the form  etc
    everything_except_shipping = lambda f: not (f.startswith('shipping_'))
    everything = None
    order_payment_gateway_transaction_id = request.POST.get('order_payment_gateway_transaction_id', None)
    call_id = request.POST.get('callId')
    confirming_and_paying = False
    if order_payment_gateway_transaction_id:
        confirming_and_paying = True
    else:
        order_payment_gateway_transaction_id = request.POST.get('merchTrans', None)
        if not order_payment_gateway_transaction_id:
            raise VMeFlowError("No order.payment_gateway_transaction_id or merchTrans to find or create order with!")

    try:
        # TODO-VME: This should only be called once, when the post-back comes in when they're returning from the lightbox
        vme_checkout_details = ap_checkout_details(order_payment_gateway_transaction_id, call_id, session_currency(request), order_payment_gateway_transaction_id)
    except CybersourceResponseException:
        # TODO-VME: This is just an example, but basically, whereever
        # we are hitting the cybersource or vme API then we need to
        # catch the appropriate exception(s) from the underlying call
        # to _run_transaction and then do the appropriate thing. In
        # this example, aborting the order may or may not be the
        # appropriate thing to do. We need to clarify and confirm this.
        return render(request, 'shop/vme_aborted.html')

    order = get_order_from_merch_trans_number(request, order_payment_gateway_transaction_id, vme_checkout_details)

    if confirming_and_paying:
        shipping_type_id = request.POST.get('id')
        shipping_detail_country = request.POST.get('shipping_detail_country')
        discount_code = request.POST.get("discount_code", None)
        order_form_data = {k: v for k, v in request.POST.iteritems()
            if not k in ['order_payment_gateway_transaction_id', 'callId']}
        what_to_hide = everything_except_shipping
    else:
        # it's the post back from v.me

        # so if the post back is coming from cart-flow style checkout,
        # we'll get back the v.me shipping address in shipTo from ap_checkout_details
        # but if they've done a payment-flow style checkout, that won't be there so
        # we just want the shipping details off the order, which they entered in
        # the billing/shipping form
        shipping_detail_country = order.shipping_detail_country
        shipping_type_id = order.shipping_type
        discount_code = order.discount_code
        # for now, we don't need to override with anything from v.me cos we are just doing
        # payment flow right now....TODO-VME: later for cart flow though, we'll need to tweek
        # build_order_form_data() so it overrides the appropriate shipping address fields
        # if they exist in the response...though they do seem to exist regardless of
        # whether it's payment or cart flow...which could be a problem...
        order_form_data = vme_build_order_form_data(vme_checkout_details, model_to_dict(order), order.payment_gateway_transaction_id)
        what_to_hide = everything

    # Need to stash the shipping_type in the session here cos sadly that's
    # where the discount form grabs it from in order to validate whether the
    # discount code is valid for the shipping type
    shipping_type = get_freight_type_for_id(order.currency, shipping_type_id)
    request.session['shipping_type'] = shipping_type.id
    shipping_form = ShippingForm(request, order.currency, initial={'id': shipping_type.id})
    if not is_valid_shipping_choice(order.currency, shipping_type.id, shipping_detail_country):
        shipping_form.errors['id'] = 'The chosen shipping option is invalid for the destination country.'

    # We need the discount form too because discount code validity depends on
    # shipping type chosen, which depends on shipping address
    discount_form = DiscountForm(request, {'discount_code': discount_code})
    discount_form.is_valid()

    form_class = get_callable(settings.SHOP_CHECKOUT_FORM_CLASS)
    form_args = dict(
        request=request,
        step=1,
        initial=order_form_data,
        data=order_form_data,
        hidden=what_to_hide
    )
    order_form = form_class(**form_args)

    # For the payment flow, this should never happen really
    if shipping_form.errors or not order_form.is_valid() or not discount_form.is_valid():
        # TODO-VME: implement actual response so they can fix whatever was invalid
        # so when the make some changes after something was invalid....we want
        # to I think, resubmit the form....and obviously validate their address and stuff
        # and make sure that gets stored on the order again too....but what about if the shipping
        # amount changes....by ap_confirm_purchase won't get called until all forms validate....
        # but we WILL need to make sure that we resave the order form onto the order
        # TODO-VME: need to make sure that we resave the order form onto the order
        form_args['hidden'] = everything_except_shipping
        order_form = form_class(**form_args)
        order_form.is_valid()
        return vme_confirmation_response(request, order, order_form, shipping_form, shipping_type.charge, discount_form, call_id)

    # If an item is out of stock, then for now we just abort the transaction
    no_stock = []
    cart = request.cart
    for cart_item in cart.has_no_stock():
        no_stock += [unicode(cart_item.variation())]
        cart_item.delete()
    if len(no_stock) != 0:
        delattr(cart, '_cached_items')
        log_no_stock_order_aborted(order, no_stock)
        order.delete()
        # TODO-VME: need to tweek up this template to how they want it
        return render(request, 'shop/vme_aborted.html')

    if not confirming_and_paying:
        return vme_confirmation_response(request, order, order_form, shipping_form, shipping_type.charge, discount_form, call_id)

    ###################
    # Confirm and pay #
    ###################

    # TODO-VME: need to do the actual payment. need a way to distinguish between the
    # the post back from v.me and the post that comes from the user clicking confirm
    # on the confirm & pay page (submitting the hidden order form)

    # So now we try to actually do the payment
    for field_name, value in order_form.cleaned_data.iteritems():
        if hasattr(order, field_name) and value:
            setattr(order, field_name, value)
    order.shipping_total = shipping_type.charge
    order.payment_gateway_transaction_type = settings.VME
    try:
        # TODO-VME: so here we need to be looking at reponse codes and potentially
        # throwing checkout errors....what do we want to do if their payment
        # fails or we deem them too risky? Just show them the error message
        # and leave them at this page, so they can try again (which will probably be
        # fruitless) or abort them? I prefer abort them.

        ap_confirm_purchase(order, call_id)
        auth_result = ap_auth(order, call_id)

        # Log the transaction to Decision Manager
        risk_indicator = getattr(
            getattr(auth_result, 'apReply', None),
            'riskIndicator', None
        )
        order = vme_update_order_billing_details(auth_result, order)
        if risk_indicator:
            # Update order with billing details from ap_auth
            try:
                afs_result = afs(order, call_id, risk_indicator)
            except CybersourceRequiresReview:
                order.status = ORDER_REVIEW
            else:
                order.status = ORDER_PROCESSED

        # Now capture their money
        capture_result = ap_capture(order, auth_result.requestID)

        response = finalise_order(
            # TODO-VME: maybe this should be something different from the ap_auth.requestID
            # this should probably be I think auth_result.apReply.orderID which is
            # actually the call_id from V.Me
            capture_result.requestID,
            request,
            order,
            order_form.cleaned_data
        )
        # We need to get rid of these magic numbers but this means "PROCESSED"
        order.save()
        return response
    except (VMeFlowError, checkout.CheckoutError):
        # Revert product stock changes and delete order
        for item in request.cart:
            try:
                variation = ProductVariation.objects.get(sku=item.sku)
            except ProductVariation.DoesNotExist:
                pass
            else:
                amount = item.quantity
                variation.num_in_stock += amount
                variation.num_in_stock_pool += amount
        order.delete()
        return render(request, 'shop/vme_aborted.html')



def _emanates_from_cart_page(request):
    """Return true if the referer was the cart page."""
    referer = request.META.get('HTTP_REFERER', '')
    bits = re.sub('^https?:\/\/', '', referer).split('/')
    try:
        match = resolve('/' + '/'.join(bits[1:]))
    except Resolver404:
        pass
    else:
        if match.view_name == 'shop_cart':
            return True
    return False


@add_header_sameorigin
@ensure_csrf_cookie
def vme_button(request, form=None):
    if settings.VME in settings.SHOP_CARD_TYPES:
        if not form:
            form = get_callable(settings.SHOP_CHECKOUT_FORM_CLASS)(
                request, 1,
                initial=checkout.initial_order_data(request)
            )
        order = form.save(commit=False)
        order.setup(request, provisional=True)
        order.payment_gateway_transaction_id = request.cart.id
        result = ap_initiate(order)
        on_cart_page = _emanates_from_cart_page(request)
        return HttpResponse({
            simplejson.dumps({
                'amount': result.apInitiateReply.amount,
                'apikey': result.apInitiateReply.publicKey,
                'merchantid': result.apReply.merchantUUID,
                'siteid': result.apReply.merchantSiteID,
                'currency': result.purchaseTotals.currency,
                'merch_trans': result.apReply.purchaseID,
                'product_desc': result.apReply.productID,
                'product_id': result.apReply.productID,
                'token': result.apInitiateReply.signature,
                'button-style': 'checkout' if on_cart_page else 'payment',
                'collect-shipping': 'true' if on_cart_page else 'false',
            }),
        })
    else:
        return HttpResponse('not applicable')


def get_vme_context(request):
    context = {}
    if settings.VME in settings.SHOP_CARD_TYPES:
        # TODO-VME: check that this setting exists and if not throw an ImproperlyConfigured exception
        context['vme_static_assets_server'] = _cybersetting("vme_static_assets_server")
        context['vme_secure_return_url'] = "http{secure}://{site}{url}".format(
            secure='' if settings.DEBUG or settings.TEST else 's',
            site=Site.objects.get_current().domain,
            url=reverse('return_from_checkout_with_vme')
        )
    return context


@add_header_sameorigin
@never_cache
def checkout_steps(request, extends_template="base.html"):
    """
    Display the order form and handle processing of each step.
    """
    # Do the authentication check here rather than using standard
    # login_required decorator. This means we can check for a custom
    # LOGIN_URL and fall back to our own login view.
    authenticated = request.user.is_authenticated()
    if settings.SHOP_CHECKOUT_ACCOUNT_REQUIRED and not authenticated:
        url = "%s?next=%s" % (settings.LOGIN_URL, reverse("shop_checkout"))
        return redirect(url)

    # Determine the Form class to use during the checkout process
    form_class = get_callable(settings.SHOP_CHECKOUT_FORM_CLASS)

    step = int(request.POST.get("step", checkout.CHECKOUT_STEP_FIRST))
    initial = checkout.initial_order_data(request)
    form = form_class(request, step, initial=initial)
    data = request.POST
    checkout_errors = []

    cart = request.cart
    no_stock = []
    for cart_item in cart.has_no_stock():
        no_stock += [unicode(cart_item.variation())]
        cart_item.delete()
    recalculate_discount(request)
    if len(no_stock) != 0:
        delattr(cart, '_cached_items')
        recalculate_discount(request)
    if request.POST.get("back") is not None:
        # Back button in the form was pressed - load the order form
        # for the previous step and maintain the field values entered.
        step -= 1
        form = form_class(request, step, initial=initial)
    elif request.method == "POST" and cart.has_items():
        # This is the execution path when the user clicks the 'proceed
        # to payment' button on the billing/shipping details page
        # and also when they click 'next' on the payment form page
        sensitive_card_fields = ("card_number", "card_expiry_month",
                                 "card_expiry_year", "card_ccv")
        form = form_class(request, step, initial=initial, data=data)
        request.session['order'] = dict([(k, v) for k, v in form.data.items()
                                         if k not in ['csrfmiddlewaretoken'] +
                                         list(sensitive_card_fields)])
        if form.is_valid() and no_stock == []:
            # Copy the current form fields to the session so that
            # they're maintained if the customer leaves the checkout
            # process, but remove sensitive fields from the session
            # such as the credit card fields so that they're never
            # stored anywhere.
            request.session["order"] = dict(form.cleaned_data)
            for field in sensitive_card_fields:
                if field in request.session["order"]:
                    del request.session["order"][field]

            # If they are going to 'pay with paypal' then we redirect them
            if form.cleaned_data['card_type'].lower() == 'paypal' and step == checkout.CHECKOUT_STEP_PAYMENT:
                order = form.save(commit=False)
                order.setup(request)
                redirect_to_url = set_express_checkout(order, address_override='1')
                return redirect(redirect_to_url)

            # FIRST CHECKOUT STEP - handle shipping and discount code.
            if step == checkout.CHECKOUT_STEP_FIRST:
                # This happens on the 'proceed to payment' post
                # on the billing/shipping details page too
                try:
                    billship_handler(request, form)
                    tax_handler(request, form)
                except checkout.CheckoutError, e:
                    checkout_errors.append(e)
                form.set_discount()


            # FINAL CHECKOUT STEP - handle payment and process order.
            if step == checkout.CHECKOUT_STEP_LAST and not checkout_errors:
                # Create and save the inital order object so that
                # the payment handler has access to all of the order
                # fields. If there is a payment error then delete the
                # order, otherwise remove the cart items from stock
                # and send the order reciept email.
                order = form.save(commit=False)
                order.setup(request)
                # Try payment.
                try:
                    transaction_id = payment_handler(request, form, order)
                except checkout.CheckoutError, e:
                    # Revert product stock changes and delete order
                    for item in request.cart:
                        try:
                            variation = ProductVariation.objects.get(sku=item.sku)
                        except ProductVariation.DoesNotExist:
                            pass
                        else:
                            amount = item.quantity
                            variation.num_in_stock += amount
                            variation.num_in_stock_pool += amount
                    order.delete()
                    # Error in payment handler.
                    checkout_errors.append(e)
                    if settings.SHOP_CHECKOUT_STEPS_CONFIRMATION:
                        step -= 1
                except Requires3DSecureVerification as threed_exc:
                    form.cleaned_data['encryption_key'] = threed_exc.get_xid()
                    threed_txn = ThreeDSecureTransaction(
                        card_and_billing_data=form.cleaned_data,
                        order_id=order.id,
                        pareq=threed_exc.get_pareq()
                    )
                    threed_txn.save()
                    return threed_exc.get_redirect_response(request, threed_txn.transaction_slug, extends_template)
                else:
                    response = finalise_order(
                        transaction_id,
                        request,
                        order,
                        form.cleaned_data
                    )
                    return response

            # If any checkout errors, assign them to a new form and
            # re-run is_valid. If valid, then set form to the next step.
            form = form_class(request, step, initial=initial, data=data,
                             errors=checkout_errors)
            if form.is_valid():
                step += 1
                form = form_class(request, step, initial=initial)

    step_vars = checkout.CHECKOUT_STEPS[step - 1]
    template = "shop/%s.html" % step_vars["template"]
    CHECKOUT_STEP_FIRST = step == checkout.CHECKOUT_STEP_FIRST
    form.label_suffix = ''
    context = {"form": form, "CHECKOUT_STEP_FIRST": CHECKOUT_STEP_FIRST,
               "extends_template": extends_template,
               'error_message': request.GET.get('error_message'),
               "step_title": step_vars["title"], "step_url": step_vars["url"],
               "steps": checkout.CHECKOUT_STEPS, "step": step,
               'no_stock': no_stock}
    context.update(get_cybersource_device_fingerprint_context())
    context.update(get_vme_context(request))
    return render(request, template, context)


def abort(request, transaction_slug, template="shop/aborted.html"):
    try:
        threed_txn = ThreeDSecureTransaction.objects.get(transaction_slug=transaction_slug)
        if threed_txn.order:
            try:
                order = Order.objects.get(id=threed_txn.order.id)
            except Order.DoesNotExist:
                pass
            else:
                order.delete()
    except ThreeDSecureTransaction.DoesNotExist:
        raise Http404
    return render(request, template)


def exchange_rates():
    cache_key = 'exchange-rates'
    data = cache.get(cache_key, None)
    if data:
        return data
    try:
        response = urlopen('http://openexchangerates.org/api/latest.json?app_id=377193e83fbb41d592e4521a8ec7d35e', timeout=5)
        if response.getcode() == 200:
            json_rates = response.read()
    except URLError:
        logger.error('The exchange rates API is unreachable')
        json_rates = '{"rates":{"AUD":0.966098, "USD": 1, "MYR": 3.074717, "HKD": 7.75365, "SGD": 1.22935}}'
    cache.set(cache_key, json_rates, 3600 * 24)
    return json_rates

def get_or_create_discount(order):
    template_code = getattr(settings, 'FACTORY_PURCHASE_DISCOUNT_TPL', False)
    if not template_code:
        return None
    key = '%s%s%s' % (settings.SECRET_KEY, order.id, template_code)
    code = hashlib.md5(key).hexdigest()[:15].upper()
    try:
        return DiscountCode.objects.get(code=code)
    except DiscountCode.DoesNotExist:
        pass
    try:
        discount = DiscountCode.objects.get(code=template_code)
    except DiscountCode.DoesNotExist:
        return None
    products = list(discount.products.all().values_list('id', flat=True))
    categories = list(discount.categories.all().values_list('id', flat=True))
    discount.pk = None
    discount.active = True
    discount.allowed_no_of_uses = 1
    discount.code = code
    discount.save()
    discount.products.add(*products)
    discount.categories.add(*categories)
    return discount

def complete(request, template="shop/complete.html", extends_template="base.html"):
    """
    Redirected to once an order is complete - pass the order object
    for tracking items via Google Anayltics, and displaying in
    the template if required.
    """
    try:
        order = Order.objects.from_request(request)
    except Order.DoesNotExist:
        raise Http404
    items = order.items.all()
    # Assign product names to each of the items since they're not
    # stored.
    skus = [item.sku for item in items]
    variations = ProductVariation.objects.filter(sku__in=skus)
    names = {}
    categories = {}
    for variation in variations.select_related(depth=1):
        product = variation.product
        names[variation.sku] = product.title
        has_categories = product.categories.all().exists()
        categories[variation.sku] = ('%s | %s (%s)' %
                                    (product.categories.all()[0].slug if has_categories else 'NA',
                                     product.rms_category.name if product.rms_category else 'NA',
                                     product.rms_category.code if product.rms_category else 'NA'))
    for i, item in enumerate(items):
        setattr(items[i], "name", names[item.sku])
        setattr(items[i], "category", categories[item.sku])
    discount = get_or_create_discount(order)
    gpp_code = GPPPoint.gpp_code(order)
    context = {"order": order,
               'gpp_code': gpp_code if order.id != request.session.get('latest_order', '') else None,
               "items": items,
               'track_transaction': order.id != request.session.get('latest_order', ''),
               "extends_template": extends_template,
               'exchange_rates': exchange_rates(),
               'discount': discount,
               "steps": checkout.CHECKOUT_STEPS}
    request.session['latest_order'] = order.id
    return render(request, template, context)


def invoice(request, order_id, template="shop/order_invoice.html", extends_template="base.html"):
    """
    Display a plain text invoice for the given order. The order must
    belong to the user which is checked via session or ID if
    authenticated, or if the current user is staff.
    """
    lookup = {"id": order_id}
    if not request.user.is_authenticated():
        lookup["key"] = request.session.session_key
    elif not request.user.is_staff:
        lookup["user_id"] = request.user.id
    order = get_object_or_404(Order, **lookup)

    context = {
        "order": order,
        "extends_template": extends_template,
        }

    context.update(order.details_as_dict())
    context = RequestContext(request, context)

    if request.GET.get("format") == "pdf":
        response = HttpResponse(mimetype="application/pdf")
        name = slugify("%s-invoice-%s" % (settings.SITE_TITLE, order.id))
        response["Content-Disposition"] = "attachment; filename=%s.pdf" % name
        html = get_template(template).render(context)
        import ho.pisa
        ho.pisa.CreatePDF(html, response)
        return response
    return render(request, template, context)

############################
# Cybersource Silent Order #
############################

def cybersource_signature(secret, data):
    return base64.b64encode(hmac.new(secret, data, hashlib.sha256).digest())

def cybersource_fields_address(data):
    bill_to_country_iso = Country.objects.get(name=data['billing_detail_country']).iso
    ship_to_country_iso = Country.objects.get(name=data['shipping_detail_country']).iso
    res = dict(bill_to_address_country=bill_to_country_iso,
               ship_to_address_country=ship_to_country_iso)
    for k1, k2 in [('city', 'city'),
                   ('line1', 'street'),
                   ('line2', 'street2'),
                   ('postal_code', 'postcode'),
                   ('state', 'state'),
                   ('forename', 'first_name'),
                   ('surname', 'last_name'),
                   ('phone', 'phone')]:
        res['bill_to_address_' + k1] = data['billing_detail_' + k2]
        res['ship_to_address_' + k1] = data['shipping_detail_' + k2]
    for k1, k2 in [('forename', 'first_name'),
                   ('surname', 'last_name')]:
        res['bill_to_' + k1] = data['billing_detail_' + k2]
        res['ship_to_' + k1] = data['shipping_detail_' + k2]
    return res

def cybersource_fields_item(cart):
    res = {}
    index = 0
    for i, item in enumerate(cart.items.all()):
        if i > 49:
            break
        index = i
        res['item_%s_sku' % index] = item.sku
        res['item_%s_name' % index] = item.title
        res['item_%s_unit_price' % index] = '%.2f' % item.unit_price
        res['item_%s_quantity' % index] = item.quantity
        res['item_%s_code' % index] = 'default'
    res['line_item_count'] = str(index + 1)
    return res

def cybersource_post(form, request):
    'Send payment data to Cybersource Secure Acceptance'
    sa_settings = settings.SECURE_ACCEPTANCE
    data = form.data
    CARD_TYPE = dict(Visa='001', Mastercard='002')

    # extend user session if it's about to expire
    if request.session.get_expiry_age() < 60 * 20:
        request.session.set_expiry(60 * 20)

    # Unsigned fields (All request fields should be signed to prevent data tampering, with the exception of the card_number field and the signature field.)
    unsigned_fields = dict(card_number=data['card_number'],)

    # Signed fields
    date_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    signed_fields = dict(
        access_key=sa_settings['access_key'],
        profile_id=sa_settings['profile_id'],
        signed_date_time=date_time,
        locale='en-us',
        transaction_type='sale',
        signed_field_names='',
        unsigned_field_names='')
    amount = _order_totals(dict(request=request))['order_total']
    signed_fields.update(dict(
        transaction_uuid=hashlib.sha1('%s%s' % (request.cart.id, data)).hexdigest(),
        reference_number=hashlib.md5('%s%s' % (request.session.session_key, date_time)).hexdigest(),
        merchant_secure_data1=request.session.session_key,
        amount='%.2f' % amount,
        bill_to_email=data['billing_detail_email'],
        card_expiry_date='%s-%s' % (data['card_expiry_month'],
                                    data['card_expiry_year']),
        card_type=CARD_TYPE[data['card_type']],
        card_cvn=data['card_ccv'],
        currency=session_currency(request),
        payment_method='card'))
    signed_fields.update(cybersource_fields_address(data))
    signed_fields.update(cybersource_fields_item(request.cart))
    signed_fields['signed_field_names'] = ','.join(signed_fields.keys())
    signed_fields['unsigned_field_names'] = ','.join(unsigned_fields.keys())

    # Cybersource form
    all_fields = dict(signature=cybersource_signature(
            sa_settings['secret'],
            ','.join(['%s=%s' % x for x in signed_fields.items()])))
    all_fields.update(signed_fields)
    all_fields.update(unsigned_fields)
    return render_to_response('shop/cybersource_sa_post.html',
                              dict(all_fields=all_fields,
                                   cybersource_url=sa_settings['url'],))

@csrf_exempt
def cybersource_complete(request):
    'Cybersource redirects to this function when the transaction finished'
    if request.method != 'POST':
        return redirect('shop_checkout')

    # check errors
    if request.POST.get('decision') in ['DECLINE', 'ERROR']:
        return redirect(reverse('shop_checkout') +
                        '?' +
                        urlencode(dict(error_message='Payment failed (%s)' % request.POST.get('reason_code'))))

    # wait for cybersource_hook to finish
    order = None
    for i in range(30):
        try:
            order = Order.objects.get(transaction_id=request.POST.get('transaction_id'))
            break
        except Order.DoesNotExist:
            time.sleep(1)

    # update response cookies and redirect
    response = redirect("shop_complete")
    remembered = request.session.get('remembered')
    if remembered:
        set_cookie(response, "remember", remembered,
                   secure=request.is_secure())
        del request.session['remembered']
    else:
        response.delete_cookie('remember')
    return response


####################
# Cybersource hook #
####################

@csrf_exempt
def cybersource_hook(request):
    'Cybersource calls this functions after the transaction is completed'
    post = request.POST
    sa_settings = settings.SECURE_ACCEPTANCE
    transaction_id = post.get('transaction_id')
    logger_payments.debug('Cybersource hook POST: %s' % post)

    # verify data
    signature = post.get('signature', False);
    signed_field_names = post.get('signed_field_names', False)
    if not signature or not signed_field_names:
        logger_payments.error('Cybersource SA signature is missing id=%s' % transaction_id)
        return HttpResponse('Signature is missing')
    signed_fields_string = ','.join([
            '%s=%s' % (x, post.get(x, ''))
            for x in signed_field_names.split(',')])
    signature_check = cybersource_signature(sa_settings['secret'], signed_fields_string)
    if signature != signature_check:
        logger_payments.error('Cybersource SA signature is invalid id=%s' % transaction_id)
        return HttpResponse('Signature is invalid')

    # check status
    if post.get('decision') in ['DECLINE', 'ERROR']:
        logger_payments.error('Cybersource SA decision rejected id=%s' % transaction_id)
        return HttpResponse('Rejected')
    if post.get('decision') == 'REVIEW':
        status = ORDER_REVIEW
        logger_payments.error('Cybersource SA decision review id=%s' % transaction_id)
    else:
        status = ORDER_PROCESSED

    # create and finalize order
    customer_session = SessionStore(session_key=post.get('req_merchant_secure_data1'))
    try:
        customer_cart = Cart.objects.get(id=customer_session['cart'])
    except (Cart.DoesNotExist, KeyError):
        logger_payments.error('Cybersource SA session data is not available id=%s' % transaction_id)
        return HttpResponse('Session data is not available')

    class CustomRequest():
        session = customer_session
        cart = customer_cart
        user = AnonymousUser()
        def get_full_path(self):
            return ''
        def is_secure(self):
            return True
    request = CustomRequest()
    if '%.2f' % _order_totals(dict(request=request))['order_total'] != post.get('req_amount'):
        logger_payments.error('Cybersource SA invalid amount id=%s' % transaction_id)
        return HttpResponse('Cybersource SA invalid amount id=%s' % transaction_id)
    form_data = request.session['order']
    order = Order(**dict((k, v) for k, v in form_data.items()
                          if k in Order._meta.get_all_field_names()))
    order.setup(request)
    order.key = post.get('req_reference_number')
    order.status = status
    finalise_order(transaction_id, request, order, form_data)

    # backup remember cookie
    if form_data.get('remember'):
        request.session['remembered'] = "%s:%s" % (sign(order.key), order.key)
    request.session.save()
    return HttpResponse('ok')
