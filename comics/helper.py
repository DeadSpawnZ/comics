import io
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image

def generate_image_jpge(base_name, image) -> InMemoryUploadedFile:
    if not image:
        return

    img = Image.open(image)
    img_format = img.format or "JPEG"

    # Covert RGBA to RGB if necessary
    if img_format == "PNG" and img.mode in ("RGBA", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
        img_format = "JPEG"
    elif img.mode != "RGB":
        img = img.convert("RGB")
    
    # Save new JPEG image as original
    original_io = io.BytesIO()
    img.save(original_io, format="JPEG", quality=95)
    original_io.seek(0)

    original_filename = f"{base_name}.jpg"
    return InMemoryUploadedFile(
        original_io,
        "ImageField",
        original_filename,
        "image/jpeg",
        original_io.getbuffer().nbytes,
        None,
    )

def collectable_image_path(instance, filename):
    ext = filename.split('.')[-1]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = instance.name.replace(" ", "_")
    filename = f"{name}_{timestamp}.{ext}"
    return os.path.join("collectables", filename)