from collections import defaultdict

from django.db import migrations


def add_library_navigation(apps, schema_editor):
    NavigationBar = apps.get_model("judge", "NavigationBar")
    menu = NavigationBar.objects.using(schema_editor.connection.alias)
    entries = list(menu.order_by("tree_id", "lft", "pk"))
    if not entries:  # A fresh installation loads the default navbar fixture separately.
        return

    def entry_for(key, path, label, regex):
        entry = next((item for item in entries if item.key == key), None)
        if entry is None:
            entry = next((item for item in entries if item.path.rstrip("/") == path.rstrip("/")), None)
        if entry is None:
            entry = menu.create(key=key, label=label, path=path, regex=regex,
                                order=1, lft=0, rght=0, tree_id=0, level=0)
            entries.append(entry)
        entry.label, entry.path, entry.regex = label, path, regex
        return entry

    exams = entry_for("exams", "/library/exams/", "Thư viện đề thi", r"^/library/exams(?:/|$)")
    documents = entry_for("library", "/library/", "Kho tài liệu", r"^/library(?:/|$)")
    exams.parent_id = None
    documents.parent_id = exams.pk
    children = defaultdict(list)
    for entry in entries:
        children[entry.parent_id].append(entry)
    roots = children[None]
    roots.remove(exams)
    anchor = next((i for i, entry in enumerate(roots) if entry.key == "contest"), len(roots) - 1)
    roots.insert(anchor + 1, exams)

    def visit(entry, tree_id, level, index):
        entry.tree_id, entry.level, entry.lft = tree_id, level, index
        index += 1
        for order, child in enumerate(children[entry.pk], 1):
            child.order = order
            index = visit(child, tree_id, level + 1, index)
        entry.rght = index
        return index + 1

    for order, root in enumerate(roots, 1):
        root.order = order
        visit(root, order, 0, 1)
    menu.bulk_update(entries, ("parent", "order", "lft", "rght", "tree_id", "level", "label", "path", "regex"))


class Migration(migrations.Migration):
    dependencies = [("judge", "0293_exam_mock_archive")]
    operations = [migrations.RunPython(add_library_navigation, migrations.RunPython.noop)]
