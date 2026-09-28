#!/bin/bash

# Assuming you are at the root of the Django project
USED_FILES="used_images.txt"

# Base folder where your images are
MEDIA_ROOT="./media"

# Find all JPG images in the image folders
find "$MEDIA_ROOT/images/originals" "$MEDIA_ROOT/images/thumbnails" -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) | while read -r file; do
    # Convert to an absolute path
    abs_path="$(realpath "$file")"

    # Check whether the file is in the list
    if ! grep -Fxq "$abs_path" "$USED_FILES"; then
        echo "Eliminando: $abs_path"
        rm "$abs_path"
        # echo "Eliminar: $abs_path"
    fi
done
