/* Componentes propios para reemplazar los controles del sistema.

   Todo funciona por mejora progresiva: el control nativo (<select>, el
   input de archivo, el de fecha) se queda en el DOM, oculto, y sigue
   siendo la fuente de verdad. Encima se dibuja el control disenado y se
   sincronizan. Asi los formularios se envian igual, el JS que lee
   `select.selectedOptions` sigue andando, y si este archivo no carga la
   app queda fea pero usable.

   Para reaccionar a un cambio hecho por codigo, dispara un `change` sobre
   el control nativo y el disenado se actualiza solo. */
(function () {
  'use strict';

  const ANIMAR = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let contador = 0;
  const nuevoId = (prefijo) => prefijo + '-' + (++contador);

  /* ------------------------------------------------------------------ *
   * Avisos (toasts)                                                     *
   * ------------------------------------------------------------------ */
  let pilaToasts = null;

  function contenedorToasts() {
    if (!pilaToasts) {
      pilaToasts = document.createElement('div');
      pilaToasts.className = 'ui-toasts';
      pilaToasts.setAttribute('role', 'status');
      pilaToasts.setAttribute('aria-live', 'polite');
      document.body.appendChild(pilaToasts);
    }
    return pilaToasts;
  }

  function toast(mensaje, tipo, duracion) {
    if (!mensaje) return null;
    const caja = document.createElement('div');
    caja.className = 'ui-toast ui-toast-' + (tipo || 'ok');

    const icono = document.createElement('span');
    icono.className = 'ui-toast-icono';
    icono.setAttribute('aria-hidden', 'true');
    icono.textContent = tipo === 'error' ? '!' : '✓';

    const texto = document.createElement('div');
    texto.className = 'ui-toast-texto';
    // El mensaje puede traer marcado simple desde el servidor.
    texto.innerHTML = mensaje;

    const cerrar = document.createElement('button');
    cerrar.type = 'button';
    cerrar.className = 'ui-toast-cerrar';
    cerrar.setAttribute('aria-label', 'Cerrar aviso');
    cerrar.textContent = '×';

    caja.appendChild(icono);
    caja.appendChild(texto);
    caja.appendChild(cerrar);
    contenedorToasts().appendChild(caja);
    requestAnimationFrame(() => caja.classList.add('visible'));

    let temporizador = null;
    const quitar = () => {
      clearTimeout(temporizador);
      caja.classList.remove('visible');
      if (ANIMAR) {
        setTimeout(() => caja.remove(), 200);
      } else {
        caja.remove();
      }
    };
    // Los errores no se van solos: suelen traer algo que hay que leer.
    const espera = duracion !== undefined ? duracion : (tipo === 'error' ? 0 : 6000);
    if (espera > 0) {
      temporizador = setTimeout(quitar, espera);
      caja.addEventListener('mouseenter', () => clearTimeout(temporizador));
      caja.addEventListener('mouseleave', () => { temporizador = setTimeout(quitar, 2500); });
    }
    cerrar.addEventListener('click', quitar);
    return quitar;
  }

  /* ------------------------------------------------------------------ *
   * Confirmacion                                                        *
   * ------------------------------------------------------------------ */
  function confirmar(opciones) {
    const cfg = Object.assign(
      { titulo: 'Confirmar', mensaje: '', aceptar: 'Aceptar', cancelar: 'Cancelar',
        peligroso: false, opcion: null },
      opciones || {}
    );
    // Con `opcion` el dialogo lleva ademas una casilla y resuelve
    // { ok, opcion } en vez de un booleano, para no romper a quien no la usa.
    return new Promise((resolver) => {
      const previo = document.activeElement;
      const fondo = document.createElement('div');
      fondo.className = 'ui-modal-fondo';

      const caja = document.createElement('div');
      caja.className = 'ui-modal';
      caja.setAttribute('role', 'dialog');
      caja.setAttribute('aria-modal', 'true');
      const idTitulo = nuevoId('ui-modal-titulo');
      caja.setAttribute('aria-labelledby', idTitulo);

      const titulo = document.createElement('h2');
      titulo.id = idTitulo;
      titulo.className = 'ui-modal-titulo';
      titulo.textContent = cfg.titulo;

      const cuerpo = document.createElement('p');
      cuerpo.className = 'ui-modal-texto';
      cuerpo.textContent = cfg.mensaje;

      let casilla = null;
      if (cfg.opcion) {
        const etiqueta = document.createElement('label');
        etiqueta.className = 'ui-modal-opcion';
        casilla = document.createElement('input');
        casilla.type = 'checkbox';
        casilla.checked = !!cfg.opcion.marcado;
        etiqueta.appendChild(casilla);
        etiqueta.appendChild(document.createTextNode(' ' + cfg.opcion.etiqueta));
        caja.appendChild(etiqueta);
      }

      const acciones = document.createElement('div');
      acciones.className = 'ui-modal-acciones';

      const btnCancelar = document.createElement('button');
      btnCancelar.type = 'button';
      btnCancelar.className = 'btn';
      btnCancelar.textContent = cfg.cancelar;

      const btnAceptar = document.createElement('button');
      btnAceptar.type = 'button';
      btnAceptar.className = 'btn ' + (cfg.peligroso ? 'btn-peligro-solido' : 'btn-primary');
      btnAceptar.textContent = cfg.aceptar;

      acciones.appendChild(btnCancelar);
      acciones.appendChild(btnAceptar);
      caja.insertBefore(titulo, caja.firstChild);
      if (cfg.mensaje) caja.insertBefore(cuerpo, titulo.nextSibling);
      caja.appendChild(acciones);
      fondo.appendChild(caja);
      document.body.appendChild(fondo);
      document.body.classList.add('ui-sin-scroll');
      requestAnimationFrame(() => fondo.classList.add('visible'));
      btnAceptar.focus();

      function cerrar(aceptado) {
        const respuesta = cfg.opcion
          ? { ok: aceptado, opcion: !!(casilla && casilla.checked) }
          : aceptado;
        document.removeEventListener('keydown', alTeclado, true);
        fondo.classList.remove('visible');
        const quitar = () => {
          fondo.remove();
          document.body.classList.remove('ui-sin-scroll');
          if (previo && previo.focus) previo.focus();
          resolver(respuesta);
        };
        if (ANIMAR) setTimeout(quitar, 150); else quitar();
      }

      function alTeclado(evento) {
        if (evento.key === 'Escape') {
          evento.preventDefault();
          cerrar(false);
        } else if (evento.key === 'Tab') {
          // El foco no sale del dialogo.
          const focos = casilla ? [casilla, btnCancelar, btnAceptar] : [btnCancelar, btnAceptar];
          const indice = focos.indexOf(document.activeElement);
          evento.preventDefault();
          const siguiente = evento.shiftKey ? indice - 1 : indice + 1;
          focos[(siguiente + focos.length) % focos.length].focus();
        }
      }

      document.addEventListener('keydown', alTeclado, true);
      fondo.addEventListener('mousedown', (e) => { if (e.target === fondo) cerrar(false); });
      btnCancelar.addEventListener('click', () => cerrar(false));
      btnAceptar.addEventListener('click', () => cerrar(true));
    });
  }

  /* ------------------------------------------------------------------ *
   * Panel flotante compartido (selects y calendario)                    *
   *                                                                     *
   * Se cuelga del <body> con position fixed en vez de dentro del        *
   * control: varios selects viven en tablas con overflow, que recortan  *
   * cualquier panel posicionado adentro.                                *
   * ------------------------------------------------------------------ */
  function abrirPanel(ancla, panel, opciones) {
    const cfg = opciones || {};
    document.body.appendChild(panel);
    panel.hidden = false;

    function ubicar() {
      const r = ancla.getBoundingClientRect();
      const alto = panel.offsetHeight;
      const espacioAbajo = window.innerHeight - r.bottom;
      const arriba = espacioAbajo < alto + 12 && r.top > alto + 12;
      panel.style.left = Math.max(8, Math.min(r.left, window.innerWidth - panel.offsetWidth - 8)) + 'px';
      panel.style.top = (arriba ? r.top - alto - 6 : r.bottom + 6) + 'px';
      if (cfg.igualarAncho) panel.style.minWidth = r.width + 'px';
    }

    ubicar();
    // Segunda pasada: la primera midio antes de aplicar minWidth.
    requestAnimationFrame(ubicar);

    const alDesplazar = () => ubicar();
    window.addEventListener('scroll', alDesplazar, true);
    window.addEventListener('resize', alDesplazar);

    function cerrar() {
      window.removeEventListener('scroll', alDesplazar, true);
      window.removeEventListener('resize', alDesplazar);
      document.removeEventListener('mousedown', alClicFuera, true);
      panel.remove();
      if (cfg.alCerrar) cfg.alCerrar();
    }

    function alClicFuera(evento) {
      if (!panel.contains(evento.target) && !ancla.contains(evento.target)) cerrar();
    }
    document.addEventListener('mousedown', alClicFuera, true);
    return cerrar;
  }

  /* ------------------------------------------------------------------ *
   * Select                                                              *
   * ------------------------------------------------------------------ */
  function mejorarSelect(select) {
    if (select.dataset.uiListo || select.dataset.nativo !== undefined) return;
    select.dataset.uiListo = '1';

    const multiple = select.multiple;
    const contenedor = document.createElement('div');
    contenedor.className = 'ui-select' + (select.disabled ? ' deshabilitado' : '');

    const boton = document.createElement('button');
    boton.type = 'button';
    boton.className = 'ui-select-boton';
    boton.disabled = select.disabled;
    boton.setAttribute('aria-haspopup', 'listbox');
    boton.setAttribute('aria-expanded', 'false');
    if (select.id) boton.id = select.id + '-boton';
    const etiqueta = select.id
      ? document.querySelector('label[for="' + CSS.escape(select.id) + '"]')
      : null;
    if (etiqueta) {
      if (!etiqueta.id) etiqueta.id = nuevoId('ui-etiqueta');
      boton.setAttribute('aria-labelledby', etiqueta.id + ' ' + (boton.id || ''));
      etiqueta.addEventListener('click', (e) => { e.preventDefault(); boton.focus(); });
    } else if (select.getAttribute('aria-label')) {
      boton.setAttribute('aria-label', select.getAttribute('aria-label'));
    }

    const texto = document.createElement('span');
    texto.className = 'ui-select-texto';
    const flecha = document.createElement('span');
    flecha.className = 'ui-select-flecha';
    flecha.setAttribute('aria-hidden', 'true');
    boton.appendChild(texto);
    boton.appendChild(flecha);
    contenedor.appendChild(boton);

    select.classList.add('ui-nativo-oculto');
    select.parentNode.insertBefore(contenedor, select);
    contenedor.appendChild(select);

    function opciones() {
      return Array.from(select.options);
    }

    function pintarTexto() {
      const elegidas = opciones().filter((o) => o.selected);
      if (!elegidas.length) {
        texto.textContent = multiple ? 'Todas' : (opciones()[0] ? opciones()[0].text : '');
        texto.classList.toggle('vacio', multiple);
      } else if (elegidas.length === 1) {
        texto.textContent = elegidas[0].text;
        texto.classList.remove('vacio');
      } else {
        texto.textContent = elegidas.length + ' seleccionadas';
        texto.classList.remove('vacio');
      }
    }

    let cerrarPanel = null;
    let activo = 0;

    function abrir() {
      if (cerrarPanel || select.disabled) return;
      const panel = document.createElement('div');
      panel.className = 'ui-select-panel';
      panel.setAttribute('role', 'listbox');
      if (multiple) panel.setAttribute('aria-multiselectable', 'true');
      panel.id = nuevoId('ui-panel');

      const filas = opciones().map((opcion, indice) => {
        const fila = document.createElement('div');
        fila.className = 'ui-select-opcion';
        fila.setAttribute('role', 'option');
        fila.id = panel.id + '-o' + indice;
        fila.setAttribute('aria-selected', opcion.selected ? 'true' : 'false');
        fila.dataset.indice = String(indice);

        const marca = document.createElement('span');
        marca.className = 'ui-select-marca';
        marca.setAttribute('aria-hidden', 'true');
        marca.textContent = opcion.selected ? '✓' : '';
        const nombre = document.createElement('span');
        nombre.textContent = opcion.text;
        fila.appendChild(marca);
        fila.appendChild(nombre);

        fila.addEventListener('mouseenter', () => marcarActivo(indice, false));
        fila.addEventListener('click', () => elegir(indice));
        panel.appendChild(fila);
        return fila;
      });

      function marcarActivo(indice, desplazar) {
        activo = Math.max(0, Math.min(indice, filas.length - 1));
        filas.forEach((f, i) => f.classList.toggle('activa', i === activo));
        const fila = filas[activo];
        if (fila) {
          panel.setAttribute('aria-activedescendant', fila.id);
          if (desplazar !== false) fila.scrollIntoView({ block: 'nearest' });
        }
      }

      function elegir(indice) {
        const opcion = select.options[indice];
        if (!opcion) return;
        if (multiple) {
          opcion.selected = !opcion.selected;
          filas[indice].setAttribute('aria-selected', opcion.selected ? 'true' : 'false');
          filas[indice].querySelector('.ui-select-marca').textContent = opcion.selected ? '✓' : '';
        } else {
          select.selectedIndex = indice;
        }
        select.dispatchEvent(new Event('change', { bubbles: true }));
        pintarTexto();
        if (!multiple) cerrar();
      }

      function cerrar() {
        if (cerrarPanel) {
          const fn = cerrarPanel;
          cerrarPanel = null;
          fn();
        }
      }

      const seleccionada = opciones().findIndex((o) => o.selected);
      cerrarPanel = abrirPanel(boton, panel, {
        igualarAncho: true,
        alCerrar: () => {
          boton.setAttribute('aria-expanded', 'false');
          boton.removeAttribute('aria-controls');
          contenedor.classList.remove('abierto');
          cerrarPanel = null;
        }
      });
      boton.setAttribute('aria-expanded', 'true');
      boton.setAttribute('aria-controls', panel.id);
      contenedor.classList.add('abierto');
      marcarActivo(seleccionada >= 0 ? seleccionada : 0);

      let buscado = '';
      let borrarBusqueda = null;
      panel.dataset.teclado = '1';
      boton._uiTeclado = function (evento) {
        if (evento.key === 'ArrowDown') { evento.preventDefault(); marcarActivo(activo + 1); }
        else if (evento.key === 'ArrowUp') { evento.preventDefault(); marcarActivo(activo - 1); }
        else if (evento.key === 'Home') { evento.preventDefault(); marcarActivo(0); }
        else if (evento.key === 'End') { evento.preventDefault(); marcarActivo(filas.length - 1); }
        else if (evento.key === 'Enter' || evento.key === ' ') { evento.preventDefault(); elegir(activo); }
        else if (evento.key === 'Escape') { evento.preventDefault(); cerrar(); boton.focus(); }
        else if (evento.key === 'Tab') { cerrar(); }
        else if (evento.key.length === 1) {
          // Busqueda al escribir, como en un select nativo.
          buscado += evento.key.toLowerCase();
          clearTimeout(borrarBusqueda);
          borrarBusqueda = setTimeout(() => { buscado = ''; }, 600);
          const encontrado = opciones().findIndex((o) => o.text.toLowerCase().startsWith(buscado));
          if (encontrado >= 0) marcarActivo(encontrado);
        }
      };
    }

    boton.addEventListener('click', () => (cerrarPanel ? cerrarPanel() : abrir()));
    boton.addEventListener('keydown', (evento) => {
      if (cerrarPanel && boton._uiTeclado) return boton._uiTeclado(evento);
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].indexOf(evento.key) !== -1) {
        evento.preventDefault();
        abrir();
      }
    });
    // Cambios hechos por codigo: basta con disparar `change`.
    select.addEventListener('change', pintarTexto);
    pintarTexto();
  }

  /* ------------------------------------------------------------------ *
   * Campo de archivo                                                    *
   * ------------------------------------------------------------------ */
  function mejorarArchivo(input) {
    if (input.dataset.uiListo) return;
    input.dataset.uiListo = '1';

    const zona = document.createElement('div');
    zona.className = 'ui-archivo';
    zona.tabIndex = 0;
    zona.setAttribute('role', 'button');
    const varios = input.multiple;
    zona.setAttribute('aria-label', varios ? 'Elegir archivos' : 'Elegir archivo');

    const icono = document.createElement('span');
    icono.className = 'ui-archivo-icono';
    icono.setAttribute('aria-hidden', 'true');
    icono.textContent = '↑';

    const detalle = document.createElement('div');
    detalle.className = 'ui-archivo-detalle';
    const principal = document.createElement('span');
    principal.className = 'ui-archivo-nombre';
    const secundario = document.createElement('span');
    secundario.className = 'ui-archivo-ayuda';
    detalle.appendChild(principal);
    detalle.appendChild(secundario);

    zona.appendChild(icono);
    zona.appendChild(detalle);

    input.classList.add('ui-nativo-oculto');
    input.parentNode.insertBefore(zona, input);

    function kb(bytes) {
      return bytes >= 1024 * 1024
        ? (bytes / 1024 / 1024).toFixed(1) + ' MB'
        : (bytes / 1024).toFixed(1) + ' KB';
    }

    function pintar() {
      const elegidos = Array.from((input.files || []));
      if (!elegidos.length) {
        zona.classList.remove('con-archivo');
        principal.textContent = varios ? 'Elegi uno o varios archivos' : 'Elegi un archivo';
        secundario.textContent = varios ? 'o arrastralos aca' : 'o arrastralo aca';
        return;
      }
      zona.classList.add('con-archivo');
      const peso = elegidos.reduce((total, f) => total + f.size, 0);
      if (elegidos.length === 1) {
        principal.textContent = elegidos[0].name;
        secundario.textContent = kb(peso) + ' · click para cambiar';
      } else {
        // Con muchos archivos la lista entera no entra: se nombran los
        // primeros y se cuenta el resto.
        const muestra = elegidos.slice(0, 3).map((f) => f.name).join(', ');
        const resto = elegidos.length - 3;
        principal.textContent = elegidos.length + ' archivos';
        secundario.textContent =
          muestra + (resto > 0 ? ' y ' + resto + ' mas' : '') + ' · ' + kb(peso);
      }
    }

    zona.addEventListener('click', () => input.click());
    zona.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
    });
    input.addEventListener('change', pintar);

    ['dragenter', 'dragover'].forEach((evento) =>
      zona.addEventListener(evento, (e) => {
        e.preventDefault();
        zona.classList.add('encima');
      })
    );
    ['dragleave', 'drop'].forEach((evento) =>
      zona.addEventListener(evento, (e) => {
        e.preventDefault();
        zona.classList.remove('encima');
      })
    );
    zona.addEventListener('drop', (e) => {
      const archivos = e.dataTransfer && e.dataTransfer.files;
      if (archivos && archivos.length) {
        input.files = archivos;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    pintar();
  }

  /* ------------------------------------------------------------------ *
   * Fecha                                                               *
   *                                                                     *
   * El input nativo muestra el formato del idioma del navegador, que en  *
   * un Chrome en ingles es mm/dd/aaaa: confuso para una fecha argentina. *
   * ------------------------------------------------------------------ */
  const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const DIAS = ['L', 'M', 'M', 'J', 'V', 'S', 'D'];

  function aTexto(iso) {
    if (!iso) return '';
    const p = iso.split('-');
    return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : '';
  }

  function aIso(texto) {
    const m = /^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$/.exec((texto || '').trim());
    if (!m) return '';
    let [, d, mes, a] = m;
    if (a.length === 2) a = '20' + a;
    const fecha = new Date(Number(a), Number(mes) - 1, Number(d));
    if (fecha.getDate() !== Number(d) || fecha.getMonth() !== Number(mes) - 1) return '';
    return `${a.padStart(4, '0')}-${String(mes).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }

  function mejorarFecha(input) {
    if (input.dataset.uiListo) return;
    input.dataset.uiListo = '1';

    const contenedor = document.createElement('div');
    contenedor.className = 'ui-fecha';

    const campo = document.createElement('input');
    campo.type = 'text';
    campo.className = 'ui-fecha-campo';
    campo.placeholder = 'dd/mm/aaaa';
    campo.inputMode = 'numeric';
    campo.autocomplete = 'off';
    if (input.id) {
      campo.id = input.id + '-texto';
      const etiqueta = document.querySelector('label[for="' + CSS.escape(input.id) + '"]');
      if (etiqueta) etiqueta.setAttribute('for', campo.id);
    }

    const boton = document.createElement('button');
    boton.type = 'button';
    boton.className = 'ui-fecha-boton';
    boton.setAttribute('aria-label', 'Abrir calendario');
    boton.textContent = '▦';

    contenedor.appendChild(campo);
    contenedor.appendChild(boton);
    input.classList.add('ui-nativo-oculto');
    input.parentNode.insertBefore(contenedor, input);
    contenedor.appendChild(input);

    const sincronizar = () => { campo.value = aTexto(input.value); };
    sincronizar();
    input.addEventListener('change', sincronizar);

    campo.addEventListener('change', () => {
      const iso = aIso(campo.value);
      if (!campo.value.trim()) {
        input.value = '';
      } else if (iso) {
        input.value = iso;
      } else {
        campo.value = aTexto(input.value);  // texto invalido: se descarta
        return;
      }
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    let cerrarPanel = null;
    boton.addEventListener('click', () => {
      if (cerrarPanel) { cerrarPanel(); cerrarPanel = null; return; }

      const base = input.value ? new Date(input.value + 'T12:00:00') : new Date();
      let anio = base.getFullYear();
      let mes = base.getMonth();

      const panel = document.createElement('div');
      panel.className = 'ui-calendario';
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-label', 'Calendario');

      function dibujar() {
        panel.innerHTML = '';
        const cabecera = document.createElement('div');
        cabecera.className = 'ui-cal-cabecera';
        const anterior = document.createElement('button');
        anterior.type = 'button';
        anterior.className = 'ui-cal-nav';
        anterior.setAttribute('aria-label', 'Mes anterior');
        anterior.textContent = '‹';
        const titulo = document.createElement('span');
        titulo.className = 'ui-cal-titulo';
        titulo.textContent = MESES[mes] + ' ' + anio;
        const siguiente = document.createElement('button');
        siguiente.type = 'button';
        siguiente.className = 'ui-cal-nav';
        siguiente.setAttribute('aria-label', 'Mes siguiente');
        siguiente.textContent = '›';
        anterior.addEventListener('click', () => {
          mes -= 1; if (mes < 0) { mes = 11; anio -= 1; } dibujar();
        });
        siguiente.addEventListener('click', () => {
          mes += 1; if (mes > 11) { mes = 0; anio += 1; } dibujar();
        });
        cabecera.appendChild(anterior);
        cabecera.appendChild(titulo);
        cabecera.appendChild(siguiente);
        panel.appendChild(cabecera);

        const grilla = document.createElement('div');
        grilla.className = 'ui-cal-grilla';
        DIAS.forEach((d) => {
          const celda = document.createElement('span');
          celda.className = 'ui-cal-dia-nombre';
          celda.textContent = d;
          grilla.appendChild(celda);
        });

        const primero = new Date(anio, mes, 1);
        const desplazamiento = (primero.getDay() + 6) % 7;  // la semana empieza el lunes
        const diasMes = new Date(anio, mes + 1, 0).getDate();
        for (let i = 0; i < desplazamiento; i += 1) {
          grilla.appendChild(document.createElement('span'));
        }
        const hoy = new Date();
        const hoyIso = `${hoy.getFullYear()}-${String(hoy.getMonth() + 1).padStart(2, '0')}-${String(hoy.getDate()).padStart(2, '0')}`;
        for (let dia = 1; dia <= diasMes; dia += 1) {
          const iso = `${anio}-${String(mes + 1).padStart(2, '0')}-${String(dia).padStart(2, '0')}`;
          const celda = document.createElement('button');
          celda.type = 'button';
          celda.className = 'ui-cal-dia';
          if (iso === input.value) celda.classList.add('elegido');
          if (iso === hoyIso) celda.classList.add('hoy');
          celda.textContent = String(dia);
          celda.addEventListener('click', () => {
            input.value = iso;
            campo.value = aTexto(iso);
            input.dispatchEvent(new Event('change', { bubbles: true }));
            if (cerrarPanel) { cerrarPanel(); cerrarPanel = null; }
          });
          grilla.appendChild(celda);
        }
        panel.appendChild(grilla);

        const pie = document.createElement('div');
        pie.className = 'ui-cal-pie';
        const limpiar = document.createElement('button');
        limpiar.type = 'button';
        limpiar.className = 'btn-sm';
        limpiar.textContent = 'Borrar';
        limpiar.addEventListener('click', () => {
          input.value = '';
          campo.value = '';
          input.dispatchEvent(new Event('change', { bubbles: true }));
          if (cerrarPanel) { cerrarPanel(); cerrarPanel = null; }
        });
        pie.appendChild(limpiar);
        panel.appendChild(pie);
      }

      dibujar();
      cerrarPanel = abrirPanel(contenedor, panel, { alCerrar: () => { cerrarPanel = null; } });
    });
  }

  /* ------------------------------------------------------------------ *
   * Menu desplegable (el de usuario, arriba a la derecha)               *
   *                                                                     *
   * Declarativo: un contenedor [data-menu] con un boton [data-menu-boton]
   * y un panel [data-menu-panel]. Se cierra con Escape, al clickear      *
   * afuera y al elegir algo.                                            *
   * ------------------------------------------------------------------ */
  function mejorarMenu(caja) {
    if (caja.dataset.uiListo) return;
    caja.dataset.uiListo = '1';
    const boton = caja.querySelector('[data-menu-boton]');
    const panel = caja.querySelector('[data-menu-panel]');
    if (!boton || !panel) return;

    function abierto() { return !panel.hidden; }

    function cerrar(devolverFoco) {
      if (!abierto()) return;
      panel.hidden = true;
      caja.classList.remove('abierto');
      boton.setAttribute('aria-expanded', 'false');
      document.removeEventListener('keydown', alTeclado, true);
      document.removeEventListener('mousedown', alClickAfuera, true);
      if (devolverFoco) boton.focus();
    }

    function alTeclado(evento) {
      if (evento.key === 'Escape') { evento.preventDefault(); cerrar(true); }
    }

    function alClickAfuera(evento) {
      if (!caja.contains(evento.target)) cerrar(false);
    }

    function abrir() {
      panel.hidden = false;
      caja.classList.add('abierto');
      boton.setAttribute('aria-expanded', 'true');
      document.addEventListener('keydown', alTeclado, true);
      document.addEventListener('mousedown', alClickAfuera, true);
      const primero = panel.querySelector('a, button');
      if (primero) primero.focus();
    }

    boton.addEventListener('click', () => (abierto() ? cerrar(true) : abrir()));
    // Un enlace o un submit cierran solos; el resto (cambiar el tema) no.
    panel.addEventListener('click', (evento) => {
      if (evento.target.closest('[data-menu-queda]')) return;
      cerrar(false);
    });
  }

  /* ------------------------------------------------------------------ *
   * Arranque                                                            *
   * ------------------------------------------------------------------ */
  function mejorar(raiz) {
    const ambito = raiz || document;
    ambito.querySelectorAll('[data-menu]').forEach(mejorarMenu);
    ambito.querySelectorAll('select').forEach(mejorarSelect);
    ambito.querySelectorAll('input[type="file"]').forEach(mejorarArchivo);
    ambito.querySelectorAll('input[type="date"]').forEach(mejorarFecha);
  }

  function convertirFlashes() {
    document.querySelectorAll('.flash[data-toast]').forEach((caja) => {
      toast(caja.innerHTML, caja.classList.contains('err') ? 'error' : 'ok');
      caja.remove();
    });
  }

  function engancharConfirmaciones() {
    document.querySelectorAll('form[data-confirmar]').forEach((form) => {
      form.addEventListener('submit', async (evento) => {
        if (form.dataset.uiConfirmado) {
          delete form.dataset.uiConfirmado;
          return;
        }
        evento.preventDefault();
        const acepta = await confirmar({
          titulo: form.dataset.confirmarTitulo || 'Confirmar',
          mensaje: form.dataset.confirmar,
          aceptar: form.dataset.confirmarAceptar || 'Confirmar',
          peligroso: form.dataset.confirmarPeligroso !== undefined
        });
        if (acepta) {
          form.dataset.uiConfirmado = '1';
          if (typeof form.requestSubmit === 'function') form.requestSubmit();
          else form.submit();
        }
      });
    });
  }

  function iniciar() {
    mejorar(document);
    convertirFlashes();
    engancharConfirmaciones();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }

  window.UI = { toast, confirmar, mejorar, aIso, aTexto };
})();
