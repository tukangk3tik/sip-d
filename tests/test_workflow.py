from pathlib import Path


def test_python_deployment_workflow_runs_tests_and_python_service():
    workflow = Path(".github/workflows/ci-cd.yml").read_text()
    assert "actions/setup-python" in workflow
    assert "python3 -m pytest -q" in workflow
    assert "sip-d-python.service" in workflow
    assert "systemctl enable sip-d-python" in workflow
    assert "systemctl restart sip-d-python" in workflow
