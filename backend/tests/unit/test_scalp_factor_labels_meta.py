"""短线进化目标标签 / meta usable 门控单测。"""
import numpy as np
import pandas as pd


def test_forward_returns_default_is_simple_pct(monkeypatch):
    import backend.services.evolution.factor_evolution_loop as evo

    monkeypatch.setattr(evo, "_ACTIVE_EVO_PERIOD", "4h")
    monkeypatch.setenv("FEATURE_FACTOR_LABELS_ENABLED", "false")
    # reload flag inside factor_labels is read at call time via import
    close = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 110.0], dtype=float)
    df = pd.DataFrame({"close": close, "high": close, "low": close, "open": close, "volume": 1.0})
    fwd = evo._forward_returns(df, horizon=2)
    assert abs(fwd[0] - (102.0 / 100.0 - 1.0)) < 1e-9
    assert fwd[-1] == 0.0


def test_forward_returns_5m_can_use_triple_barrier(monkeypatch):
    import backend.services.evolution.factor_evolution_loop as evo
    import backend.services.evolution.factor_labels as fl

    monkeypatch.setattr(evo, "_ACTIVE_EVO_PERIOD", "5m")
    monkeypatch.setattr(fl, "FEATURE_FACTOR_LABELS_ENABLED", True)

    n = 80
    # 制造一段上涨再回落，便于障碍打出非零标签
    close = np.linspace(100, 120, n // 2).tolist() + np.linspace(120, 100, n - n // 2).tolist()
    close = np.asarray(close, dtype=float)
    df = pd.DataFrame(
        {
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "open": close,
            "volume": np.ones(n),
        }
    )

    def _fake_labels(d, horizon_bars=12, **kwargs):
        # 返回交替非零标签，证明路径被走到
        s = pd.Series([1 if i % 3 == 0 else (-1 if i % 3 == 1 else 0) for i in range(len(d))], index=d.index)
        return s

    monkeypatch.setattr(fl, "build_triple_barrier_labels", _fake_labels)
    out = evo._forward_returns(df, horizon=5)
    assert len(out) == n
    assert np.any(out != 0)
    assert set(np.unique(out)).issubset({-1.0, 0.0, 1.0})


def test_predict_win_prob_requires_usable_by_default(monkeypatch, tmp_path):
    from backend.services import scalp_meta_trainer as sm

    model_path = tmp_path / "scalp_meta_model.pkl"
    import joblib
    from sklearn.dummy import DummyClassifier

    X = np.array([[0.0], [1.0]])
    y = np.array([0, 1])
    clf = DummyClassifier(strategy="most_frequent").fit(X, y)
    joblib.dump(
        {
            "model": clf,
            "feature_cols": ["factor_score"],
            "meta": {"usable": False},
        },
        model_path,
    )
    monkeypatch.setattr(sm, "_MODEL_PATH", str(model_path))
    sm._MODEL_CACHE["obj"] = None
    sm._MODEL_CACHE["mtime"] = 0

    assert sm.predict_win_prob({"factor_score": 0.5}, require_usable=True) is None
    shadow = sm.predict_win_prob({"factor_score": 0.5}, require_usable=False)
    assert shadow is not None
