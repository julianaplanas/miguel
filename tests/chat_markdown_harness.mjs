/* Carga app/static/js/chat.js sin DOM y comprueba el render de markdown.

   El bug que motiva esto: el modelo responde con tablas | a | b | y lineas
   --- de separacion, y el chat las pintaba como texto plano con barras. */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const fuente = readFileSync(new URL('../app/static/js/chat.js', import.meta.url), 'utf8');

function escaparHtml(texto) {
  return String(texto)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Un document minimo: getElementById no encuentra nada (asi chat.js solo
// expone el markdown) y createElement sirve para escapar texto.
const ventana = {
  document: {
    getElementById: () => null,
    createElement: () => {
      const nodo = { _t: '' };
      Object.defineProperty(nodo, 'textContent', { set(v) { nodo._t = v; } });
      Object.defineProperty(nodo, 'innerHTML', { get() { return escaparHtml(nodo._t); } });
      return nodo;
    }
  }
};
ventana.window = ventana;
vm.createContext(ventana);
vm.runInContext(fuente, ventana);

const md = (texto) => ventana.ChatMarkdown(texto).html;
const fallos = [];
function espera(nombre, html, ...trozos) {
  for (const trozo of trozos) {
    if (!html.includes(trozo)) fallos.push(`${nombre}: falta ${JSON.stringify(trozo)} en ${html}`);
  }
}
function noEspera(nombre, html, ...trozos) {
  for (const trozo of trozos) {
    if (html.includes(trozo)) fallos.push(`${nombre}: sobra ${JSON.stringify(trozo)} en ${html}`);
  }
}

const tabla = md(
  'POR BANCO:\n\n| Banco | Gasto Total (ARS) | % |\n|-------|------|---|\n| **Galicia** | 8.284.184,76 | 85,7% |\n| Santander | 1.388.119,20 | 14,3% |\n\nFin.'
);
espera('tabla', tabla,
  '<table>',
  '<tr><th>Banco</th><th>Gasto Total (ARS)</th><th>%</th></tr>',
  '<td><strong>Galicia</strong></td><td class="num">8.284.184,76</td><td class="num">85,7%</td>',
  '<p>Fin.</p>');
noEspera('tabla', tabla, '-----', '<p>|');

const sinBordes = md('a | b\n--- | ---\n1 | 2');
// Sin barra al principio y al final no es una tabla para este render:
// se queda como texto, pero no rompe nada.
noEspera('sin bordes', sinBordes, '<table>');

const separador = md('Antes\n\n---\n\nDespues');
espera('separador', separador, '<p>Antes</p>', '<hr>', '<p>Despues</p>');
noEspera('separador', separador, '<p>---</p>');

const listaYtabla = md('- uno\n- dos\n| a | b |\n|---|---|\n| 1 | 2 |');
espera('lista y tabla', listaYtabla, '</ul>\n<div class="table-wrap"><table>');

const alineada = md('| a | b |\n|:---|---:|\n| x | 1 |');
espera('alineacion', alineada, '<td>x</td><td class="num">1</td>');

const escapada = md('| <b>x</b> | 1 |\n|---|---|\n| y | 2 |');
espera('escapado', escapada, '&lt;b&gt;x&lt;/b&gt;');
noEspera('escapado', escapada, '<b>x</b>');

if (fallos.length) {
  console.error(fallos.join('\n'));
  process.exit(1);
}
console.log('ok');
