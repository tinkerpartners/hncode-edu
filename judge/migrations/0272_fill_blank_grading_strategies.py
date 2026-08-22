from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("judge", "0271_quizquestion_fill_blank_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quizquestion",
            name="grading_strategy",
            field=models.CharField(
                choices=[
                    ("all_or_nothing", "All or Nothing"),
                    ("partial_credit", "Partial Credit (with penalty)"),
                    ("right_minus_wrong", "Right Minus Wrong"),
                    ("correct_only", "Correct Only (no penalty)"),
                    ("blank_weighted", "Correct Blanks Only (custom weights)"),
                    ("blank_ladder", "Blanks Ladder (graduation-exam ratio)"),
                ],
                default="all_or_nothing",
                help_text="How to calculate score for multiple answer and fill in the blanks questions",
                max_length=20,
                verbose_name="Grading Strategy",
            ),
        ),
    ]
