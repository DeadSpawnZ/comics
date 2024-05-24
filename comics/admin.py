from django.contrib import admin

# Register your models here.

from .models import (
    Comic,
    Editorial,
    Title,
    Publishing,
    Artist,
    Printing
)

admin.site.register(Comic)
admin.site.register(Editorial)
admin.site.register(Publishing)
admin.site.register(Title)
admin.site.register(Artist)
admin.site.register(Printing)