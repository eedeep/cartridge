from django.contrib import admin
from django.contrib.contenttypes.models import ContentType

from cartridge.taggit.models import Tag, TaggedItem, TagFacet

from cartridge.shop.models import Product
from cartridge.shop.forms import TagAdminForm


class TaggedItemInline(admin.StackedInline):
    model = TaggedItem

class TagFacetAdmin(admin.ModelAdmin):
    filter_horizontal = ("tags", )
    search_fields=["name", "slug", "tags__name"]

class TagAdmin(admin.ModelAdmin):
    list_display = ["__unicode__", "name", "slug", "ranking"]
    list_editable = ("name",)
    search_fields=["name", "slug"]
    form = TagAdminForm

    def save_model(self, request, obj, form, change):
        super(TagAdmin, self).save_model(request, obj, form, change)

        # First delete any TaggedItems for this tag (equivalent of
        # clear but since we are coming at it 'from the other end' we
        # need to do it this way)
        TaggedItem.objects.filter(
            tag_id=obj.id,
            content_type_id=ContentType.objects.get_for_model(Product).id
        ).delete()

        # Now add whatever the user has selected, to the tag
        for product in form.cleaned_data['products']:
            product.tags.add(obj)
            product.save()


admin.site.register(Tag, TagAdmin)
admin.site.register(TagFacet, TagFacetAdmin)
