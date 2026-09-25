/* Dashboard: filtros, KPIs, graficos y tabla de movimientos. */
(function () {
  const form = document.getElementById('filtros');
  const panel = document.getElementById('panel');
  const vacio = document.getElementById('estado-vacio');
  const charts = {};
  let ultimoResumen = null;
  let categoriasConocidas = [];
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

  function pintarKpis(k, meses, data) {
    document.getElementById('kpi-gasto').textContent = Viz.money(k.total_expense);
    // Cuando falta un tipo de cambio, estos numeros son de una sola moneda.
    // Decirlo en la tarjeta evita leer el total como si fuera todo.
    const fuera = (data.currencies || []).filter(function (c) { return c !== data.currency; });
    const soloUna = !data.converted && fuera.length ? ' · solo ' + data.currency : '';
    document.getElementById('kpi-periodo').textContent =
      (k.date_min ? k.date_min + ' - ' + k.date_max : 'Sin fechas en los datos') + soloUna;
    document.getElementById('kpi-mensual').textContent = Viz.money(k.monthly_average);
    document.getElementById('kpi-meses').textContent =
      meses === 1 ? 'sobre 1 mes' : 'sobre ' + meses + ' meses';
    document.getElementById('kpi-medio').textContent = Viz.money(k.average_expense);
    document.getElementById('kpi-movimientos').textContent =
      k.transactions + ' movimientos · ' + k.categories + ' categorias · ' + k.people + ' personas' +
      soloUna;
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

    /* Con una sola persona, "por persona" y el cruce son una barra unica
       que repite el total: ocupan media pantalla y no dicen nada. Vuelven
       a aparecer en cuanto haya dos. */
    const variasPersonas = (data.by_person || []).length > 1;
    document.getElementById('card-persona').hidden = !variasPersonas;
    document.getElementById('card-cruce').hidden = !variasPersonas;
    if (variasPersonas) {
      charts.persona = Viz.horizontalBars(document.getElementById('chart-persona'), data.by_person, 6);
    }

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
    if (variasPersonas) {
      charts.cruce = Viz.stackedBars(document.getElementById('chart-cruce'), etiquetas, datasets);
    }
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

  /* La categoria se edita en la misma celda: un click la convierte en un
     selector con las categorias conocidas. */
  function celdaCategoria(r) {
    const td = document.createElement('td');
    const boton = document.createElement('button');
    boton.type = 'button';
    boton.className = 'cat-edit';
    boton.textContent = r.categoria;
    if (r.categoria === 'Sin categoria') boton.classList.add('vacia');
    boton.title = 'Cambiar categoria';
    boton.addEventListener('click', function () { abrirEditor(td, r); });
    td.appendChild(boton);
    return td;
  }

  function abrirEditor(td, r) {
    td.innerHTML = '';
    const caja = document.createElement('div');
    caja.className = 'cat-editor';

    const select = document.createElement('select');
    categoriasConocidas.forEach(function (c) {
      const op = document.createElement('option');
      op.value = c;
      op.textContent = c;
      op.selected = c === r.categoria;
      select.appendChild(op);
    });
    const nueva = document.createElement('option');
    nueva.value = '__nueva__';
    nueva.textContent = '+ Nueva...';
    select.appendChild(nueva);

    const texto = document.createElement('input');
    texto.type = 'text';
    texto.placeholder = 'Nombre de la categoria';
    texto.hidden = true;
    select.addEventListener('change', function () {
      texto.hidden = select.value !== '__nueva__';
      if (!texto.hidden) texto.focus();
    });

    const todos = document.createElement('label');
    todos.className = 'cat-todos';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = true;
    todos.appendChild(check);
    todos.appendChild(document.createTextNode(' Aplicar a todos los que digan lo mismo'));

    const guardar = document.createElement('button');
    guardar.type = 'button';
    guardar.className = 'btn-sm btn-primary';
    guardar.textContent = 'Guardar';

    const cancelar = document.createElement('button');
    cancelar.type = 'button';
    cancelar.className = 'btn-sm';
    cancelar.textContent = 'Cancelar';
    cancelar.addEventListener('click', function () {
      td.replaceWith(celdaCategoria(r));
    });

    guardar.addEventListener('click', async function () {
      const categoria = select.value === '__nueva__' ? texto.value.trim() : select.value;
      if (!categoria) { texto.focus(); return; }
      guardar.disabled = true;
      try {
        const res = await fetch('/api/transacciones/' + r.id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
          body: JSON.stringify({
            categoria: categoria,
            aplicar_a_similares: check.checked,
            patron: r.descripcion
          })
        });
        if (!res.ok) { throw new Error((await res.json()).detail || res.status); }
        const data = await res.json();
        if (categoriasConocidas.indexOf(categoria) === -1) categoriasConocidas.push(categoria);
        avisar(data.aplicados > 1
          ? categoria + ': se aplico a ' + data.aplicados + ' movimientos.'
          : 'Categoria actualizada.');
        cargar();
      } catch (e) {
        avisar('No se pudo guardar: ' + e.message, true);
        guardar.disabled = false;
      }
    });

    caja.appendChild(select);
    caja.appendChild(texto);
    caja.appendChild(guardar);
    caja.appendChild(cancelar);
    caja.appendChild(todos);
    td.appendChild(caja);
    UI.mejorar(caja);
    const boton = caja.querySelector('.ui-select-boton');
    (boton || select).focus();
  }

  function avisar(texto, esError) {
    UI.toast(texto, esError ? 'error' : 'ok');
  }

  /* Borrar un movimiento. Ningun lector de PDF acierta siempre: si se cuela
     una linea de totales, tiene que poder sacarse sin borrar el archivo
     entero. Como esas lineas suelen repetirse en todos los resumenes, el
     dialogo ofrece borrar de una todos los que digan lo mismo. */
  function celdaBorrar(r) {
    const td = document.createElement('td');
    const boton = document.createElement('button');
    boton.type = 'button';
    boton.className = 'fila-borrar';
    boton.textContent = '\u00d7';
    boton.title = 'Borrar este movimiento';
    boton.setAttribute('aria-label', 'Borrar ' + (r.descripcion || 'movimiento'));
    boton.addEventListener('click', function () { borrarMovimiento(r, boton); });
    td.appendChild(boton);
    return td;
  }

  async function borrarMovimiento(r, boton) {
    const etiqueta = (r.descripcion || 'Sin descripcion') + ' · ' + Viz.money(r.importe, r.moneda);
    const respuesta = await UI.confirmar({
      titulo: 'Borrar movimiento',
      mensaje: etiqueta + (r.fecha ? ' (' + UI.aTexto(r.fecha) + ')' : '') +
        '. No se puede deshacer: para recuperarlo habria que volver a importar el archivo.',
      aceptar: 'Borrar',
      peligroso: true,
      opcion: { etiqueta: 'Borrar todos los que digan lo mismo', marcado: false }
    });
    if (!respuesta || !respuesta.ok) return;
    boton.disabled = true;
    try {
      const res = await fetch('/api/transacciones/' + r.id + '?similares=' + (respuesta.opcion ? 1 : 0),
        { method: 'DELETE', headers: { 'Accept': 'application/json' } });
      if (!res.ok) { throw new Error((await res.json()).detail || res.status); }
      const data = await res.json();
      avisar(data.borrados > 1
        ? 'Se borraron ' + data.borrados + ' movimientos.'
        : 'Movimiento borrado.');
      cargar();
    } catch (e) {
      avisar('No se pudo borrar: ' + e.message, true);
      boton.disabled = false;
    }
  }

  function pintarMovimientos(data, append) {
    const tbody = document.getElementById('tabla-movimientos');
    if (!append) tbody.innerHTML = '';
    data.rows.forEach(function (r) {
      const tr = document.createElement('tr');
      [r.fecha, r.descripcion || '-'].forEach(function (value) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.appendChild(td);
      });
      tr.appendChild(celdaCategoria(r));
      const persona = document.createElement('td');
      persona.textContent = r.persona;
      tr.appendChild(persona);
      // El nombre del archivo se repite en todas las filas y es largo: se
      // recorta y queda completo en el title.
      const archivo = document.createElement('td');
      archivo.className = 'col-archivo';
      archivo.textContent = r.archivo;
      archivo.title = r.archivo;
      tr.appendChild(archivo);
      const importe = document.createElement('td');
      importe.className = 'num';
      importe.textContent = Viz.money(r.importe, r.moneda);
      tr.appendChild(importe);
      const moneda = document.createElement('td');
      moneda.textContent = r.moneda || '';
      tr.appendChild(moneda);
      tr.appendChild(celdaBorrar(r));
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
      if (data.mode === 'historical') {
        let texto = 'Convertido a ' + data.currency +
          ' con el tipo de cambio de la fecha de cada movimiento.';
        if (data.historical_fallbacks) {
          texto += ' ' + data.historical_fallbacks + ' movimiento' +
            (data.historical_fallbacks === 1 ? '' : 's') + ' sin historico (' +
            (data.fallback_currencies || []).join(', ') + ') usaron el tipo actual.';
        }
        conversion.textContent = texto;
      } else {
        const partes = Object.keys(data.rates || {})
          .filter(function (c) { return c !== data.currency; })
          .map(function (c) { return '1 ' + c + ' = ' + data.rates[c] + ' ' + data.currency; });
        conversion.textContent = 'Todo convertido a ' + data.currency +
          ' con el tipo de cambio actual' + (partes.length ? ' (' + partes.join(' · ') + ')' : '') + '.';
      }
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
        f.code === data.currency
          ? '—'
          : (data.mode === 'historical'
              ? 'segun la fecha'
              : (f.rate ? '1 = ' + f.rate + ' ' + data.currency : 'sin cargar'))
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
    pintarKpis(data.kpis, data.by_month.length, data);
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
    ['persona', 'categoria'].forEach(function (id) {
      const select = document.getElementById(id);
      Array.from(select.options).forEach(function (o) { o.selected = false; });
      // El control disenado se entera por el evento, no por el cambio directo.
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const moneda = document.getElementById('moneda');
    if (moneda) {
      moneda.value = '';
      moneda.dispatchEvent(new Event('change', { bubbles: true }));
    }
    ['desde', 'hasta'].forEach(function (id) {
      const campo = document.getElementById(id);
      campo.value = '';
      campo.dispatchEvent(new Event('change', { bubbles: true }));
    });
    cargar();
  });

  document.getElementById('cargar-mas').addEventListener('click', function () {
    cargarMovimientos(true);
  });

  /* El modo oscuro tiene sus propios pasos de color: hay que repintar. */
  document.addEventListener('tema-cambiado', function () {
    if (ultimoResumen) pintarGraficos(ultimoResumen);
  });

  /* Los paneles plegados recuerdan como los dejaste: si sos de mirar la
     tabla de movimientos, no hay que abrirla en cada carga. */
  ['panel-top', 'panel-movimientos'].forEach(function (id) {
    const panel = document.getElementById(id);
    if (!panel) return;
    try {
      if (localStorage.getItem('abierto:' + id) === '1') panel.open = true;
    } catch (e) {}
    panel.addEventListener('toggle', function () {
      try { localStorage.setItem('abierto:' + id, panel.open ? '1' : '0'); } catch (e) {}
    });
  });

  fetch('/api/categorias')
    .then(function (r) { return r.ok ? r.json() : { categorias: [] }; })
    .then(function (d) { categoriasConocidas = d.categorias || []; });

  cargar();
})();
