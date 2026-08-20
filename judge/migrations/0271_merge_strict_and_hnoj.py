from django.db import migrations


class Migration(migrations.Migration):
    """Reunite the two migration leaves that grew in parallel.

    `feat/strict-contest` added 0268_contest_strict_mode / 0269_alter_notification_category
    while main added 0268_widen_language_key / 0269_widen_contest_key /
    0270_language_file_only for the tinhoctre.vn content migration. Both 0268s depend on
    0267, so the graph has two leaves and Django refuses to migrate until they are joined.

    The two sets touch different tables (contest strict fields and the notification
    category choices on one side; language and contest key widths on the other), so no
    reconciliation is needed -- this is an empty merge, exactly what
    `makemigrations --merge` produces.
    """

    dependencies = [
        ("judge", "0269_alter_notification_category"),
        ("judge", "0270_language_file_only"),
    ]

    operations = []
