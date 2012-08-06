# -*- coding: utf-8 -*-
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding model 'TagFacet'
        db.create_table('taggit_tagfacet', (
            ('id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('name', self.gf('django.db.models.fields.CharField')(max_length=100)),
        ))
        db.send_create_signal('taggit', ['TagFacet'])

        # Adding model 'Tag'
        db.create_table('taggit_tag', (
            ('id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('name', self.gf('django.db.models.fields.CharField')(max_length=100)),
            ('slug', self.gf('django.db.models.fields.SlugField')(unique=True, max_length=100)),
            ('ranking', self.gf('django.db.models.fields.IntegerField')(default=1000)),
        ))
        db.send_create_signal('taggit', ['Tag'])

        # Adding M2M table for field facets on 'Tag'
        db.create_table('taggit_tag_facets', (
            ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True)),
            ('tag', models.ForeignKey(orm['taggit.tag'], null=False)),
            ('tagfacet', models.ForeignKey(orm['taggit.tagfacet'], null=False))
        ))
        db.create_unique('taggit_tag_facets', ['tag_id', 'tagfacet_id'])

        # Adding model 'TaggedItem'
        db.create_table('taggit_taggeditem', (
            ('id', self.gf('django.db.models.fields.AutoField')(primary_key=True)),
            ('tag', self.gf('django.db.models.fields.related.ForeignKey')(related_name='taggit_taggeditem_items', to=orm['taggit.Tag'])),
            ('object_id', self.gf('django.db.models.fields.IntegerField')(db_index=True)),
            ('content_type', self.gf('django.db.models.fields.related.ForeignKey')(related_name='taggit_taggeditem_tagged_items', to=orm['contenttypes.ContentType'])),
        ))
        db.send_create_signal('taggit', ['TaggedItem'])

    def backwards(self, orm):
        # Deleting model 'TagFacet'
        db.delete_table('taggit_tagfacet')

        # Deleting model 'Tag'
        db.delete_table('taggit_tag')

        # Removing M2M table for field facets on 'Tag'
        db.delete_table('taggit_tag_facets')

        # Deleting model 'TaggedItem'
        db.delete_table('taggit_taggeditem')

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
            'facets': ('django.db.models.fields.related.ManyToManyField', [], {'to': "orm['taggit.TagFacet']", 'symmetrical': 'False'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'ranking': ('django.db.models.fields.IntegerField', [], {'default': '1000'}),
            'slug': ('django.db.models.fields.SlugField', [], {'unique': 'True', 'max_length': '100'})
        },
        'taggit.tagfacet': {
            'Meta': {'object_name': 'TagFacet'},
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'})
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