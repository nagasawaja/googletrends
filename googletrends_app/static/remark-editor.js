(() => {
  const PLACEHOLDER = "点击添加备注";

  function renderDisplay(cell, remark) {
    cell.dataset.remark = remark;
    cell.innerHTML = "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "remark-display";
    button.textContent = remark || PLACEHOLDER;
    button.addEventListener("click", () => renderEditor(cell));
    cell.appendChild(button);
  }

  function renderEditor(cell) {
    const saveUrl = cell.dataset.saveUrl;
    const original = cell.dataset.remark || "";
    cell.innerHTML = "";

    const form = document.createElement("form");
    form.className = "remark-editor";

    const textarea = document.createElement("textarea");
    textarea.name = "remark";
    textarea.rows = 3;
    textarea.maxLength = 500;
    textarea.value = original;

    const actions = document.createElement("div");
    actions.className = "remark-actions";

    const save = document.createElement("button");
    save.type = "submit";
    save.className = "button small primary";
    save.textContent = "保存";

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button small";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => renderDisplay(cell, original));

    actions.append(save, cancel);
    form.append(textarea, actions);
    cell.appendChild(form);
    textarea.focus();
    textarea.select();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      const body = new URLSearchParams();
      body.set("remark", textarea.value);
      try {
        const response = await fetch(saveUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          },
          body,
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = await response.json();
        renderDisplay(cell, payload.remark || "");
      } catch (error) {
        save.disabled = false;
        textarea.classList.add("error");
      }
    });
  }

  document.querySelectorAll(".remark-cell").forEach((cell) => {
    const remark = cell.dataset.remark || "";
    renderDisplay(cell, remark);
  });
})();
