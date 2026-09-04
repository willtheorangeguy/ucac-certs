"""What the file store will accept, and what it does with it."""

import pytest

from lss_report.web.files import MAX_BYTES, FileStore, RejectedUpload, clean_filename, detect

import io

PDF = b"%PDF-1.7\nnot really a document"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16


@pytest.mark.parametrize(
    "payload, content_type",
    [
        (PDF, "application/pdf"),
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (WEBP, "image/webp"),
        (HEIC, "image/heic"),
    ],
)
def test_each_accepted_kind_is_recognised_by_its_own_bytes(payload, content_type):
    assert detect(payload[:12]).content_type == content_type


@pytest.mark.parametrize(
    "payload",
    [
        b"<script>alert(1)</script>",
        b"PK\x03\x04zip archive",
        b"%!PS-Adobe-3.0",
        b"",
    ],
)
def test_anything_else_is_not_recognised(payload):
    assert detect(payload[:12]) is None


def test_a_disguised_file_is_refused_whatever_it_is_called(tmp_path):
    store = FileStore(tmp_path)
    with pytest.raises(RejectedUpload):
        store.save(io.BytesIO(b"<script>alert(1)</script>"))
    # Nothing is left behind for a later request to serve.
    assert list(tmp_path.glob("*")) == []


def test_a_saved_file_keeps_its_bytes_under_a_generated_name(tmp_path):
    store = FileStore(tmp_path)
    stored = store.save(io.BytesIO(PDF))
    assert stored.stored_name.endswith(".pdf")
    assert stored.size == len(PDF)
    assert store.path(stored.stored_name).read_bytes() == PDF


def test_two_uploads_of_the_same_file_do_not_collide(tmp_path):
    store = FileStore(tmp_path)
    first = store.save(io.BytesIO(PDF))
    second = store.save(io.BytesIO(PDF))
    assert first.stored_name != second.stored_name
    assert len(list(tmp_path.glob("*"))) == 2


def test_an_oversized_upload_is_refused_and_leaves_nothing_on_disk(tmp_path):
    store = FileStore(tmp_path)
    with pytest.raises(RejectedUpload):
        store.save(io.BytesIO(PDF + b"\x00" * MAX_BYTES))
    assert list(tmp_path.glob("*")) == []


def test_deleting_a_stored_file_is_safe_to_repeat(tmp_path):
    store = FileStore(tmp_path)
    stored = store.save(io.BytesIO(PNG))
    store.delete(stored.stored_name)
    store.delete(stored.stored_name)
    assert list(tmp_path.glob("*")) == []


@pytest.mark.parametrize("name", ["../../etc/passwd", "..", "", "a/b.pdf", r"x\y.pdf"])
def test_the_store_only_addresses_names_it_generated(tmp_path, name):
    with pytest.raises(ValueError):
        FileStore(tmp_path).path(name)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("nlspc.pdf", "nlspc.pdf"),
        (r"C:\Users\robin\Desktop\card.png", "card.png"),
        ("../../etc/passwd", "passwd"),
        ("bronze\r\ncross.pdf", "bronzecross.pdf"),
        ("   ", "certificate"),
        (".", "certificate"),
    ],
)
def test_an_uploads_own_name_is_reduced_to_something_safe(raw, expected):
    assert clean_filename(raw) == expected
