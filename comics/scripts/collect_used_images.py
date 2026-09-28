# scripts/collect_used_images.py
from pathlib import Path
from comics.models import Edition


def collect_used_image_paths(output_file="used_images.txt"):
    used_files = set()

    for edition in Edition.objects.all():
        if edition.image:
            used_files.add(Path(edition.image.path).resolve())
        if edition.thumbnail:
            used_files.add(Path(edition.thumbnail.path).resolve())

    with open(output_file, "w") as f:
        for path in sorted(used_files):
            f.write(str(path) + "\n")

    print(f"Archivo generado con {len(used_files)} rutas en uso: {output_file}")
