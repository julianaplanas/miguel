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
  // Orden de la tabla de movimientos. Va al servidor porque la tabla esta
  // paginada: ordenar solo lo cargado daria un orden falso.
  let orden = { campo: 'fecha', dir: 'desc' };

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

    /* El alto crece con la cantidad de categorias: con todas listadas, una
       caja fija las apretaba arriba y dejaba media tarjeta vacia. */
    const lienzoCategoria = document.getElementById('chart-categoria');
    lienzoCategoria.parentElement.style.height =
      Math.max(220, data.by_category.length * 30 + 60) + 'px';
    charts.categoria = Viz.horizontalBars(lienzoCategoria, data.by_category, 'auto');

    /* Con una sola persona, "por persona" y el cruce son una barra unica
       que repite el total: ocupan media pantalla y no dicen nada. Vuelven
       a aparecer en cuanto haya dos. */
    const variasPersonas = (data.by_person || []).length > 1;
    document.getElementById('card-persona').hidden = !variasPersonas;
    document.getElementById('card-cruce').hidden = !variasPersonas;
    if (variasPersonas) {
      charts.persona = Viz.horizontalBars(document.getElementById('chart-persona'), data.by_person, 'auto');
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
    const coloresCruce = Viz.colorsFor(datasets.map(function (d) { return d.label; }));
    Viz.renderLegend(document.getElementById('legend-cruce'), datasets.map(function (d, i) {
      return { label: d.label, color: coloresCruce[i] };
    }));

    /* Categorias mes a mes: apilado, con los meses abajo. Responde las dos
       preguntas de una: cuanto se fue en cada categoria cada mes, y como
       se compara un mes con otro. */
    const porMes = data.month_category || { categories: [], rows: [] };
    const cardMes = document.getElementById('card-mes-categoria');
    cardMes.hidden = porMes.rows.length < 2;
    if (!cardMes.hidden) {
      const meses = porMes.rows.map(function (r) { return Viz.monthLabel(r.month); });
      const series = porMes.categories.map(function (cat, idx) {
        return {
          label: cat,
          data: porMes.rows.map(function (r) {
            return cat === 'Otros' ? r.others : (r.values[idx] || 0);
          })
        };
      });
      charts.mesCategoria = Viz.stackedBars(
        document.getElementById('chart-mes-categoria'), meses, series, true
      );
      const coloresMes = Viz.colorsFor(series.map(function (s) { return s.label; }));
      Viz.renderLegend(document.getElementById('legend-mes-categoria'),
        series.map(function (s, i) { return { label: s.label, color: coloresMes[i] }; }));
    }
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
        // La fila se actualiza donde esta, y si la regla se aplico a otras
        // con la misma descripcion, esas tambien. La lista no se recarga:
        // no se pierde el scroll ni el orden.
        r.categoria = categoria;
        td.replaceWith(celdaCategoria(r));
        if (check.checked) {
          document.querySelectorAll('#tabla-movimientos tr').forEach(function (fila) {
            if (fila.dataset.descripcion !== r.descripcion) return;
            const celda = fila.querySelector('.cat-edit');
            if (!celda) return;
            celda.textContent = categoria;
            celda.classList.toggle('vacia', categoria === 'Sin categoria');
          });
        }
        cargarResumen();
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
      // Se quitan las filas de la tabla en vez de recargarla: asi no se
      // pierde el scroll ni el orden elegido.
      const fila = boton.closest('tr');
      if (data.borrados > 1) {
        document.querySelectorAll('#tabla-movimientos tr').forEach(function (otra) {
          if (otra.dataset.descripcion === (r.descripcion || '')) otra.remove();
        });
      } else if (fila) {
        fila.remove();
      }
      cargarResumen();
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
      tr.dataset.descripcion = r.descripcion || '';
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
    const res = await fetch('/api/transacciones?' + params + 'limit=' + PAGINA +
      '&offset=' + (append ? offset : 0) + '&orden=' + orden.campo + '&dir=' + orden.dir);
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

  /* Refresca KPIs y graficos SIN tocar la tabla de movimientos. Es lo que
     se llama al corregir una categoria: si se recargara la tabla entera,
     volverias al principio de la lista cada vez que arreglas una fila. */
  async function cargarResumen() {
    const qs = queryString();
    const res = await fetch('/api/resumen' + (qs ? '?' + qs : ''));
    if (!res.ok) return;
    const data = await res.json();
    ultimoResumen = data;
    Viz.setCurrency(data.currency);
    pintarAvisos(data);
    pintarMonedas(data);
    if (data.kpis.transactions === 0) return;
    pintarKpis(data.kpis, data.by_month.length, data);
    pintarGraficos(data);
    pintarTop(data.top_transactions);
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
    const mes = document.getElementById('mes');
    if (mes) {
      limpiando = true;
      mes.value = '';
      mes.dispatchEvent(new Event('change', { bubbles: true }));
      limpiando = false;
    }
    cargar();
  });

  document.getElementById('cargar-mas').addEventListener('click', function () {
    cargarMovimientos(true);
  });

  /* Ordenar por cualquier columna, como en una planilla: un click ordena,
     otro invierte. La flecha dice por cual y hacia donde. */
  function pintarOrden() {
    document.querySelectorAll('.orden').forEach(function (boton) {
      const activo = boton.dataset.orden === orden.campo;
      boton.classList.toggle('activo', activo);
      boton.dataset.dir = activo ? orden.dir : '';
    });
  }

  document.querySelectorAll('.orden').forEach(function (boton) {
    boton.addEventListener('click', function () {
      const campo = boton.dataset.orden;
      if (orden.campo === campo) {
        orden.dir = orden.dir === 'asc' ? 'desc' : 'asc';
      } else {
        orden.campo = campo;
        // Un texto se lee mejor de la A a la Z; una fecha o un importe,
        // de mayor a menor.
        orden.dir = ['descripcion', 'categoria', 'persona', 'archivo', 'moneda'].indexOf(campo) >= 0
          ? 'asc' : 'desc';
      }
      pintarOrden();
      offset = 0;
      cargarMovimientos(false);
    });
  });
  pintarOrden();

  /* Elegir un mes llena el rango de fechas: es el mismo filtro, mas comodo. */
  let limpiando = false;
  const selectorMes = document.getElementById('mes');
  if (selectorMes) {
    selectorMes.addEventListener('change', function () {
      if (limpiando) return;  // "Limpiar" ya recarga por su cuenta
      const desde = document.getElementById('desde');
      const hasta = document.getElementById('hasta');
      if (selectorMes.value) {
        const partes = selectorMes.value.split('-');
        const anio = Number(partes[0]);
        const mes = Number(partes[1]);
        const ultimo = new Date(anio, mes, 0).getDate();
        desde.value = selectorMes.value + '-01';
        hasta.value = selectorMes.value + '-' + String(ultimo).padStart(2, '0');
      } else {
        desde.value = '';
        hasta.value = '';
      }
      [desde, hasta].forEach(function (campo) {
        campo.dispatchEvent(new Event('change', { bubbles: true }));
      });
      cargar();
    });
  }

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
