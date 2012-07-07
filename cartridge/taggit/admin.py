from django.contrib import admin

from cartridge.taggit.models import Tag, TaggedItem, TagFacet


class TaggedItemInline(admin.StackedInline):
    model = TaggedItem

class TagAdmin(admin.ModelAdmin):
    list_display = ["__unicode__", "ranking"]
    search_fields=["name", "slug"]
    inlines = [
        TaggedItemInline
    ]


admin.site.register(Tag, TagAdmin)
admin.site.register(TagFacet)
