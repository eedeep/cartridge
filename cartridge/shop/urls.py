
from django.conf.urls.defaults import patterns, url


urlpatterns = patterns("cartridge.shop.views",
    url("^product/(?P<slug>.*)/$", "product", name="shop_product"),
    url("^wishlist/$", "wishlist", name="shop_wishlist"),
    url("^cart/$", "cart", name="shop_cart"),
    url("^checkout/$", "checkout_steps", name="shop_checkout"),
    url("^checkout/complete/$", "complete", name="shop_complete"),
    url("^invoice/(?P<order_id>\d+)/$", "invoice", name="shop_invoice"),
    #url("^check_unprocessed_orders/(?P<lookback_minutes>.*)/(?P<threshold_minutes>.*)/$", "check_unprocessed_orders"),
    #url("^search/$", "search", name="shop_search"),
    #    url("^cart/ajax/decrease/$", "shop_ajax_decrease", name="shop_ajax_decrease"),
    #url("^cart/ajax/increase/$", "shop_ajax_increase", name="shop_ajax_increase"),
    #url("^cart/ajax/shipping/$", "shop_ajax_shipping", name="shop_ajax_shipping"),
    #url("^cart/ajax/discount/$", "shop_ajax_discount", name="shop_ajax_discount"),
    #url("^category/ajax/(?P<slug>.*)/$", "category_products_ajax", name="category_products_ajax"),
)
