from django.db import migrations, models


class Migration(migrations.Migration):
    """Organization.is_visible (A05, #95).

    Default True, so every existing group stays listed and open exactly as
    before; admins switch it off per group.
    """

    dependencies = [
        ("judge", "0274_organization_registrant_set_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="is_visible",
            field=models.BooleanField(
                default=True,
                help_text="Show this group in lists and search and let non-members open its pages. Members and group admins always keep access.",
                verbose_name="publicly visible",
            ),
        ),
    ]
