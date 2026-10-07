import io
from pathlib import Path
import zipfile

from PIL import Image
import pytest

from erp import create_app
from erp.config import data_root, migrate_data_root
from erp.db import get_db, init_db
from erp.services.accounting import create_product
from erp.utils.backup import create_backup


def _image_bytes(fmt="PNG", color="red", size=(12, 12)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=fmt)
    return output.getvalue()


def test_product_image_upload_is_reencoded_served_and_included_in_backup_and_migration(tmp_path):
    init_db()
    product_id = create_product("图片商品", "A1", "个", 1200)
    client = create_app().test_client()
    response = client.post(
        f"/products/{product_id}/image",
        data={"image": (io.BytesIO(_image_bytes()), "disguised.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "商品图片" in html and 'accept="image/jpeg,image/png"' in html
    with get_db() as conn:
        image_path = conn.execute("SELECT image_path FROM products WHERE id=?", (product_id,)).fetchone()["image_path"]
    assert image_path and not Path(image_path).is_absolute()
    stored = data_root() / "product_images" / image_path
    assert stored.exists()
    with Image.open(stored) as image:
        assert image.format == "JPEG"
        assert image.size == (12, 12)
    served = client.get(f"/products/{product_id}/image")
    assert served.status_code == 200
    with zipfile.ZipFile(create_backup("images_test")) as backup:
        assert f"product_images/{image_path}" in backup.namelist()

    target = tmp_path / "migrated-data"
    migrate_data_root(target)
    assert (target / "product_images" / image_path).exists()


def test_invalid_or_oversize_image_does_not_replace_existing_image():
    init_db()
    product_id = create_product("图片保留商品", "", "个", 100)
    client = create_app().test_client()
    first = client.post(
        f"/products/{product_id}/image",
        data={"image": (io.BytesIO(_image_bytes()), "first.png")},
        content_type="multipart/form-data",
    )
    assert first.status_code == 200
    with get_db() as conn:
        old_path = conn.execute("SELECT image_path FROM products WHERE id=?", (product_id,)).fetchone()["image_path"]

    for filename, content in [
        ("fake.png", b"not an image"),
        ("large.png", b"x" * (5 * 1024 * 1024 + 1)),
    ]:
        response = client.post(
            f"/products/{product_id}/image",
            data={"image": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )
        assert response.status_code == 400
        with get_db() as conn:
            current = conn.execute("SELECT image_path FROM products WHERE id=?", (product_id,)).fetchone()["image_path"]
        assert current == old_path
        assert (data_root() / "product_images" / old_path).exists()


def test_image_rejects_unsupported_format_and_pixel_bomb():
    init_db()
    product_id = create_product("图片限制商品", "", "个", 100)
    client = create_app().test_client()
    gif = io.BytesIO()
    Image.new("RGB", (2, 2)).save(gif, format="GIF")
    for content in [gif.getvalue(), _image_bytes(size=(5000, 5000))]:
        response = client.post(
            f"/products/{product_id}/image",
            data={"image": (io.BytesIO(content), "input.png")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 400
        assert "商品图片" in response.get_data(as_text=True)
