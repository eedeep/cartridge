
from django.conf.urls.defaults import patterns, url


urlpatterns = patterns("cartridge.shop.views",
    url("^return-from-checkout-with-paypal/$", "return_from_checkout_with_paypal", name="return_from_checkout_with_paypal"),
    url("^cancel-checkout-with-paypal/$", "cancel_checkout_with_paypal", name="cancel_checkout_with_paypal"),
    url("^product/(?P<slug>.*)/$", "product", name="shop_product"),
    url("^modal/(?P<slug>.*)/$", "product", {"template": "shop/product_modal.html"}, name="shop_product_modal"),
    url("^wishlist/$", "wishlist", name="shop_wishlist"),
    url("^cart/$", "cart", name="shop_cart"),
    url("^checkout/$", "checkout_steps", name="shop_checkout"),
    url("^checkout/complete/$", "complete", name="shop_complete"),
    url('^checkout/cybersource-hook/$', 'cybersource_hook', name='cybersource_hook'),
    url('^checkout/cybersource-complete/$', 'cybersource_complete', name='cybersource_complete'),
    url("^checkout/aborted/(?P<transaction_slug>.*)/$", "abort", name="shop_abort"),
    url("^invoice/(?P<order_id>\d+)/$", "invoice", name="shop_invoice"),
    #url("^check_unprocessed_orders/(?P<lookback_minutes>.*)/(?P<threshold_minutes>.*)/$", "check_unprocessed_orders"),
    #url("^search/$", "search", name="shop_search"),
    #url("^cart/ajax/discount/$", "shop_ajax_discount", name="shop_ajax_discount"),
    #url("^category/ajax/(?P<slug>.*)/$", "category_products_ajax", name="category_products_ajax"),
)
