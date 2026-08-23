from django import forms
from django.contrib import admin
from django.contrib.flatpages.admin import FlatPageAdmin as DjangoFlatPageAdmin
from django.contrib.flatpages.forms import FlatpageForm
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from judge.widgets import HeavyPreviewAdminPageDownWidget

# A flatpage is rendered by whichever template it names. Django stores that as a
# free-text path in FlatPage.template_name, which is neither discoverable nor
# typo-proof for the people editing these pages, so the admin offers it as a
# format picker instead.
#
# The two formats are genuinely different, which is why this is per-page rather
# than a site-wide switch:
#
#   Markdown                the same pipeline as problem statements and blog
#                           posts — LaTeX via arithmatex, tables, admonitions —
#                           but bleach-sanitized, so inline `style` attributes
#                           and any tag outside ALLOWED_TAGS are dropped.
#   HTML / Django template  render_django(): the content is rendered as-is and
#                           {% %} template tags work.
#
# Switching a page that already has HTML content over to Markdown can therefore
# lose markup. New pages default to Markdown; existing pages keep what they were
# written in until someone changes it deliberately.
FLATPAGE_MARKDOWN_TEMPLATE = "flatpages/markdown.html"
FLATPAGE_HTML_TEMPLATE = ""

FLATPAGE_FORMAT_CHOICES = [
    (FLATPAGE_MARKDOWN_TEMPLATE, _("Markdown")),
    (FLATPAGE_HTML_TEMPLATE, _("HTML / Django template")),
]


class FlatPageForm(FlatpageForm):
    class Meta(FlatpageForm.Meta):
        widgets = {}
        if HeavyPreviewAdminPageDownWidget is not None:
            widgets["content"] = HeavyPreviewAdminPageDownWidget(
                preview=reverse_lazy("blog_preview")
            )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        choices = list(FLATPAGE_FORMAT_CHOICES)

        # A page may already name a template this picker does not list — the
        # legacy flatpages/markdown_math.html, or something hand-set. Keep it in
        # the dropdown so opening the page and saving it does not silently
        # rewrite a deliberate choice.
        current = self.instance.template_name if self.instance.pk else None
        if current and current not in {value for value, _label in choices}:
            choices.append((current, current))

        field = self.fields["template_name"]
        field.label = _("Content format")
        field.help_text = _(
            "Markdown is rendered like problem statements and blog posts, LaTeX "
            "included, but is HTML-sanitized. HTML / Django template renders the "
            "content as-is and allows template tags. Changing the format of a page "
            "that already has content may change how it renders."
        )
        # template_name stays a CharField, so this is a dropdown without strict
        # choice validation — an unlisted stored value still round-trips.
        field.widget = forms.Select(choices=choices)
        field.required = False

        if self.instance.pk is None and not self.initial.get("template_name"):
            self.initial["template_name"] = FLATPAGE_MARKDOWN_TEMPLATE


class FlatPageAdmin(DjangoFlatPageAdmin):
    form = FlatPageForm
    list_display = ("url", "title", "content_format")
    list_filter = ("template_name", "registration_required", "sites")
    search_fields = ("url", "title", "content")
    fieldsets = (
        (None, {"fields": ("url", "title", "sites")}),
        (_("Content"), {"fields": ("template_name", "content")}),
        (
            _("Advanced options"),
            {"classes": ("collapse",), "fields": ("registration_required",)},
        ),
    )

    @admin.display(description=_("Content format"))
    def content_format(self, obj):
        if obj.template_name == FLATPAGE_MARKDOWN_TEMPLATE:
            return _("Markdown")
        if obj.template_name:
            return obj.template_name
        return _("HTML / Django template")
