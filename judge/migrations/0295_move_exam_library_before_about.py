from django.core.cache import cache
from django.db import migrations, transaction


def move_library(apps, schema_editor, *, anchor, after=False):
    alias = schema_editor.connection.alias
    menu = apps.get_model("judge", "NavigationBar").objects.using(alias)
    entries = list(menu.only("key", "parent_id", "tree_id", "order").order_by("tree_id", "lft"))
    roots = [entry for entry in entries if entry.parent_id is None]
    library = next((entry for entry in roots if entry.key == "exams"), None)
    neighbor = next((entry for entry in roots if entry.key == anchor), None)
    if library is None or neighbor is None:
        return

    reordered = [entry for entry in roots if entry.pk != library.pk]
    reordered.insert(reordered.index(neighbor) + int(after), library)
    slots = {
        entry.tree_id: (slot.tree_id, slot.order)
        for slot, entry in zip(roots, reordered)
    }
    for entry in entries:
        entry.tree_id, root_order = slots[entry.tree_id]
        if entry.parent_id is None:
            entry.order = root_order
    menu.bulk_update(entries, ("tree_id", "order"))
    transaction.on_commit(lambda: cache.delete("nb:"), using=alias)


def forward(apps, schema_editor):
    move_library(apps, schema_editor, anchor="about")


def backward(apps, schema_editor):
    move_library(apps, schema_editor, anchor="contest", after=True)


class Migration(migrations.Migration):
    dependencies = [("judge", "0294_exam_library_navigation")]
    operations = [migrations.RunPython(forward, backward)]
