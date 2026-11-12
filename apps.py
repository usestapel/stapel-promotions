from django.apps import AppConfig


class PromotionsConfig(AppConfig):
    name = "stapel_promotions"
    label = "promotions"
    verbose_name = "Promotions"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import actions  # noqa: F401
        from . import checks  # noqa: F401
        from . import errors  # noqa: F401
        from . import flows  # noqa: F401
        from . import functions  # noqa: F401

        from stapel_core.gdpr import gdpr_registry, register_gdpr_owner

        from .gdpr import OWNER, SUBJECT_TYPES, PromotionsGDPRProvider, erase_subject

        if not any(p.section == OWNER for p in gdpr_registry.providers):
            gdpr_registry.register(PromotionsGDPRProvider())
        register_gdpr_owner(OWNER, SUBJECT_TYPES, erase_subject)
