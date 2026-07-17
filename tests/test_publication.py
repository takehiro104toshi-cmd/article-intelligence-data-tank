"""§30-29〜38: package生成・gzip・checksum・manifest・件数/サイズ上限・
private/restricted/raw本文除外・atomic publication。"""
import gzip
import json
from datetime import datetime, timezone

from tank.cluster import new_cluster_id, upsert_cluster
from tank.market_reaction import MarketReactionStore
from tank.publication import build_package, publish_package, validate_package_schema
from tank.private_store import PrivateArticleStore
from tests.factories import make_article


def _empty_reaction_store(tmp_path):
    return MarketReactionStore(str(tmp_path / "reactions.json"))


def test_package_schema_validates():
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    pkg = build_package([], {}, {}, _EmptyReactionStore(), [], {"total_articles": 0}, {}, now=now)
    assert validate_package_schema(pkg)


class _EmptyReactionStore:
    def load_all(self):
        return {}

    def has_any_reaction(self, cluster_id):
        return False

    def reaction_magnitude(self, cluster_id):
        return 0.0


def test_hot_articles_capped_at_limit():
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    articles = [make_article(url=f"https://s{i}.example/1", source_domain=f"s{i}.example",
                             title=f"記事{i}", published_at=now) for i in range(150)]
    pkg = build_package(articles, {}, {}, _EmptyReactionStore(), [], {}, {}, now=now,
                        limits={"max_hot_articles": 100})
    assert len(pkg["hot_articles"]) <= 100


def test_package_size_limit_enforced():
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    articles = [make_article(url=f"https://s{i}.example/1", source_domain=f"s{i}.example",
                             title=f"記事タイトルその{i}" * 20, published_at=now) for i in range(500)]
    pkg = build_package(articles, {}, {}, _EmptyReactionStore(), [], {}, {}, now=now,
                        limits={"max_hot_articles": 500, "package_max_uncompressed_mb": 0.05})
    size_mb = len(json.dumps(pkg, ensure_ascii=False).encode("utf-8")) / (1024 * 1024)
    assert size_mb <= 0.05 + 0.01  # トリムにより上限近くまで抑えられる


def test_private_and_restricted_body_never_in_package(tmp_path):
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    private_store = PrivateArticleStore(str(tmp_path / "private"))
    article_id = private_store.save("https://paywalled.example/1", "有料記事タイトル", "ここに全文本文が入る" * 50)
    public_view = private_store.get_public_view(article_id)

    pkg = build_package([public_view], {}, {}, _EmptyReactionStore(), [], {}, {}, now=now)
    blob = json.dumps(pkg, ensure_ascii=False)
    assert "ここに全文本文が入る" not in blob
    assert "full_body" not in blob


def test_raw_description_never_in_package_only_short_excerpt():
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    long_body = "本文" * 500
    art = make_article(url="https://x.example/1", description=long_body, published_at=now)
    pkg = build_package([art], {}, {}, _EmptyReactionStore(), [], {}, {}, now=now)
    blob = json.dumps(pkg, ensure_ascii=False)
    assert long_body not in blob


def test_gzip_and_checksum_and_manifest(tmp_path):
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    pkg = build_package([], {}, {}, _EmptyReactionStore(), [], {"total_articles": 0}, {}, now=now)
    manifest = publish_package(pkg, str(tmp_path / "published"))
    assert manifest["publication_status"] == "success"
    assert "checksum" in manifest and len(manifest["checksum"]) == 64  # sha256 hex

    package_path = tmp_path / "published" / "intelligence_package.json.gz"
    assert package_path.exists()
    decompressed = gzip.decompress(package_path.read_bytes())
    restored = json.loads(decompressed)
    assert restored["schema_version"] == pkg["schema_version"]


def test_atomic_publication_leaves_no_temp_files(tmp_path):
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    pkg = build_package([], {}, {}, _EmptyReactionStore(), [], {}, {}, now=now)
    publish_package(pkg, str(tmp_path / "published"))
    leftovers = list((tmp_path / "published").glob("*.tmp"))
    assert leftovers == []


def test_invalid_schema_fails_publication(tmp_path):
    manifest = publish_package({"broken": True}, str(tmp_path / "published"))
    assert manifest["publication_status"] == "failed"
    assert not (tmp_path / "published" / "intelligence_package.json.gz").exists()
