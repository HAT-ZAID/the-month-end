"""Disk cache contract: identity, expiry, corruption and the disabled path."""
from __future__ import annotations

import json

from code.cache import (
    DECISIONS_NAMESPACE,
    DEFAULT_CACHE_DIR,
    OCR_NAMESPACE,
    DiskCache,
    cache_key,
    canonical_json,
    main as cache_cli,
)


class Clock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _cache(tmp_path, clock=None, **kwargs) -> DiskCache:
    return DiskCache(tmp_path / "cache", ttl_days=30, clock=clock or Clock(), **kwargs)


def test_put_then_get_returns_the_same_value(tmp_path):
    cache = _cache(tmp_path)
    payload = {"image": "images/image_01.png", "sha256": "a" * 64, "model": "qwen3-vl:2b"}
    value = {"raw_text": "Total 120.00", "amount": "120.00", "backend": "vlm"}

    assert cache.get(OCR_NAMESPACE, payload) is None
    assert cache.put(OCR_NAMESPACE, payload, value) is True

    assert cache.get(OCR_NAMESPACE, payload) == value
    assert cache.hits == 1 and cache.misses == 1 and cache.writes == 1


def test_payload_changes_invalidate_the_entry(tmp_path):
    cache = _cache(tmp_path)
    base = {"image": "images/image_01.png", "sha256": "a" * 64}
    cache.put(OCR_NAMESPACE, base, {"amount": "10"})

    changed_bytes = dict(base, sha256="b" * 64)
    changed_model = dict(base, model="qwen3-vl:2b-instruct")
    changed_prompt = dict(base, prompt_version=2)

    assert cache.get(OCR_NAMESPACE, changed_bytes) is None
    assert cache.get(OCR_NAMESPACE, changed_model) is None
    assert cache.get(OCR_NAMESPACE, changed_prompt) is None
    # The original entry is untouched.
    assert cache.get(OCR_NAMESPACE, base) == {"amount": "10"}


def test_identical_bytes_at_two_paths_are_two_entries(tmp_path):
    # No duplicate-collapsing: the path is part of the key even when the bytes are
    # the same, so both images keep their own record.
    cache = _cache(tmp_path)
    first = {"image": "images/image_01.png", "sha256": "c" * 64}
    second = {"image": "images/image_02.png", "sha256": "c" * 64}

    cache.put(OCR_NAMESPACE, first, {"amount": "1"})
    cache.put(OCR_NAMESPACE, second, {"amount": "2"})

    assert cache.get(OCR_NAMESPACE, first) == {"amount": "1"}
    assert cache.get(OCR_NAMESPACE, second) == {"amount": "2"}
    assert cache.path_for(OCR_NAMESPACE, first) != cache.path_for(OCR_NAMESPACE, second)


def test_namespaces_never_collide(tmp_path):
    payload = {"image": "images/image_01.png"}
    assert cache_key(OCR_NAMESPACE, payload) != cache_key(DECISIONS_NAMESPACE, payload)


def test_entries_expire_and_are_removed_on_read(tmp_path):
    clock = Clock()
    cache = _cache(tmp_path, clock=clock)
    payload = {"image": "images/image_01.png"}
    cache.put(OCR_NAMESPACE, payload, {"amount": "5"})
    path = cache.path_for(OCR_NAMESPACE, payload)

    clock.advance(29 * 86400)
    assert cache.get(OCR_NAMESPACE, payload) == {"amount": "5"}
    assert path.exists()

    clock.advance(2 * 86400)
    assert cache.get(OCR_NAMESPACE, payload) is None
    assert not path.exists(), "an expired entry is deleted, not just ignored"
    assert cache.expired == 1


def test_purge_expired_removes_only_old_entries(tmp_path):
    clock = Clock()
    cache = _cache(tmp_path, clock=clock)
    old = {"image": "images/old.png"}
    fresh = {"image": "images/fresh.png"}
    cache.put(OCR_NAMESPACE, old, {"amount": "1"})

    clock.advance(40 * 86400)
    cache.put(OCR_NAMESPACE, fresh, {"amount": "2"})

    removed = cache.purge_expired()

    assert removed == 1
    assert cache.get(OCR_NAMESPACE, old) is None
    assert cache.get(OCR_NAMESPACE, fresh) == {"amount": "2"}


def test_corrupt_entry_is_a_miss_and_is_discarded(tmp_path):
    cache = _cache(tmp_path)
    payload = {"image": "images/image_01.png"}
    path = cache.path_for(OCR_NAMESPACE, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert cache.get(OCR_NAMESPACE, payload) is None
    assert not path.exists()
    assert cache.hits == 0


def test_entry_without_a_value_is_a_miss(tmp_path):
    cache = _cache(tmp_path)
    payload = {"image": "images/image_01.png"}
    path = cache.path_for(OCR_NAMESPACE, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stored_at": 1_700_000_000.0}), encoding="utf-8")

    assert cache.get(OCR_NAMESPACE, payload) is None


def test_disabled_cache_neither_reads_nor_writes(tmp_path):
    cache = _cache(tmp_path, enabled=False)
    payload = {"image": "images/image_01.png"}

    assert cache.put(OCR_NAMESPACE, payload, {"amount": "1"}) is False
    assert cache.get(OCR_NAMESPACE, payload) is None
    assert not cache.root.exists()
    assert cache.purge_expired() == 0


def test_environment_can_disable_and_retarget_the_cache(tmp_path):
    env = {"BUY_WAIT_CACHE_DISABLE": "1", "BUY_WAIT_CACHE_DIR": str(tmp_path / "elsewhere")}
    cache = DiskCache(env=env)

    assert cache.enabled is False
    assert cache.root == tmp_path / "elsewhere"


def test_writes_are_atomic_and_leave_no_temp_files(tmp_path):
    cache = _cache(tmp_path)
    payload = {"image": "images/image_01.png"}
    cache.put(OCR_NAMESPACE, payload, {"amount": "1"})
    cache.put(OCR_NAMESPACE, payload, {"amount": "2"})

    leftovers = list((tmp_path / "cache").glob("**/*.tmp"))
    assert leftovers == []
    assert cache.get(OCR_NAMESPACE, payload) == {"amount": "2"}


def test_canonical_json_is_order_insensitive():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json({"a": 2}) != canonical_json({"a": 3})


def test_cli_purge_removes_expired_entries(tmp_path, capsys):
    clock = Clock()
    cache = _cache(tmp_path, clock=clock)
    cache.put(OCR_NAMESPACE, {"image": "images/old.png"}, {"amount": "1"})
    clock.advance(31 * 86400)

    code = cache_cli(["--root", str(tmp_path / "cache"), "--purge", "--stats"])

    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["purged"] == 1
    assert printed["stats"]["enabled"] is True
    assert not any((tmp_path / "cache").glob("**/*.json"))


def test_cli_reports_entries_without_purging(tmp_path, capsys):
    cache = _cache(tmp_path)
    cache.put(OCR_NAMESPACE, {"image": "images/one.png"}, {"amount": "1"})

    cache_cli(["--root", str(tmp_path / "cache")])

    printed = json.loads(capsys.readouterr().out)
    assert printed["entries"] == 1
    assert "purged" not in printed


def test_default_cache_root_is_repo_anchored():
    # One checkout, one cache: the default must not depend on the working directory.
    assert DEFAULT_CACHE_DIR.is_absolute()
    assert DEFAULT_CACHE_DIR.parts[-2:] == ("data", "cache")
