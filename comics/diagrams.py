"""Mermaid diagrams of the project, generated from the code so the README cannot drift from it.

- `schema_markdown()`: the database schema. An overview of the areas (one per module of
  comics.models) and one ER diagram per area. Historical tables (django-simple-history) are left
  out and listed as a note instead, and many-to-many fields with an explicit `through` model are
  drawn through that model only.
- `modules_markdown()`: which parts of the code import which, from the `import` statements of every
  module (also the ones inside functions), grouped into the components listed in COMPONENTS.

Both return Markdown (headings, notes and ```mermaid blocks) for `manage.py diagrams` to place
between the markers of the README. The output is sorted so that regenerating without code changes
gives the same text. The headings and notes are in Spanish, like the README.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db.models import Field, ForeignObjectRel, ManyToManyField, Model

APP_LABEL = "comics"

# Areas of the schema: module of comics.models -> heading. The order is the order in the README.
SCHEMA_AREAS: dict[str, str] = {
    "catalog": "Catálogo",
    "collections": "Colecciones",
    "change_requests": "Solicitudes de cambio",
    "achievements": "Logros",
    "notifications": "Notificaciones y anuncios",
    "profiles": "Perfiles y permisos",
}

# Components of the modules diagram: (glob over the dotted module name, node id, label). A module
# belongs to the first component whose pattern matches; modules matching none are left out. A node
# listed again with an empty label only adds modules to it.
COMPONENTS: list[tuple[str, str, str]] = [
    ("comic.urls", "urls", "comic.urls<br/>rutas"),
    ("comics.views.manage*", "views_manage", "comics.views.manage_*<br/>Gestión"),
    ("comics.views*", "views_site", "comics.views<br/>sitio y revisiones"),
    ("comics.admin*", "admin", "comics.admin<br/>admin de Django"),
    ("comics.management*", "commands", "comics.management<br/>comandos"),
    ("comics.signals", "signals", "comics.signals<br/>señales"),
    ("comics.decorators", "web", "decorators · middleware<br/>context_processors"),
    ("comics.middleware", "web", ""),
    ("comics.context_processors", "web", ""),
    ("comics.forms*", "forms", "comics.forms"),
    ("comics.catalog_sources*", "catalog_sources", "comics.catalog_sources<br/>Metron y Comic Vine"),
    ("comics.change_requests", "change_requests", "comics.change_requests<br/>flujo de solicitudes"),
    ("comics.versions", "versions", "comics.versions<br/>aplicar y revertir versiones"),
    ("comics.achievements*", "achievements", "comics.achievements<br/>motor de logros"),
    ("comics.models*", "models", "comics.models"),
    ("comics.history", "history", "comics.history<br/>historial"),
    ("comics.roles", "roles", "comics.roles<br/>roles y permisos"),
    ("comics.helper", "helper", "comics.helper<br/>utilidades"),
]

# Layers of the modules diagram (a subgraph each): heading -> node ids.
LAYERS: dict[str, tuple[str, ...]] = {
    "Entrada": ("urls", "views_site", "views_manage", "admin", "commands", "signals", "web"),
    "Formularios y servicios": ("forms", "catalog_sources", "change_requests", "versions", "achievements"),
    "Datos": ("models", "history", "roles", "helper"),
}

# Packages scanned for the modules diagram, relative to BASE_DIR.
SOURCE_PACKAGES = ("comic", "comics")
SKIPPED_DIRS = {"migrations", "tests", "static", "templates", "scripts", "__pycache__"}


# ---------- Schema ----------


def _comics_models() -> list[type[Model]]:
    """The app's models without the historical ones, which mirror a tracked model."""
    models = apps.get_app_config(APP_LABEL).get_models()
    return [model for model in models if getattr(model, "instance_type", None) is None]


def _area(model: type[Model]) -> str:
    return model.__module__.rsplit(".", 1)[-1]


def _attribute(field: Field) -> str:
    keys = []
    if field.primary_key:
        keys.append("PK")
    if field.is_relation:
        keys.append("FK")
    if field.unique and not field.primary_key:
        keys.append("UK")
    line = f"{type(field).__name__} {field.name}"
    if keys:
        line += f" {', '.join(keys)}"
    if field.null:
        line += ' "null"'
    return line


def _relationships(model: type[Model]) -> list[str]:
    """The relationships `model` declares, from the side of the referenced model."""
    lines = []
    for field in model._meta.get_fields():
        if isinstance(field, ForeignObjectRel) or not field.is_relation:
            continue
        target = field.related_model.__name__
        if isinstance(field, ManyToManyField):
            if not field.remote_field.through._meta.auto_created:
                continue  # drawn through its explicit model, which has the foreign keys
            lines.append(f'{model.__name__} }}o--o{{ {target} : "{field.name}"')
        elif field.one_to_one:
            parent = "|o" if field.null else "||"
            lines.append(f'{target} {parent}--o| {model.__name__} : "{field.name}"')
        else:
            parent = "|o" if field.null else "||"
            lines.append(f'{target} {parent}--o{{ {model.__name__} : "{field.name}"')
    return lines


def _er_diagram(models: list[type[Model]]) -> str:
    lines = ["erDiagram"]
    for model in models:
        lines.append(f"    {model.__name__} {{")
        lines.extend(f"        {_attribute(field)}" for field in model._meta.concrete_fields)
        lines.append("    }")
    lines.extend(f"    {relationship}" for model in models for relationship in _relationships(model))
    return "\n".join(lines)


def _tracked_models() -> list[str]:
    """Models with a version history, with the many-to-many fields it also records."""
    tracked = []
    for model in apps.get_app_config(APP_LABEL).get_models():
        instance_type = getattr(model, "instance_type", None)
        if instance_type is None or not hasattr(instance_type._meta, "simple_history_manager_attribute"):
            continue
        m2m = sorted(field.name for field in getattr(model, "_history_m2m_fields", None) or [])
        tracked.append(f"`{instance_type.__name__}`" + (f" (con {', '.join(m2m)})" if m2m else ""))
    return sorted(tracked)


def _areas_overview(models: list[type[Model]]) -> str:
    """One node per area listing its models, and an arrow for each area another one references."""
    by_area: dict[str, list[str]] = defaultdict(list)
    edges: dict[tuple[str, str], int] = defaultdict(int)
    for model in models:
        by_area[_area(model)].append(model.__name__)
        for field in model._meta.get_fields():
            if isinstance(field, ForeignObjectRel) or not field.is_relation:
                continue
            target = field.related_model
            target_area = _area(target) if target._meta.app_label == APP_LABEL else "auth"
            if target_area != _area(model):
                edges[(_area(model), target_area)] += 1
    lines = ["flowchart LR"]
    for area, heading in SCHEMA_AREAS.items():
        names = ", ".join(sorted(by_area[area]))
        lines.append(f'    {area}["<b>{heading}</b><br/>{names}"]')
    lines.append('    auth["<b>django.contrib.auth</b><br/>User, Group"]')
    for (source, target), count in sorted(edges.items()):
        lines.append(f"    {source} -->|{count}| {target}")
    return "\n".join(lines)


def schema_markdown() -> str:
    models = _comics_models()
    unknown = sorted({_area(model) for model in models} - set(SCHEMA_AREAS))
    if unknown:
        raise ValueError(f"Models in modules missing from SCHEMA_AREAS: {', '.join(unknown)}")
    parts = [
        "### Vista general",
        "",
        "Cada caja es un módulo de `comics/models/`; una flecha indica que los modelos de un área "
        "tienen relaciones hacia otra (el número es cuántas).",
        "",
        "```mermaid",
        _areas_overview(models),
        "```",
        "",
        "Tienen historial de versiones (`django-simple-history`, tablas `Historical*`, que no se "
        f"dibujan): {', '.join(_tracked_models())}.",
    ]
    for area, heading in SCHEMA_AREAS.items():
        area_models = [model for model in models if _area(model) == area]
        parts += ["", f"### {heading}", "", "```mermaid", _er_diagram(area_models), "```"]
    return "\n".join(parts)


# ---------- Modules ----------


def _module_name(path: Path, base_dir: Path) -> str:
    parts = list(path.relative_to(base_dir).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _source_modules(base_dir: Path) -> dict[str, Path]:
    modules = {}
    for package in SOURCE_PACKAGES:
        for path in sorted((base_dir / package).rglob("*.py")):
            if SKIPPED_DIRS.isdisjoint(path.relative_to(base_dir).parts):
                modules[_module_name(path, base_dir)] = path
    return modules


def _imported_modules(module: str, path: Path, known: set[str]) -> set[str]:
    """The project modules `module` imports, resolving relative imports and `from pkg import mod`."""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    imported = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(anchor + ([base] if base else []))
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)
    return {name for name in imported if name in known}


def _component(module: str) -> str | None:
    for pattern, node, _label in COMPONENTS:
        if fnmatch(module, pattern):
            return node
    return None


def modules_markdown(base_dir: Path | None = None) -> str:
    base_dir = base_dir or Path(settings.BASE_DIR)
    modules = _source_modules(base_dir)
    edges = set()
    for module, path in modules.items():
        source = _component(module)
        for imported in _imported_modules(module, path, set(modules)):
            target = _component(imported)
            if source and target and source != target:
                edges.add((source, target))
    labels = {node: label for _pattern, node, label in COMPONENTS if label}
    lines = ["flowchart TD"]
    for index, (heading, nodes) in enumerate(LAYERS.items()):
        lines.append(f'    subgraph layer{index}["{heading}"]')
        lines.extend(f'        {node}["{labels[node]}"]' for node in nodes)
        lines.append("    end")
    lines.extend(f"    {source} --> {target}" for source, target in sorted(edges))
    return "\n".join(["```mermaid", *lines, "```"])
