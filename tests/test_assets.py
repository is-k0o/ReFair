import gzip

from refair.assets import content_sha256
from refair.storage import SQLiteRepository


def test_different_urls_with_identical_content_share_identity(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()

    first = repository.add_asset(b"const version = 1;", "/static/chunk/124.js")
    second = repository.add_asset(b"const version = 1;", "/assets/foo.js?v=4")

    assert first.content_hash == second.content_hash
    assert set(second.observed_urls) == {
        "/static/chunk/124.js",
        "/assets/foo.js?v=4",
    }


def test_same_url_with_changed_content_creates_new_version(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "refair.sqlite3")
    repository.initialize()

    old = repository.add_asset(b"const version = 1;", "/app.js")
    new = repository.add_asset(b"const version = 2;", "/app.js")

    assert old.content_hash != new.content_hash
    assert old.observed_urls == new.observed_urls == ("/app.js",)


def test_identical_filename_with_different_body_has_different_identity() -> None:
    assert content_sha256(b"one") != content_sha256(b"two")


def test_different_filename_and_path_with_same_body_has_same_identity() -> None:
    body = b"export const stable = true;"
    identities = {
        content_sha256(body),
        content_sha256(body),
    }
    assert len(identities) == 1


def test_compressed_and_decompressed_body_share_content_identity() -> None:
    body = b"const compressed = true;"

    assert content_sha256(gzip.compress(body), "gzip") == content_sha256(body)
