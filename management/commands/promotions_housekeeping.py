from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Expire lapsed promotion holds and purge old released/expired attempts."

    def handle(self, *args, **options):
        from stapel_promotions.services.redemption import expire_reservations, purge_attempts

        expired = expire_reservations()
        purged = purge_attempts()
        self.stdout.write(f"expired {expired}, purged {purged}")
