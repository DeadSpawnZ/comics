from comics.models import Comic  # ajusta el import al path correcto
from django.db import transaction

# Opcional: Mapeo por si algún día quieres renombrar o validar
VALID_PRINTINGS = {choice.value for choice in Comic.PrintingChoices}


@transaction.atomic
def populate_comic_printing_field():
    comics = Comic.objects.select_related("publishing__printing").all()
    updated = 0
    skipped = 0

    for comic in comics:
        if comic.publishing and comic.publishing.printing:
            printing_name = comic.publishing.printing.name.strip()

            if printing_name in VALID_PRINTINGS:
                if comic.printing != printing_name:
                    comic.printing = printing_name
                    comic.save(update_fields=["printing"])
                    updated += 1
            else:
                print(f"[SKIPPED] Comic ID {comic.id} - Invalid printing: '{printing_name}'")
                skipped += 1
        else:
            print(f"[SKIPPED] Comic ID {comic.id} has no publishing or printing.")
            skipped += 1

    print(f"Updated {updated} comics.")
    print(f"Skipped {skipped} comics.")
