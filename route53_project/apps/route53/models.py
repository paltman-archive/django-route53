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


class HostedZone(models.Model):
    
    name = models.CharField(max_length=512, unique=True)
    
    zone_id = models.CharField(max_length=128, editable=False)
    
    created_by = models.ForeignKey(User, editable=False)
    created_on = models.DateTimeField(default=datetime.datetime.now, editable=False)
    deleted_on = models.DateTimeField(null=True, blank=True, editable=False)
    
    class Meta:
        unique_together = ["name", "deleted_on"]
    
    def __unicode__(self):
        return unicode(self.name)
    
    def delete(self):
        route53().delete_hosted_zone(self.zone_id)
        self.deleted_on = datetime.datetime.now()
        super(HostedZone, self).save()
    
    @staticmethod
    def sync_all(who):
        zones = route53().get_all_hosted_zones()["ListHostedZonesResponse"]["HostedZones"]
        for zone in zones:
            try:
                hz = HostedZone.objects.get(name=zone["Name"], deleted_on__isnull=True)
            except HostedZone.DoesNotExist:
                hz = HostedZone.objects.create(
                    name=zone["Name"],
                    zone_id=zone["Id"].replace("/hostedzone/", ""),
                    created_by=who
                )
            hz.sync()
    
    def sync(self):
        self.name = route53().get_hosted_zone(self.zone_id)["GetHostedZoneResponse"]["HostedZone"]["Name"]
        super(HostedZone, self).save()
        
        self.records.all().delete()
        rrsets = route53().get_all_rrsets(self.zone_id)
        
        for record_set in rrsets:
            record_obj = self.records.create(
                name = record_set.name,
                kind = getattr(Record, record_set.type),
                ttl = record_set.ttl
            )
            for record in record_set.resource_records:
                record_obj.values.create(value=record)
    
    def save(self, *args, **kwargs):
        if self.pk is None:
            r = route53().create_hosted_zone(self.name)["CreateHostedZoneResponse"]
            self.zone_id = r["HostedZone"]["Id"].replace("/hostedzone/", "")
            super(HostedZone, self).save(*args, **kwargs)
            
            record = self.records.create(
                kind=Record.NS,
                name=self.name
            )
            
            for ns in r["DelegationSet"]["NameServers"]:
                record.values.create(
                    value=ns
                )
            
            self.changes.create(
                change_id=r["ChangeInfo"]["Id"].replace("/change/", ""),
                
            )
        # @@@ raise exception or just noop? hosted zones can only be created/deleted
    
    @property
    def nameservers(self):
        ns = []
        for record in self.records.filter(kind=Record.NS):
            for value in record.values.all():
                ns.append(value.value)
        return ns


class Record(models.Model):
    """
    import boto
    conn = boto.connect_route53()
    from boto.route53.record import ResourceRecordSets
    changes = ResourceRecordSets(conn, hosted_zone_id, comment)
    change = changes.add_change("CREATE", name, type, ttl)
    change.add_value(value)
    something = changes.commit()
    """
    
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
    
    def make_record(self): # @@@ should this be in a post_save signal handler for handling the save of a Record with RecorvValue inlines?
        changes = ResourceRecordSets(route53(), self.zone.zone_id, "Managed by gondor.io")
        for value in self.values.all():
            change = changes.add_change("CREATE", self.name, self.get_kind_display(), self.ttl)
            change.add_value(value.value)
        self.changes.create(
            change_id=changes.commit()["ChangeResourceRecordSetsResponse"]["ChangeInfo"]["Id"].replace("/change/", "")
        )
    
    def __unicode__(self):
        return u"%s %s" % (
            self.zone,
            self.get_kind_display()
        )


class RecordValue(models.Model):
    
    record = models.ForeignKey(Record, related_name="values")
    value = models.CharField(max_length=4000)
    
    def __unicode__(self):
        return unicode(self.value)


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



