/* Helpers de graficos: paleta, formato y configuracion comun de Chart.js. */
(function () {
  const SERIES_SLOTS = 8;

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (value || '').trim() || fallback;
  }

  const theme = {
    get surface() { return cssVar('--surface', '#fcfcfb'); },
    get grid() { return cssVar('--grid', '#e1e0d9'); },
    get axis() { return cssVar('--axis', '#c3c2b7'); },
    get muted() { return cssVar('--text-muted', '#898781'); },
    get secondary() { return cssVar('--text-secondary', '#52514e'); },
    get primary() { return cssVar('--text-primary', '#0b0b0b'); },
    /* Los colores categoricos se asignan en orden fijo, nunca ciclados: a
       partir del octavo, las series se agrupan en "Otros" en el servidor. */
    series(index) { return cssVar('--series-' + ((index % SERIES_SLOTS) + 1), '#2a78d6'); }
  };

  const currency = window.APP_CURRENCY || 'EUR';
  const locale = window.APP_LOCALE || 'es-ES';

  const moneyFmt = new Intl.NumberFormat(locale, {
    style: 'currency', currency: currency, maximumFractionDigits: 2
  });
  const compactFmt = new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 });

  function money(value) {
    if (value === null || value === undefined || isNaN(value)) return '-';
    try { return moneyFmt.format(value); } catch (e) { return value.toFixed(2) + ' ' + currency; }
  }
  function compact(value) {
    try { return compactFmt.format(value); } catch (e) { return String(value); }
  }
  function monthLabel(key) {
    const parts = String(key).split('-');
    if (parts.length !== 2) return key;
    const names = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
    const idx = parseInt(parts[1], 10) - 1;
    return (names[idx] || parts[1]) + ' ' + parts[0].slice(2);
  }

  function baseOptions(extra) {
    const options = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: theme.primary,
          titleColor: theme.surface,
          bodyColor: theme.surface,
          padding: 10,
          cornerRadius: 8,
          displayColors: true,
          boxWidth: 8,
          boxHeight: 8,
          usePointStyle: true,
          callbacks: {
            label: function (ctx) {
              const label = ctx.dataset.label ? ctx.dataset.label + ': ' : '';
              const raw = ctx.parsed.y !== undefined && ctx.parsed.y !== null && !isNaN(ctx.parsed.y)
                ? ctx.parsed.y : ctx.parsed.x !== undefined ? ctx.parsed.x : ctx.parsed;
              return label + money(raw);
            }
          }
        }
      }
    };
    return Object.assign(options, extra || {});
  }

  function moneyScale(axis) {
    return Object.assign({
      grid: { color: theme.grid, drawTicks: false, drawBorder: false },
      border: { display: false },
      ticks: { color: theme.muted, font: { size: 11 }, callback: function (v) { return compact(v); } }
    }, axis || {});
  }

  function categoryScale(axis) {
    return Object.assign({
      grid: { display: false, drawBorder: false },
      border: { color: theme.axis },
      ticks: { color: theme.secondary, font: { size: 11 }, autoSkip: false }
    }, axis || {});
  }

  /* Barras horizontales de una sola serie: sin leyenda (el titulo nombra la serie),
     extremos redondeados de 4px anclados a la linea base. */
  function horizontalBars(canvas, items, colorIndex) {
    const labels = items.map(function (i) { return i.label; });
    const data = items.map(function (i) { return i.value; });
    const color = theme.series(colorIndex === undefined ? 0 : colorIndex);
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          data: data,
          backgroundColor: color,
          borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 4, bottomRight: 4 },
          borderSkipped: false,
          borderColor: theme.surface,
          borderWidth: 2,
          maxBarThickness: 26
        }]
      },
      options: baseOptions({
        indexAxis: 'y',
        scales: { x: moneyScale({ beginAtZero: true }), y: categoryScale() }
      })
    });
  }

  function verticalBars(canvas, items, colorIndex) {
    const color = theme.series(colorIndex === undefined ? 0 : colorIndex);
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels: items.map(function (i) { return i.label; }),
        datasets: [{
          data: items.map(function (i) { return i.value; }),
          backgroundColor: color,
          borderRadius: { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 },
          borderSkipped: false,
          borderColor: theme.surface,
          borderWidth: 2,
          maxBarThickness: 44
        }]
      },
      options: baseOptions({
        scales: { y: moneyScale({ beginAtZero: true }), x: categoryScale() }
      })
    });
  }

  /* Un solo eje siempre: gasto e ingreso comparten escala porque comparten unidad. */
  function timeLines(canvas, labels, seriesList) {
    return new Chart(canvas, {
      type: 'line',
      data: {
        labels: labels,
        datasets: seriesList.map(function (s, i) {
          const color = theme.series(s.colorIndex === undefined ? i : s.colorIndex);
          return {
            label: s.label,
            data: s.data,
            borderColor: color,
            backgroundColor: color,
            borderWidth: 2,
            pointRadius: labels.length > 24 ? 0 : 4,
            pointHoverRadius: 6,
            pointBorderColor: theme.surface,
            pointBorderWidth: 2,
            tension: 0.25,
            fill: false
          };
        })
      },
      options: baseOptions({
        scales: { y: moneyScale({ beginAtZero: true }), x: categoryScale({ ticks: { color: theme.muted, font: { size: 11 }, autoSkip: true, maxRotation: 0 } }) }
      })
    });
  }

  /* Barras apiladas: 2px de hueco (del color de la superficie) entre segmentos. */
  function stackedBars(canvas, labels, seriesList) {
    return new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: seriesList.map(function (s, i) {
          return {
            label: s.label,
            data: s.data,
            backgroundColor: theme.series(i),
            borderColor: theme.surface,
            borderWidth: 2,
            borderRadius: 4,
            borderSkipped: false,
            maxBarThickness: 28
          };
        })
      },
      options: baseOptions({
        indexAxis: 'y',
        scales: {
          x: Object.assign(moneyScale({ beginAtZero: true }), { stacked: true }),
          y: Object.assign(categoryScale(), { stacked: true })
        }
      })
    });
  }

  function doughnut(canvas, items) {
    return new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: items.map(function (i) { return i.label; }),
        datasets: [{
          data: items.map(function (i) { return i.value; }),
          backgroundColor: items.map(function (_, i) { return theme.series(i); }),
          borderColor: theme.surface,
          borderWidth: 2
        }]
      },
      options: baseOptions({ cutout: '58%' })
    });
  }

  /* Leyenda en HTML: siempre presente cuando hay 2+ series, con texto en
     tokens de tinta (nunca del color de la serie). */
  function renderLegend(container, entries) {
    if (!container) return;
    container.innerHTML = '';
    if (!entries || entries.length < 2) return;
    entries.forEach(function (entry, i) {
      const item = document.createElement('span');
      item.className = 'item';
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = entry.color || theme.series(i);
      const label = document.createElement('span');
      label.textContent = entry.label;
      item.appendChild(swatch);
      item.appendChild(label);
      container.appendChild(item);
    });
  }

  window.Viz = {
    theme: theme,
    money: money,
    compact: compact,
    monthLabel: monthLabel,
    baseOptions: baseOptions,
    horizontalBars: horizontalBars,
    verticalBars: verticalBars,
    timeLines: timeLines,
    stackedBars: stackedBars,
    doughnut: doughnut,
    renderLegend: renderLegend
  };
})();
