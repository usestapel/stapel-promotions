"""Bulk unique codes for one promotion, printed one per line (or to --out)."""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generate COUNT unique promo codes for the promotion with KEY."

    def add_arguments(self, parser):
        parser.add_argument("promotion_key")
        parser.add_argument("count", type=int)
        parser.add_argument("--prefix", default="")
        parser.add_argument("--length", type=int, default=None)
        parser.add_argument("--usage-limit", type=int, default=None,
                            help="Uses per code (1 = single-use); default unlimited.")
        parser.add_argument("--out", default=None, help="Write the codes to this file instead of stdout.")

    def handle(self, *args, **options):
        from stapel_promotions.models import Promotion
        from stapel_promotions.services import PromotionsError
        from stapel_promotions.services.codes import generate

        promotion = Promotion.objects.filter(key=options["promotion_key"]).first()
        if promotion is None:
            raise CommandError(f"no promotion with key {options['promotion_key']!r}")
        if options["count"] < 1:
            raise CommandError("count must be at least 1")
        try:
            batch, codes = generate(
                promotion, options["count"], prefix=options["prefix"], length=options["length"],
                usage_limit=options["usage_limit"],
            )
        except PromotionsError as exc:
            raise CommandError(exc.key) from None
        text = "\n".join(codes) + "\n"
        if options["out"]:
            with open(options["out"], "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            self.stdout.write(text, ending="")
        self.stderr.write(f"batch {batch}: {len(codes)} codes")
