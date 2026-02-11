from django.core.management.base import BaseCommand
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import spectacular_settings
import json


class Command(BaseCommand):
    help = 'Generate openapi.json file using drf-spectacular'

    def handle(self, *args, **options):
        generator = SchemaGenerator()
        schema = generator.get_schema(request=None, public=True)
        with open('openapi.json', 'w') as f:
            json.dump(schema, f, default=str, indent=2)
        self.stdout.write(self.style.SUCCESS('openapi.json generated'))
