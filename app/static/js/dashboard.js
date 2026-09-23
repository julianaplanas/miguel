/* Dashboard: filtros, KPIs, graficos y tabla de movimientos. */
(function () {
  const form = document.getElementById('filtros');
  const panel = document.getElementById('panel');
  const vacio = document.getElementById('estado-vacio');
  const charts = {};
  let ultimoResumen = null;
  let offset = 0;
  const PAGINA = 100;

  function queryString() {
    const params = new URLSearchParams();
    const desde = document.getElementById('desde').value;
    const hasta = document.getElementById('hasta').value;
    if (desde) params.append('desde', desde);
    if (hasta) params.append('hasta', hasta);
    Array.from(document.getElementById('persona').selectedOptions)
      .forEach(function (o) { params.append('persona', o.value); });
    Array.from(document.getElementById('categoria').selectedOptions)
      .forEach(function (o) { params.append('categoria', o.value); });
    var moneda = document.getElementById('moneda');
    if (moneda && moneda.value) { params.append('moneda', moneda.value); }
    return params.toString();
  }

  function destruir() {
    Object.keys(charts).forEach(function (key) {
      if (charts[key]) { charts[key].destroy(); charts[key] = null; }
    });
  }

  function pintarKpis(k, meses) {
    document.getElementById('kpi-gasto').textContent = Viz.money(k.total_expense);
    document.getElementById('kpi-periodo').textContent =
      k.date_min ? k.date_min + ' - ' + k.date_max : 'Sin fechas en los datos';
    document.getElementById('kpi-mensual').textContent = Viz.money(k.monthly_average);
    document.getElementById('kpi-meses').textContent =
      meses === 1 ? 'sobre 1 mes' : 'sobre ' + meses + ' meses';
    document.getElementById('kpi-medio').textContent = Viz.money(k.average_expense);
    document.getElementById('kpi-movimientos').textContent =
      k.transactions + ' movimientos · ' + k.categories + ' categorias · ' + k.people + ' personas';
    const balance = document.getElementById('kpi-balance');
    balance.textContent = Viz.money(k.net);
    balance.className = 'value ' + (k.net >= 0 ? 'good' : 'bad');
    document.getElementById('kpi-ingresos').textContent = 'Ingresos: ' + Viz.money(k.total_income);
  }

  function pintarGraficos(data) {
    destruir();

    const meses = data.by_month;
    const series = [{ label: 'Gasto', data: meses.map(function (m) { return m.expense; }), colorIndex: 0 }];
    const hayIngresos = meses.some(function (m) { return m.income > 0; });
    if (hayIngresos) {
      series.push({ label: 'Ingreso', data: meses.map(function (m) { return m.income; }), colorIndex: 2 });
    }
    charts.mes = Viz.timeLines(
      document.getElementById('chart-mes'),
      meses.map(function (m) { return Viz.monthLabel(m.label); }),
      series
    );
    Viz.renderLegend(document.getElementById('legend-mes'), series.map(function (s, i) {
      return { label: s.label, color: Viz.theme.series(s.colorIndex === undefined ? i : s.colorIndex) };
    }));

    charts.categoria = Viz.horizontalBars(document.getElementById('chart-categoria'), data.by_category, 0);
    charts.persona = Viz.horizontalBars(document.getElementById('chart-persona'), data.by_person, 6);

    const cruce = data.person_category;
    const etiquetas = cruce.rows.map(function (r) { return r.person; });
    const datasets = cruce.categories.map(function (cat, idx) {
      return {
        label: cat,
        data: cruce.rows.map(function (r) {
          return cat === 'Otros' ? r.others : (r.values[idx] || 0);
        })
      };
    });
    charts.cruce = Viz.stackedBars(document.getElementById('chart-cruce'), etiquetas, datasets);
    Viz.renderLegend(document.getElementById('legend-cruce'), datasets.map(function (d, i) {
      return { label: d.label, color: Viz.theme.series(i) };
    }));

    charts.semana = Viz.verticalBars(document.getElementById('chart-semana'), data.by_weekday, 0);
  }

  function pintarTop(rows) {
    const tbody = document.getElementById('tabla-top');
    tbody.innerHTML = '';
    rows.forEach(function (r) {
      const tr = document.createElement('tr');
      [r.date, r.description || '-', r.category, r.person].forEach(function (value) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.appendChild(td);
      });
      const td = document.createElement('td');
      td.className = 'num';
      td.textContent = Viz.money(r.amount);
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
  }

  function pintarMovimientos(data, append) {
    const tbody = document.getElementById('tabla-movimientos');
    if (!append) tbody.innerHTML = '';
    data.rows.forEach(function (r) {
      const tr = document.createElement('tr');
      [r.fecha, r.descripcion || '-', r.categoria, r.persona, r.archivo].forEach(function (value) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.appendChild(td);
      });
      const importe = document.createElement('td');
      importe.className = 'num';
      importe.textContent = Viz.money(r.importe, r.moneda);
      tr.appendChild(importe);
      const moneda = document.createElement('td');
      moneda.textContent = r.moneda || '';
      tr.appendChild(moneda);
      tbody.appendChild(tr);
    });
    const mostrados = Math.min(data.offset + data.rows.length, data.total);
    document.getElementById('movimientos-total').textContent = mostrados + ' de ' + data.total;
    const boton = document.getElementById('cargar-mas');
    boton.hidden = mostrados >= data.total;
    offset = mostrados;
  }

  async function cargarMovimientos(append) {
    const qs = queryString();
    const params = qs ? qs + '&' : '';
    const res = await fetch('/api/transacciones?' + params + 'limit=' + PAGINA + '&offset=' + (append ? offset : 0));
    if (!res.ok) return;
    pintarMovimientos(await res.json(), append);
  }


  function pintarAvisos(data) {
    const faltan = document.getElementById('aviso-monedas');
    const conversion = document.getElementById('aviso-conversion');
    if (data.missing_rates && data.missing_rates.length) {
      faltan.hidden = false;
      faltan.innerHTML = 'Falta el tipo de cambio de <strong>' + data.missing_rates.join(', ') +
        '</strong>, asi que no se pueden juntar las monedas. Mostrando solo ' + data.currency +
        '. <a href="/ajustes">Cargar tipo de cambio</a>';
    } else {
      faltan.hidden = true;
    }
    if (data.converted) {
      conversion.hidden = false;
      const partes = Object.keys(data.rates || {})
        .filter(function (c) { return c !== data.currency; })
        .map(function (c) { return '1 ' + c + ' = ' + data.rates[c] + ' ' + data.currency; });
      conversion.textContent = 'Todo convertido a ' + data.currency +
        (partes.length ? ' (' + partes.join(' · ') + ').' : '.');
    } else {
      conversion.hidden = true;
    }
  }

  function pintarMonedas(data) {
    const card = document.getElementById('card-monedas');
    const tbody = document.getElementById('tabla-monedas');
    const filas = data.by_currency || [];
    card.hidden = filas.length < 2;
    tbody.innerHTML = '';
    if (filas.length < 2) return;
    filas.forEach(function (f) {
      const tr = document.createElement('tr');
      const celdas = [
        f.code,
        Viz.money(f.expense, f.code),
        Viz.money(f.income, f.code),
        String(f.transactions),
        f.code === data.currency ? '—' : (f.rate ? '1 = ' + f.rate + ' ' + data.currency : 'sin cargar')
      ];
      celdas.forEach(function (valor, i) {
        const td = document.createElement('td');
        if (i >= 1 && i <= 3) td.className = 'num';
        td.textContent = valor;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  async function cargar() {
    const qs = queryString();
    document.getElementById('exportar').href = '/api/exportar.csv' + (qs ? '?' + qs : '');
    const res = await fetch('/api/resumen' + (qs ? '?' + qs : ''));
    if (!res.ok) {
      if (res.status === 401) { window.location.href = '/login'; }
      return;
    }
    const data = await res.json();
    ultimoResumen = data;
    Viz.setCurrency(data.currency);
    pintarAvisos(data);
    pintarMonedas(data);
    const sinDatos = data.kpis.transactions === 0;
    vacio.hidden = !sinDatos;
    panel.hidden = sinDatos;
    if (sinDatos) { destruir(); return; }
    pintarKpis(data.kpis, data.by_month.length);
    pintarGraficos(data);
    pintarTop(data.top_transactions);
    offset = 0;
    cargarMovimientos(false);
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    cargar();
  });

  document.getElementById('limpiar').addEventListener('click', function () {
    document.getElementById('desde').value = '';
    document.getElementById('hasta').value = '';
    ['persona', 'categoria'].forEach(function (id) {
      Array.from(document.getElementById(id).options).forEach(function (o) { o.selected = false; });
    });
    const moneda = document.getElementById('moneda');
    if (moneda) moneda.value = '';
    cargar();
  });

  document.getElementById('cargar-mas').addEventListener('click', function () {
    cargarMovimientos(true);
  });

  /* El modo oscuro tiene sus propios pasos de color: hay que repintar. */
  document.addEventListener('tema-cambiado', function () {
    if (ultimoResumen) pintarGraficos(ultimoResumen);
  });

  cargar();
})();
