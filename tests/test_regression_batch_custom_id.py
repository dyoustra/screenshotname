"""The Batch API's own limit on custom_id, which the fake transport never enforced.

Every batch test passed while the real endpoint rejected the first request:
`requests.0.custom_id: String should have at most 64 characters`.
"""

from pathlib import Path

from shotname.hashing import content_hash

BATCH_CUSTOM_ID_MAX = 64


def test_content_hash_fits_the_batch_custom_id_limit(tmp_path: Path) -> None:
    path = tmp_path / "shot.png"
    path.write_bytes(b"\x89PNG" + bytes(range(256)) * 64)
    assert len(content_hash(path)) <= BATCH_CUSTOM_ID_MAX
