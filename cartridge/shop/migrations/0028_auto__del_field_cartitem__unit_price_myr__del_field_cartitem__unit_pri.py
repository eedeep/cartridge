# -*- coding: utf-8 -*-
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding field 'Product.rms_category'
        db.add_column('shop_product', 'rms_category',
                      self.gf('django.db.models.fields.related.ForeignKey')(to=orm['rdws.RmsOrganisationUnit'], null=True, blank=True),
                      keep_default=False)

    def backwards(self, orm):
        # Deleting field 'Product.rms_category'
        db.delete_column('shop_product', 'rms_category_id')

    models = {
        'contenttypes.contenttype': {
            'Meta': {'ordering': "('name',)", 'unique_together': "(('app_label', 'model'),)", 'object_name': 'ContentType', 'db_table': "'django_content_type'"},
            'app_label': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'model': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'})
        },
        'countries.country': {
            'Meta': {'ordering': "('printable_name',)", 'object_name': 'Country', 'db_table': "'country'"},
            'iso': ('django.db.models.fields.CharField', [], {'max_length': '2', 'primary_key': 'True'}),
            'iso3': ('django.db.models.fields.CharField', [], {'max_length': '3', 'null': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '128'}),
            'numcode': ('django.db.models.fields.PositiveSmallIntegerField', [], {'null': 'True'}),
            'printable_name': ('django.db.models.fields.CharField', [], {'max_length': '128'}),
            'rms_code': ('django.db.models.fields.CharField', [], {'max_length': '12', 'null': 'True'})
        },
        'generic.assignedkeyword': {
            'Meta': {'ordering': "('_order',)", 'object_name': 'AssignedKeyword'},
            '_order': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'content_type': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['contenttypes.ContentType']"}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'keyword': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'assignments'", 'to': "orm['generic.Keyword']"}),
            'object_pk': ('django.db.models.fields.IntegerField', [], {})
        },
        'generic.keyword': {
            'Meta': {'object_name': 'Keyword'},
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'site': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['sites.Site']"}),
            'slug': ('django.db.models.fields.CharField', [], {'max_length': '2000', 'null': 'True', 'blank': 'True'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '500'})
        },
        'generic.rating': {
            'Meta': {'object_name': 'Rating'},
            'content_type': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['contenttypes.ContentType']"}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'object_pk': ('django.db.models.fields.IntegerField', [], {}),
            'value': ('django.db.models.fields.IntegerField', [], {})
        },
        'pages.page': {
            'Meta': {'ordering': "('titles',)", 'object_name': 'Page'},
            '_order': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'content_model': ('django.db.models.fields.CharField', [], {'max_length': '50', 'null': 'True'}),
            'description': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'expiry_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'gen_description': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'in_footer': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'in_navigation': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'keywords': ('mezzanine.generic.fields.KeywordsField', [], {'object_id_field': "'object_pk'", 'to': "orm['generic.AssignedKeyword']"}),
            'keywords_string': ('django.db.models.fields.CharField', [], {'max_length': '500', 'blank': 'True'}),
            'login_required': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'parent': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "'children'", 'null': 'True', 'to': "orm['pages.Page']"}),
            'publish_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'short_url': ('django.db.models.fields.URLField', [], {'max_length': '200', 'null': 'True', 'blank': 'True'}),
            'site': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['sites.Site']"}),
            'slug': ('django.db.models.fields.CharField', [], {'max_length': '2000', 'null': 'True', 'blank': 'True'}),
            'status': ('django.db.models.fields.IntegerField', [], {'default': '2'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '500'}),
            'titles': ('django.db.models.fields.CharField', [], {'max_length': '1000', 'null': 'True'})
        },
        'rdws.rmsorganisationunit': {
            'Meta': {'object_name': 'RmsOrganisationUnit'},
            'code': ('django.db.models.fields.IntegerField', [], {'unique': 'True', 'max_length': '50'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'level': ('django.db.models.fields.PositiveIntegerField', [], {'db_index': 'True'}),
            'lft': ('django.db.models.fields.PositiveIntegerField', [], {'db_index': 'True'}),
            'mapped_online_categories': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['shop.Category']", 'symmetrical': 'False', 'blank': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50'}),
            'org_type': ('django.db.models.fields.CharField', [], {'default': "'DIVISION'", 'max_length': '20'}),
            'parent': ('mptt.fields.TreeForeignKey', [], {'blank': 'True', 'related_name': "'children'", 'null': 'True', 'to': "orm['rdws.RmsOrganisationUnit']"}),
            'rght': ('django.db.models.fields.PositiveIntegerField', [], {'db_index': 'True'}),
            'tree_id': ('django.db.models.fields.PositiveIntegerField', [], {'db_index': 'True'})
        },
        'shop.bundlediscount': {
            'Meta': {'object_name': 'BundleDiscount'},
            '_applied_message_usd': ('django.db.models.fields.CharField', [], {'max_length': '256', 'null': 'True', 'blank': 'True'}),
            '_applied_upsell_message_usd': ('django.db.models.fields.CharField', [], {'max_length': '256', 'null': 'True', 'blank': 'True'}),
            '_bundled_unit_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_title_usd': ('django.db.models.fields.CharField', [], {'max_length': '64', 'null': 'True', 'blank': 'True'}),
            '_upsell_message_usd': ('django.db.models.fields.CharField', [], {'max_length': '256', 'null': 'True', 'blank': 'True'}),
            'active': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'applied_message': ('django.db.models.fields.CharField', [], {'max_length': '256', 'null': 'True', 'blank': 'True'}),
            'applied_upsell_message': ('django.db.models.fields.CharField', [], {'max_length': '256', 'null': 'True', 'blank': 'True'}),
            'bundled_unit_price': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'categories': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['shop.Category']", 'symmetrical': 'False', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'products': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['shop.Product']", 'symmetrical': 'False', 'blank': 'True'}),
            'quantity': ('django.db.models.fields.IntegerField', [], {'default': '2'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'upsell_message': ('django.db.models.fields.CharField', [], {'max_length': '256', 'null': 'True', 'blank': 'True'}),
            'upsell_product': ('django.db.models.fields.CharField', [], {'max_length': '100', 'null': 'True', 'blank': 'True'}),
            'valid_from': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'valid_to': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'})
        },
        'shop.cart': {
            'Meta': {'object_name': 'Cart'},
            'currency': ('django.db.models.fields.CharField', [], {'max_length': '6', 'null': 'True', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'last_updated': ('django.db.models.fields.DateTimeField', [], {'auto_now': 'True', 'null': 'True', 'blank': 'True'})
        },
        'shop.cartitem': {
            'Meta': {'object_name': 'CartItem'},
            '_unit_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            'bundle_quantity': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'bundle_title': ('django.db.models.fields.CharField', [], {'max_length': '100', 'null': 'True', 'blank': 'True'}),
            'bundle_unit_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'cart': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'items'", 'to': "orm['shop.Cart']"}),
            'description': ('django.db.models.fields.CharField', [], {'max_length': '200'}),
            'discount_unit_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'image': ('django.db.models.fields.CharField', [], {'max_length': '200', 'null': 'True'}),
            'quantity': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'sku': ('cartridge.shop.fields.SKUField', [], {'max_length': '20'}),
            'total_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'unit_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'url': ('django.db.models.fields.CharField', [], {'max_length': '200'})
        },
        'shop.category': {
            'Meta': {'ordering': "('_order',)", 'object_name': 'Category', '_ormbases': ['pages.Page']},
            'content': ('mezzanine.core.fields.RichTextField', [], {}),
            'hide_sizes': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'page_ptr': ('django.db.models.fields.related.OneToOneField', [], {'to': "orm['pages.Page']", 'unique': 'True', 'primary_key': 'True'})
        },
        'shop.categorypageimage': {
            'Meta': {'object_name': 'CategoryPageImage'},
            'active': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'alt_text': ('django.db.models.fields.CharField', [], {'max_length': '140', 'blank': 'True'}),
            'created': ('django.db.models.fields.DateTimeField', [], {'auto_now_add': 'True', 'blank': 'True'}),
            'expiry_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'image': ('django.db.models.fields.files.ImageField', [], {'max_length': '100', 'blank': 'True'}),
            'link': ('django.db.models.fields.URLField', [], {'max_length': '160', 'blank': 'True'}),
            'panel': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['shop.CategoryTheme']"}),
            'publish_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'})
        },
        'shop.categorytheme': {
            'Meta': {'object_name': 'CategoryTheme', 'db_table': "'shop_categorypage'"},
            'category': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['shop.Category']", 'unique': 'True'}),
            'created': ('django.db.models.fields.DateTimeField', [], {'auto_now_add': 'True', 'blank': 'True'}),
            'expiry_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'just_arrived': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'catergorypage_just_arrived_set'", 'to': "orm['shop.Category']"}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '60'}),
            'publish_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'status': ('django.db.models.fields.IntegerField', [], {'default': '1'})
        },
        'shop.discountcode': {
            'Meta': {'object_name': 'DiscountCode'},
            '_discount_deduct_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_discount_exact_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_min_purchase_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            'active': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'allowed_no_of_uses': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'categories': ('django.db.models.fields.related.ManyToManyField', [], {'symmetrical': 'False', 'related_name': "'discountcode_related'", 'blank': 'True', 'to': "orm['shop.Category']"}),
            'code': ('cartridge.shop.fields.DiscountCodeField', [], {'unique': 'True', 'max_length': '20'}),
            'discount_deduct': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'discount_exact': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'discount_percent': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '4', 'decimal_places': '2', 'blank': 'True'}),
            'free_shipping': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'min_purchase': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'no_of_times_used': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'products': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['shop.Product']", 'symmetrical': 'False', 'blank': 'True'}),
            'shipping_restriction': ('django.db.models.fields.related.ManyToManyField', [], {'symmetrical': 'False', 'to': "orm['shop.ShippingOption']", 'null': 'True', 'blank': 'True'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'valid_from': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'valid_to': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'})
        },
        'shop.footercategorypageimage': {
            'Meta': {'object_name': 'FooterCategoryPageImage', '_ormbases': ['shop.CategoryPageImage']},
            'categorypageimage_ptr': ('django.db.models.fields.related.OneToOneField', [], {'to': "orm['shop.CategoryPageImage']", 'unique': 'True', 'primary_key': 'True'})
        },
        'shop.order': {
            'Meta': {'ordering': "('-id',)", 'object_name': 'Order'},
            'additional_instructions': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'billing_detail_city': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'billing_detail_country': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'billing_detail_email': ('django.db.models.fields.EmailField', [], {'max_length': '75'}),
            'billing_detail_first_name': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'billing_detail_last_name': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'billing_detail_phone': ('django.db.models.fields.CharField', [], {'max_length': '16'}),
            'billing_detail_postcode': ('django.db.models.fields.CharField', [], {'max_length': '10'}),
            'billing_detail_state': ('django.db.models.fields.CharField', [], {'max_length': '12'}),
            'billing_detail_street': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'billing_detail_street2': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '32', 'blank': 'True'}),
            'currency': ('django.db.models.fields.CharField', [], {'max_length': '6', 'null': 'True', 'blank': 'True'}),
            'discount_code': ('cartridge.shop.fields.DiscountCodeField', [], {'max_length': '20', 'blank': 'True'}),
            'discount_total': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'has_rms_order_id': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'item_total': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'key': ('django.db.models.fields.CharField', [], {'max_length': '40'}),
            'payment_gateway_transaction_id': ('django.db.models.fields.CharField', [], {'max_length': '80', 'blank': 'True'}),
            'payment_gateway_transaction_type': ('django.db.models.fields.CharField', [], {'max_length': '80', 'blank': 'True'}),
            'rms_customer_id': ('django.db.models.fields.CharField', [], {'max_length': '60', 'blank': 'True'}),
            'rms_last_submitted': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'rms_message': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '200', 'blank': 'True'}),
            'rms_order_id': ('django.db.models.fields.CharField', [], {'max_length': '60', 'blank': 'True'}),
            'shipping_detail_city': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'shipping_detail_country': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'shipping_detail_first_name': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'shipping_detail_last_name': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'shipping_detail_phone': ('django.db.models.fields.CharField', [], {'max_length': '16'}),
            'shipping_detail_postcode': ('django.db.models.fields.CharField', [], {'max_length': '12'}),
            'shipping_detail_state': ('django.db.models.fields.CharField', [], {'max_length': '12'}),
            'shipping_detail_street': ('django.db.models.fields.CharField', [], {'max_length': '32'}),
            'shipping_detail_street2': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '32', 'blank': 'True'}),
            'shipping_total': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'shipping_type': ('django.db.models.fields.CharField', [], {'max_length': '50', 'blank': 'True'}),
            'status': ('django.db.models.fields.IntegerField', [], {'default': '1'}),
            'time': ('django.db.models.fields.DateTimeField', [], {'auto_now_add': 'True', 'null': 'True', 'blank': 'True'}),
            'total': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'transaction_id': ('django.db.models.fields.CharField', [], {'max_length': '255', 'null': 'True', 'blank': 'True'}),
            'user_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'})
        },
        'shop.orderitem': {
            'Meta': {'object_name': 'OrderItem'},
            'bundle_quantity': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'bundle_title': ('django.db.models.fields.CharField', [], {'max_length': '100', 'null': 'True', 'blank': 'True'}),
            'bundle_unit_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'description': ('django.db.models.fields.CharField', [], {'max_length': '200'}),
            'discount_unit_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'order': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'items'", 'to': "orm['shop.Order']"}),
            'quantity': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'sku': ('cartridge.shop.fields.SKUField', [], {'max_length': '20'}),
            'total_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'unit_price': ('cartridge.shop.fields.MoneyField', [], {'default': "'0'", 'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'})
        },
        'shop.primarycategorypageimage': {
            'Meta': {'object_name': 'PrimaryCategoryPageImage', '_ormbases': ['shop.CategoryPageImage']},
            'categorypageimage_ptr': ('django.db.models.fields.related.OneToOneField', [], {'to': "orm['shop.CategoryPageImage']", 'unique': 'True', 'primary_key': 'True'})
        },
        'shop.product': {
            'Meta': {'ordering': "('ranking', 'title')", 'object_name': 'Product'},
            '_sale_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_unit_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_was_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            'available': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'bundle_discount_id': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'categories': ('django.db.models.fields.related.ManyToManyField', [], {'symmetrical': 'False', 'related_name': "'products'", 'blank': 'True', 'to': "orm['shop.Category']"}),
            'content': ('mezzanine.core.fields.RichTextField', [], {}),
            'date_added': ('django.db.models.fields.DateTimeField', [], {'auto_now_add': 'True', 'null': 'True', 'blank': 'True'}),
            'date_images_last_synced': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'date_stock_last_synced': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'description': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'expiry_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'featured': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'first_published_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'gen_description': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'image': ('django.db.models.fields.CharField', [], {'max_length': '100', 'null': 'True', 'blank': 'True'}),
            'in_stock': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'keywords': ('mezzanine.generic.fields.KeywordsField', [], {'object_id_field': "'object_pk'", 'to': "orm['generic.AssignedKeyword']"}),
            'keywords_string': ('django.db.models.fields.CharField', [], {'max_length': '500', 'blank': 'True'}),
            'master_item_code': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '64'}),
            'product_colours': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '500', 'blank': 'True'}),
            'product_sizes': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '255', 'blank': 'True'}),
            'publish_date': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'ranking': ('django.db.models.fields.IntegerField', [], {'default': '250'}),
            'rating': ('mezzanine.generic.fields.RatingField', [], {'object_id_field': "'object_pk'", 'to': "orm['generic.Rating']"}),
            'rating_average': ('django.db.models.fields.FloatField', [], {'default': '0'}),
            'rating_count': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'related_products': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['shop.Product']", 'symmetrical': 'False', 'blank': 'True'}),
            'rms_category': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['rdws.RmsOrganisationUnit']", 'null': 'True', 'blank': 'True'}),
            'sale_from': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'sale_id': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'sale_price': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'sale_to': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'short_url': ('django.db.models.fields.URLField', [], {'max_length': '200', 'null': 'True', 'blank': 'True'}),
            'site': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['sites.Site']"}),
            'slug': ('django.db.models.fields.CharField', [], {'max_length': '2000', 'null': 'True', 'blank': 'True'}),
            'status': ('django.db.models.fields.IntegerField', [], {'default': '2'}),
            'sync_images': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'sync_stock': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '500'}),
            'unit_price': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'upsell_products': ('django.db.models.fields.related.ManyToManyField', [], {'related_name': "'upsell_products_rel_+'", 'blank': 'True', 'to': "orm['shop.Product']"})
        },
        'shop.productaction': {
            'Meta': {'unique_together': "(('product', 'timestamp'),)", 'object_name': 'ProductAction'},
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'product': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'actions'", 'to': "orm['shop.Product']"}),
            'timestamp': ('django.db.models.fields.IntegerField', [], {}),
            'total_cart': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'total_purchase': ('django.db.models.fields.IntegerField', [], {'default': '0'})
        },
        'shop.productimage': {
            'Meta': {'ordering': "('_order',)", 'object_name': 'ProductImage'},
            '_order': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'description': ('django.db.models.fields.CharField', [], {'max_length': '100', 'blank': 'True'}),
            'file': ('django.db.models.fields.files.ImageField', [], {'max_length': '100'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'product': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'images'", 'to': "orm['shop.Product']"})
        },
        'shop.productoption': {
            'Meta': {'object_name': 'ProductOption'},
            'display_name': ('django.db.models.fields.CharField', [], {'max_length': '100', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('cartridge.shop.fields.OptionField', [], {'max_length': '50', 'null': 'True'}),
            'ranking': ('django.db.models.fields.IntegerField', [], {'default': '100'}),
            'type': ('django.db.models.fields.IntegerField', [], {})
        },
        'shop.productsyncrequest': {
            'Meta': {'object_name': 'ProductSyncRequest'},
            'images': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'product': ('django.db.models.fields.related.OneToOneField', [], {'to': "orm['shop.Product']", 'unique': 'True', 'primary_key': 'True'}),
            'stock': ('django.db.models.fields.BooleanField', [], {'default': 'False'})
        },
        'shop.productvariation': {
            'Meta': {'ordering': "('-default',)", 'object_name': 'ProductVariation'},
            '_sale_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_unit_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_was_price_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            'bundle_discount_id': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'default': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'image': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['shop.ProductImage']", 'null': 'True', 'on_delete': 'models.SET_NULL', 'blank': 'True'}),
            'num_in_stock': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'num_in_stock_pool': ('django.db.models.fields.PositiveIntegerField', [], {'default': '0', 'blank': 'True'}),
            'option1': ('cartridge.shop.fields.OptionField', [], {'max_length': '50', 'null': 'True'}),
            'option2': ('cartridge.shop.fields.OptionField', [], {'max_length': '50', 'null': 'True'}),
            'product': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'variations'", 'to': "orm['shop.Product']"}),
            'sale_from': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'sale_id': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'sale_price': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'sale_to': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'sku': ('cartridge.shop.fields.SKUField', [], {'unique': 'True', 'max_length': '20'}),
            'unit_price': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'})
        },
        'shop.sale': {
            'Meta': {'object_name': 'Sale'},
            '_discount_deduct_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            '_discount_exact_usd': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '12', 'decimal_places': '2', 'blank': 'True'}),
            'active': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'categories': ('django.db.models.fields.related.ManyToManyField', [], {'symmetrical': 'False', 'related_name': "'sale_related'", 'blank': 'True', 'to': "orm['shop.Category']"}),
            'discount_deduct': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'discount_exact': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'discount_percent': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '4', 'decimal_places': '2', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'products': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['shop.Product']", 'symmetrical': 'False', 'blank': 'True'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'valid_from': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'valid_to': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'})
        },
        'shop.secondarycategorypageimage': {
            'Meta': {'object_name': 'SecondaryCategoryPageImage', '_ormbases': ['shop.CategoryPageImage']},
            'categorypageimage_ptr': ('django.db.models.fields.related.OneToOneField', [], {'to': "orm['shop.CategoryPageImage']", 'unique': 'True', 'primary_key': 'True'})
        },
        'shop.shippingoption': {
            'Meta': {'ordering': "['price']", 'object_name': 'ShippingOption'},
            'active': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'countries': ('django.db.models.fields.related.ManyToManyField', [], {'symmetrical': 'False', 'related_name': "'availablecountries'", 'blank': 'True', 'to': "orm['countries.Country']"}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'price': ('cartridge.shop.fields.MoneyField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '2', 'blank': 'True'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '200'}),
            'valid_from': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'valid_to': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'})
        },
        'sites.site': {
            'Meta': {'ordering': "('domain',)", 'object_name': 'Site', 'db_table': "'django_site'"},
            'domain': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50'})
        },
        'taggit.tag': {
            'Meta': {'ordering': "['name']", 'object_name': 'Tag'},
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'ranking': ('django.db.models.fields.IntegerField', [], {'default': '1000'}),
            'slug': ('django.db.models.fields.SlugField', [], {'unique': 'True', 'max_length': '100'})
        },
        'taggit.taggeditem': {
            'Meta': {'object_name': 'TaggedItem'},
            'content_type': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'taggit_taggeditem_tagged_items'", 'to': "orm['contenttypes.ContentType']"}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'object_id': ('django.db.models.fields.IntegerField', [], {'db_index': 'True'}),
            'tag': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'taggit_taggeditem_items'", 'to': "orm['taggit.Tag']"})
        }
    }

    complete_apps = ['shop']
