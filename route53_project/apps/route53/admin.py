from django.contrib import admin

from route53 import models


admin.site.register(models.HostedZone)
admin.site.register(models.Record)
admin.site.register(models.RecordValue)
admin.site.register(models.HostedZoneChange)
admin.site.register(models.RecordChange)
