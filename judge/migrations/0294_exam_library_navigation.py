import json
from collections import defaultdict

from django.core.cache import cache
from django.db import migrations, transaction

BACKUP_KEY = "exam_library.nav_backup"
FIELDS = (
    "key",
    "label",
    "path",
    "regex",
    "parent_id",
    "order",
    "tree_id",
    "level",
    "lft",
    "rght",
)


def rebuild(menu, entries):
    children = defaultdict(list)
    for entry in entries:
        children[entry.parent_id].append(entry)

    def visit(entry, tree_id, level, index):
        entry.tree_id, entry.level, entry.lft = tree_id, level, index
        index += 1
        for child in sorted(children[entry.pk], key=lambda item: (item.order, item.pk)):
            index = visit(child, tree_id, level + 1, index)
        entry.rght = index
        return index + 1

    for tree_id, root in enumerate(
        sorted(children[None], key=lambda item: (item.order, item.pk)), 1
    ):
        visit(root, tree_id, 0, 1)
    menu.bulk_update(entries, FIELDS)


def invalidate_cache(alias):
    transaction.on_commit(lambda: cache.delete("nb:"), using=alias)


def add_library_navigation(apps, schema_editor):
    alias = schema_editor.connection.alias
    menu = apps.get_model("judge", "NavigationBar").objects.using(alias)
    config = apps.get_model("judge", "MiscConfig").objects.using(alias)
    entries = list(menu.order_by("tree_id", "lft", "pk"))
    if not entries:
        return
    original = [
        {field: getattr(entry, field) for field in ("id", *FIELDS)} for entry in entries
    ]

    def entry_for(key, path, label, regex):
        entry = next((item for item in entries if item.key == key), None)
        if entry is None:
            entry = next(
                (item for item in entries if item.path.rstrip("/") == path.rstrip("/")),
                None,
            )
        if entry is None:
            entry = menu.create(
                key=key,
                label=label,
                path=path,
                regex=regex,
                order=1,
                lft=0,
                rght=0,
                tree_id=0,
                level=0,
            )
            entries.append(entry)
        entry.label, entry.path, entry.regex = label, path, regex
        return entry

    exams = entry_for(
        "exams", "/library/exams/", "Thư viện đề thi", r"^/library/exams(?:/|$)"
    )
    documents = entry_for("library", "/library/", "Kho tài liệu", r"^/library(?:/|$)")
    exams.parent_id = None
    documents.parent_id = exams.pk
    roots = [
        entry for entry in entries if entry.parent_id is None and entry.pk != exams.pk
    ]
    anchor = next(
        (i for i, root in enumerate(roots) if root.key == "about"), len(roots)
    )
    roots.insert(anchor, exams)
    for order, root in enumerate(roots, 1):
        root.order = order
    created_ids = [
        entry.pk for entry in entries if entry.pk not in {row["id"] for row in original}
    ]
    config.update_or_create(
        key=BACKUP_KEY,
        defaults={"value": json.dumps({"original": original, "created": created_ids})},
    )
    rebuild(menu, entries)
    invalidate_cache(alias)


def restore_navigation(apps, schema_editor):
    alias = schema_editor.connection.alias
    menu = apps.get_model("judge", "NavigationBar").objects.using(alias)
    config = apps.get_model("judge", "MiscConfig").objects.using(alias)
    backup = config.filter(key=BACKUP_KEY).first()
    if backup is None:
        return
    snapshot = json.loads(backup.value)
    original = {row["id"]: row for row in snapshot["original"]}
    created = set(snapshot["created"])
    entries = list(menu.all())
    existing_ids = {entry.pk for entry in entries} - created
    for entry in entries:
        if entry.pk in original:
            for field in FIELDS:
                setattr(entry, field, original[entry.pk][field])
        if entry.parent_id and entry.parent_id not in existing_ids:
            entry.parent_id = None
    retained = [entry for entry in entries if entry.pk not in created]
    rebuild(menu, retained)
    menu.filter(pk__in=created).delete()
    backup.delete()
    invalidate_cache(alias)


class Migration(migrations.Migration):
    dependencies = [("judge", "0293_exam_mock_archive")]
    operations = [migrations.RunPython(add_library_navigation, restore_navigation)]
