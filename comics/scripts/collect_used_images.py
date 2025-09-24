# scripts/collect_used_images.py
from pathlib import Path
from comics.models import Comic


def collect_used_image_paths(output_file="used_images.txt"):
    used_files = set()

    for comic in Comic.objects.all():
        if comic.image:
            used_files.add(Path(comic.image.path).resolve())
        if comic.thumbnail:
            used_files.add(Path(comic.thumbnail.path).resolve())

    with open(output_file, "w") as f:
        for path in sorted(used_files):
            f.write(str(path) + "\n")

    print(f"Archivo generado con {len(used_files)} rutas en uso: {output_file}")
