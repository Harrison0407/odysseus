from django.apps import apps
from django.contrib import admin

for _model in apps.get_app_config("requests").get_models():
    try:
        admin.site.register(_model)
    except admin.sites.AlreadyRegistered:
        pass
