# tests/ml/test_hub.py
"""The campaign kit's persistent cache.

The daemon runs with ``HF_HUB_OFFLINE=1`` (the embedding model is cached, so
nothing should need the network), but the kit was downloaded into a fresh temp
dir per process — so under the daemon's own env the freemium campaign could
never rank: ``qualifier_for`` got no kit, the scoring pass declined, and a
qualified lead waited "to be ranked" forever with no error in the log. The cache
fix is: download once, persist next to the DB, load the persisted copy first.
"""
import json

import joblib
import numpy as np
import pytest
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from openoutreach.core.ml import hub


def _fake_model():
    X = np.array([np.ones(8), np.zeros(8)], dtype=np.float64)
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("gpr", GaussianProcessRegressor(alpha=0.01, random_state=42)),
    ])
    model.fit(X, np.array([1.0, 0.0]))
    return model


def _write_kit(dir_path, model):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "config.json").write_text(json.dumps({
        "product_docs": "a product",
        "campaign_target": "a target",
        "booking_link": "https://example.com/",
    }))
    joblib.dump(model, dir_path / "model.joblib")


@pytest.fixture
def fresh_kit(monkeypatch, tmp_path):
    """Point the cache at a temp dir and reset the module's lazy cache."""
    monkeypatch.setattr(hub, "kit_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(hub, "_cached_kit", None)
    monkeypatch.setattr(hub, "_cache_attempted", False)
    return tmp_path


def test_offline_fetch_loads_from_persistent_cache(fresh_kit):
    """A kit already on disk loads when no download is possible (daemon env)."""
    _write_kit(fresh_kit, _fake_model())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(hub, "download_kit", lambda: None)
        kit = hub.fetch_kit()
    assert kit is not None
    assert kit["config"]["campaign_target"] == "a target"
    assert hasattr(kit["model"], "predict")


def test_downloaded_kit_is_persisted_for_later_offline_runs(fresh_kit, tmp_path):
    """A fresh download lands in the cache, so the next process can go offline."""
    src = tmp_path / "downloaded"
    _write_kit(src, _fake_model())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(hub, "download_kit", lambda: src)
        assert hub.fetch_kit() is not None
    assert (fresh_kit / "config.json").exists()
    assert (fresh_kit / "model.joblib").exists()

    # A later process (fresh lazy cache, no network) loads what was persisted.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(hub, "download_kit", lambda: None)
        mp.setattr(hub, "_cached_kit", None)
        mp.setattr(hub, "_cache_attempted", False)
        assert hub.fetch_kit() is not None


def test_corrupt_cache_falls_through_to_download(fresh_kit, tmp_path):
    """A cache that cannot be loaded does not poison later runs."""
    (fresh_kit / "config.json").write_text("{not json")
    (fresh_kit / "model.joblib").write_bytes(b"not a model")

    src = tmp_path / "downloaded"
    _write_kit(src, _fake_model())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(hub, "download_kit", lambda: src)
        kit = hub.fetch_kit()
    assert kit is not None
    # And the fresh download replaced the corrupt cache.
    assert "a target" in (fresh_kit / "config.json").read_text()
