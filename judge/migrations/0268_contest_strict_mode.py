import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("judge", "0267_alter_homeherosection_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="contest",
            name="is_strict",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Lock participants into browser fullscreen and record violations "
                    "(leaving fullscreen, switching tab, pasting, navigating away). "
                    "Not supported on iPhone."
                ),
                verbose_name="strict mode",
            ),
        ),
        migrations.AddField(
            model_name="contest",
            name="strict_violation_limit",
            field=models.PositiveSmallIntegerField(
                default=3,
                help_text=(
                    "Number of counted violations before a participant is "
                    "automatically disqualified. Only used when strict mode is on."
                ),
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(50),
                ],
                verbose_name="strict violation limit",
            ),
        ),
        migrations.AddField(
            model_name="contest",
            name="strict_grace_seconds",
            field=models.PositiveSmallIntegerField(
                default=20,
                help_text=(
                    "Seconds a participant has to return to fullscreen before being "
                    "disqualified. Only used when strict mode is on."
                ),
                validators=[
                    django.core.validators.MinValueValidator(5),
                    django.core.validators.MaxValueValidator(300),
                ],
                verbose_name="strict grace period",
            ),
        ),
        migrations.AddField(
            model_name="contest",
            name="strict_autoban",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If off, violations are still recorded but nobody is "
                    "automatically disqualified (monitor-only mode)."
                ),
                verbose_name="strict auto-disqualify",
            ),
        ),
        migrations.AddField(
            model_name="contestparticipation",
            name="strict_violations",
            field=models.PositiveIntegerField(
                default=0,
                help_text=(
                    "Number of counted strict-mode violations. The violation log is "
                    "authoritative; this is the fast counter compared against the "
                    "limit."
                ),
                verbose_name="strict mode violations",
            ),
        ),
        migrations.AddField(
            model_name="contestparticipation",
            name="strict_armed_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When this participant entered fullscreen and armed their "
                    "proctored session. Null means they never armed, and cannot "
                    "submit."
                ),
                null=True,
                verbose_name="strict session started",
            ),
        ),
        migrations.AddField(
            model_name="contestparticipation",
            name="strict_last_seen",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Last heartbeat from this participant's proctored session."
                ),
                null=True,
                verbose_name="strict session last seen",
            ),
        ),
        migrations.CreateModel(
            name="ContestViolationLog",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("session_start", "Session started"),
                            ("returned", "Returned to fullscreen"),
                            ("fullscreen_exit", "Left fullscreen"),
                            ("focus_lost", "Switched tab or window"),
                            ("paste", "Paste blocked"),
                            ("copy", "Copy blocked"),
                            ("cut", "Cut blocked"),
                            ("context_menu", "Context menu blocked"),
                            ("blocked_key", "Blocked keyboard shortcut"),
                            ("navigate_away", "Tried to navigate away"),
                            ("grace_expired", "Grace period expired"),
                            ("strict_gap", "Monitoring gap"),
                            ("ip_change", "IP address changed"),
                            ("auto_ban", "Automatically disqualified"),
                            ("admin_unban", "Unbanned by an administrator"),
                            ("session_end", "Session ended"),
                        ],
                        max_length=32,
                        verbose_name="action",
                    ),
                ),
                (
                    "detail",
                    models.CharField(blank=True, max_length=255, verbose_name="detail"),
                ),
                (
                    "violation_number",
                    models.PositiveIntegerField(
                        default=0,
                        help_text=(
                            "Counter value after this event, or 0 if it was not "
                            "counted."
                        ),
                        verbose_name="violation number",
                    ),
                ),
                (
                    "is_automated",
                    models.BooleanField(default=True, verbose_name="automated"),
                ),
                (
                    "ip",
                    models.GenericIPAddressField(
                        blank=True, null=True, verbose_name="IP address"
                    ),
                ),
                (
                    "created",
                    models.DateTimeField(auto_now_add=True, verbose_name="event time"),
                ),
                (
                    "contest",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="violation_logs",
                        to="judge.contest",
                        verbose_name="contest",
                    ),
                ),
                (
                    "moderator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="judge.profile",
                        verbose_name="moderator",
                    ),
                ),
                (
                    "participation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="violations",
                        to="judge.contestparticipation",
                        verbose_name="participation",
                    ),
                ),
            ],
            options={
                "verbose_name": "contest violation log",
                "verbose_name_plural": "contest violation logs",
                "ordering": ["-created"],
                "indexes": [
                    models.Index(
                        fields=["contest", "-created"],
                        name="judge_conte_contest_f1f316_idx",
                    ),
                    models.Index(
                        fields=["participation", "-created"],
                        name="judge_conte_partici_ef8052_idx",
                    ),
                ],
            },
        ),
    ]
