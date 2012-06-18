
from decimal import Decimal
import locale

from django import template

from cartridge.shop.utils import set_locale


register = template.Library()


@register.filter
def currency(value):
    """
    Format a value as currency according to locale.
    """
    set_locale()
    if not value:
        value = 0
    if hasattr(locale, "currency"):
        value = locale.currency(value)
    else:
        # based on locale.currency() in python >= 2.5
        conv = locale.localeconv()
        value = [conv["currency_symbol"], conv["p_sep_by_space"] and " " or "",
            (("%%.%sf" % conv["frac_digits"]) % value).replace(".",
            conv["mon_decimal_point"])]
        if not conv["p_cs_precedes"]:
            value.reverse()
        value = "".join(value)
    return value

@register.filter
def productOptionColourName(value):
    from cartridge.shop.models import ProductOption
    return ProductOption.colourName(value)

def _order_totals(context):
    """
    Add ``item_total``, ``shipping_total``, ``discount_total`` and
    ``order_total`` to the template context. Use the order object for
    email receipts, or the cart object for checkout.
    """
    if "order" in context:
        for f in ("item_total", "shipping_total", "discount_total"):
            context[f] = getattr(context["order"], f)
    else:
        context["item_total"] = context["request"].cart.total_price()
        if context["item_total"] == 0:
            # Ignore session if cart has no items, as cart may have
            # expired sooner than the session.
            context["discount_total"] = context["shipping_total"] = 0
        else:
            for f in ("shipping_type", "shipping_total", "discount_total"):
                context[f] = context["request"].session.get(f, None)
    context["order_total"] = context.get("item_total", None)
    if context.get("shipping_total", None) is not None:
        context["order_total"] += Decimal(str(context["shipping_total"]))
    if context.get("discount_total", None) is not None:
        context["order_total"] -= context["discount_total"]
    return context


@register.inclusion_tag("shop/includes/order_totals.html", takes_context=True)
def order_totals(context):
    """
    HTML version of order_totals.
    """
    return _order_totals(context)

@register.inclusion_tag("shop/your_cart.html", takes_context=True)
def your_cart(context):
    """
    HTML version of order_totals.
    """
    return _order_totals(context)

@register.inclusion_tag("shop/includes/order_totals.txt", takes_context=True)
def order_totals_text(context):
    """
    Text version of order_totals.
    """
    return _order_totals(context)


@register.inclusion_tag("shop/product_sorting.html", takes_context=True)
def product_sorting(context, products):
    """
    Renders the links for each product sort option.
    """
    sort_options = [(option[0], slugify(option[0])) for option in
                                        settings.SHOP_PRODUCT_SORT_OPTIONS]
    querystring = context["request"].REQUEST.get("query", "")
    if querystring:
        querystring = "&query=" + quote(querystring)
    else:
        del sort_options[0]
    context.update({"selected_option": getattr(products, "sort"),
                    "sort_options": sort_options, "querystring": querystring})
    return context


@register.inclusion_tag("shop/product_paging.html", takes_context=True)
def product_paging(context, products):
    """
    Renders the links for each page number in a paginated list of products.
    """
    settings = context["settings"]
    querystring = ""
    page_range = products.paginator.page_range
    page_links = settings.SHOP_MAX_PAGING_LINKS
    if len(page_range) > page_links:
        start = min(products.paginator.num_pages - page_links,
            max(0, products.number - (page_links / 2) - 1))
        page_range = page_range[start:start + page_links]
    context.update({"products": products, "querystring": querystring,
                    "page_range": page_range})
    return context

@register.inclusion_tag("shop/related_products.html", takes_context=True)
def related_products_by_keywords(context, product):
    """
    Using the keywords rather than the explicit related products.
    """
    from cartridge.shop.models import Product
    keyword_ids = product.keywords.values_list('keyword', flat=True)
    products = Product.objects.published().filter(in_stock=True).exclude(pk=product.pk).filter(keywords__keyword__in=keyword_ids)
    context.update({
        "related_products": products,
        })
    return context
