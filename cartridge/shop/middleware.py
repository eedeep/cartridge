
from mezzanine.conf import settings

from cartridge.shop.models import Cart
from cartridge_api.views import shop_ajax_cart_details

class SSLRedirect(object):

    def __init__(self):
        old = ("SHOP_SSL_ENABLED", "SHOP_FORCE_HOST", "SHOP_FORCE_SSL_VIEWS")
        for name in old:
            try:
                getattr(settings, name)
            except AttributeError:
                pass
            else:
                import warnings
                warnings.warn("The settings %s are deprecated; "
                    "use SSL_ENABLED, SSL_FORCE_HOST and "
                    "SSL_FORCE_URL_PREFIXES, and add "
                    "mezzanine.core.middleware.SSLRedirectMiddleware to "
                    "MIDDLEWARE_CLASSES." % ", ".join(old))
                break


class ShopMiddleware(SSLRedirect):
    """
    Adds cart and wishlist attributes to the current request.
    """
    def process_request(self, request):
        request.cart = Cart.objects.from_request(request)
        wishlist = request.COOKIES.get("wishlist", "").split(",")
        if not wishlist[0]:
            wishlist = []
        request.wishlist = wishlist

    def process_template_response(self, request, response):
        if hasattr(response, 'context_data'):
            response.context_data['minicart_details'] = shop_ajax_cart_details(request, False)
        return response
