import json

from test_stage4 import Scripted, call

from app.api import chat as chat_api
from app.core.config import get_settings
from app.providers.llm import Delta
from scripts import evaluate_optimization_context as module


def test_runner_observes_current_inputs_without_paid_calls(tmp_path, monkeypatch):
    settings = get_settings()
    for key, value in dict(
        llm_base_url="https://example.test/v1",
        llm_model="fixture",
        llm_api_key="test-only",
    ).items():
        monkeypatch.setattr(settings, key, value)

    def embeddings(texts):
        return [[1.0] + [0.0] * 511 for _ in texts]

    monkeypatch.setattr(module, "embed_texts", embeddings)
    monkeypatch.setattr(chat_api, "embed_texts", embeddings)
    monkeypatch.setattr(
        chat_api,
        "get_llm_provider",
        lambda: Scripted(
            [
                [call("规则")],
                [Delta("资料事实 [1]", finish_reason="stop")],
                [Delta('{"supported":true}', finish_reason="stop")],
            ]
        ),
    )
    output = module.run(live=True, output_root=tmp_path)
    records = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert len(records) == 16
    assert all(len(r["model_input_hashes"]) == 3 for r in records)
    assert all(r["snapshot_ids"] and r["citations"] == 1 for r in records)
    assert json.loads((output / "budget.json").read_text())["reserved"] == 0
