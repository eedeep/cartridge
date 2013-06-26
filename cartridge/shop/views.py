import base64
import hashlib
import hashlib
import hmac
import itertools
import logging
import time
from urllib2 import urlopen, URLError
from decimal import Decimal

logger = logging.getLogger("cottonon")
logger_payments = logging.getLogger("payments")

from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import info
from django.contrib.sessions.backends.cached_db import SessionStore
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.core.urlresolvers import get_callable, reverse
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

from mezzanine.conf import settings
from mezzanine.utils.importing import import_dotted_path
from mezzanine.utils.views import render, set_cookie

from cartridge.shop import checkout
from cartridge.shop.forms import AddProductForm, DiscountForm, CartItemFormSet, ShippingForm
from cartridge.shop.models import Product, ProductVariation, Order, Cart, Category
from cartridge.shop.models import DiscountCode, BundleDiscount
from cartridge.shop.utils import recalculate_discount, sign, \
     shipping_form_for_cart, discount_form_for_cart
from multicurrency.templatetags.multicurrency_tags import _order_totals

from countries.models import Country

#TODO remove multicurrency imports from cartridge
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
from cottonon_shop.cybersource import _cybersetting


# TODO remove cartwatcher imports from cartridge
try:
    from cartwatcher.promotions.models import Promotion
except ImportError:  # keep running if cartwatcher not installed
    Promotion = None

#TODO remove cottonon_shop imports from cartridge
from cottonon_shop.cybersource import Requires3DSecureVerification
from cottonon_shop.models import ThreeDSecureTransaction, \
     create_subscriber_from_dict

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
    context = {
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
        # luxury TODO: print the deal's "post applied" message if cart has met requirements.
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
        if express_checkout_details['SHIPPINGCALCULATIONMODE'] == 'FlatRate':
            shipping_type_id = express_checkout_details['SHIPPINGOPTIONNAME']
        else:
            shipping_type_id = express_checkout_details['SHIPPINGOPTIONNAME'].split('|')[0].strip()
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
            order.status = 2
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
    context = {"order": order,
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
        status = 3
        logger_payments.error('Cybersource SA decision review id=%s' % transaction_id)
    else:
        status = 2

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
