(function () {
  function setupChartTooltip(wrapper) {
    var tooltip = wrapper.querySelector(".chart-tooltip");
    var svg = wrapper.querySelector(".chart");
    var crosshair = wrapper.querySelector(".chart-crosshair");
    var points = Array.prototype.slice.call(wrapper.querySelectorAll(".trend-hit"));
    if (!tooltip || !svg || points.length === 0) {
      return;
    }

    var chartPoints = points.map(function (point) {
      return {
        element: point,
        x: Number(point.dataset.x),
        y: Number(point.dataset.y),
        date: point.dataset.date,
        value: point.dataset.value,
      };
    });
    var activePoint = null;

    function toSvgPoint(event) {
      var svgPoint = svg.createSVGPoint();
      svgPoint.x = event.clientX;
      svgPoint.y = event.clientY;
      return svgPoint.matrixTransform(svg.getScreenCTM().inverse());
    }

    function nearestPoint(x) {
      return chartPoints.reduce(function (nearest, point) {
        return Math.abs(point.x - x) < Math.abs(nearest.x - x) ? point : nearest;
      }, chartPoints[0]);
    }

    function activatePoint(point) {
      if (activePoint && activePoint !== point) {
        activePoint.element.classList.remove("active");
      }
      activePoint = point;
      point.element.classList.add("active");
    }

    function showTooltip(event) {
      var svgPosition = toSvgPoint(event);
      var point = nearestPoint(svgPosition.x);
      var rect = wrapper.getBoundingClientRect();
      activatePoint(point);

      if (crosshair) {
        crosshair.setAttribute("x1", point.x);
        crosshair.setAttribute("x2", point.x);
        crosshair.classList.add("visible");
      }

      tooltip.innerHTML =
        '<div class="chart-tooltip-time">' +
        point.date +
        '</div><div class="chart-tooltip-value">指数 ' +
        point.value +
        "</div>";
      tooltip.classList.add("visible");

      var tooltipWidth = tooltip.offsetWidth;
      var tooltipHeight = tooltip.offsetHeight;
      var cursorX = event.clientX - rect.left;
      var cursorY = event.clientY - rect.top;
      var tooltipX = cursorX + 14;
      var tooltipY = cursorY + 14;

      if (tooltipX + tooltipWidth > rect.width - 8) {
        tooltipX = cursorX - tooltipWidth - 14;
      }
      if (tooltipY + tooltipHeight > rect.height - 8) {
        tooltipY = cursorY - tooltipHeight - 14;
      }

      tooltip.style.left = Math.max(8, tooltipX) + "px";
      tooltip.style.top = Math.max(8, tooltipY) + "px";
    }

    function hideTooltip() {
      tooltip.classList.remove("visible");
      if (crosshair) {
        crosshair.classList.remove("visible");
      }
      if (activePoint) {
        activePoint.element.classList.remove("active");
        activePoint = null;
      }
    }

    svg.addEventListener("mousemove", showTooltip);
    svg.addEventListener("mouseleave", hideTooltip);

    points.forEach(function (point) {
      point.addEventListener("focus", function () {
        activatePoint(chartPoints[points.indexOf(point)]);
      });
    });
  }

  document.querySelectorAll(".chart-wrap").forEach(setupChartTooltip);
})();
