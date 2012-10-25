
import hmac
from locale import setlocale, LC_MONETARY
try:
    from hashlib import sha512 as digest
except ImportError:
    from md5 import new as digest

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import ugettext as _

from mezzanine.conf import settings

class EmptyCart(object):
    """
A dummy cart object used before any items have been added.
Used to avoid querying the database for cart and items on each
request.
"""

    id = None
    currency = None
    has_items = lambda *a, **k: False
    skus = lambda *a, **k: []
    upsell_products = lambda *a, **k: []
    total_quantity = lambda *a, **k: 0
    total_price = lambda *a, **k: 0
    calculate_discount = lambda *a, **k: 0
    has_no_stock = lambda *a, **k: []
    has_items = lambda *a, **k: False
    __int__ = lambda *a, **k: 0
    __iter__ = lambda *a, **k: iter([])
    switch_currency = lambda *a: None
    def __init__(self, request):
        """
Store the request so we can add the real cart ID to the
session if any items get added.
"""
        self._request = request

    def add_item(self, *args, **kwargs):
        """
Create a real cart object, add the items to it and store
the cart ID in the session.
"""
        from multicurrency.models import MultiCurrencyCart
        cart = MultiCurrencyCart.objects.create(currency=self.currency)
        cart.add_item(*args, **kwargs)
        self._request.session["cart"] = cart.id

def make_choices(choices):
    """
    Zips a list with itself for field choices.
    """
    return zip(choices, choices)


def recalculate_discount(request):
    """
    Updates an existing discount code when the cart is modified.
    """
    from cartridge.shop.forms import DiscountForm
    from cartridge.shop.models import Cart
    # Rebind the cart to request since it's been modified.
    request.cart = Cart.objects.from_request(request)
    discount_code = request.session.get("discount_code", "")
    discount_form = DiscountForm(request, {"discount_code": discount_code})
    if discount_form.is_valid():
        discount_form.set_discount()
    else:
        try:
            del request.session["discount_total"]
        except KeyError:
            pass

def set_discount(request, discount_total):
    """
    Stores the discount total the session.
    """
    if discount_total == None:
        if request.session.has_key("discount_total"): del request.session["discount_total"]
        if "discount_code" in request.session: del request.session["discount_code"]
    else:
        request.session["discount_total"] = discount_total

def set_shipping(request, shipping_type=None, shipping_total=None):
    """
    Stores the shipping type and total in the session.
    """
    request.session["shipping_type"] = shipping_type
    request.session["shipping_total"] = shipping_total

def sign(value):
    """
    Returns the hash of the given value, used for signing order key stored in
    cookie for remembering address fields.
    """
    return hmac.new(settings.SECRET_KEY, value, digest).hexdigest()


def set_locale():
    """
    Sets the locale for currency formatting.
    """
    currency_locale = settings.SHOP_CURRENCY_LOCALE
    try:
        if setlocale(LC_MONETARY, currency_locale) == "C":
            # C locale doesn't contain a suitable value for "frac_digits".
            raise
    except:
        msg = _("Invalid currency locale specified for SHOP_CURRENCY_LOCALE: "
                "'%s'. You'll need to set the locale for your system, or "
                "configure the SHOP_CURRENCY_LOCALE setting in your settings "
                "module.")
        raise ImproperlyConfigured(msg % currency_locale)

def make_sku_safe(sku):
    """
    XXX: This is is not needed and should be *only* in the product upload
         I have added the decimal safety hack until we replace the product upload
         with a better system at which time this should be removed - MB
    """
    clean_sku = sku.replace("/", "_")
    if clean_sku.endswith('.0'):
        print "**ERROR** Decimal value found in xlsx file in sku %s" % clean_sku
        clean_sku = clean_sku.split('.')[0]
    return clean_sku


def render_to_pdf(template_src, context_dict):
    """ Courtesy: http://stackoverflow.com/questions/1377446/html-to-pdf-for-a-django-site """
    template = get_template(template_src)
    context = Context(context_dict)
    html  = template.render(context)
    result = StringIO.StringIO()

    pdf = pisa.pisaDocument(StringIO.StringIO(html.encode("ISO-8859-1")), result)
    if not pdf.err:
         return HttpResponse(result.getvalue(), mimetype='application/pdf')
    return HttpResponse('We had some errors<pre>%s</pre>' % escape(html))

