from django.db import migrations, models


class Migration(migrations.Migration):
    """Add Language.file_only / file_size_limit, ported from the VNOJ schema.

    File-only languages (Scratch, output-only) submit an uploaded artifact instead of
    source text. The judge's SCRATCH executor only downloads the submission source URL
    when the bridge tells it the submission is file-only; without this flag it hands the
    URL string to scratch-run as if it were the project JSON and every submission fails
    with "Not a valid Scratch file".
    """

    dependencies = [
        ("judge", "0269_widen_contest_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="language",
            name="file_only",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Whether submissions in this language are an uploaded file rather "
                    "than source text. The judge downloads the file from the submission "
                    "source URL."
                ),
                verbose_name="file-only language",
            ),
        ),
        migrations.AddField(
            model_name="language",
            name="file_size_limit",
            field=models.IntegerField(
                blank=True,
                default=0,
                help_text=(
                    "Maximum size of an uploaded submission file, for file-only languages."
                ),
                verbose_name="submission file size limit",
            ),
        ),
    ]
