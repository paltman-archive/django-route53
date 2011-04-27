import datetime

from django.db import models
from django.conf import settings

from django.contrib.auth.models import User

import boto

from boto.route53.record import ResourceRecordSets



def route53():
    return boto.connect_route53(
        aws_access_key_id=settings.DNS_AWS_ACCESS_KEY,
        aws_secret_access_key=settings.DNS_AWS_SECRET_ACCESS_KEY
    )


def commit_record(hosted_zone_id, name, kind, value, change="CREATE", ttl=600, comment=""):
    changes = ResourceRecordSets(route53(), hosted_zone_id, comment)
    change = changes.add_change(change, name, kind, ttl)
    change.add_value(value)
    return changes.commit()


class HostedZone(models.Model):
    
    name = models.CharField(max_length=512)
    
    zone_id = models.CharField(max_length=128, editable=False, unique=True)
    
    created_by = models.ForeignKey(User)
    created_on = models.DateTimeField(default=datetime.datetime.now, editable=False)
    deleted_on = models.DateTimeField(null=True, blank=True, editable=False)
    
    def active(self):
        return self.deleted_on is None
    active.boolean = True
    
    def __unicode__(self):
        return u"[%s] %s" % (self.zone_id, self.name)
    
    class Meta:
        ordering = ["name", "zone_id"]
    
    def delete(self):
        route53().delete_hosted_zone(self.zone_id)
        self.deleted_on = datetime.datetime.now()
        super(HostedZone, self).save()
    
    # @@@ pull this out into a management command
    @staticmethod
    def sync_all(who):
        zones = route53().get_all_hosted_zones()["ListHostedZonesResponse"]["HostedZones"]
        for zone in zones:
            try:
                hz = HostedZone.objects.get(zone_id=zone["Id"].replace("/hostedzone/", ""))
            except HostedZone.DoesNotExist:
                hz = HostedZone(
                    name = zone["Name"],
                    zone_id = zone["Id"].replace("/hostedzone/", ""),
                    created_by = who
                )
                hz.save(skip_api_call=True)
            hz.sync()
    
    def sync(self):
        self.name = route53().get_hosted_zone(self.zone_id)["GetHostedZoneResponse"]["HostedZone"]["Name"]
        super(HostedZone, self).save()
        
        for r in self.records.all():
            r.delete(skip_api_call=True)
        rrsets = route53().get_all_rrsets(self.zone_id)
        
        for record_set in rrsets:
            for record in record_set.resource_records:
                r = Record(
                    zone = self,
                    name = record_set.name,
                    kind = getattr(Record, record_set.type),
                    ttl = record_set.ttl,
                    value = record,
                    created_by = self.created_by
                )
                r.save(skip_api_call=True)
    
    def save(self, *args, **kwargs):
        skip_api_call = kwargs.pop("skip_api_call", False)
        if skip_api_call:
            super(HostedZone, self).save(*args, **kwargs)
        else:
            if self.pk is None:
                r = route53().create_hosted_zone(self.name)["CreateHostedZoneResponse"]
                self.zone_id = r["HostedZone"]["Id"].replace("/hostedzone/", "")
                super(HostedZone, self).save(*args, **kwargs)
                
                for ns in r["DelegationSet"]["NameServers"]:
                    record = Record(
                        zone = self,
                        kind = Record.NS,
                        name = self.name,
                        value = ns,
                        created_by = self.created_by
                    )
                    record.save(skip_api_call=True)
                
                self.changes.create(
                    change_id=r["ChangeInfo"]["Id"].replace("/change/", ""),
                    
                )
            # @@@ raise exception or just noop? hosted zones can only be created/deleted
    
    @property
    def nameservers(self):
        return [r.value for r in self.records.filter(kind=Record.NS)]


class Record(models.Model):
    
    A = 1
    AAAA = 2
    CNAME = 3
    MX = 4
    NS = 5
    PTR = 6
    SOA = 7
    SPF = 8
    SRV = 9
    TXT = 10
    
    RECORD_KINDS = [
        (A, "A"),
        (AAAA, "AAAA"),
        (CNAME, "CNAME"),
        (MX, "MX"),
        (NS, "NS"),
        (PTR, "PTR"),
        (SOA, "SOA"),
        (SPF, "SPF"),
        (SRV, "SRV"),
        (TXT, "TXT")
    ]
    
    name = models.CharField(max_length=512)
    zone = models.ForeignKey(HostedZone, related_name="records")
    kind = models.IntegerField(choices=RECORD_KINDS)
    ttl = models.IntegerField(default=60)
    value = models.CharField(max_length=4000)
    
    created_by = models.ForeignKey(User)
    created_on = models.DateTimeField(default=datetime.datetime.now, editable=False)
    deleted_on = models.DateTimeField(null=True, blank=True, editable=False)
    
    def active(self):
        return self.deleted_on is None
    active.boolean = True
    
    def __unicode__(self):
        return u"%s %s" % (
            self.zone,
            self.get_kind_display()
        )
    
    def delete(self, skip_api_call=False):
        if not skip_api_call:
            response = commit_record(
                self.zone.zone_id,
                self.name,
                self.get_kind_display(),
                self.value,
                change="DELETE",
                ttl=self.ttl,
                comment="Managed by django-route53"
            )
            
            self.changes.create(
                change_id=response["ChangeResourceRecordSetsResponse"]["ChangeInfo"]["Id"].replace("/change/", "")
            )
        
        self.deleted_on = datetime.datetime.now()
        super(Record, self).save()
    
    def save(self, *args, **kwargs):
        skip_api_call = kwargs.pop("skip_api_call", False)
        
        if skip_api_call:
            super(Record, self).save(*args, **kwargs)
        else:
            if self.pk is None:
                response = commit_record(
                    self.zone.zone_id,
                    self.name,
                    self.get_kind_display(),
                    self.value,
                    change="CREATE",
                    ttl=self.ttl,
                    comment="Managed by django-route53"
                )
                super(Record, self).save(*args, **kwargs)
                self.changes.create(
                    change_id=response["ChangeResourceRecordSetsResponse"]["ChangeInfo"]["Id"].replace("/change/", "")
                )
        # @@@ raise exception or just noop? hosted zones can only be created/deleted


class Change(models.Model):
    
    change_id = models.CharField(max_length=128)
    
    def get_status(self):
        return route53().get_change(self.change_id)["GetChangeResponse"]["ChangeInfo"]["Status"]
    
    class Meta:
        abstract = True


class HostedZoneChange(Change):
    
    zone = models.ForeignKey(HostedZone, related_name="changes")


class RecordChange(Change):
    
    record = models.ForeignKey(Record, related_name="changes")



