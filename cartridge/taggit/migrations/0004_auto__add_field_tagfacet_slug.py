# -*- coding: utf-8 -*-
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding field 'TagFacet.slug'
        db.add_column('taggit_tagfacet', 'slug',
                      self.gf('django.db.models.fields.SlugField')(default='', max_length=100, blank=True),
                      keep_default=False)

        # Adding M2M table for field tags on 'TagFacet'
        db.create_table('taggit_tagfacet_tags', (
            ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True)),
            ('tagfacet', models.ForeignKey(orm['taggit.tagfacet'], null=False)),
            ('tag', models.ForeignKey(orm['taggit.tag'], null=False))
        ))
        db.create_unique('taggit_tagfacet_tags', ['tagfacet_id', 'tag_id'])

        # Removing M2M table for field facets on 'Tag'
        db.delete_table('taggit_tag_facets')

    def backwards(self, orm):
        # Deleting field 'TagFacet.slug'
        db.delete_column('taggit_tagfacet', 'slug')

        # Removing M2M table for field tags on 'TagFacet'
        db.delete_table('taggit_tagfacet_tags')

        # Adding M2M table for field facets on 'Tag'
        db.create_table('taggit_tag_facets', (
            ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True)),
            ('tag', models.ForeignKey(orm['taggit.tag'], null=False)),
            ('tagfacet', models.ForeignKey(orm['taggit.tagfacet'], null=False))
        ))
        db.create_unique('taggit_tag_facets', ['tag_id', 'tagfacet_id'])

    models = {
        'contenttypes.contenttype': {
            'Meta': {'ordering': "('name',)", 'unique_together': "(('app_label', 'model'),)", 'object_name': 'ContentType', 'db_table': "'django_content_type'"},
            'app_label': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'model': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'})
        },
        'taggit.tag': {
            'Meta': {'object_name': 'Tag'},
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'ranking': ('django.db.models.fields.IntegerField', [], {'default': '1000'}),
            'slug': ('django.db.models.fields.SlugField', [], {'max_length': '100'})
        },
        'taggit.tagfacet': {
            'Meta': {'object_name': 'TagFacet'},
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'slug': ('django.db.models.fields.SlugField', [], {'max_length': '100', 'blank': 'True'}),
            'tags': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['taggit.Tag']", 'symmetrical': 'False'})
        },
        'taggit.taggeditem': {
            'Meta': {'object_name': 'TaggedItem'},
            'content_type': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'taggit_taggeditem_tagged_items'", 'to': "orm['contenttypes.ContentType']"}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'object_id': ('django.db.models.fields.IntegerField', [], {'db_index': 'True'}),
            'tag': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'taggit_taggeditem_items'", 'to': "orm['taggit.Tag']"})
        }
    }

    complete_apps = ['taggit']
