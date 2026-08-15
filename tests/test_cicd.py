from __future__ import annotations

from pathlib import Path

import yaml

from deploy.bootstrap_cicd import github_subject

ROOT = Path(__file__).resolve().parents[1]


def test_github_oidc_subject_is_restricted_to_main() -> None:
    assert (
        github_subject("misanthropics-ai", "0815", "main")
        == "repo:misanthropics-ai/0815:ref:refs/heads/main"
    )


def test_deploy_workflow_uses_oidc_and_immutable_revision() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-aws.yml").read_text(encoding="utf-8")

    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "AWS_ACCESS_KEY_ID" not in workflow
    assert "AWS_SECRET_ACCESS_KEY" not in workflow
    assert "AWS_SESSION_TOKEN" not in workflow


def test_demo_runtime_preserves_data_and_requires_imdsv2() -> None:
    remote = (ROOT / "deploy" / "ssm_deploy.sh").read_text(encoding="utf-8")
    template = (ROOT / "deploy" / "cloudformation" / "github-actions-demo.yaml").read_text(
        encoding="utf-8"
    )

    assert 'data_volume="ai-rec-data"' in remote
    assert '--volume "${data_volume}:/app/backend/data"' in remote
    assert "/opt/app/backend/data" in remote
    assert "systemctl start backend.service" in remote
    assert "HttpTokens: required" in template
    assert "HttpPutResponseHopLimit: 2" in template
    assert "token.actions.githubusercontent.com:sub" in template


class CloudFormationLoader(yaml.SafeLoader):
    pass


def _construct_cloudformation_tag(
    loader: CloudFormationLoader, tag_suffix: str, node: yaml.Node
) -> object:
    del tag_suffix
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CloudFormationLoader.add_multi_constructor("!", _construct_cloudformation_tag)


def test_cloudformation_template_is_valid_yaml() -> None:
    template_path = ROOT / "deploy" / "cloudformation" / "github-actions-demo.yaml"
    parsed = yaml.load(template_path.read_text(encoding="utf-8"), Loader=CloudFormationLoader)

    assert parsed["Resources"]["GitHubDeployRole"]["Type"] == "AWS::IAM::Role"
    assert parsed["Resources"]["AppInstance"]["Condition"] == "CreateAppInstance"
    assert parsed["Parameters"]["ExistingInstanceId"]["Default"] == ""
    assert parsed["Resources"]["AppInstance"]["Properties"]["MetadataOptions"] == {
        "HttpEndpoint": "enabled",
        "HttpTokens": "required",
        "HttpPutResponseHopLimit": 2,
    }


def test_bootstrap_can_reuse_the_existing_runtime() -> None:
    bootstrap = (ROOT / "deploy" / "bootstrap_cicd.py").read_text(encoding="utf-8")
    legacy = (ROOT / "deploy" / "deploy_ec2.py").read_text(encoding="utf-8")

    assert '"ExistingInstanceId": existing["InstanceId"] if existing else ""' in bootstrap
    assert "prepare_existing_instance(ssm, existing" in bootstrap
    assert "HttpPutResponseHopLimit=2" in bootstrap
    assert 'elif "--update" in sys.argv' in legacy
    assert "embed_creds" not in legacy
