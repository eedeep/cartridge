"""
Checkout process utilities.
"""

from decimal import Decimal

from django.contrib.sites.models import Site
from django.utils.translation import ugettext as _
from django.template.loader import get_template, TemplateDoesNotExist

from mezzanine.conf import settings
from mezzanine.utils.email import send_mail_template

from cartridge.shop.models import Order
from cartridge.shop.utils import set_shipping, sign


class CheckoutError(Exception):
    """
    Should be raised in billing/shipping and payment handlers for
    cases such as an invalid shipping address or an unsuccessful
    payment.
    """
    pass


def default_billship_handler(request, order_form):
    """
    Default billing/shipping handler - called when the first step in
    the checkout process with billing/shipping address fields is
    submitted. Implement your own and specify the path to import it
    from via the setting ``SHOP_HANDLER_BILLING_SHIPPING``.
    This function will typically contain any shipping calculation
    where the shipping amount can then be set using the function
    ``cartridge.shop.utils.set_shipping``. The Cart object is also
    accessible via ``request.cart``
    """
    if not request.session.get('free_shipping'):
        settings.use_editable()
        set_shipping(request, _("Flat rate shipping"),
                    settings.SHOP_DEFAULT_SHIPPING_VALUE)


def default_payment_handler(request, order_form, order):
    """
    Default payment handler - called when the final step of the
    checkout process with payment information is submitted. Implement
    your own and specify the path to import it from via the setting
    ``SHOP_HANDLER_PAYMENT``. This function will typically contain
    integration with a payment gateway. Raise
    cartridge.shop.checkout.CheckoutError("error message") if payment
    is unsuccessful.
    """
    pass


def default_order_handler(request, order_form, order):
    """
    Default order handler - called when the order is complete and
    contains its final data. Implement your own and specify the path
    to import it from via the setting ``SHOP_HANDLER_ORDER``.
    """
    pass


def initial_order_data(request):
    """
    Return the initial data for the order form - favours request.POST,
    then session, then the last order deterined by either the current
    authenticated user, or from previous the order cookie set with
    "remember my details".
    """
    if request.method == "POST":
        order_data = dict(request.POST.items())

        # The POST dictionary decides that you don't really
        # want lists of data and so only returns the last item
        # of the subscription_options. So go back and get the
        # value directly. Seems this is probably not django's
        # problem rather a result of the way cgi.parse_qs works.
        order_data['subscription_options'] = request.POST.getlist(
            'subscription_options',
        )
        return order_data
    if "order" in request.session:
        # This seems somewhat arbitrary as a place to put this but
        # we want to clear any previously calculated tax from the session
        # if the user is going back to the billing/shipping page to edit
        # their details prior to completeing their checkout. Reason for this
        # is largely due to US tax calculations being
        # dependent on the shipping address specified by the customer
        request.session.pop('tax_total', None)
        request.session.pop('tax_type', None)
        return request.session["order"]
    previous_lookup = {}
    if request.user.is_authenticated():
        previous_lookup["user_id"] = request.user.id
    remembered = request.COOKIES.get("remember", "").split(":")
    if len(remembered) == 2 and remembered[0] == sign(remembered[1]):
        previous_lookup["key"] = remembered[1]
    initial = {}
    if previous_lookup:
        previous_orders = Order.objects.filter(**previous_lookup).values()[:1]
        if len(previous_orders) > 0:
            initial.update(previous_orders[0])
            if 'discount_code' in initial:
                initial.pop('discount_code')
            # Set initial value for "same billing/shipping" based on
            # whether both sets of address fields are all equal.
            shipping = lambda f: "shipping_%s" % f[len("billing_"):]
            if any([f for f in initial if f.startswith("billing_") and
                shipping(f) in initial and
                initial[f] != initial[shipping(f)]]):
                initial["same_billing_shipping"] = False
    initial['discount_code'] = request.session.get('discount_code', None)


    if 'shipping_detail_country' not in initial:
        currency = request.session['currency']
        store_config = settings.STORE_CONFIGS[currency]
        default_country = store_config.name.upper()
        initial['shipping_detail_country'] = default_country
        initial['billing_detail_country'] = default_country

    return initial


def _order_email_context(order):
    """ Return the context with all info rendering an order receipt will need.
    Used by send_order_email and export to PDF in admin """
    order_context = {"order": order, "order_items": order.items.all()}

    store_config = settings.STORE_CONFIGS[order.currency]
    order_context["tax_type"] = store_config.tax_type
    order_context['site_domain'] = Site.objects.get(id=settings.SITE_ID).domain
    # This caters for stores which calculate tax via a tax
    # handler, like the US store for example
    if hasattr(order, "tax_total"):
        order_context["tax_amount"] = order.tax_total
    else:
        order_context["tax_amount"] = order.item_total * store_config.tax_rate

    for fieldset in ("billing_detail", "shipping_detail"):
        fields = [(f.verbose_name, getattr(order, f.name)) for f in
            order._meta.fields if f.name.startswith(fieldset)]
        order_context["order_%s_fields" % fieldset] = fields
    return order_context


def send_email(template_name, subject, request, order):
    from cottonon_shop.models import GPPPoint
    from cartridge.shop.views import get_or_create_discount
    order_context = _order_email_context(order)
    order_context['discount'] = get_or_create_discount(order)
    order_context['gpp_code'] = GPPPoint.gpp_code(order)
    order_context["request"] = request
    send_mail_template(_(subject), template_name,
                       settings.SHOP_ORDER_FROM_EMAIL, order.billing_detail_email,
                       context=order_context)


def send_optional_pre_order_email(request, order):
    """
    If configured, send an email advising the customer that their
    order receipt is on the way.
    """
    if settings.SEND_PRE_ORDER_RECEIPT_EMAIL:
        send_email("shop/email/pre_order_advisory", "Order Receipt Coming", request, order)


def send_order_email(request, order):
    """
    Send order receipt email on successful order.
    """
    send_email("shop/email/order_receipt", "Order Receipt", request, order)


# Set up some constants for identifying each checkout step.
CHECKOUT_STEPS = [{"template": "billing_shipping", "url": "details",
                   "title": _("Details")}]
CHECKOUT_STEP_FIRST = CHECKOUT_STEP_PAYMENT = CHECKOUT_STEP_LAST = 1
if settings.SHOP_CHECKOUT_STEPS_SPLIT:
    CHECKOUT_STEPS[0].update({"url": "billing-shipping",
                              "title": _("Address")})
    if settings.SHOP_PAYMENT_STEP_ENABLED:
        CHECKOUT_STEPS.append({"template": "payment", "url": "payment",
                                "title": _("Payment")})
        CHECKOUT_STEP_PAYMENT = CHECKOUT_STEP_LAST = 2
if settings.SHOP_CHECKOUT_STEPS_CONFIRMATION:
    CHECKOUT_STEPS.append({"template": "confirmation", "url": "confirmation",
                           "title": _("Confirmation")})
    CHECKOUT_STEP_LAST += 1
