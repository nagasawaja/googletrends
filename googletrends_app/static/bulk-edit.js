(() => {
  const form = document.getElementById("bulk-timeframe-form");
  if (!form) {
    return;
  }

  const checkboxes = Array.prototype.slice.call(
    document.querySelectorAll('input[name="keyword_ids"]')
  );

  function setAll(checked) {
    checkboxes.forEach((checkbox) => {
      checkbox.checked = checked;
    });
  }

  const selectAll = form.querySelector("[data-select-all]");
  const selectNone = form.querySelector("[data-select-none]");
  const timeframeCheckboxes = Array.prototype.slice.call(
    document.querySelectorAll('input[name="timeframes"]')
  );
  const selectTimeframesAll = form.querySelector("[data-select-timeframes-all]");
  const selectTimeframesNone = form.querySelector("[data-select-timeframes-none]");

  if (selectAll) {
    selectAll.addEventListener("click", () => setAll(true));
  }

  if (selectNone) {
    selectNone.addEventListener("click", () => setAll(false));
  }

  function setTimeframes(checked) {
    timeframeCheckboxes.forEach((checkbox) => {
      checkbox.checked = checked;
    });
  }

  if (selectTimeframesAll) {
    selectTimeframesAll.addEventListener("click", () => setTimeframes(true));
  }

  if (selectTimeframesNone) {
    selectTimeframesNone.addEventListener("click", () => setTimeframes(false));
  }
})();
