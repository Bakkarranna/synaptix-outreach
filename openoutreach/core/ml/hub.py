# openoutreach/core/ml/hub.py
"""Campaign kit: download from HuggingFace, lazy-load, freemium campaign import."""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_cached_kit: Optional[dict] = None
_cache_attempted = False


# ------------------------------------------------------------------
# Kit download & loading
# ------------------------------------------------------------------

_DEFAULT_REPO_ID = "eracle/campaign-kit"


def kit_cache_dir() -> Path:
    """Where a downloaded kit is kept between processes.

    The daemon runs with ``HF_HUB_OFFLINE=1`` (the embedding model is cached, so
    nothing should need the network) — but the kit is downloaded fresh per process
    into a temp dir, which makes the freemium campaign silently unrunnable under
    the very env the daemon uses: ``qualifier_for`` gets no kit, the scoring pass
    declines, and a qualified freemium lead waits "to be ranked" forever with no
    error in the log. Persisting the download here (next to the DB) means the
    first online run caches it and every later run — offline or not — loads it.
    """
    from django.conf import settings

    return Path(settings.BASE_DIR) / "data" / "kit-cache"


def download_kit(revision: str = "v3") -> Optional[Path]:
    """Download campaign kit from HuggingFace Hub to a temp directory. Returns path or None.

    v3 is the email-first pivot kit: config.json keys the campaign target as
    ``campaign_target`` (renamed from ``campaign_objective``), and the product
    description/target reflect the pivoted product.
    """
    try:
        import huggingface_hub
        from huggingface_hub import snapshot_download

        huggingface_hub.utils.disable_progress_bars()
        logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
        logging.getLogger("filelock").setLevel(logging.WARNING)

        kit_dir = Path(tempfile.mkdtemp(prefix="openoutreach-kit-"))
        path = snapshot_download(
            repo_id=_DEFAULT_REPO_ID,
            revision=revision,
            local_dir=str(kit_dir),
        )
        # Remove HF download metadata cache — not needed after download
        shutil.rmtree(kit_dir / ".cache", ignore_errors=True)
        logger.debug("[Freemium] Kit downloaded to %s", path)
        return Path(path)
    except Exception:
        logger.debug("[Freemium] Kit download failed", exc_info=True)
        return None


def load_kit_config(kit_dir: Path) -> Optional[dict]:
    """Parse config.json from kit directory. Returns dict or None."""
    try:
        config_path = kit_dir / "config.json"
        data = json.loads(config_path.read_text())

        required = ("product_docs", "campaign_target", "booking_link")
        for key in required:
            if key not in data:
                logger.debug("[Freemium] Kit config missing key: %s", key)
                return None

        logger.debug("[Freemium] Kit config loaded")
        return data
    except Exception:
        logger.debug("[Freemium] Kit config load failed", exc_info=True)
        return None


def load_kit_model(kit_dir: Path):
    """Load pre-trained model from kit. Returns any sklearn-compatible estimator or None.

    The loaded object just needs a ``predict(X)`` method — it can be a
    Pipeline, a bare estimator, or any future model architecture.
    """
    try:
        import warnings

        import joblib
        from sklearn.exceptions import InconsistentVersionWarning

        # The published kit is pickled under whatever sklearn version trained it
        # (currently 1.8.0); unpickling under a newer runtime is expected and safe.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InconsistentVersionWarning)
            model = joblib.load(kit_dir / "model.joblib")

        if not hasattr(model, "predict"):
            logger.debug("[Freemium] Kit model has no predict() method")
            return None

        logger.debug("[Freemium] Kit model loaded (%s)", type(model).__name__)
        return model
    except Exception:
        logger.debug("[Freemium] Kit model load failed", exc_info=True)
        return None


def fetch_kit() -> Optional[dict]:
    """Lazy-load and cache the kit. Returns {"config": ..., "model": ...} or None.

    Order matters: the persistent cache is tried first because it is the only path
    that works offline; a fresh download lands in the cache (atomically, via a temp
    dir) so the next run — including the daemon's ``HF_HUB_OFFLINE=1`` env — finds
    it. A corrupt cache falls through to a fresh download rather than poisoning
    every later run.
    """
    global _cached_kit, _cache_attempted

    if _cache_attempted:
        return _cached_kit

    _cache_attempted = True

    cache = kit_cache_dir()
    config = load_kit_config(cache)
    model = load_kit_model(cache)
    if config is not None and model is not None:
        _cached_kit = {"config": config, "model": model}
        return _cached_kit

    kit_dir = download_kit()
    if kit_dir is None:
        return None

    config = load_kit_config(kit_dir)
    if config is None:
        return None

    model = load_kit_model(kit_dir)
    if model is None:
        return None

    _persist_cache(kit_dir)
    _cached_kit = {"config": config, "model": model}
    return _cached_kit


def _persist_cache(kit_dir: Path) -> None:
    """Copy the freshly downloaded kit into the persistent cache dir."""
    try:
        dest = kit_cache_dir()
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("config.json", "model.joblib"):
            src = kit_dir / name
            if src.exists():
                shutil.copy2(src, dest / name)
        logger.debug("[Freemium] Kit cached in %s", dest)
    except Exception:
        logger.debug("[Freemium] Kit cache persist failed", exc_info=True)
