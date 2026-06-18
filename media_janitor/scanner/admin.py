from django.contrib import admin

from .models import Blob, Config, Scan

admin.site.register(Config)
admin.site.register(Scan)
admin.site.register(Blob)
