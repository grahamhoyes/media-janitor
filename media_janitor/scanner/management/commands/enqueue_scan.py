"""Enqueue the full-share scan task.

This is the hook a CronJob (or the web "refresh now" button) will call. It only
enqueues onto the django-tasks-db queue; the `db_worker` process runs the work.
"""

from django.core.management.base import BaseCommand

from scanner.tasks import scan


class Command(BaseCommand):
    help = "Enqueue a full-share scan task onto the database task queue."

    def handle(self, *args, **options):
        result = scan.enqueue()
        self.stdout.write(self.style.SUCCESS(f"Enqueued scan task: {result.id}"))
