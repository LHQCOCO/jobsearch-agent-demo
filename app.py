"""求职决策 Agent 演示入口：一个 Flask 应用，一个蓝图，没有前端构建链。

启动后打开 http://127.0.0.1:8899/jobsearch
"""
from __future__ import annotations

from flask import Flask, redirect, url_for

from cls.scenarios.jobsearch.web import jobsearch_bp

HOST = "127.0.0.1"
PORT = 8899
# 简历上传上限：单份 25 MB，表单再留一点余量
MAX_CONTENT_LENGTH = 30 * 1024 * 1024


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.register_blueprint(jobsearch_bp)

    @app.get("/")
    def index():
        return redirect(url_for("jobsearch.jobsearch_page"))

    return app


app = create_app()


if __name__ == "__main__":
    print(f"求职决策 Agent 演示：http://{HOST}:{PORT}/jobsearch")
    app.run(host=HOST, port=PORT, debug=False)
