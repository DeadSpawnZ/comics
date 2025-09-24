#!/bin/bash

# Asumiendo que estás en el root del proyecto Django
USED_FILES="used_images.txt"

# Carpeta base donde están tus imágenes
MEDIA_ROOT="./media"

# Busca todas las imágenes JPG en las carpetas de imágenes
find "$MEDIA_ROOT/images/originals" "$MEDIA_ROOT/images/thumbnails" -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) | while read -r file; do
    # Convertimos a ruta absoluta
    abs_path="$(realpath "$file")"

    # Verificamos si el archivo está en la lista
    if ! grep -Fxq "$abs_path" "$USED_FILES"; then
        echo "Eliminando: $abs_path"
        rm "$abs_path"
        # echo "Eliminar: $abs_path"
    fi
done
