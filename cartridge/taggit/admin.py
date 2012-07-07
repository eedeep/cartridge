from django.contrib import admin

from cartridge.taggit.models import Tag, TaggedItem, TagFacet


class TaggedItemInline(admin.StackedInline):
    model = TaggedItem

class TagFacetAdmin(admin.ModelAdmin):
    filter_horizontal = ("tags", )
    search_fields=["name", "slug", "tags__name"]

class TagAdmin(admin.ModelAdmin):
    list_display = ["__unicode__", "name", "slug", "ranking"]
    list_editable = ("name",)
    search_fields=["name", "slug"]
    inlines = [
        TaggedItemInline
    ]


admin.site.register(Tag, TagAdmin)
admin.site.register(TagFacet, TagFacetAdmin)
