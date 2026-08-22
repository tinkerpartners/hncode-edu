from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("judge", "0270_language_file_only"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quizquestion",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("MC", "Multiple Choice"),
                    ("MA", "Multiple Answer"),
                    ("SA", "Short Answer"),
                    ("FB", "Fill in the Blanks"),
                    ("ES", "Essay"),
                    ("TF", "True/False"),
                ],
                max_length=2,
                verbose_name="Question Type",
            ),
        ),
    ]
