"""求职决策演示页：页面渲染、真实运行、参数校验与决策卡路由。"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

import app as app_module
from cls import config
from cls.scenarios.jobsearch.documents import JDS, RESUMES

ROOT = Path(__file__).resolve().parents[1]
JD_TEXT = (ROOT / "samples" / "jobs" / "jd_ai_agent.md").read_text(encoding="utf-8")
CV_TEXT = (ROOT / "samples" / "jobs" / "resume_sample.md").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """trace、决策卡、sqlite 记忆全部落在临时目录，避免污染仓库 data/。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    JDS.clear()
    RESUMES.clear()
    yield
    JDS.clear()
    RESUMES.clear()


@pytest.fixture
def client():
    return app_module.app.test_client()


def _run_id(html: str) -> str:
    match = re.search(r"run_id</span><b>([0-9a-z]+)", html)
    assert match, "页面没有渲染 run_id"
    return match.group(1)


def test_page_renders_form_and_capability_boundary(client):
    resp = client.get("/jobsearch")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "开始分析" in html
    assert "未接入本页" in html          # 模型增强状态如实标注
    assert "规则实现（确定性）" in html
    assert "还没有运行结果" in html


def test_autorun_renders_real_run_output(client):
    resp = client.get("/jobsearch?autorun=1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "匹配矩阵" in html
    assert "缺口与选项" in html
    assert "改写建议" in html
    assert "FACT_ADDED" in html
    assert "新增RAG、Docker、Kubernetes" in html   # 来自真实运行的打回理由
    assert "第二轮已删除该条" in html
    assert "第 1 轮草稿" in html and "第 2 轮草稿" in html
    assert "运行时间线" in html
    _run_id(html)


def test_analyze_post_with_samples(client):
    resp = client.post("/jobsearch/analyze",
                       data={"jd_text": JD_TEXT, "resume_text": CV_TEXT})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "匹配矩阵" in html
    assert "req_id=req-03" in html
    assert "证据层打回记录" in html


def test_analyze_rejects_short_input(client):
    resp = client.post("/jobsearch/analyze", data={"jd_text": "太短", "resume_text": CV_TEXT})
    assert resp.status_code == 400
    assert "JD 正文过短" in resp.get_data(as_text=True)

    resp = client.post("/jobsearch/analyze", data={"jd_text": JD_TEXT, "resume_text": "太短"})
    assert resp.status_code == 400
    assert "简历内容过短" in resp.get_data(as_text=True)


def test_analyze_accepts_resume_upload(client):
    resp = client.post(
        "/jobsearch/analyze",
        data={"jd_text": JD_TEXT,
              "resume_file": (io.BytesIO(CV_TEXT.encode("utf-8")), "resume_sample.md")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "匹配矩阵" in resp.get_data(as_text=True)


def test_brief_route_serves_generated_card_and_404(client):
    html = client.get("/jobsearch?autorun=1").get_data(as_text=True)
    run_id = _run_id(html)
    resp = client.get(f"/jobsearch/brief/{run_id}")
    assert resp.status_code == 200
    assert "投递决策卡" in resp.get_data(as_text=True)
    assert client.get("/jobsearch/brief/notexist").status_code == 404
