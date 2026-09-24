/* Chat: render de markdown ligero, bloques ```chart y envio al backend. */
(function () {
  const log = document.getElementById('chat-log');
  const form = document.getElementById('chat-form');
  const input = document.getElementById('mensaje');
  const boton = document.getElementById('enviar');
  const estado = document.getElementById('estado');
  const charts = [];

  function escapar(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /* Markdown minimo y seguro: se escapa todo primero y luego se aplican
     unas pocas reglas. Nada de HTML crudo del modelo. */
  function markdown(text) {
    const bloques = [];
    let src = text.replace(/```([a-zA-Z]*)\n([\s\S]*?)```/g, function (_, lang, code) {
      bloques.push({ lang: (lang || '').toLowerCase(), code: code });
      return '\u0000BLOQUE' + (bloques.length - 1) + '\u0000';
    });

    let html = escapar(src);
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^##\s+(.+)$/gm, '<h3>$1</h3>');

    const lineas = html.split('\n');
    const salida = [];
    let lista = null;
    lineas.forEach(function (linea) {
      const punto = linea.match(/^\s*[-*]\s+(.*)$/);
      const numero = linea.match(/^\s*\d+[.)]\s+(.*)$/);
      if (punto || numero) {
        const tipo = punto ? 'ul' : 'ol';
        if (lista !== tipo) {
          if (lista) salida.push('</' + lista + '>');
          salida.push('<' + tipo + '>');
          lista = tipo;
        }
        salida.push('<li>' + (punto ? punto[1] : numero[1]) + '</li>');
      } else {
        if (lista) { salida.push('</' + lista + '>'); lista = null; }
        if (linea.trim()) salida.push('<p>' + linea + '</p>');
      }
    });
    if (lista) salida.push('</' + lista + '>');

    return { html: salida.join('\n'), bloques: bloques };
  }

  function especificacionGrafico(code) {
    try {
      const spec = JSON.parse(code);
      if (!spec || !Array.isArray(spec.labels) || !Array.isArray(spec.series)) return null;
      return spec;
    } catch (e) {
      return null;
    }
  }

  function dibujarGrafico(contenedor, spec) {
    const caja = document.createElement('div');
    caja.className = 'chat-chart';
    if (spec.title) {
      const titulo = document.createElement('div');
      titulo.className = 'card-head';
      titulo.innerHTML = '<h2>' + escapar(spec.title) + '</h2>';
      caja.appendChild(titulo);
    }
    const wrap = document.createElement('div');
    wrap.className = 'chart-box';
    const canvas = document.createElement('canvas');
    wrap.appendChild(canvas);
    caja.appendChild(wrap);
    const leyenda = document.createElement('div');
    leyenda.className = 'legend';
    caja.appendChild(leyenda);
    contenedor.appendChild(caja);

    const tipo = (spec.type || 'bar').toLowerCase();
    const series = spec.series.map(function (s, i) {
      return { label: s.label || ('Serie ' + (i + 1)), data: (s.data || []).map(Number), colorIndex: i };
    });

    let chart;
    if (tipo === 'line') {
      chart = Viz.timeLines(canvas, spec.labels, series);
    } else if (tipo === 'doughnut' || tipo === 'pie') {
      const items = spec.labels.map(function (label, i) {
        return { label: label, value: Number((series[0].data || [])[i] || 0) };
      });
      chart = Viz.doughnut(canvas, items);
      Viz.renderLegend(leyenda, items.map(function (item, i) {
        return { label: item.label, color: Viz.theme.series(i) };
      }));
      charts.push(chart);
      return;
    } else if (series.length > 1) {
      chart = Viz.stackedBars(canvas, spec.labels, series);
    } else {
      const items = spec.labels.map(function (label, i) {
        return { label: label, value: Number(series[0].data[i] || 0) };
      });
      chart = spec.labels.length > 7
        ? Viz.horizontalBars(canvas, items, 0)
        : Viz.verticalBars(canvas, items, 0);
    }
    if (series.length > 1) {
      Viz.renderLegend(leyenda, series.map(function (s, i) {
        return { label: s.label, color: Viz.theme.series(i) };
      }));
    }
    charts.push(chart);
  }

  function pintarBurbuja(bubble, texto) {
    const resultado = markdown(texto);
    let html = resultado.html;
    resultado.bloques.forEach(function (bloque, i) {
      const marca = '\u0000BLOQUE' + i + '\u0000';
      if (bloque.lang === 'chart') {
        html = html.replace(new RegExp('<p>' + marca + '</p>|' + marca), '<div data-grafico="' + i + '"></div>');
      } else {
        html = html.replace(new RegExp('<p>' + marca + '</p>|' + marca),
          '<pre><code>' + escapar(bloque.code) + '</code></pre>');
      }
    });
    bubble.innerHTML = html;
    bubble.querySelectorAll('[data-grafico]').forEach(function (nodo) {
      const bloque = resultado.bloques[parseInt(nodo.getAttribute('data-grafico'), 10)];
      const spec = especificacionGrafico(bloque.code);
      if (spec) {
        dibujarGrafico(nodo, spec);
      } else {
        nodo.innerHTML = '<pre><code>' + escapar(bloque.code) + '</code></pre>';
      }
    });
  }

  function anadirMensaje(rol, texto) {
    const vacio = log.querySelector('[data-vacio]');
    if (vacio) vacio.remove();
    const msg = document.createElement('div');
    msg.className = 'msg ' + rol;
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = rol === 'user' ? 'Tu' : 'IA';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    msg.appendChild(avatar);
    msg.appendChild(bubble);
    log.appendChild(msg);
    pintarBurbuja(bubble, texto);
    log.scrollTop = log.scrollHeight;
    return bubble;
  }

  // Render del historial cargado con la pagina.
  log.querySelectorAll('[data-markdown]').forEach(function (bubble) {
    pintarBurbuja(bubble, bubble.getAttribute('data-markdown'));
    bubble.removeAttribute('data-markdown');
  });
  log.scrollTop = log.scrollHeight;

  async function enviar(texto) {
    if (!texto.trim()) return;
    anadirMensaje('user', texto);
    input.value = '';
    boton.disabled = true;
    input.disabled = true;
    estado.innerHTML = '<span class="spinner"></span> Pensando...';
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ message: texto })
      });
      if (res.status === 401) { window.location.href = '/login'; return; }
      const data = await res.json();
      if (!res.ok) {
        anadirMensaje('assistant', 'No pude responder: ' + (data.detail || res.status));
      } else {
        anadirMensaje('assistant', data.reply);
      }
    } catch (e) {
      anadirMensaje('assistant', 'Error de red: ' + e.message);
    } finally {
      estado.textContent = '';
      boton.disabled = false;
      input.disabled = false;
      input.focus();
    }
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    enviar(input.value);
  });

  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      enviar(input.value);
    }
  });

  document.getElementById('sugerencias').addEventListener('click', function (event) {
    const boton = event.target.closest('button[data-texto]');
    if (boton) enviar(boton.getAttribute('data-texto'));
  });

  document.getElementById('limpiar-chat').addEventListener('click', async function () {
    const acepta = await UI.confirmar({
      titulo: 'Borrar historial',
      mensaje: 'Se borra toda la conversacion. Los datos y los archivos no se tocan.',
      aceptar: 'Borrar',
      peligroso: true
    });
    if (!acepta) return;
    await fetch('/api/chat/limpiar', { method: 'POST', headers: { 'Accept': 'application/json' } });
    window.location.reload();
  });
})();
