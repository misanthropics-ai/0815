from __future__ import annotations

from scripts import check_bedrock


class FakeBedrockClient:
    def converse(self, **kwargs):
        assert kwargs["modelId"] == "test.model"
        return {
            "output": {"message": {"content": [{"text": "Bedrock is ready"}]}},
            "usage": {"totalTokens": 7},
        }


def test_bedrock_check_uses_environment(monkeypatch, capsys) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test.model")
    monkeypatch.setattr(check_bedrock.boto3, "client", lambda *args, **kwargs: FakeBedrockClient())

    assert check_bedrock.main() == 0
    output = capsys.readouterr().out
    assert "model: test.model" in output
    assert "tokens: 7" in output
