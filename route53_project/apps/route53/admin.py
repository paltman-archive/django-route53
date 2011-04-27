from django.contrib import admin

from route53 import models


class RecordInline(admin.TabularInline):
    model = models.Record
    extra = 2
    
    def queryset(self, request):
        qs = super(RecordInline, self).queryset(request)
        return qs.filter(deleted_on__isnull=True)


admin.site.register(models.HostedZone,
    list_display = ["id", "name", "zone_id", "created_by", "active"],
    list_filter = ["deleted_on"],
    search_fields = ["name", "zone_id"],
    readonly_fields = ["deleted_on", "created_on"],
    inlines = [RecordInline]
)
admin.site.register(models.Record,
    list_display = ["id", "name", "kind", "value", "active"],
    list_filter = ["deleted_on", "kind", "name"],
    search_fields = ["name", "value", "kind"],
    readonly_fields = ["deleted_on", "created_on"],
)
admin.site.register(models.HostedZoneChange)
admin.site.register(models.RecordChange)
