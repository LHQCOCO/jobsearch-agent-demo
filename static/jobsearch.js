// 演示页只做两件事：显示已选文件名、提交时避免重复点击。
(function () {
  var input = document.getElementById("resumeFile");
  var label = document.getElementById("resumeFileName");
  if (input && label) {
    input.addEventListener("change", function () {
      label.textContent = input.files && input.files.length
        ? "已选择：" + input.files[0].name
        : "未选择文件（也可直接粘贴）";
    });
  }
  var form = document.querySelector(".jform");
  var btn = document.getElementById("btnAnalyze");
  if (form && btn) {
    form.addEventListener("submit", function () {
      btn.disabled = true;
      btn.textContent = "分析中…";
    });
  }
})();
