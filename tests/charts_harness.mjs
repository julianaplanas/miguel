/* Carga app/static/js/charts.js con un DOM minimo y comprueba pointValue.

   El bug que motiva esto: en barras horizontales (indexAxis 'y') el valor
   esta en parsed.x y en parsed.y va el indice de la fila, asi que el
   tooltip mostraba 0, 1, 2... en vez del importe. */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const fuente = readFileSync(new URL('../app/static/js/charts.js', import.meta.url), 'utf8');

const ventana = {
  APP_CURRENCY: 'ARS',
  APP_LOCALE: 'es-AR',
  Intl,
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  document: { documentElement: {}, createElement: () => ({ style: {}, appendChild() {} }) }
};
ventana.window = ventana;
vm.createContext(ventana);
vm.runInContext(fuente, ventana);

const casos = [
  ['barra horizontal', { parsed: { x: 480000, y: 2 }, chart: { options: { indexAxis: 'y' } } }, 480000],
  ['barra vertical', { parsed: { x: 3, y: 12500 }, chart: { options: {} } }, 12500],
  ['linea', { parsed: { x: 1, y: 98000 }, chart: { options: {} } }, 98000],
  ['doughnut', { parsed: 2550, chart: { options: {} } }, 2550],
  ['apilada horizontal', { parsed: { x: 76200, y: 0 }, chart: { options: { indexAxis: 'y' } } }, 76200],
  ['valor cero en horizontal', { parsed: { x: 0, y: 1 }, chart: { options: { indexAxis: 'y' } } }, 0],
  ['sin parsed usa raw', { raw: 123.45, chart: { options: {} } }, 123.45]
];

const fallos = [];
for (const [nombre, ctx, esperado] of casos) {
  const obtenido = ventana.Viz.pointValue(ctx);
  if (obtenido !== esperado) {
    fallos.push(`${nombre}: esperaba ${esperado}, dio ${obtenido}`);
  }
}

if (fallos.length) {
  console.error(fallos.join('\n'));
  process.exit(1);
}
console.log('ok');
