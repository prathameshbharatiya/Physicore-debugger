import hashlib

import pytest

from physicore_collector.checksums import FileTooLargeError, read_and_hash, sha256_file


def test_known_vectors(tmp_path):
    f = tmp_path / "abc.bin"
    f.write_bytes(b"abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    e = tmp_path / "empty.bin"
    e.write_bytes(b"")
    assert sha256_file(e) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.mark.parametrize("chunk", [1, 3, 7, 4096, 10**6])
def test_chunk_size_does_not_change_result(tmp_path, chunk):
    data = bytes(range(256)) * 50
    f = tmp_path / "d.bin"
    f.write_bytes(data)
    assert sha256_file(f, chunk_size=chunk) == hashlib.sha256(data).hexdigest()
    content, digest = read_and_hash(f, max_bytes=10**6, chunk_size=chunk)
    assert content == data and digest == hashlib.sha256(data).hexdigest()


def test_file_is_actually_read_in_chunks(tmp_path, monkeypatch):
    f = tmp_path / "d.bin"
    f.write_bytes(b"x" * 100)
    reads = []
    real_open = open

    def spy_open(*a, **k):
        fh = real_open(*a, **k)
        orig = fh.read
        fh.read = lambda n=-1: (reads.append(n), orig(n))[1]
        return fh

    import physicore_collector.checksums as mod
    monkeypatch.setattr(mod, "open", spy_open, raising=False)
    sha256_file(f, chunk_size=10)
    assert reads and all(n == 10 for n in reads) and len(reads) >= 10


def test_invalid_chunk_size(tmp_path):
    f = tmp_path / "d.bin"
    f.write_bytes(b"x")
    for fn in (lambda: sha256_file(f, chunk_size=0),
               lambda: read_and_hash(f, max_bytes=5, chunk_size=-1)):
        with pytest.raises(ValueError):
            fn()


def test_missing_file_raises_not_swallowed(tmp_path):
    with pytest.raises(FileNotFoundError):
        sha256_file(tmp_path / "nope")


def test_read_and_hash_enforces_limit(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 101)
    with pytest.raises(FileTooLargeError):
        read_and_hash(f, max_bytes=100, chunk_size=10)
    f.write_bytes(b"x" * 100)  # exactly at the limit is allowed
    assert len(read_and_hash(f, max_bytes=100)[0]) == 100


def test_copy_and_hash_is_byte_exact_and_returns_digest(tmp_path):
    from physicore_collector.checksums import copy_and_hash
    data = bytes(range(256)) * 1000
    src, dst = tmp_path / "s", tmp_path / "d"
    src.write_bytes(data)
    digest, size = copy_and_hash(src, dst, chunk_size=777)
    assert dst.read_bytes() == data and size == len(data)
    assert digest == hashlib.sha256(data).hexdigest()


def test_copy_and_hash_refuses_to_overwrite(tmp_path):
    from physicore_collector.checksums import copy_and_hash
    src, dst = tmp_path / "s", tmp_path / "d"
    src.write_bytes(b"new")
    dst.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        copy_and_hash(src, dst)
    assert dst.read_bytes() == b"precious"
