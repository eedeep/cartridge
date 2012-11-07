import hashlib
import itertools
import logging
from urllib2 import urlopen, URLError
from decimal import Decimal
from datetime import datetime

logger = logging.getLogger("cottonon")

from django.contrib.messages import info
from django.core.urlresolvers import get_callable, reverse
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template import RequestContext
from django.template.defaultfilters import slugify
from django.template.loader import get_template
from django.utils import simplejson
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.translation import ugettext as _
from django.views.decorators.cache import never_cache
from django.core.cache import cache
from django.db.models import Sum, Q

from mezzanine.conf import settings
from mezzanine.utils.importing import import_dotted_path
from mezzanine.utils.views import render, set_cookie

from cartridge.shop import checkout
from cartridge.shop.forms import AddProductForm, DiscountForm, CartItemFormSet, ShippingForm
from cartridge.shop.models import Product, ProductVariation, Order, Cart
from cartridge.shop.models import DiscountCode, BundleDiscount
from cartridge.shop.utils import recalculate_discount, sign

#TODO remove multicurrency imports from cartridge
from multicurrency.models import MultiCurrencyProduct, MultiCurrencyProductVariation
from multicurrency.utils import \
    session_currency, default_local_freight_type, is_local_shipping_option
from multicurrency.templatetags.multicurrency_tags import \
    local_currency, formatted_price, _order_totals

from cottonon_shop.forms import WishlistEmailForm

#TODO remove cartwatcher imports from cartridge
try:
    from cartwatcher.promotions.models import Promotion
except ImportError: #keep running if cartwatcher not installed
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
        image__isnull=False,
    )

    variations_json = simplejson.dumps([dict([(f, getattr(v, f))
                                        for f in fields])
                                        for v in variations])
    currency = session_currency(request)
    context = {
        "product": product,
        "extends_template": extends_template,
        "images": product.reduced_image_set(variations),
        "variations": variations,
        "variations_json": variations_json,
        "has_available_variations": any(v.has_price(currency) for v in variations),
        "related": product.related_products.published(for_user=request.user),
        "add_product_form": add_product_form
        }

    if product.bundle_discount_id:
        try:
            bundle_discount = BundleDiscount.objects.active().get(
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
    elif len(variations) > 0:
        variation = variations[0]
        cached_context = dict(
            keywords=','.join([unicode(x) for x in product.keywords.all()]),
            size_chart=product.size_chart,
            has_price=variation.has_price(currency),
            on_sale=variation.on_sale(currency),
            is_marked_down=variation.is_marked_down(currency))
        cache.set(cache_key, cached_context, settings.CACHE_TIMEOUT['product_details'])
        context.update(cached_context)

    #Get the first promotion for this object
    if Promotion:
        #luxury TODO: print the deal's "post applied" message if cart has met requirements.
        upsell_promotions = Promotion.active.promotions_for_products(request.cart, [product])
        if upsell_promotions.count()>0:
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
        print "Variation does not exist"
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
       'discount_total': updated_context['discount_total'],
       'bundle_discount_total': updated_context['bundle_discount_total'],
       'total_price':  updated_context['order_total'],
       'shipping_total': updated_context['shipping_total'],
    }
    return simplejson.dumps(data, cls=DjangoJSONEncoder)

def _discount_form_for_cart(request):
    discount_code = request.POST.get("discount_code", None)
    if discount_code is None:
        discount_code = request.session.get("discount_code", None)
    return DiscountForm(request, {'discount_code': discount_code})

def _shipping_form_for_cart(request, currency):
    """
    If the user is submitting the form, get the shipping option from the
    form post. Otherwise, try to grab it from the session in order to
    set it to whatever it is currently (eg in the case of an ajax request
    coming through to verify the discount code, this is what we want to
    happen). If its not set in the session, then set it to the default
    shipping type for the current session currency.
    If the shipping type is not available as an option (eg set to FREE SHIPPING)
    then use the default, on the assumption that the free shipping value
    will be re-applied by set_shipping (which takes discount codes into
    consideration)
    """
    shipping_option = request.POST.get("shipping_option", None)
    if not shipping_option:
        shipping_option = request.session.get("shipping_type")
        if shipping_option is None or not is_local_shipping_option(currency, shipping_option) or \
            shipping_option == settings.FREE_SHIPPING:
            shipping_option = default_local_freight_type(currency).id
    return ShippingForm(request, currency, {"id": shipping_option})

def cart(request, template="shop/cart.html", extends_template="base.html"):
    """
    Display cart and handle removing items from the cart.
    """
    currency = session_currency(request)
    cart_formset = CartItemFormSet(instance=request.cart)
    shipping_form = _shipping_form_for_cart(request, currency)
    discount_form = _discount_form_for_cart(request)

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
            if discount_valid:
                discount_form.set_discount()
            shipping_valid = shipping_form.is_valid()
            if shipping_valid:
                shipping_form.set_shipping()
            valid = True if discount_valid and shipping_valid else False
            if not request.is_ajax() and valid:
                return redirect('shop_checkout')
        if valid:
            if request.is_ajax():
                return HttpResponse(_discount_data(request, discount_form), "application/javascript")
            else:
                return redirect("shop_checkout")

    context = {"cart_formset": cart_formset}
    settings.use_editable()
    if (settings.SHOP_DISCOUNT_FIELD_IN_CART and
        len(DiscountCode.objects.active()[:1]) > 0):
        context["discount_form"] = discount_form
    context["shipping_form"] = shipping_form

    context["extends_template"] = extends_template
    context['CURRENT_REGION'] = getattr(settings, 'CURRENT_REGION', '')
    context['bundle_discount_total'] = request.session.get('bundle_discount_total', None)

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
                'Please subscribe me to': ','.join(card_and_billing_data['subscription_options']),
                'I have read and accept the Privacy Policy': card_and_billing_data['privacy_policy'],
        })



    return response


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
    if len(no_stock) != 0:
        delattr(cart, '_cached_items')
    if request.POST.get("back") is not None:
        # Back button in the form was pressed - load the order form
        # for the previous step and maintain the field values entered.
        step -= 1
        form = form_class(request, step, initial=initial)
    elif request.method == "POST" and cart.has_items():
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

            # FIRST CHECKOUT STEP - handle shipping and discount code.
            if step == checkout.CHECKOUT_STEP_FIRST:
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
               "step_title": step_vars["title"], "step_url": step_vars["url"],
               "steps": checkout.CHECKOUT_STEPS, "step": step,
               'no_stock':no_stock}
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
    for variation in variations.select_related(depth=1):
        names[variation.sku] = variation.product.title
    for i, item in enumerate(items):
        setattr(items[i], "name", names[item.sku])
    context = {"order": order, "items": items,
               "extends_template": extends_template,
               'exchange_rates': exchange_rates(),
               "steps": checkout.CHECKOUT_STEPS}
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
